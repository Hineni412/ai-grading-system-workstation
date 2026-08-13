"""Migration-authoritative schema guards for both application databases.

Historical migration files are immutable. Runtime compatibility initializers
must reach the current migration version without adding schema of their own.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "update_tools"))

from migrate_db import run_migrations  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_historical_baseline_generator_refuses_to_rewrite_migrations(
) -> None:
    import hashlib

    import tools.generate_schema_baseline as generator

    baselines = [
        _PROJECT_ROOT / "migrations" / target / "000_baseline_schema.sql"
        for target in ("grading", "question_bank")
    ]
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in baselines
    }

    assert generator.main() == 2
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in baselines
    } == before


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
        key
        for key in runtime_schema
        if runtime_schema[key] != migrated_schema[key]
    ]
    assert not diffs, "对象定义漂移: " + "; ".join(
        f"{key}: runtime={runtime_schema[key][:120]} vs migrated={migrated_schema[key][:120]}"
        for key in diffs[:3]
    )


def test_grading_initializers_match_current_migration_schema(tmp_path: Path) -> None:
    from backend.jobs.store import JobStore
    from db_manager import DBManager
    from grading_run_store import GradingRunStore

    runtime_db = tmp_path / "runtime.db"
    DBManager(runtime_db).initialize()
    GradingRunStore(runtime_db).initialize()
    JobStore(runtime_db).initialize()

    migrated_db = tmp_path / "migrated.db"
    report = run_migrations(
        "grading",
        db_path=migrated_db,
        migrations_dir=_PROJECT_ROOT / "migrations" / "grading",
    )
    assert report.error is None, report.error

    _assert_equivalent(runtime_db, migrated_db)


def test_jobs_schema_is_introduced_only_by_003(tmp_path: Path) -> None:
    import shutil

    through_002 = tmp_path / "through_002"
    through_002.mkdir()
    grading_migrations = _PROJECT_ROOT / "migrations" / "grading"
    for name in (
        "000_baseline_schema.sql",
        "001_init_migration_tracking.sql",
        "002_add_grading_run_ledger.sql",
    ):
        shutil.copy2(grading_migrations / name, through_002 / name)

    db_path = tmp_path / "grading.db"
    report = run_migrations(
        "grading",
        db_path=db_path,
        migrations_dir=through_002,
    )
    assert report.error is None, report.error
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone() is None

    report = run_migrations(
        "grading",
        db_path=db_path,
        migrations_dir=grading_migrations,
    )
    assert report.error is None, report.error
    with sqlite3.connect(db_path) as conn:
        objects = {
            (row[0], row[1])
            for row in conn.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE name = 'jobs' OR name LIKE 'idx_jobs_%'"
            )
        }
    assert objects == {
        ("table", "jobs"),
        ("index", "idx_jobs_status_created"),
        ("index", "idx_jobs_type_created"),
    }


def test_question_bank_initializer_matches_current_migration_schema(tmp_path: Path) -> None:
    from question_bank.database.schema import initialize_database

    runtime_db = tmp_path / "runtime.db"
    initialize_database(runtime_db)

    migrated_db = tmp_path / "migrated.db"
    report = run_migrations(
        "question_bank",
        db_path=migrated_db,
        migrations_dir=_PROJECT_ROOT / "migrations" / "question_bank",
    )
    assert report.error is None, report.error

    _assert_equivalent(runtime_db, migrated_db)
