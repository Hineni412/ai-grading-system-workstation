from __future__ import annotations
import json
import sqlite3
from types import SimpleNamespace
from pathlib import Path
import pytest
from update_tools import migrate_db
from update_tools.migrate_db import run_migrations
import shutil


from backend.schema_migrations import SchemaVersionError, ensure_application_schema, ensure_schema_current


def migration_tooling__write_migration(migrations_dir: Path, name: str, sql: str) -> None:
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / name).write_text(sql, encoding="utf-8")


def test_first_backup_failure_leaves_untracked_legacy_database_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "legacy.db"
    migrations_dir = tmp_path / "migs"
    migration_tooling__write_migration(
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


def migration_tooling__synthetic_update_paths(tmp_path: Path) -> SimpleNamespace:
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
        migration_tooling__write_migration(
            root,
            "000_synthetic.sql",
            "CREATE TABLE preserved (value TEXT);\n"
            + "\n".join(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY);" for table in tables),
        )
        ensure_schema_current(target, database, migrations_dir=root)
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO preserved VALUES (?)", (target,))
        migration_tooling__write_migration(root, "001_synthetic.sql", "CREATE TABLE added (value TEXT);")
    return paths


@pytest.mark.parametrize("fail_second", [False, True])
def test_confirmed_update_reuses_protected_migration_and_rolls_back_both_databases(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    fail_second: bool,
) -> None:
    from backend.ops import offline
    from backend.ops.journal import OpsOperationJournal

    paths = migration_tooling__synthetic_update_paths(tmp_path_factory.mktemp("TEST-up"))
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
    migration_tooling__write_migration(target / "migrations" / "grading", "000_old.sql", "old migration")
    migration_tooling__write_migration(update / "migrations" / "grading", "000_new.sql", "new migration")
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


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


@pytest.mark.parametrize("target", ["grading", "question_bank"])
def test_current_schema_contract_does_not_replace_migration_checksum_validation(
    tmp_path: Path,
    target: str,
) -> None:
    database = tmp_path / f"{target}.db"
    ensure_schema_current(target, database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum = 'changed' WHERE id = "
            "(SELECT MIN(id) FROM schema_migrations)"
        )
    with pytest.raises(SchemaVersionError, match="checksum does not match"):
        ensure_schema_current(target, database)


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


@pytest.mark.parametrize("target", ["grading", "question_bank"])
def test_historical_database_requires_conversion_manifest_and_keeps_its_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    from backend import schema_migrations

    source = sorted((PROJECT_ROOT / "migrations" / target).glob("*.sql"))
    legacy = tmp_path / "TEST-legacy-sql"
    legacy.mkdir()
    # This fixture contains pre-normalisation draft_json, even when later
    # unrelated migrations (such as reference stars) have been appended.
    legacy_source = [f for f in source if f.stem < "049_deduplicate_recommendation_drafts"] if target == "question_bank" else source[:-1]
    for file in legacy_source:
        shutil.copy2(file, legacy / file.name)
    database = tmp_path / "TEST-legacy.db"
    ensure_schema_current(target, database, migrations_dir=legacy)
    if target == "question_bank":
        draft_id = "d" * 64
        original = {"draft_id": draft_id, "revision": 1, "students": [], "locked": False}
        edited = {**original, "revision": 2, "locked": True}
        first_json, second_json = json.dumps(original), json.dumps(edited)
        assembly = json.dumps({"source": "TEST-assembly", "revision": "s" * 64})
        with sqlite3.connect(database) as connection:
            connection.execute("""INSERT INTO personalized_recommendation_drafts
                (draft_id, request_token, input_fingerprint, result_version, engine_version,
                 source_version, request_json, draft_json, revision, created_by)
                VALUES (?, ?, ?, ?, '', ?, '{}', ?, 2, 'TEST')""",
                (draft_id, "a" * 32, "b" * 64, "c" * 64, "d" * 64, second_json))
            connection.executemany("""INSERT INTO personalized_recommendation_events
                (draft_id, request_token, command_hash, action, actor_ref, reason,
                 expected_revision, resulting_revision, before_json, after_json, resulting_draft_json)
                VALUES (?, ?, ?, ?, 'TEST', 'TEST', ?, ?, ?, ?, ?)""", [
                (draft_id, "a" * 32, "e" * 64, "created", 0, 1, "{}", first_json, first_json),
                (draft_id, "b" * 32, "f" * 64, "locked", 1, 2, '{"locked":false}', '{"locked":true}', second_json),
                (draft_id, "c" * 32, "0" * 64, "created", 0, 2, "{}", assembly, second_json),
            ])
            connection.execute("""INSERT INTO personalized_paper_instances
                (paper_instance_id, operation_token, operation_fingerprint, draft_id,
                 draft_revision, draft_result_version, paper_batch_id, series_version, student_id,
                 status, layout_version, budget_version, budget_json, snapshot_json, signing_secret,
                 reviewed_docx_path, reviewed_docx_sha256, frozen_pdf_path, frozen_pdf_sha256,
                 page_count, created_by)
                VALUES (?, ?, ?, ?, 2, ?, ?, 1, 'TEST-student', 'frozen', 'TEST', 'TEST',
                        '{}', ?, ?, 'TEST.docx', ?, 'TEST.pdf', ?, 1, 'TEST')""",
                ("p" * 64, "p" * 32, "q" * 64, draft_id, "r" * 64, "s" * 64,
                 second_json, "t" * 64, "u" * 64, "v" * 64))
            connection.execute("""INSERT INTO personalized_paper_batches
                (batch_run_id, operation_token, operation_fingerprint, request_json,
                 draft_id, draft_revision, paper_batch_id, created_by)
                VALUES (?, ?, ?, '{}', ?, 2, ?, 'TEST')""",
                ("w" * 64, "w" * 32, "x" * 64, draft_id, "s" * 64))
            connection.commit()
            conflict_database = tmp_path / "TEST-conflicting-revisions.db"
            with sqlite3.connect(conflict_database) as conflict:
                connection.backup(conflict)
                conflict.execute("UPDATE personalized_recommendation_events SET resulting_draft_json='{}' WHERE request_token=?", ("c" * 32,))
    before = database.read_bytes()
    manifest_root = tmp_path / "TEST-current-sql"
    shutil.copytree(PROJECT_ROOT / "migrations" / target, manifest_root / target)
    monkeypatch.setattr(schema_migrations, "_DEFAULT_MIGRATIONS_ROOT", manifest_root)
    removed = tmp_path / "TEST-sql-removed"
    shutil.move(str(manifest_root), str(removed))
    with pytest.raises(SchemaVersionError, match="migration manifest is empty"):
        ensure_schema_current(target, database, allow_existing_migrations=False)
    assert database.read_bytes() == before
    shutil.move(str(removed), str(manifest_root))
    with pytest.raises(schema_migrations.SchemaMigrationRequired) as required:
        ensure_schema_current(target, database, allow_existing_migrations=False)
    assert required.value.pending == tuple(f.stem for f in source if f not in legacy_source)
    assert database.read_bytes() == before
    report = run_migrations(target, db_path=database, migrations_dir=manifest_root / target,
                            backup_dir_override=tmp_path / "TEST-backups")
    assert report.error is None
    if target == "question_bank":
        with sqlite3.connect(database) as connection:
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert connection.execute("SELECT draft_json FROM personalized_recommendation_revisions ORDER BY revision").fetchall() == [(first_json,), (second_json,)]
            assert connection.execute("SELECT action,before_json,after_json FROM personalized_recommendation_events ORDER BY event_id").fetchall() == [
                ("created", "{}", "{}"), ("locked", '{"locked":false}', '{"locked":true}'),
                ("created", "{}", assembly),
            ]
            assert connection.execute("""SELECT r.draft_json FROM personalized_recommendation_drafts d
                JOIN personalized_recommendation_revisions r USING (draft_id,revision)""").fetchone() == (second_json,)
            assert connection.execute("SELECT status,snapshot_json,frozen_pdf_path FROM personalized_paper_instances").fetchone() == ("frozen", second_json, "TEST.pdf")
            assert connection.execute("SELECT draft_id,draft_revision FROM personalized_paper_batches").fetchone() == (draft_id, 2)
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute("UPDATE personalized_recommendation_revisions SET draft_json='{}'")
        conflict_report = run_migrations(target, db_path=conflict_database,
            migrations_dir=manifest_root / target, backup_dir_override=tmp_path / "TEST-conflict-backups")
        assert conflict_report.error is not None
        assert "conflicting recommendation revision bodies" in conflict_report.results[-1].message
        with sqlite3.connect(conflict_database) as connection:
            assert connection.execute("SELECT draft_json FROM personalized_recommendation_drafts").fetchone() == (second_json,)
            assert connection.execute("SELECT COUNT(*) FROM personalized_recommendation_events").fetchone() == (3,)
            assert connection.execute("SELECT resulting_draft_json FROM personalized_recommendation_events WHERE request_token=?", ("c" * 32,)).fetchone() == ("{}",)
            assert connection.execute("SELECT name FROM sqlite_master WHERE name='personalized_recommendation_revisions'").fetchone() is None
