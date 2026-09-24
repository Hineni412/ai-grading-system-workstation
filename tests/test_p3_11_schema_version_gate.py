from __future__ import annotations

import os
import shutil
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
QUESTION_BANK_MIGRATIONS = PROJECT_ROOT / "migrations" / "question_bank"
CURRENT_QUESTION_BANK_MIGRATION = sorted(
    QUESTION_BANK_MIGRATIONS.glob("*.sql")
)[-1].stem
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

    assert result.current_version == "013_dual_track_ai_scores"
    assert result.applied == (
        "000_baseline_schema",
        "001_init_migration_tracking",
        "002_add_grading_run_ledger",
        "003_add_jobs",
        "004_add_jobs_result_json",
        "005_add_status_constraints",
        "006_knowledge_ids_primary",
        "007_drop_legacy_knowledge_id",
        "008_drop_legacy_cli_tables",
        "009_add_teacher_score_locks",
        "010_workspace_ai_tasks",
        "011_add_session_curriculum_volume",
        "012_exclusive_session_names",
        "013_dual_track_ai_scores",
    )
    with sqlite3.connect(database) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "grading_sessions",
        "grading_runs",
        "jobs",
        "workspace_ai_tasks",
        "workspace_ai_handoffs",
    } <= tables


def test_schema_inspection_is_memoized_until_schema_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend import schema_migrations

    database = tmp_path / "question_bank.db"
    initialize_database(database)
    # initialize_database already verified the schema through the same gate;
    # clear the memo and the full-check ledger so this test counts only its
    # own calls. gc.collect() forces the initializer's connection to close.
    import gc
    gc.collect()
    schema_migrations._SCHEMA_INSPECT_CACHE.clear()
    schema_migrations._FULL_CHECK_DONE.clear()

    full_checks: list[bool] = []
    real_inspect = schema_migrations._inspect_schema_version_uncached

    def counting_inspect(*args, **kwargs):
        full_checks.append(bool(kwargs.get("full_check", True)))
        return real_inspect(*args, **kwargs)

    monkeypatch.setattr(
        schema_migrations,
        "_inspect_schema_version_uncached",
        counting_inspect,
    )

    schema_migrations.inspect_schema_version("question_bank", database)
    schema_migrations.inspect_schema_version("question_bank", database)
    assert full_checks == [True]

    # A data write leaves the memo key (file identity + schema_version +
    # history) unchanged: the cached verification is reused and no
    # integrity/fk check runs again for this file.
    from contextlib import closing

    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO papers (title, semester) VALUES ('memo-probe', '')"
        )
        connection.commit()
    schema_migrations.inspect_schema_version("question_bank", database)
    assert full_checks == [True]

    # A schema change bumps PRAGMA schema_version, so the next inspection
    # re-verifies the signature/history and rejects the drift — without
    # repeating the full integrity/fk checks on the same file identity.
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE _memo_probe (id INTEGER)")
        connection.commit()
    with pytest.raises(SchemaVersionError):
        schema_migrations.inspect_schema_version("question_bank", database)
    assert full_checks == [True, False]
    # Failed verifications are never memoized.
    with pytest.raises(SchemaVersionError):
        schema_migrations.inspect_schema_version("question_bank", database)
    assert full_checks == [True, False, False]

    # A replaced file has a new identity: the next inspection runs the full
    # integrity/fk checks again even though the path did not change.
    # (initialize_database runs the same gate on the replacement file, so
    # count relative to the current length.)
    before_replace = len(full_checks)
    replacement = tmp_path / "replacement.db"
    initialize_database(replacement)
    os.replace(replacement, database)
    schema_migrations.inspect_schema_version("question_bank", database)
    assert full_checks[-1] is True
    schema_migrations.inspect_schema_version("question_bank", database)
    assert len(full_checks) == before_replace + 2


def test_exclusive_name_migration_preserves_historical_duplicates_but_blocks_new_ones(
    tmp_path: Path,
) -> None:
    database = tmp_path / "grading.db"
    current_migrations = PROJECT_ROOT / "migrations" / "grading"
    legacy_migrations = tmp_path / "legacy-migrations"
    legacy_migrations.mkdir()
    for source in sorted(current_migrations.glob("*.sql")):
        if source.name >= "012_":
            continue
        shutil.copy2(source, legacy_migrations / source.name)
    ensure_schema_current("grading", database, migrations_dir=legacy_migrations)
    with sqlite3.connect(database) as connection:
        connection.executemany(
            """
            INSERT INTO grading_sessions (session_name, rubric_path, answer_key_path)
            VALUES (?, '', '')
            """,
            [("0526test",), (" 0526TEST ",)],
        )

    result = ensure_schema_current(
        "grading",
        database,
        migrations_dir=current_migrations,
    )

    assert result.current_version == "013_dual_track_ai_scores"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM grading_sessions"
        ).fetchone() == (2,)
        with pytest.raises(sqlite3.IntegrityError, match="session_name_conflict"):
            connection.execute(
                """
                INSERT INTO grading_sessions (session_name, rubric_path, answer_key_path)
                VALUES ('0526Test', '', '')
                """
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

    with pytest.raises(
        SchemaVersionError,
        match=(
            "schema differs|migration 005_add_status_constraints failed|"
            "migration history is missing"
        ),
    ):
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
        "013_dual_track_ai_scores"
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
        ("005_add_status_constraints", 1),
        ("006_knowledge_ids_primary", 1),
        ("007_drop_legacy_knowledge_id", 1),
        ("008_drop_legacy_cli_tables", 1),
        ("009_add_teacher_score_locks", 1),
        ("010_workspace_ai_tasks", 1),
        ("011_add_session_curriculum_volume", 1),
        ("012_exclusive_session_names", 1),
        ("013_dual_track_ai_scores", 1),
    ]
    assert len(list((tmp_path / "backups").glob("*.db"))) == 14


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
        "005_add_status_constraints",
        "006_knowledge_ids_primary",
        "007_drop_legacy_knowledge_id",
        "008_drop_legacy_cli_tables",
        "009_add_teacher_score_locks",
        "010_workspace_ai_tasks",
        "011_add_session_curriculum_volume",
        "012_exclusive_session_names",
        "013_dual_track_ai_scores",
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
    assert current == ("013_dual_track_ai_scores",)


def test_question_bank_initializer_uses_current_migrations(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question_bank.db"

    initialize_database(database)

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
    assert current == (CURRENT_QUESTION_BANK_MIGRATION,)


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

    assert results["grading"].current_version == "013_dual_track_ai_scores"
    assert results["question_bank"].current_version == CURRENT_QUESTION_BANK_MIGRATION


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


@pytest.mark.parametrize(
    "entrypoint",
    ("db_manager", "job_store", "grading_run_store", "question_bank"),
)
def test_compatibility_initializer_uses_formal_backup_directory(
    entrypoint: str,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / entrypoint
    database = data_root / "databases" / (
        "question_bank.db"
        if entrypoint == "question_bank"
        else "grading_system.db"
    )

    if entrypoint == "db_manager":
        DBManager(database).initialize()
    elif entrypoint == "job_store":
        JobStore(database)
    elif entrypoint == "grading_run_store":
        GradingRunStore(database)
    else:
        initialize_database(database)

    assert list((data_root / "backups").glob("*.db"))
    assert not (database.parent / "backups").exists()


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
        api_profiles_path=tmp_path / "config" / "api_profiles.json",
        legacy_api_profiles_paths=(),
        workspace_dir=lambda workspace_id, *, create=False: (
            data_root / "workspaces" / workspace_id
        ),
    )

    with TestClient(create_app(path_manager=paths)) as client:
        assert client.get("/healthz").status_code == 200

    with sqlite3.connect(paths.qb_db_path) as connection:
        current = connection.execute(
            "SELECT migration_name FROM schema_migrations "
            "WHERE success = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert current == (CURRENT_QUESTION_BANK_MIGRATION,)


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
