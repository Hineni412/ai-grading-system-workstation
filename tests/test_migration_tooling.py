"""migrate_db 工具增强：触发器切分、路径覆盖、stamp-only 打标。"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "update_tools"))

import migrate_db  # noqa: E402
from migrate_db import run_migrations  # noqa: E402


TRIGGER_MIGRATION = """
-- 含触发器的迁移：BEGIN…END 块内有分号，不能被裸分号切分破坏。
CREATE TABLE IF NOT EXISTS demo_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT
);

CREATE TRIGGER IF NOT EXISTS demo_rows_label_required
BEFORE INSERT ON demo_rows
WHEN NEW.label IS NULL OR TRIM(NEW.label) = ''
BEGIN
    SELECT RAISE(ABORT, 'demo_rows.label must be nonblank');
END;

CREATE INDEX IF NOT EXISTS idx_demo_rows_label ON demo_rows(label);
"""


def _write_migration(migrations_dir: Path, name: str, sql: str) -> None:
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / name).write_text(sql, encoding="utf-8")


def test_run_migrations_supports_path_override_and_triggers(tmp_path: Path) -> None:
    db_path = tmp_path / "demo.db"
    migrations_dir = tmp_path / "migs"
    _write_migration(migrations_dir, "001_demo_trigger.sql", TRIGGER_MIGRATION)

    report = run_migrations(
        "grading",
        db_path=db_path,
        migrations_dir=migrations_dir,
    )

    assert report.error is None, report.error
    conn = sqlite3.connect(db_path)
    try:
        objects = {
            (row[0], row[1])
            for row in conn.execute("SELECT type, name FROM sqlite_master").fetchall()
        }
        assert ("table", "demo_rows") in objects
        assert ("trigger", "demo_rows_label_required") in objects
        assert ("index", "idx_demo_rows_label") in objects
        # 触发器体必须完整：空 label 插入应被拦截
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO demo_rows (label) VALUES ('')")
        conn.execute("INSERT INTO demo_rows (label) VALUES ('ok')")
        applied = {
            row[0]
            for row in conn.execute(
                "SELECT migration_name FROM schema_migrations WHERE success = 1"
            ).fetchall()
        }
        assert "001_demo_trigger" in applied
    finally:
        conn.close()


def test_stamp_only_records_without_executing(tmp_path: Path) -> None:
    db_path = tmp_path / "demo.db"
    migrations_dir = tmp_path / "migs"
    _write_migration(
        migrations_dir,
        "001_create_table.sql",
        "CREATE TABLE IF NOT EXISTS should_not_exist (id INTEGER PRIMARY KEY);",
    )

    report = run_migrations(
        "grading",
        db_path=db_path,
        migrations_dir=migrations_dir,
        stamp_only=True,
    )

    assert report.error is None, report.error
    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "should_not_exist" not in tables  # 未执行
        applied = {
            row[0]
            for row in conn.execute(
                "SELECT migration_name FROM schema_migrations WHERE success = 1"
            ).fetchall()
        }
        assert applied == {"001_create_table"}  # 但已记录

        # 打标后再正常执行：该迁移被视为已应用，不再执行
        report2 = run_migrations("grading", db_path=db_path, migrations_dir=migrations_dir)
        assert report2.error is None
        tables2 = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "should_not_exist" not in tables2
    finally:
        conn.close()


def test_dry_run_does_not_create_migration_tracking_table(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    migrations_dir = tmp_path / "migs"
    _write_migration(
        migrations_dir,
        "001_create_table.sql",
        "CREATE TABLE should_not_exist (id INTEGER PRIMARY KEY);",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE legacy_data (id INTEGER PRIMARY KEY)")

    report = run_migrations(
        "grading",
        db_path=db_path,
        migrations_dir=migrations_dir,
        dry_run=True,
    )

    assert report.error is None, report.error
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone() is None


def test_first_backup_failure_leaves_untracked_legacy_database_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "legacy.db"
    migrations_dir = tmp_path / "migs"
    _write_migration(
        migrations_dir,
        "001_create_table.sql",
        "CREATE TABLE should_not_exist (id INTEGER PRIMARY KEY);",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE legacy_data (id INTEGER PRIMARY KEY)")

    monkeypatch.setattr(
        migrate_db,
        "_backup_database",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError(str(tmp_path / "private-backup"))
        ),
    )

    report = run_migrations(
        "grading",
        db_path=db_path,
        migrations_dir=migrations_dir,
    )

    assert report.error == "migration backup failed; database unchanged"
    assert str(tmp_path) not in report.error
    with sqlite3.connect(db_path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert tables == {"legacy_data"}


def test_path_override_does_not_touch_real_targets(tmp_path: Path) -> None:
    db_path = tmp_path / "isolated.db"
    migrations_dir = tmp_path / "migs"
    _write_migration(
        migrations_dir,
        "001_noop.sql",
        "CREATE TABLE IF NOT EXISTS noop_table (id INTEGER PRIMARY KEY);",
    )

    report = run_migrations("question_bank", db_path=db_path, migrations_dir=migrations_dir)

    assert report.error is None
    assert report.db_path == str(db_path)
    assert db_path.exists()
    assert report.results[0].backup_path is None or str(report.results[0].backup_path).startswith(str(tmp_path))


def test_migration_status_uses_explicit_candidate_without_opening_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.db"
    candidate = tmp_path / "candidate.db"
    migrations_dir = tmp_path / "migs"
    _write_migration(migrations_dir, "001_demo.sql", "SELECT 1;")
    with sqlite3.connect(candidate) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations ("
            "id INTEGER PRIMARY KEY, migration_name TEXT, applied_at TEXT, success INTEGER)"
        )
        conn.execute(
            "INSERT INTO schema_migrations (migration_name, applied_at, success) "
            "VALUES ('001_demo', '2026-07-12T12:00:00', 1)"
        )

    monkeypatch.setattr(
        migrate_db,
        "_get_targets",
        lambda: {
            "grading": {
                "db_path": source,
                "migrations_dir": migrations_dir,
            }
        },
    )

    status = migrate_db.get_migration_status(
        "grading",
        db_path_override=candidate,
    )

    assert status["db_exists"] is True
    assert status["applied"] == ["001_demo"]
    assert not source.exists()
