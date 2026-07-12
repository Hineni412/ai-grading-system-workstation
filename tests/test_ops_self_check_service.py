from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from backend.ops.service import OpsSelfCheckService


def _database_state(path: Path) -> dict[str, bytes | None]:
    return {
        suffix: candidate.read_bytes() if candidate.exists() else None
        for suffix in ("", "-wal", "-shm", "-journal")
        for candidate in (Path(f"{path}{suffix}"),)
    }


def _make_pm(tmp_path: Path) -> SimpleNamespace:
    data = tmp_path / "data"
    databases = data / "databases"
    backups = data / "backups"
    logs = tmp_path / "logs"
    for directory in (data, databases, backups, logs, data / "reports", data / "outputs"):
        directory.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        version="v-test",
        data_root=data,
        databases_dir=databases,
        backups_dir=backups,
        logs_dir=logs,
        reports_dir=data / "reports",
        outputs_dir=data / "outputs",
        db_path=databases / "grading_system.db",
        qb_db_path=databases / "question_bank.db",
        api_profiles_path=tmp_path / "api_profiles.json",
    )


def _create_database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations ("
            "id INTEGER PRIMARY KEY, migration_name TEXT, checksum TEXT, "
            "success INTEGER, applied_at TEXT)"
        )


def test_snapshot_reads_candidates_without_changing_source_files(tmp_path: Path) -> None:
    pm = _make_pm(tmp_path)
    _create_database(pm.db_path)
    _create_database(pm.qb_db_path)
    before = {
        "grading": _database_state(pm.db_path),
        "question_bank": _database_state(pm.qb_db_path),
    }
    service = OpsSelfCheckService(
        pm,
        tool_checker=lambda _key: True,
        zip_backup_loader=lambda: [],
    )

    snapshot = service.build_snapshot()

    assert {item["key"] for item in snapshot["databases"]} == {
        "grading",
        "question_bank",
    }
    assert all(item["integrity"] == "ok" for item in snapshot["databases"])
    assert _database_state(pm.db_path) == before["grading"]
    assert _database_state(pm.qb_db_path) == before["question_bank"]
    assert not any(pm.data_root.glob(".ops-write-probe-*"))


def test_bad_database_is_path_free_error_item(tmp_path: Path) -> None:
    pm = _make_pm(tmp_path)
    pm.db_path.write_text("not a database C:/private/student-answer", encoding="utf-8")
    service = OpsSelfCheckService(
        pm,
        tool_checker=lambda _key: True,
        zip_backup_loader=lambda: [],
    )

    snapshot = service.build_snapshot()
    grading = next(item for item in snapshot["databases"] if item["key"] == "grading")

    assert grading["status"] == "error"
    assert grading["integrity"] == "unavailable"
    serialized = json.dumps(snapshot).lower()
    assert "not a database" not in serialized
    assert "c:/private" not in serialized


def test_snapshot_reports_only_boolean_api_configuration_and_tool_ids(tmp_path: Path) -> None:
    pm = _make_pm(tmp_path)
    pm.api_profiles_path.write_text(
        json.dumps([{"name": "secret profile", "api_key": "sk-secret"}]),
        encoding="utf-8",
    )
    service = OpsSelfCheckService(
        pm,
        tool_checker=lambda key: key == "microsoft_word",
        zip_backup_loader=lambda: [],
    )

    snapshot = service.build_snapshot()

    assert snapshot["api_configured"] is True
    assert [item["key"] for item in snapshot["tools"]] == [
        "microsoft_word",
        "libreoffice",
        "pdflatex",
    ]
    serialized = json.dumps(snapshot)
    assert "sk-secret" not in serialized
    assert "secret profile" not in serialized


def test_backup_projection_drops_paths_sorts_and_limits(tmp_path: Path) -> None:
    pm = _make_pm(tmp_path)
    older = pm.backups_dir / "grading_before_startup_20260711_120000.db"
    newer = pm.backups_dir / "question_bank_before_stamp_20260712_120000.db"
    older.write_bytes(b"old")
    newer.write_bytes(b"newer")
    older.touch()
    newer.touch()
    service = OpsSelfCheckService(
        pm,
        tool_checker=lambda _key: True,
        zip_backup_loader=lambda: [
            {
                "filename": "backup_20260713_120000_manual.zip",
                "path": "C:/private/backups/backup.zip",
                "size": 99,
                "time": "2026-07-13 12:00:00",
                "reason": "manual api_key=sk-secret",
                "mtime": 9_999_999_999.0,
            }
        ],
    )

    payload = service.list_backups(limit=2)

    assert payload["returned"] == 2
    assert payload["items"][0]["kind"] == "zip"
    assert payload["items"][0]["reason"] == "manual"
    assert all(
        set(item) == {"kind", "filename", "created_at", "reason", "size_bytes"}
        for item in payload["items"]
    )
    assert "C:/private" not in json.dumps(payload)
    assert "sk-secret" not in json.dumps(payload)
