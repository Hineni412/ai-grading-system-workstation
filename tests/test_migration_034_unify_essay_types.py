"""034_unify_essay_types 迁移行为：题型归一 + 子类转 special_type 标签。"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUESTION_BANK_MIGRATIONS = PROJECT_ROOT / "migrations" / "question_bank"
MIGRATION_034 = QUESTION_BANK_MIGRATIONS / "034_unify_essay_types.sql"

sys.path.insert(0, str(PROJECT_ROOT / "update_tools"))

from migrate_db import run_migrations  # noqa: E402


def _seed_database_through_033(database: Path) -> None:
    through_033 = database.parent / "question-bank-migrations-through-033"
    through_033.mkdir(exist_ok=True)
    for source in sorted(QUESTION_BANK_MIGRATIONS.glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= 33:
            shutil.copy2(source, through_033 / source.name)
    report = run_migrations(
        "question_bank",
        db_path=database,
        migrations_dir=through_033,
    )
    assert report.error is None, report.error
    with sqlite3.connect(database) as connection:
        connection.executemany(
            """
            INSERT INTO questions (id, question_number, question_type, question_text)
            VALUES (?, ?, ?, ?)
            """,
            (
                (1, "1", "解答题（画图）", "请用尺规作图完成作图"),
                (2, "2", "解答题（计算）", "计算下列各式"),
                (3, "3", "解答题（证明）", "证明三角形全等"),
                (4, "4", "解答题", "阅读材料并回答问题"),
                (5, "5", "选择题", "下列结论正确的是"),
            ),
        )
        # 已有人工子类标签的题：迁移不得重复插入同值标签。
        connection.execute(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, confidence, source
            ) VALUES (2, 'special_type', '计算', 1.0, 'manual')
            """
        )


def _special_type_tags(database: Path) -> list[tuple[object, ...]]:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            """
            SELECT question_id, tag_value, confidence, source, model_name
            FROM question_tags
            WHERE tag_type = 'special_type'
            ORDER BY question_id, id
            """
        ).fetchall()


def _question_types(database: Path) -> dict[int, str]:
    with sqlite3.connect(database) as connection:
        return dict(
            connection.execute("SELECT id, question_type FROM questions").fetchall()
        )


def test_034_unifies_essay_types_and_converts_subtypes_to_tags(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_database_through_033(database)

    report = run_migrations(
        "question_bank",
        db_path=database,
        migrations_dir=QUESTION_BANK_MIGRATIONS,
    )

    assert report.error is None, report.error
    migration = next(item for item in report.results if item.name == MIGRATION_034.stem)
    assert migration.status == "applied"
    # 三种子类题型与裸"解答题"都归一为"解答题"；其他题型不动。
    assert _question_types(database) == {
        1: "解答题",
        2: "解答题",
        3: "解答题",
        4: "解答题",
        5: "选择题",
    }
    # 子类转标签 confidence=0.7、source 标注迁移来源；已有人工标签不重复插入；
    # 裸"解答题"（id=4）不补猜，保持未标注。
    assert _special_type_tags(database) == [
        (1, "画图", 0.7, "question_type_migration", None),
        (2, "计算", 1.0, "manual", None),
        (3, "证明", 0.7, "question_type_migration", None),
    ]


def test_034_sql_is_idempotent_when_reexecuted(tmp_path: Path) -> None:
    database = tmp_path / "question-bank.db"
    _seed_database_through_033(database)
    report = run_migrations(
        "question_bank",
        db_path=database,
        migrations_dir=QUESTION_BANK_MIGRATIONS,
    )
    assert report.error is None, report.error
    tags_before = _special_type_tags(database)

    # 迁移机制按记录不会重复应用；直接重放 SQL 验证 NOT EXISTS 幂等。
    with sqlite3.connect(database) as connection:
        connection.executescript(MIGRATION_034.read_text(encoding="utf-8"))

    assert _special_type_tags(database) == tags_before
    assert _question_types(database) == {
        1: "解答题",
        2: "解答题",
        3: "解答题",
        4: "解答题",
        5: "选择题",
    }
