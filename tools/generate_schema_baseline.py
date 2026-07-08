"""生成两库的 Schema 基线迁移（000_baseline_schema.sql）。

原理：在临时目录新建空库 → 运行真实的运行时初始化 → 导出 sqlite_master 中的
表/索引/触发器原文 → 改写为幂等 ``IF NOT EXISTS`` → 写入迁移文件。
"与运行时 DDL 等价"因此是构造性成立的，不依赖人工比对。

用法（项目根目录）::

    runtime\\python\\python.exe tools/generate_schema_baseline.py

仅在运行时 DDL 发生实质变化、需要重建基线时重跑；日常 Schema 变更应写增量迁移。
"""

from __future__ import annotations

import re
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

_IF_NOT_EXISTS_RE = re.compile(
    r"^(CREATE\s+(?:TABLE|INDEX|UNIQUE\s+INDEX|TRIGGER))\s+(?!IF\s+NOT\s+EXISTS)",
    re.IGNORECASE,
)


def _dump_schema_objects(db_path: Path) -> list[str]:
    """导出库中业务对象的 CREATE 语句（幂等化），按 表→索引→触发器、名字排序。"""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations' "
        ).fetchall()
    finally:
        conn.close()

    order = {"table": 0, "index": 1, "trigger": 2}
    rows = [r for r in rows if r[0] in order]
    rows.sort(key=lambda r: (order[r[0]], str(r[1])))

    statements: list[str] = []
    for _type, _name, sql in rows:
        text = str(sql).strip()
        text = _IF_NOT_EXISTS_RE.sub(lambda m: m.group(1) + " IF NOT EXISTS ", text)
        statements.append(text.rstrip(";") + ";")
    return statements


def _write_baseline(statements: list[str], output_path: Path, label: str) -> None:
    header = (
        f"-- {label} Schema 基线（000）：与运行时初始化等价的幂等 DDL。\n"
        f"-- 由 tools/generate_schema_baseline.py 于 {datetime.now().strftime('%Y-%m-%d %H:%M')} 生成，请勿手工编辑；\n"
        f"-- 运行时 DDL 实质变化时重跑生成器重建本文件（漂移由 tests/test_schema_baseline.py 守卫）。\n\n"
    )
    output_path.write_text(header + "\n\n".join(statements) + "\n", encoding="utf-8")


def _build_fresh_grading(db_path: Path) -> None:
    from db_manager import DBManager
    from grading_run_store import GradingRunStore

    DBManager(db_path).initialize()
    GradingRunStore(db_path).initialize()


def _build_fresh_question_bank(db_path: Path) -> None:
    from question_bank.database.schema import initialize_database

    initialize_database(db_path)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="schema_baseline_") as tmp:
        tmp_dir = Path(tmp)

        grading_db = tmp_dir / "grading_fresh.db"
        _build_fresh_grading(grading_db)
        grading_statements = _dump_schema_objects(grading_db)
        grading_out = _PROJECT_ROOT / "migrations" / "grading" / "000_baseline_schema.sql"
        _write_baseline(grading_statements, grading_out, "阅卷库")
        print(f"[grading] {len(grading_statements)} 个对象 -> {grading_out}")

        qb_db = tmp_dir / "question_bank_fresh.db"
        _build_fresh_question_bank(qb_db)
        qb_statements = _dump_schema_objects(qb_db)
        qb_out = _PROJECT_ROOT / "migrations" / "question_bank" / "000_baseline_schema.sql"
        _write_baseline(qb_statements, qb_out, "题库库")
        print(f"[question_bank] {len(qb_statements)} 个对象 -> {qb_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
