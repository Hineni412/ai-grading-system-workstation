from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.schema_migrations import (
    SchemaVersionError,
    ensure_application_schema,
    ensure_schema_current,
)
from backend.jobs.store import JobStore
from db_manager import DBManager
from grading_run_store import GradingRunStore
from question_bank.database.schema import initialize_database
from update_tools.migrate_db import run_migrations


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SCHEMA_OWNERS = (
    PROJECT_ROOT / "db_manager.py",
    PROJECT_ROOT / "backend" / "jobs" / "store.py",
    PROJECT_ROOT / "grading_run_store.py",
    PROJECT_ROOT / "question_bank" / "database" / "schema.py",
)


def test_schema_gate_bootstraps_empty_grading_database(tmp_path: Path) -> None:
    database = tmp_path / "grading.db"

    result = ensure_schema_current(
        "grading",
        database,
        migrations_dir=PROJECT_ROOT / "migrations" / "grading",
    )

    assert result.current_version == "004_add_jobs_result_json"
    assert result.applied == (
        "000_baseline_schema",
        "001_init_migration_tracking",
        "002_add_grading_run_ledger",
        "003_add_jobs",
        "004_add_jobs_result_json",
    )
    with sqlite3.connect(database) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"grading_sessions", "grading_runs", "jobs"} <= tables


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


def test_schema_gate_rejects_recorded_checksum_drift(tmp_path: Path) -> None:
    database = tmp_path / "grading.db"
    migrations = PROJECT_ROOT / "migrations" / "grading"
    ensure_schema_current("grading", database, migrations_dir=migrations)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            UPDATE schema_migrations
            SET checksum = 'changed'
            WHERE migration_name = '002_add_grading_run_ledger'
            """
        )

    with pytest.raises(SchemaVersionError, match="checksum does not match"):
        ensure_schema_current("grading", database, migrations_dir=migrations)


def test_schema_gate_rejects_non_contiguous_migration_history(
    tmp_path: Path,
) -> None:
    database = tmp_path / "grading.db"
    migrations = PROJECT_ROOT / "migrations" / "grading"
    ensure_schema_current("grading", database, migrations_dir=migrations)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            DELETE FROM schema_migrations
            WHERE migration_name = '001_init_migration_tracking'
            """
        )

    with pytest.raises(SchemaVersionError, match="history has a gap"):
        ensure_schema_current("grading", database, migrations_dir=migrations)


def test_schema_gate_rejects_empty_migration_manifest(tmp_path: Path) -> None:
    database = tmp_path / "grading.db"
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                migration_name TEXT NOT NULL UNIQUE,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                checksum TEXT,
                success INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO schema_migrations (migration_name, checksum, success)
            VALUES ('999_future_schema', 'future', 1)
            """
        )

    with pytest.raises(SchemaVersionError, match="manifest"):
        ensure_schema_current("grading", database, migrations_dir=migrations)


def test_schema_gate_rejects_incomplete_untracked_legacy_schema(
    tmp_path: Path,
) -> None:
    database = tmp_path / "grading.db"
    migrations = PROJECT_ROOT / "migrations" / "grading"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE grading_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_name TEXT NOT NULL,
                rubric_path TEXT NOT NULL,
                answer_key_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                is_deleted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            )
            """
        )

    with pytest.raises(SchemaVersionError, match="schema differs"):
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
        assert connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'partial_table'
            """
        ).fetchone() is None


def test_schema_gate_serializes_concurrent_bootstrap(tmp_path: Path) -> None:
    database = tmp_path / "grading.db"
    migrations = PROJECT_ROOT / "migrations" / "grading"

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _index: ensure_schema_current(
                    "grading",
                    database,
                    migrations_dir=migrations,
                ),
                range(4),
            )
        )

    assert {result.current_version for result in results} == {
        "004_add_jobs_result_json"
    }
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT migration_name, COUNT(*)
            FROM schema_migrations
            WHERE success = 1
            GROUP BY migration_name
            ORDER BY migration_name
            """
        ).fetchall()
    assert rows == [
        ("000_baseline_schema", 1),
        ("001_init_migration_tracking", 1),
        ("002_add_grading_run_ledger", 1),
        ("003_add_jobs", 1),
        ("004_add_jobs_result_json", 1),
    ]
    assert len(list((tmp_path / "backups").glob("*.db"))) == 5


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


def test_db_manager_initialize_uses_current_grading_migrations(
    tmp_path: Path,
) -> None:
    database = tmp_path / "grading.db"

    DBManager(database).initialize()

    with sqlite3.connect(database) as connection:
        applied = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT migration_name
                FROM schema_migrations
                WHERE success = 1
                ORDER BY id
                """
            )
        ]
    assert applied == [
        "000_baseline_schema",
        "001_init_migration_tracking",
        "002_add_grading_run_ledger",
        "003_add_jobs",
        "004_add_jobs_result_json",
    ]


@pytest.mark.parametrize("initializer", [JobStore, GradingRunStore])
def test_grading_store_initializers_use_current_migrations(
    tmp_path: Path,
    initializer: type[JobStore] | type[GradingRunStore],
) -> None:
    database = tmp_path / "grading.db"

    initializer(database)

    with sqlite3.connect(database) as connection:
        current = connection.execute(
            """
            SELECT migration_name
            FROM schema_migrations
            WHERE success = 1
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    assert current == ("004_add_jobs_result_json",)


def test_question_bank_initializer_uses_current_migrations(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question_bank.db"

    initialize_database(database, seed_skills=False)

    with sqlite3.connect(database) as connection:
        current = connection.execute(
            """
            SELECT migration_name
            FROM schema_migrations
            WHERE success = 1
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    assert current == ("008_add_unified_skill_catalog",)


@pytest.mark.parametrize("source_path", RUNTIME_SCHEMA_OWNERS)
def test_runtime_schema_owners_do_not_embed_ddl(source_path: Path) -> None:
    source = source_path.read_text(encoding="utf-8").upper()

    assert "CREATE TABLE" not in source
    assert "ALTER TABLE" not in source
    assert "CREATE INDEX" not in source
    assert "CREATE TRIGGER" not in source


def test_application_schema_gate_checks_both_databases(tmp_path: Path) -> None:
    paths = SimpleNamespace(
        project_root=PROJECT_ROOT,
        db_path=tmp_path / "grading.db",
        qb_db_path=tmp_path / "question_bank.db",
    )

    results = ensure_application_schema(paths)

    assert results["grading"].current_version == "004_add_jobs_result_json"
    assert (
        results["question_bank"].current_version
        == "008_add_unified_skill_catalog"
    )


def test_application_schema_gate_uses_formal_backup_directory(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    paths = SimpleNamespace(
        project_root=PROJECT_ROOT,
        db_path=data_root / "databases" / "grading.db",
        qb_db_path=data_root / "databases" / "question_bank.db",
        backups_dir=data_root / "backups",
    )

    ensure_application_schema(paths)

    assert list(paths.backups_dir.glob("*.db"))
    assert not (paths.db_path.parent / "backups").exists()


def test_fastapi_lifespan_checks_both_schema_versions(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    paths = SimpleNamespace(
        project_root=PROJECT_ROOT,
        version="test",
        data_root=data_root,
        db_path=data_root / "databases" / "grading.db",
        qb_db_path=data_root / "databases" / "question_bank.db",
        reports_dir=data_root / "reports",
        exams_dir=data_root / "exams",
        templates_dir=data_root / "templates",
        upload_config_dir=data_root / "config" / "uploaded",
        outputs_dir=data_root / "outputs",
        backups_dir=data_root / "backups",
        ops_state_dir=data_root / "ops",
    )

    with TestClient(create_app(path_manager=paths)) as client:
        assert client.get("/healthz").status_code == 200

    with sqlite3.connect(paths.qb_db_path) as connection:
        current = connection.execute(
            "SELECT migration_name FROM schema_migrations "
            "WHERE success = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert current == ("008_add_unified_skill_catalog",)


def test_fastapi_lifespan_schema_failure_does_not_expose_local_path(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "private-data"
    paths = SimpleNamespace(
        project_root=tmp_path / "missing-project",
        version="test",
        data_root=data_root,
        db_path=data_root / "databases" / "grading.db",
        qb_db_path=data_root / "databases" / "question_bank.db",
        reports_dir=data_root / "reports",
        exams_dir=data_root / "exams",
        templates_dir=data_root / "templates",
        upload_config_dir=data_root / "config" / "uploaded",
        outputs_dir=data_root / "outputs",
        backups_dir=data_root / "backups",
        ops_state_dir=data_root / "ops",
    )

    with pytest.raises(SchemaVersionError) as exc_info:
        with TestClient(create_app(path_manager=paths)):
            pass

    captured = capsys.readouterr()
    combined = captured.out + captured.err + caplog.text
    assert str(tmp_path) not in combined
    assert str(tmp_path) not in str(exc_info.value)


def test_launcher_reports_safe_schema_version_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    from backend.api import launcher

    monkeypatch.setattr(
        launcher,
        "get_path_manager",
        lambda: SimpleNamespace(project_root=tmp_path),
    )
    monkeypatch.setattr(
        launcher,
        "validate_frontend_dist",
        lambda _path: tmp_path / "frontend" / "dist",
    )
    monkeypatch.setattr(
        launcher,
        "ensure_application_schema",
        lambda _paths: (_ for _ in ()).throw(
            SchemaVersionError("C:/private/database is newer")
        ),
    )

    assert launcher.main(["--no-browser"]) == 3
    stderr = capsys.readouterr().err
    assert "数据库版本与当前程序不兼容" in stderr
    assert "C:/private" not in stderr
