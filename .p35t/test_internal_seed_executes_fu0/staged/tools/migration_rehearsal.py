"""数据库迁移预演工具。

对真实库或指定源库做只读预演：复制到临时目录，在副本上执行迁移或 stamp-only，
再检查 integrity、业务表行数和当前迁移 schema 等价性。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "update_tools"))

from migrate_db import run_migrations  # noqa: E402
from backend.schema_contracts import schema_signature  # noqa: E402


@dataclass
class RehearsalResult:
    target: str
    mode: str
    source_db: str
    copy_db: str
    ok: bool
    integrity_ok: bool = False
    schema_matches_runtime: bool = False
    business_row_count_changes: dict[str, tuple[int, int]] = field(default_factory=dict)
    qb_005_changed_paper_rows: int | None = None
    migrations_error: str | None = None
    messages: list[str] = field(default_factory=list)


def _business_row_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations'"
            ).fetchall()
        ]
        counts: dict[str, int] = {}
        for table in tables:
            counts[table] = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        return counts
    finally:
        conn.close()


def _paper_field_snapshot(db_path: Path) -> dict[int, tuple[object, object, object]]:
    conn = sqlite3.connect(db_path)
    try:
        if not _table_exists(conn, "papers"):
            return {}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(papers)").fetchall()}
        if not {"id", "province", "city", "semester"}.issubset(columns):
            return {}
        return {
            int(row[0]): (row[1], row[2], row[3])
            for row in conn.execute("SELECT id, province, city, semester FROM papers").fetchall()
        }
    finally:
        conn.close()


def _changed_paper_rows(before: dict[int, tuple[object, object, object]], db_path: Path) -> int:
    if not before:
        return 0
    after = _paper_field_snapshot(db_path)
    return sum(1 for paper_id, values in before.items() if after.get(paper_id) != values)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
    )


def _integrity_ok(db_path: Path) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def _copy_database_snapshot(source_db: Path, copy_db: Path) -> None:
    """Create a transactionally consistent copy, including committed WAL data."""
    from question_bank.services.question_read_service import (
        captured_sqlite_snapshot_path,
    )

    copy_db.unlink(missing_ok=True)
    with captured_sqlite_snapshot_path(
        source_db,
        required_tables=frozenset(),
    ) as candidate:
        with closing(sqlite3.connect(candidate)) as source:
            with closing(sqlite3.connect(copy_db)) as destination:
                source.backup(destination)


def _normalized_sql(sql: object) -> str:
    return re.sub(r"\s+", " ", str(sql)).strip().lower().replace("if not exists ", "")


def _table_names(conn: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations'"
        ).fetchall()
    ]


def _column_snapshot(conn: sqlite3.Connection, table_name: str) -> dict[str, tuple[str, int, str, int]]:
    columns: dict[str, tuple[str, int, str, int]] = {}
    for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall():
        column_name = str(row[1])
        notnull = int(row[3])
        # 旧阅卷库的 answer_regions.region_uuid 是后补字段，SQLite 无法原地
        # 改成 NOT NULL；运行时已回填数据并用触发器禁止后续空值。
        if table_name == "answer_regions" and column_name == "region_uuid":
            notnull = 0
        columns[column_name] = (
            str(row[2] or "").lower(),
            notnull,
            _normalized_sql(row[4]),
            int(row[5]),
        )
    return columns


def _foreign_key_snapshot(conn: sqlite3.Connection, table_name: str) -> set[tuple[object, ...]]:
    return {
        (row[2], row[3], row[4], row[5], row[6])
        for row in conn.execute(f'PRAGMA foreign_key_list("{table_name}")').fetchall()
    }


def _normalized_schema_sql(db_path: Path) -> dict[tuple[str, str], str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations' "
            "AND type IN ('index', 'trigger')"
        ).fetchall()
    finally:
        conn.close()
    result: dict[tuple[str, str], str] = {}
    for type_, name, sql in rows:
        result[(str(type_), str(name))] = _normalized_sql(sql)
    return result


def _semantic_schema(db_path: Path) -> dict[str, object]:
    return schema_signature(db_path)


def schemas_equivalent(left_db: Path, right_db: Path) -> bool:
    """比较 schema 语义是否等价。

    历史库中有些字段来自 ALTER TABLE ADD COLUMN，SQLite 会把新增字段放在表尾，
    与新建空库的 CREATE TABLE 文本顺序不同。这里按字段名比较列定义，避免把
    这种历史列顺序差异误判为漂移。
    """
    return _semantic_schema(left_db) == _semantic_schema(right_db)


def _build_current_migration_reference(
    target: str,
    output_db: Path,
    migrations_dir: Path,
) -> None:
    report = run_migrations(
        target,
        db_path=output_db,
        migrations_dir=migrations_dir,
    )
    if report.error:
        raise RuntimeError(report.error)


def _schema_matches_current_migrations(
    target: str,
    migrated_db: Path,
    work_dir: Path,
    migrations_dir: Path,
) -> bool:
    reference_db = work_dir / f"{target}_migration_reference.db"
    _build_current_migration_reference(target, reference_db, migrations_dir)
    return schemas_equivalent(reference_db, migrated_db)


def rehearse_database(
    target: str,
    *,
    source_db: Path,
    migrations_dir: Path,
    mode: str,
    work_dir: Path,
) -> RehearsalResult:
    if mode not in {"execute", "stamp-only"}:
        raise ValueError("mode must be 'execute' or 'stamp-only'")
    if not source_db.exists():
        return RehearsalResult(
            target=target,
            mode=mode,
            source_db=str(source_db),
            copy_db="",
            ok=False,
            messages=[f"source database does not exist: {source_db}"],
        )

    work_dir.mkdir(parents=True, exist_ok=True)
    copy_db = work_dir / f"{target}_{mode}.db"
    _copy_database_snapshot(source_db, copy_db)

    before_counts = _business_row_counts(copy_db)
    paper_snapshot = _paper_field_snapshot(copy_db) if target == "question_bank" and mode == "execute" else {}
    report = run_migrations(
        target,
        db_path=copy_db,
        migrations_dir=migrations_dir,
        stamp_only=(mode == "stamp-only"),
    )
    after_counts = _business_row_counts(copy_db)
    tables = sorted(set(before_counts) | set(after_counts))
    count_changes = {
        table: (before_counts.get(table, 0), after_counts.get(table, 0))
        for table in tables
        if before_counts.get(table, 0) != after_counts.get(table, 0)
    }

    integrity_ok = _integrity_ok(copy_db)
    schema_matches = _schema_matches_current_migrations(
        target,
        copy_db,
        work_dir,
        migrations_dir,
    )
    qb_005_changed = (
        _changed_paper_rows(paper_snapshot, copy_db)
        if target == "question_bank" and mode == "execute"
        else None
    )

    messages: list[str] = []
    if report.error:
        messages.append(report.error)
    if not integrity_ok:
        messages.append("PRAGMA integrity_check failed")
    if not schema_matches:
        messages.append("schema differs from current migrations")
    if count_changes:
        messages.append(f"business row count changes: {count_changes}")

    return RehearsalResult(
        target=target,
        mode=mode,
        source_db=str(source_db),
        copy_db=str(copy_db),
        ok=not report.error and integrity_ok and schema_matches and not count_changes,
        integrity_ok=integrity_ok,
        schema_matches_runtime=schema_matches,
        business_row_count_changes=count_changes,
        qb_005_changed_paper_rows=qb_005_changed,
        migrations_error=report.error,
        messages=messages,
    )


def _default_source_db(target: str) -> Path:
    from path_manager import get_path_manager

    pm = get_path_manager()
    if target == "grading":
        return pm.db_path
    if target == "question_bank":
        return pm.qb_db_path
    raise ValueError(f"unknown target: {target}")


def _default_migrations_dir(target: str) -> Path:
    return _PROJECT_ROOT / "migrations" / target


def _result_payload(result: RehearsalResult) -> dict[str, object]:
    return {
        "target": result.target,
        "mode": result.mode,
        "source_db": result.source_db,
        "copy_db": result.copy_db,
        "ok": result.ok,
        "integrity_ok": result.integrity_ok,
        "schema_matches_runtime": result.schema_matches_runtime,
        "business_row_count_changes": result.business_row_count_changes,
        "qb_005_changed_paper_rows": result.qb_005_changed_paper_rows,
        "migrations_error": result.migrations_error,
        "messages": result.messages,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="在副本库上预演数据库迁移")
    parser.add_argument("--target", choices=["grading", "question_bank"], help="只预演一个目标库")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="预演副本输出目录；默认使用临时目录",
    )
    args = parser.parse_args(argv)

    targets = [args.target] if args.target else ["grading", "question_bank"]
    owned_temp: tempfile.TemporaryDirectory[str] | None = None
    if args.work_dir is None:
        owned_temp = tempfile.TemporaryDirectory(prefix="migration_rehearsal_")
        work_root = Path(owned_temp.name)
    else:
        work_root = args.work_dir

    try:
        results: list[RehearsalResult] = []
        for target in targets:
            modes = ["execute"]
            if target == "question_bank":
                modes.append("stamp-only")
            for mode in modes:
                result = rehearse_database(
                    target,
                    source_db=_default_source_db(target),
                    migrations_dir=_default_migrations_dir(target),
                    mode=mode,
                    work_dir=work_root / target / mode,
                )
                results.append(result)
                status = "OK" if result.ok else "FAIL"
                print(f"[{status}] {target} {mode}: {result.copy_db}")
                if result.qb_005_changed_paper_rows is not None:
                    print(f"  QB 005 changed paper rows: {result.qb_005_changed_paper_rows}")
                for message in result.messages:
                    print(f"  - {message}")

        print(json.dumps([_result_payload(item) for item in results], ensure_ascii=False, indent=2))
        return 0 if all(item.ok for item in results) else 1
    finally:
        if owned_temp is not None:
            owned_temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
