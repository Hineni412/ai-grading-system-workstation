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
