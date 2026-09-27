"""Migration-authoritative schema guards for both application databases.

Historical migration files are immutable. Runtime compatibility initializers
must reach the current migration version without adding schema of their own.
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
