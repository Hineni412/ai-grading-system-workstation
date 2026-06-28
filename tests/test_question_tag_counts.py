from pathlib import Path

from question_bank.database.schema import connect, initialize_database
from question_bank.services.question_service import QuestionService


def test_tag_value_counts_are_exact_and_ignore_deleted_content(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (id, title, source_file, import_status) VALUES (1, 'A', 'a', 'ready')"
        )
        conn.execute(
            "INSERT INTO papers (id, title, source_file, import_status) VALUES (2, 'B', 'b', 'deleted')"
        )
        for question_id, paper_id, deleted in ((1, 1, 0), (2, 1, 0), (3, 1, 1), (4, 2, 0)):
            conn.execute(
                "INSERT INTO questions (id, paper_id, question_number, question_text, is_deleted) "
                "VALUES (?, ?, ?, '题目', ?)",
                (question_id, paper_id, str(question_id), deleted),
            )
        for question_id, value in (
            (1, "三角形全等"),
            (2, "三角形全等"),
            (3, "三角形全等"),
            (4, "三角形全等"),
            (2, "三角形全等判定"),
        ):
            conn.execute(
                "INSERT INTO question_tags (question_id, tag_type, tag_value) "
                "VALUES (?, 'knowledge_point', ?)",
                (question_id, value),
            )

    counts = QuestionService(db_path).tag_value_counts(
        "knowledge_point",
        ["三角形全等", "三角形全等判定", "不存在"],
    )

    assert counts == {"三角形全等": 2, "三角形全等判定": 1, "不存在": 0}
