from __future__ import annotations

import hashlib
import json
import shutil
import socket
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = (PROJECT_ROOT / "user_data" / "databases" / "question_bank.db").resolve()
BACKUP_ROOT = Path(
    r"D:\AI阅卷系统_工作机版_v1.5.0.user-data-swap-20260806-6df9a03d"
).resolve()
EXPECTED_OLD_RELEASE = "kgr_junior_math_2026_08_v1"
EXPECTED_NEW_RELEASE = "kgr_bnu_math_curriculum_2026_08_v2"
HISTORICAL_TABLES = (
    "papers",
    "questions",
    "question_tags",
    "question_fingerprints",
)


def _encode(value: Any) -> bytes:
    if value is None:
        return b"n;"
    if isinstance(value, bytes):
        return b"b:" + value.hex().encode("ascii") + b";"
    if isinstance(value, str):
        return b"s:" + value.encode("utf-8") + b";"
    if isinstance(value, int):
        return b"i:" + str(value).encode("ascii") + b";"
    if isinstance(value, float):
        return b"f:" + value.hex().encode("ascii") + b";"
    raise TypeError(type(value).__name__)


def _table_digest(connection: sqlite3.Connection, table: str) -> dict[str, Any]:
    quoted = '"' + table.replace('"', '""') + '"'
    digest = hashlib.sha256()
    count = 0
    for row in connection.execute(f"SELECT rowid, * FROM {quoted} ORDER BY rowid"):
        count += 1
        for value in row:
            digest.update(_encode(value))
    return {"rows": count, "sha256": digest.hexdigest()}


def _snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    active = connection.execute(
        """
        SELECT release_id, taxonomy_revision, status
        FROM knowledge_graph_releases
        WHERE status = 'active'
        """
    ).fetchone()
    return {
        "integrity_check": integrity,
        "active_release": None
        if active is None
        else {
            "release_id": str(active[0]),
            "taxonomy_revision": int(active[1]),
            "status": str(active[2]),
        },
        "historical_tables": {
            table: _table_digest(connection, table) for table in HISTORICAL_TABLES
        },
    }


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30)
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def _backup(source: Path, destination: Path) -> dict[str, Any]:
    with _connect(source) as source_connection, _connect(destination) as backup_connection:
        source_connection.backup(backup_connection)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    with _connect(destination) as connection:
        snapshot = _snapshot(connection)
    return {
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "sha256": digest,
        "snapshot": snapshot,
    }


def _assert_service_stopped() -> None:
    with socket.socket() as client:
        client.settimeout(0.75)
        if client.connect_ex(("127.0.0.1", 8035)) == 0:
            raise RuntimeError("port 8035 is listening; refusing live database activation")


def _verify_release(path: Path, before: dict[str, Any]) -> dict[str, Any]:
    from question_bank.knowledge_graph_release import (
        load_active_release,
        load_taxonomy_catalog_for_release,
        validate_release,
    )

    active = load_active_release(path)
    if active is None:
        raise RuntimeError("no active release after activation")
    catalog = load_taxonomy_catalog_for_release(active)
    report = validate_release(active, catalog)
    payload = active.payload
    result = {
        "release_id": active.release_id,
        "taxonomy_revision": active.taxonomy_revision,
        "content_hash": active.content_hash,
        "valid": report.valid,
        "catalog_knowledge_terms": sum(
            1
            for item in catalog.get("terms", [])
            if item.get("dimension") == "knowledge"
        ),
        "core_nodes": len(payload.get("core_nodes", [])),
        "fine_term_dispositions": len(payload.get("fine_term_dispositions", [])),
        "mappings": len(payload.get("mappings", [])),
        "relations": len(payload.get("relations", [])),
    }
    expected = {
        "release_id": EXPECTED_NEW_RELEASE,
        "taxonomy_revision": 4,
        "valid": True,
        "catalog_knowledge_terms": 1124,
        "core_nodes": 1124,
        "fine_term_dispositions": 1124,
        "mappings": 1124,
        "relations": 1088,
    }
    for key, value in expected.items():
        if result[key] != value:
            raise RuntimeError(f"release verification failed for {key}: {result[key]!r}")

    with _connect(path) as connection:
        after = _snapshot(connection)
    if after["integrity_check"] != "ok":
        raise RuntimeError(f"database integrity check failed: {after['integrity_check']}")
    if after["historical_tables"] != before["historical_tables"]:
        raise RuntimeError("historical question tables changed during release activation")
    result["database_snapshot"] = after
    return result


def _activate(path: Path, before: dict[str, Any]) -> dict[str, Any]:
    from question_bank.knowledge_graph_release import (
        activate_release,
        load_release,
        preview_install,
        stage_release,
    )

    release = load_release()
    preview = preview_install(path, release)
    if not preview.can_activate or preview.current_release_id != EXPECTED_OLD_RELEASE:
        raise RuntimeError("release preview no longer matches the authorized rev3-to-rev4 switch")
    stage_release(
        path,
        release,
        actor_ref="local-teacher-authorized-activation",
        source_reference="bundled:curriculum-knowledge-standard-v2",
        reason="教师授权：备份后暂存五册教材知识标准 rev4",
    )
    activate_release(
        path,
        release.release_id,
        expected_active_release_id=EXPECTED_OLD_RELEASE,
        actor_ref="local-teacher-authorized-activation",
        reason="教师授权：将活动知识标准从 rev3 切换到 rev4，不改写历史标签",
    )
    return _verify_release(path, before)


def _rollback_or_restore(backup_path: Path) -> str:
    from question_bank.knowledge_graph_release import active_release_id, rollback_release

    try:
        current = active_release_id(DB_PATH)
        if current == EXPECTED_NEW_RELEASE:
            rollback_release(
                DB_PATH,
                EXPECTED_OLD_RELEASE,
                expected_active_release_id=EXPECTED_NEW_RELEASE,
                actor_ref="local-activation-safety-rollback",
                reason="rev4 激活后校验失败，自动回退到已备份的 rev3",
            )
        if active_release_id(DB_PATH) == EXPECTED_OLD_RELEASE:
            return "release_rollback"
    except Exception:
        pass

    with _connect(backup_path) as source_connection, _connect(DB_PATH) as target_connection:
        source_connection.backup(target_connection)
    with _connect(DB_PATH) as restored:
        restored_snapshot = _snapshot(restored)
    active = restored_snapshot["active_release"]
    if restored_snapshot["integrity_check"] != "ok" or not active:
        raise RuntimeError("backup restore completed but verification failed")
    if active["release_id"] != EXPECTED_OLD_RELEASE:
        raise RuntimeError("backup restore did not return to rev3")
    return "full_backup_restore"


def main() -> int:
    _assert_service_stopped()
    expected_db = PROJECT_ROOT / "user_data" / "databases" / "question_bank.db"
    if DB_PATH != expected_db.resolve() or not DB_PATH.is_file():
        raise RuntimeError(f"unexpected database target: {DB_PATH}")
    if not BACKUP_ROOT.is_dir():
        raise RuntimeError(f"verified backup container is missing: {BACKUP_ROOT}")

    with _connect(DB_PATH) as connection:
        before = _snapshot(connection)
    active = before["active_release"]
    if before["integrity_check"] != "ok":
        raise RuntimeError("source database integrity check failed before backup")
    if not active or active["release_id"] != EXPECTED_OLD_RELEASE:
        raise RuntimeError("source database is not on the authorized rev3 release")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    operation_dir = BACKUP_ROOT / f"question-bank-rev4-activation-{stamp}"
    operation_dir.mkdir(parents=False, exist_ok=False)
    backup_path = operation_dir / "question_bank_rev3_before_rev4.db"
    backup = _backup(DB_PATH, backup_path)
    if backup["snapshot"] != before:
        raise RuntimeError("backup snapshot does not match the source database")

    rehearsal_path = operation_dir / "question_bank_rev4_rehearsal.db"
    shutil.copy2(backup_path, rehearsal_path)
    rehearsal = _activate(rehearsal_path, before)
    rehearsal["path"] = str(rehearsal_path)

    try:
        activation = _activate(DB_PATH, before)
    except Exception:
        recovery = _rollback_or_restore(backup_path)
        print(json.dumps({"status": "rolled_back", "recovery": recovery}, ensure_ascii=False))
        raise

    audit = {
        "schema_version": 1,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": str(DB_PATH),
        "backup": backup,
        "rehearsal": rehearsal,
        "activation": activation,
        "historical_tables_unchanged": True,
        "service_started": False,
    }
    audit_path = operation_dir / "activation_audit.json"
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "activated",
                "database": str(DB_PATH),
                "backup_path": str(backup_path),
                "backup_sha256": backup["sha256"],
                "audit_path": str(audit_path),
                "release": activation,
                "historical_tables_unchanged": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"rev4 activation failed safely: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
