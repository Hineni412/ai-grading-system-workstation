"""migrate_db 工具增强：触发器切分、路径覆盖、stamp-only 打标。"""

from __future__ import annotations

import sqlite3
import sys
from types import SimpleNamespace
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


def _synthetic_update_paths(tmp_path: Path) -> SimpleNamespace:
    from backend.schema_migrations import ensure_schema_current

    project = tmp_path / "TEST-update-project"
    data_root = project / "user_data"
    paths = SimpleNamespace(
        project_root=project,
        data_root=data_root,
        databases_dir=data_root / "databases",
        db_path=data_root / "databases" / "grading_system.db",
        qb_db_path=data_root / "databases" / "question_bank.db",
        backups_dir=data_root / "backups",
        ops_state_dir=tmp_path / "TEST-ops-state",
        logs_dir=project / "logs",
    )
    for target, database, tables in (
        ("grading", paths.db_path, ("students", "grading_sessions", "exam_papers")),
        ("question_bank", paths.qb_db_path, ("papers", "questions", "question_tags")),
    ):
        root = project / "migrations" / target
        _write_migration(
            root,
            "000_synthetic.sql",
            "CREATE TABLE preserved (value TEXT);\n"
            + "\n".join(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY);" for table in tables),
        )
        ensure_schema_current(target, database, migrations_dir=root)
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO preserved VALUES (?)", (target,))
        _write_migration(root, "001_synthetic.sql", "CREATE TABLE added (value TEXT);")
    return paths


@pytest.mark.parametrize("fail_second", [False, True])
def test_confirmed_update_reuses_protected_migration_and_rolls_back_both_databases(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    fail_second: bool,
) -> None:
    from backend.ops import offline
    from backend.ops.journal import OpsOperationJournal

    paths = _synthetic_update_paths(tmp_path_factory.mktemp("TEST-up"))
    original = offline.run_migrations

    def apply_or_fail(target: str, **kwargs):
        if fail_second and target == "question_bank":
            raise RuntimeError("synthetic second database failure")
        return original(target, **kwargs)

    monkeypatch.setattr(offline, "run_migrations", apply_or_fail)
    result = offline.apply_confirmed_migrations(paths=paths)
    assert result["status"] == ("rolled_back" if fail_second else "applied")
    assert result["migrations_applied"] == (0 if fail_second else 2)
    assert Path(str(result["backup_path"])).is_file()
    journal = OpsOperationJournal(paths.ops_state_dir)
    assert journal.load_public(str(result["operation_id"]))["status"] == result["status"]
    assert not journal.pending_exists()
    for target, database in (("grading", paths.db_path), ("question_bank", paths.qb_db_path)):
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT value FROM preserved").fetchone() == (target,)
            assert bool(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='added'"
            ).fetchone()) is not fail_second
            assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone() == (
                1 if fail_second else 2,
            )
    if not fail_second:
        before = {path: path.read_bytes() for path in (paths.db_path, paths.qb_db_path)}
        assert offline.apply_confirmed_migrations(paths=paths) == {
            "status": "current", "migrations_applied": 0,
        }
        assert {path: path.read_bytes() for path in before} == before


def test_update_reports_database_rollback_and_preserves_matching_code_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from update_tools import apply_update

    update = tmp_path / "TEST-update-package"
    target = tmp_path / "TEST-deployment"
    (update / "app").mkdir(parents=True)
    target.mkdir()
    (update / "update_manifest.json").write_text('{"app_version":"TEST-new"}', encoding="utf-8")
    (update / "app" / "probe.py").write_text("new code", encoding="utf-8")
    (target / "probe.py").write_text("old code", encoding="utf-8")
    (target / "VERSION").write_text("TEST-old", encoding="utf-8")
    _write_migration(target / "migrations" / "grading", "000_old.sql", "old migration")
    _write_migration(update / "migrations" / "grading", "000_new.sql", "new migration")
    (target / "update_tools").mkdir()
    (target / "update_tools" / "probe.py").write_text("old updater", encoding="utf-8")
    (update / "update_tools").mkdir()
    (update / "update_tools" / "probe.py").write_text("new updater", encoding="utf-8")
    monkeypatch.setattr(apply_update, "_run_protected_migrations", lambda _target: {
        "status": "rolled_back", "migrations_applied": 0,
        "backup_path": str(tmp_path / "TEST-controlled-backup.zip"),
    })
    result = apply_update.apply_update(update, target)
    assert result["success"] is False
    assert result["migration_status"] == "rolled_back"
    assert "已回退" in result["error"]
    assert (target / "probe.py").read_text(encoding="utf-8") == "new code"
    backup = Path(result["code_backup_dir"])
    assert (backup / "migrations" / "grading" / "000_old.sql").is_file()
    assert (backup / "update_tools" / "probe.py").read_text(encoding="utf-8") == "old updater"
    assert apply_update.rollback(target, backup.name)["success"] is True
    assert (target / "probe.py").read_text(encoding="utf-8") == "old code"
    assert (target / "migrations" / "grading" / "000_old.sql").is_file()
    assert not (target / "migrations" / "grading" / "000_new.sql").exists()
    assert (target / "update_tools" / "probe.py").read_text(encoding="utf-8") == "old updater"
