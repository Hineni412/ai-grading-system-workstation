"""Schema 漂移守卫：空库+全部迁移 ≡ 空库+运行时初始化（两库各验证）。

此后任何"只改运行时 DDL 不补迁移"（或反之）的改动都会让本测试失败。
基线由 tools/generate_schema_baseline.py 生成；漂移时先重跑生成器或补增量迁移。
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "update_tools"))

from migrate_db import run_migrations  # noqa: E402

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
        key
        for key in runtime_schema
        if runtime_schema[key] != migrated_schema[key]
    ]
    assert not diffs, "对象定义漂移: " + "; ".join(
        f"{key}: runtime={runtime_schema[key][:120]} vs migrated={migrated_schema[key][:120]}"
        for key in diffs[:3]
    )


def test_grading_migrations_match_runtime_schema(tmp_path: Path) -> None:
    from db_manager import DBManager
    from grading_run_store import GradingRunStore

    runtime_db = tmp_path / "runtime.db"
    DBManager(runtime_db).initialize()
    GradingRunStore(runtime_db).initialize()

    migrated_db = tmp_path / "migrated.db"
    report = run_migrations(
        "grading",
        db_path=migrated_db,
        migrations_dir=_PROJECT_ROOT / "migrations" / "grading",
    )
    assert report.error is None, report.error

    _assert_equivalent(runtime_db, migrated_db)


def test_question_bank_migrations_match_runtime_schema(tmp_path: Path) -> None:
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
