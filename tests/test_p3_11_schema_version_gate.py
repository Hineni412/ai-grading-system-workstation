from __future__ import annotations

import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.schema_migrations import (
    SchemaVersionError,
    ensure_application_schema,
    ensure_schema_current,
)
from update_tools.migrate_db import run_migrations


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUESTION_BANK_MIGRATIONS = PROJECT_ROOT / "migrations" / "question_bank"
CURRENT_QUESTION_BANK_MIGRATION = sorted(QUESTION_BANK_MIGRATIONS.glob("*.sql"))[
    -1
].stem
RUNTIME_SCHEMA_OWNERS = (
    PROJECT_ROOT / "db_manager.py",
    PROJECT_ROOT / "backend" / "jobs" / "store.py",
    PROJECT_ROOT / "grading_run_store.py",
    PROJECT_ROOT / "question_bank" / "database" / "schema.py",
)


def test_schema_gate_rejects_unknown_future_migration(tmp_path: Path) -> None:
    database = tmp_path / "grading.db"
    migrations = PROJECT_ROOT / "migrations" / "grading"
    ensure_schema_current("grading", database, migrations_dir=migrations)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO schema_migrations
                (migration_name, checksum, success)
            VALUES ('999_future_schema', 'future', 1)
            """
        )

    with pytest.raises(SchemaVersionError, match="newer than this application"):
        ensure_schema_current("grading", database, migrations_dir=migrations)


def test_failed_migration_rolls_back_its_partial_schema(tmp_path: Path) -> None:
    database = tmp_path / "grading.db"
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "000_fails.sql").write_text(
        """
        CREATE TABLE partial_table (id INTEGER PRIMARY KEY);
        THIS IS NOT VALID SQL;
        """,
        encoding="utf-8",
    )

    report = run_migrations(
        "grading",
        db_path=database,
        migrations_dir=migrations,
    )

    assert report.error is not None
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'partial_table'
            """
            ).fetchone()
            is None
        )


def test_migration_backup_includes_committed_wal_content(tmp_path: Path) -> None:
    database = tmp_path / "grading.db"
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "000_probe.sql").write_text(
        "CREATE TABLE migration_probe (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    source = sqlite3.connect(database)
    try:
        source.execute("PRAGMA journal_mode = WAL")
        source.execute("PRAGMA wal_autocheckpoint = 0")
        source.execute("CREATE TABLE preserved (value TEXT NOT NULL)")
        source.execute("INSERT INTO preserved VALUES ('committed-in-wal')")
        source.commit()

        report = run_migrations(
            "grading",
            db_path=database,
            migrations_dir=migrations,
        )
        assert report.error is None, report.error
        backup = Path(report.results[0].backup_path or "")
        with sqlite3.connect(backup) as copied:
            assert copied.execute("SELECT value FROM preserved").fetchone() == (
                "committed-in-wal",
            )
    finally:
        source.close()


def test_application_startup_reports_pending_existing_database_without_applying_it(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "legacy-project"
    grading_migrations = legacy_root / "migrations" / "grading"
    grading_migrations.mkdir(parents=True)
    current_grading = PROJECT_ROOT / "migrations" / "grading"
    current_migrations = sorted(current_grading.glob("*.sql"))
    # The legacy project has everything except the newest migration, so
    # startup must report exactly that one as pending.
    for migration in current_migrations[:-1]:
        shutil.copy2(migration, grading_migrations / migration.name)
    data_root = tmp_path / "data"
    grading_db = data_root / "databases" / "grading.db"
    question_bank_db = data_root / "databases" / "question_bank.db"
    ensure_schema_current(
        "grading",
        grading_db,
        migrations_dir=grading_migrations,
    )
    ensure_schema_current(
        "question_bank",
        question_bank_db,
        migrations_dir=PROJECT_ROOT / "migrations" / "question_bank",
    )
    shutil.rmtree(data_root / "backups")
    before = grading_db.read_bytes()
    paths = SimpleNamespace(
        project_root=PROJECT_ROOT,
        migration_project_root=PROJECT_ROOT,
        db_path=grading_db,
        qb_db_path=question_bank_db,
        backups_dir=data_root / "backups",
    )

    with pytest.raises(
        SchemaVersionError,
        match="pending.*protected maintenance",
    ):
        ensure_application_schema(paths)

    assert grading_db.read_bytes() == before
    assert not paths.backups_dir.exists()
