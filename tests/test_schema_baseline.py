"""Current schema contracts and immutable historical migration guards.

The current contract matches a synthetic migrated database. Runtime schema
checks need no replay when the database already has the complete history.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "update_tools"))


_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _normalized_schema(db_path: Path) -> dict[tuple[str, str], str]:
    """{(type, name) -> 归一化 SQL}，排除内部对象与 schema_migrations。"""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations' "
            "AND type IN ('table', 'index', 'trigger')"
        ).fetchall()
    finally:
        conn.close()
    result: dict[tuple[str, str], str] = {}
    for type_, name, sql in rows:
        text = re.sub(r"\s+", " ", str(sql)).strip().lower()
        text = text.replace("if not exists ", "")
        result[(str(type_), str(name))] = text
    return result


def _assert_equivalent(runtime_db: Path, migrated_db: Path) -> None:
    runtime_schema = _normalized_schema(runtime_db)
    migrated_schema = _normalized_schema(migrated_db)

    missing = sorted(set(runtime_schema) - set(migrated_schema))
    extra = sorted(set(migrated_schema) - set(runtime_schema))
    assert not missing, f"迁移缺少运行时对象: {missing}"
    assert not extra, f"迁移多出运行时没有的对象: {extra}"

    diffs = [
        key for key in runtime_schema if runtime_schema[key] != migrated_schema[key]
    ]
    assert not diffs, "对象定义漂移: " + "; ".join(
        f"{key}: runtime={runtime_schema[key][:120]} vs migrated={migrated_schema[key][:120]}"
        for key in diffs[:3]
    )


@pytest.mark.parametrize("taxonomy_revision", [None, 3, 4])
def test_question_bank_fixture_matches_fresh_schema_and_isolates_writes(
    tmp_path: Path,
    question_bank_database,
    taxonomy_revision: int | None,
) -> None:
    from question_bank.database.schema import initialize_database
    from question_bank.knowledge_graph_release import load_active_release
    from tests.current_knowledge_support import install_current_knowledge

    fresh = tmp_path / "fresh.db"
    initialize_database(fresh)
    if taxonomy_revision is not None:
        install_current_knowledge(fresh, taxonomy_revision=taxonomy_revision)

    first = question_bank_database(
        tmp_path / "first.db", taxonomy_revision=taxonomy_revision
    )
    _assert_equivalent(fresh, first)
    fresh_release = load_active_release(fresh)
    copied_release = load_active_release(first)
    assert (copied_release.release_id if copied_release else None) == (
        fresh_release.release_id if fresh_release else None
    )
    with (
        closing(sqlite3.connect(fresh)) as source,
        closing(sqlite3.connect(first)) as copy,
    ):
        tables = source.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        for (table,) in tables:
            query = 'SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"'
            assert copy.execute(query).fetchone() == source.execute(query).fetchone(), (
                table
            )
        assert copy.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        copy.execute(
            "INSERT INTO questions (question_number, question_text) VALUES ('1', 'isolated synthetic question')"
        )
        copy.commit()

    second = question_bank_database(
        tmp_path / "second.db", taxonomy_revision=taxonomy_revision
    )
    with closing(sqlite3.connect(second)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM questions").fetchone() == (0,)
    before = first.read_bytes()
    with pytest.raises(FileExistsError):
        question_bank_database(first, taxonomy_revision=taxonomy_revision)
    assert first.read_bytes() == before


@pytest.mark.parametrize("target", ["grading", "question_bank"])
def test_current_schema_copies_preserve_seed_rows_and_do_not_share_writes(
    tmp_path: Path,
    current_schema_database,
    target: str,
) -> None:
    from backend.schema_migrations import ensure_schema_current

    fresh = tmp_path / "fresh.db"
    ensure_schema_current(target, fresh)
    first = tmp_path / "first.db"
    current_schema_database(target, first)
    _assert_equivalent(fresh, first)
    with (
        closing(sqlite3.connect(fresh)) as original,
        closing(sqlite3.connect(first)) as copied,
    ):
        for (table,) in original.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ):
            query = 'SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"'
            assert (
                copied.execute(query).fetchone() == original.execute(query).fetchone()
            ), table
        assert copied.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        copied.execute("CREATE TABLE synthetic_copy_probe (value TEXT)")
        copied.commit()
    second = tmp_path / "second.db"
    current_schema_database(target, second)
    with closing(sqlite3.connect(second)) as copied:
        assert (
            copied.execute(
                "SELECT name FROM sqlite_master WHERE name = 'synthetic_copy_probe'"
            ).fetchone()
            is None
        )
    with pytest.raises(FileExistsError):
        current_schema_database(target, first)
    # A copied business database still passes the production version gate.
    assert not ensure_schema_current(
        target, second, allow_existing_migrations=False
    ).pending


@pytest.mark.parametrize("target", ["grading", "question_bank"])
def test_current_contract_matches_migrations_without_rewriting_history(target: str) -> None:
    import json

    from backend.schema_contracts import current_schema_contract_path
    from tools.generate_schema_baseline import generate_contract

    migrations = sorted((_PROJECT_ROOT / "migrations" / target).glob("*.sql"))
    before = {path: path.read_bytes() for path in migrations}
    expected = generate_contract(target)
    recorded = json.loads(current_schema_contract_path(target).read_text(encoding="utf-8"))
    assert recorded == expected
    assert {path: path.read_bytes() for path in migrations} == before


@pytest.mark.parametrize("target", ["grading", "question_bank"])
def test_complete_current_database_is_checked_without_replaying_migrations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    from backend import schema_migrations

    database = tmp_path / f"{target}.db"
    schema_migrations.ensure_schema_current(target, database)
    monkeypatch.setattr(schema_migrations, "_SCHEMA_INSPECT_CACHE", {})
    monkeypatch.setattr(schema_migrations, "_SCHEMA_SIGNATURE_CACHE", {})

    def forbid_replay(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("current database inspection must not replay migration SQL")

    monkeypatch.setattr(schema_migrations, "run_migrations", forbid_replay)
    result = schema_migrations.inspect_schema_version(target, database)
    assert not result.pending
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE synthetic_unrecorded_table (value TEXT)")
    with pytest.raises(schema_migrations.SchemaVersionError, match="differs from"):
        schema_migrations.inspect_schema_version(target, database)


@pytest.mark.parametrize("target", ["grading", "question_bank"])
def test_current_contract_opens_and_creates_databases_without_historical_sql(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    import shutil

    from backend import schema_migrations
    from backend.schema_contracts import schema_signature_document
    from update_tools.migrate_db import run_migrations

    migration_root = tmp_path / "TEST-migrations"
    source = _PROJECT_ROOT / "migrations" / target
    copied = migration_root / target
    shutil.copytree(source, copied)
    existing = tmp_path / "TEST-existing.db"
    report = run_migrations(
        target, db_path=existing, migrations_dir=copied,
        backup_dir_override=tmp_path / "TEST-setup-backups",
    )
    assert report.error is None
    before = schema_signature_document(existing)
    recorded = sqlite3.connect(existing)
    try:
        history = recorded.execute(
            "SELECT migration_name, checksum, success FROM schema_migrations ORDER BY id"
        ).fetchall()
    finally:
        recorded.close()
    shutil.move(str(migration_root), str(tmp_path / "TEST-history-removed"))
    monkeypatch.setattr(schema_migrations, "_DEFAULT_MIGRATIONS_ROOT", migration_root)

    def forbid_history(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("current databases must not read historical SQL")

    monkeypatch.setattr(schema_migrations, "_manifest_files", forbid_history)
    monkeypatch.setattr(schema_migrations, "run_migrations", forbid_history)
    assert not schema_migrations.ensure_schema_current(target, existing).pending
    fresh = tmp_path / "TEST-fresh.db"
    assert not schema_migrations.ensure_schema_current(target, fresh).pending
    assert schema_signature_document(fresh) == before
    with closing(sqlite3.connect(fresh)) as created:
        assert created.execute(
            "SELECT migration_name, checksum, success FROM schema_migrations ORDER BY id"
        ).fetchall() == history
        if target == "question_bank":
            assert created.execute(
                "SELECT singleton_id, enabled, revision, active_parameter_version, "
                "approved_evaluation_id FROM mastery_v2_rollout_state"
            ).fetchall() == [(1, 0, 1, None, None)]
    with closing(sqlite3.connect(existing)) as recorded:
        recorded.execute("UPDATE schema_migrations SET checksum = 'changed' WHERE id = 1")
        recorded.commit()
    with pytest.raises(schema_migrations.SchemaVersionError, match="checksum does not match"):
        schema_migrations.ensure_schema_current(target, existing)
    with closing(sqlite3.connect(existing)) as recorded:
        recorded.execute("UPDATE schema_migrations SET checksum = ?, success = 0 WHERE id = 1", (history[0][1],))
        recorded.commit()
    failed = existing.read_bytes()
    with pytest.raises(schema_migrations.SchemaVersionError, match="contains a failure"):
        schema_migrations.ensure_schema_current(target, existing)
    assert existing.read_bytes() == failed


@pytest.mark.parametrize("target", ["grading", "question_bank"])
def test_editing_historical_sql_does_not_change_current_runtime_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    import shutil

    from backend import schema_migrations
    from tools import generate_schema_baseline

    project = tmp_path / "TEST-project"
    migrations = project / "migrations" / target
    shutil.copytree(_PROJECT_ROOT / "migrations" / target, migrations)
    monkeypatch.setattr(schema_migrations, "_DEFAULT_MIGRATIONS_ROOT", project / "migrations")
    existing = tmp_path / "TEST-existing.db"
    schema_migrations.ensure_schema_current(target, existing)
    edited = sorted(migrations.glob("*.sql"))[0]
    with edited.open("a", encoding="utf-8") as output:
        output.write("\n-- TEST historical text changed without a business schema change\n")
    monkeypatch.setattr(schema_migrations, "_SCHEMA_INSPECT_CACHE", {})
    assert not schema_migrations.ensure_schema_current(target, existing).pending
    monkeypatch.setattr(generate_schema_baseline, "PROJECT_ROOT", project)
    assert generate_schema_baseline.main(["--target", target, "--check"]) == 1


@pytest.mark.parametrize("target", ["grading", "question_bank"])
def test_current_baseline_creation_rolls_back_without_successful_tracking_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    from dataclasses import replace

    from backend import schema_migrations
    from backend.schema_contracts import load_current_schema_contract

    original = load_current_schema_contract(target)
    assert original is not None
    broken = replace(original, creation_statements=original.creation_statements + ("INVALID TEST SQL",))
    monkeypatch.setattr(schema_migrations, "_current_contract", lambda _target: broken)
    database = tmp_path / "TEST-failed-creation.db"
    with pytest.raises(sqlite3.DatabaseError):
        schema_migrations.ensure_schema_current(target, database)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall() == []


@pytest.mark.parametrize("target", ["grading", "question_bank"])
def test_data_version_mapping_covers_every_schema_table(target: str) -> None:
    """每张表要么有 dv_ 触发器，要么在显式不跟踪清单里；映射不得含过期表名。"""
    import json

    from backend.schema_contracts import current_schema_contract_path
    from integration.data_generation import (
        GRADING_VERSION_UNTRACKED_TABLES,
        QUESTION_BANK_VERSION_DOMAIN_TABLES,
        QUESTION_BANK_VERSION_UNTRACKED_TABLES,
    )

    contract = json.loads(current_schema_contract_path(target).read_text(encoding="utf-8"))
    tables = set(contract["schema"]["tables"])
    triggers = {
        str(entry[1])
        for entry in contract["schema"]["indexes_and_triggers"]
        if entry[0] == "trigger"
    }
    if target == "grading":
        untracked = GRADING_VERSION_UNTRACKED_TABLES
        explicit: set[str] = set()
    else:
        untracked = QUESTION_BANK_VERSION_UNTRACKED_TABLES
        explicit = set().union(*QUESTION_BANK_VERSION_DOMAIN_TABLES.values())

    # 映射里的每个名字都必须仍是真实表（schema_migrations 由迁移机制自建，
    # 不进入契约表清单），防止改名后映射悄悄失效。
    assert not ((untracked | explicit) - tables - {"schema_migrations"})
    assert "data_versions" in tables
    assert explicit <= tables
    tracked = tables - untracked
    assert tracked and explicit <= tracked
    for table in tracked:
        for suffix in ("ins", "upd", "del"):
            assert f"dv_{table}_{suffix}" in triggers, table
    for table in tables & untracked:
        assert not any(name.startswith(f"dv_{table}_") for name in triggers), table


def _business_row_dump(database: Path) -> dict[str, list[tuple]]:
    with closing(sqlite3.connect(database)) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' "
                "AND name NOT IN ('schema_migrations', 'data_versions')"
            )
        ]
        return {
            table: sorted(
                connection.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr
            )
            for table in tables
        }


@pytest.mark.parametrize("target", ["grading", "question_bank"])
def test_data_version_migration_preserves_rows_and_adds_triggers(
    tmp_path: Path,
    target: str,
) -> None:
    import shutil

    from backend.schema_migrations import ensure_schema_current
    from update_tools.migrate_db import run_migrations

    source_dir = _PROJECT_ROOT / "migrations" / target
    newest = sorted(source_dir.glob("*.sql"))[-1]
    legacy_dir = tmp_path / "TEST-legacy-sql" / target
    legacy_dir.mkdir(parents=True)
    for file in source_dir.glob("*.sql"):
        if file != newest:
            shutil.copy2(file, legacy_dir / file.name)
    database = tmp_path / f"TEST-{target}.db"
    ensure_schema_current(target, database, migrations_dir=legacy_dir)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'data_versions'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'dv_%'"
        ).fetchall() == []
    before = _business_row_dump(database)

    report = run_migrations(
        target,
        db_path=database,
        migrations_dir=source_dir,
        backup_dir_override=tmp_path / "TEST-backups",
    )
    assert report.error is None, report.error

    assert _business_row_dump(database) == before
    from integration.data_generation import (
        GRADING_VERSION_UNTRACKED_TABLES,
        QUESTION_BANK_VERSION_UNTRACKED_TABLES,
    )

    untracked = (
        GRADING_VERSION_UNTRACKED_TABLES
        if target == "grading"
        else QUESTION_BANK_VERSION_UNTRACKED_TABLES
    )
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        domains = {
            row[0]
            for row in connection.execute("SELECT domain FROM data_versions")
        }
        assert domains == (
            {"results"}
            if target == "grading"
            else {"materials", "training_records", "training_ops"}
        )
        objects = connection.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall()
        dv_tables = {tbl for name, tbl in objects if str(name).startswith("dv_")}
        all_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert dv_tables == all_tables - untracked


def test_question_bank_data_version_domain_bumps(tmp_path: Path) -> None:
    from backend.schema_migrations import ensure_schema_current
    from integration.data_generation import data_versions, reset_commit_generations

    database = tmp_path / "TEST-qb-bumps.db"
    ensure_schema_current("question_bank", database)
    reset_commit_generations()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (1, 'T', 'success')"
        )
        connection.execute(
            "INSERT INTO questions(id, paper_id, question_number, question_text) "
            "VALUES (1, 1, '1', 'TEST')"
        )
        connection.execute(
            "INSERT INTO personalized_recommendation_drafts("
            "draft_id, request_token, input_fingerprint, result_version,"
            "engine_version, source_version, request_json, draft_json,"
            "status, revision, created_by) "
            "VALUES (?, ?, ?, ?, 'e', ?, '{}', '{}', 'draft', 1, 'TEST')",
            ("d" * 64, "r" * 32, "f" * 64, "v" * 64, "s" * 64),
        )
        connection.execute(
            "INSERT INTO question_tags(question_id, tag_type, tag_value) "
            "VALUES (1, 'probe', 'TEST')"
        )
        connection.commit()
    reset_commit_generations()
    assert data_versions(database) == {
        "materials": 3,
        "training_records": 0,
        "training_ops": 1,
    }


def test_grading_data_versions_jobs_untracked_results_bumps(tmp_path: Path) -> None:
    from backend.schema_migrations import ensure_schema_current
    from integration.data_generation import data_versions, reset_commit_generations

    database = tmp_path / "TEST-grading-bumps.db"
    ensure_schema_current("grading", database)
    reset_commit_generations()
    before = data_versions(database)
    assert before == {"results": 0}
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO jobs(job_type, status) VALUES ('probe', 'queued')"
        )
        connection.commit()
    reset_commit_generations()
    assert data_versions(database) == before
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO session_results("
            "session_id, student_id, paper_id, needs_human_review,"
            "raw_json, student_score, total_score) "
            "VALUES (0, 0, 0, 0, '{}', 0, 0)"
        )
        connection.execute(
            "UPDATE session_results SET student_score = 1 WHERE id = 1"
        )
        connection.commit()
    reset_commit_generations()
    assert data_versions(database) == {"results": 2}


def test_data_versions_none_when_trigger_coverage_incomplete(tmp_path: Path) -> None:
    from backend.schema_migrations import ensure_schema_current
    from integration.data_generation import data_versions, reset_commit_generations

    database = tmp_path / "TEST-coverage.db"
    ensure_schema_current("question_bank", database)
    reset_commit_generations()
    assert data_versions(database) is not None
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("DROP TRIGGER dv_question_tags_ins")
        connection.commit()
    assert data_versions(database) is None
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("DROP TABLE data_versions")
        connection.commit()
    reset_commit_generations()
    assert data_versions(database) is None
