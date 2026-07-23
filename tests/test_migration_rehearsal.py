from __future__ import annotations

from pathlib import Path

from question_bank.database.schema import initialize_database


def test_schema_equivalence_ignores_column_order_from_add_column(tmp_path: Path) -> None:
    import sqlite3

    from tools.migration_rehearsal import schemas_equivalent

    runtime_db = tmp_path / "runtime.db"
    migrated_db = tmp_path / "migrated.db"
    with sqlite3.connect(runtime_db) as conn:
        conn.execute("CREATE TABLE demo (id INTEGER PRIMARY KEY, extra TEXT, name TEXT NOT NULL)")
        conn.execute("CREATE INDEX idx_demo_name ON demo(name)")
    with sqlite3.connect(migrated_db) as conn:
        conn.execute("CREATE TABLE demo (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("ALTER TABLE demo ADD COLUMN extra TEXT")
        conn.execute("CREATE INDEX idx_demo_name ON demo(name)")

    assert schemas_equivalent(runtime_db, migrated_db)


def test_schema_equivalence_accepts_legacy_answer_region_uuid_nullable_flag(tmp_path: Path) -> None:
    import sqlite3

    from tools.migration_rehearsal import schemas_equivalent

    runtime_db = tmp_path / "runtime.db"
    migrated_db = tmp_path / "migrated.db"
    trigger_sql = """
        CREATE TRIGGER answer_regions_region_uuid_required_insert
        BEFORE INSERT ON answer_regions
        WHEN NEW.region_uuid IS NULL OR TRIM(NEW.region_uuid) = ''
        BEGIN
            SELECT RAISE(ABORT, 'answer_regions.region_uuid must be nonblank');
        END;
    """
    with sqlite3.connect(runtime_db) as conn:
        conn.execute("CREATE TABLE answer_regions (id INTEGER PRIMARY KEY, region_uuid TEXT NOT NULL UNIQUE)")
        conn.executescript(trigger_sql)
    with sqlite3.connect(migrated_db) as conn:
        conn.execute("CREATE TABLE answer_regions (id INTEGER PRIMARY KEY, region_uuid TEXT UNIQUE)")
        conn.executescript(trigger_sql)

    assert schemas_equivalent(runtime_db, migrated_db)


def test_question_bank_stamp_only_rehearsal_uses_copy_and_matches_schema(tmp_path: Path) -> None:
    from tools.migration_rehearsal import rehearse_database

    source_db = tmp_path / "source_question_bank.db"
    initialize_database(source_db)

    result = rehearse_database(
        "question_bank",
        source_db=source_db,
        migrations_dir=Path("migrations/question_bank"),
        mode="stamp-only",
        work_dir=tmp_path / "rehearsal",
    )

    assert result.ok, result.messages
    assert result.integrity_ok
    assert result.schema_matches_runtime
    assert not result.business_row_count_changes
    assert not source_db.with_name("source_question_bank.db-wal").exists()


def test_rehearsal_copy_includes_committed_wal_content(tmp_path: Path) -> None:
    import sqlite3

    from backend.schema_migrations import ensure_schema_current
    from tools.migration_rehearsal import rehearse_database

    source_db = tmp_path / "source_grading.db"
    migrations = Path("migrations/grading")
    ensure_schema_current("grading", source_db, migrations_dir=migrations)

    source = sqlite3.connect(source_db)
    try:
        source.execute("PRAGMA journal_mode = WAL")
        source.execute("PRAGMA wal_autocheckpoint = 0")
        source.execute(
            """
            INSERT INTO jobs (job_type, status)
            VALUES ('wal-probe', 'queued')
            """
        )
        source.commit()

        result = rehearse_database(
            "grading",
            source_db=source_db,
            migrations_dir=migrations,
            mode="execute",
            work_dir=tmp_path / "rehearsal",
        )
    finally:
        source.close()

    assert result.ok, result.messages
    with sqlite3.connect(result.copy_db) as copied:
        assert copied.execute(
            "SELECT job_type FROM jobs WHERE job_type = 'wal-probe'"
        ).fetchone() == ("wal-probe",)
