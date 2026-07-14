from __future__ import annotations

import sqlite3
from pathlib import Path

from question_bank.database.schema import connect, initialize_database
from question_bank.services.source_question_link_service import SourceQuestionLinkService


def _seed_question(db_path: Path, question_id: int, number: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO questions (id, question_number, question_text) VALUES (?, ?, ?)",
            (question_id, number, f"题目 {number}"),
        )


def _add_tag(db_path: Path, question_id: int, tag_type: str, tag_value: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO question_tags (question_id, tag_type, tag_value) VALUES (?, ?, ?)",
            (question_id, tag_type, tag_value),
        )


def test_parent_aware_iterator_preserves_existing_effective_item_ids() -> None:
    from session_manager import iter_effective_rubric_item_refs

    rows = list(
        iter_effective_rubric_item_refs(
            {
                "questions": [
                    {
                        "question_id": "Q2",
                        "parts": [{"part_id": "Q2(1)"}, {"part_id": "Q2(2)"}],
                    },
                    {"question_id": "Q3"},
                ]
            }
        )
    )

    assert [(item_ref, parent_ref) for item_ref, parent_ref, _question, _item in rows] == [
        ("Q2(1)", "Q2"),
        ("Q2(2)", "Q2"),
        ("Q3", "Q3"),
    ]


def test_projection_inherits_parent_link_and_reads_all_current_tags(tmp_path: Path) -> None:
    from integration.question_tag_projection_service import QuestionTagProjectionService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    _seed_question(db_path, 201, "2")
    _seed_question(db_path, 202, "4")
    for tag_type, tag_value in (
        ("knowledge_point", "三角形全等"),
        ("sub_skill", "角平分线模型"),
        ("method", "构造辅助线"),
        ("ability", "推理能力"),
        ("model", "全等模型"),
        ("error_type", "辅助线思路缺失"),
        ("prerequisite", "角平分线性质"),
    ):
        _add_tag(db_path, 201, tag_type, tag_value)
    _add_tag(db_path, 202, "ability", "运算能力")

    links = SourceQuestionLinkService(db_path)
    links.confirm_link(
        grading_session_id=7,
        source_question_id="Q2",
        bank_question_id=201,
        link_method="paper_question_number",
    )
    links.confirm_link(
        grading_session_id=7,
        source_question_id="Q4",
        bank_question_id=202,
        link_method="paper_question_number",
    )
    rubric = {
        "questions": [
            {
                "question_id": "Q2",
                "parts": [{"part_id": "Q2(1)"}, {"part_id": "Q2(2)"}],
            },
            {"question_id": "Q3"},
            {"question_id": "Q4"},
        ]
    }

    projection = QuestionTagProjectionService(db_path).project_session(
        grading_session_id=7,
        rubric=rubric,
    )

    assert projection.total_items == 4
    assert projection.covered_items == 2
    assert projection.context_by_item()["Q2(1)"] == {
        "knowledge_point": ["三角形全等"],
        "sub_skill": ["角平分线模型"],
        "method": ["构造辅助线"],
        "ability": ["推理能力"],
        "model": ["全等模型"],
        "error_type": ["辅助线思路缺失"],
        "prerequisite": ["角平分线性质"],
    }
    assert projection.context_by_item()["Q2(2)"]["knowledge_point"] == ["三角形全等"]
    assert projection.missing_items == {"Q3": "missing_link", "Q4": "missing_knowledge_point"}

    with connect(db_path) as conn:
        conn.execute(
            "UPDATE question_tags SET tag_value = '轴对称' "
            "WHERE question_id = 201 AND tag_type = 'knowledge_point'"
        )

    refreshed = QuestionTagProjectionService(db_path).project_session(
        grading_session_id=7,
        rubric=rubric,
    )
    assert refreshed.context_by_item()["Q2(1)"]["knowledge_point"] == ["轴对称"]


def test_projection_reuses_borrowed_connection_for_links_questions_and_tags(
    tmp_path: Path,
) -> None:
    from integration.question_tag_projection_service import QuestionTagProjectionService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    borrowed = sqlite3.connect(db_path)
    borrowed.row_factory = sqlite3.Row
    try:
        borrowed.execute(
            "INSERT INTO questions (id, question_number, question_text) VALUES (301, '3', '未提交题目')"
        )
        borrowed.execute(
            "INSERT INTO question_tags (question_id, tag_type, tag_value) "
            "VALUES (301, 'knowledge_point', '未提交知识点')"
        )
        borrowed.execute(
            """
            INSERT INTO grading_question_links (
                grading_session_id, source_question_id, bank_question_id,
                link_method, confidence, status, evidence_json
            ) VALUES ('9', 'Q3', 301, 'manual', 1.0, 'confirmed', '{}')
            """
        )

        projection = QuestionTagProjectionService(
            db_path,
            external_connection=borrowed,
        ).project_session(
            grading_session_id=9,
            rubric={"questions": [{"question_id": "Q3"}]},
        )

        assert projection.context_by_item()["Q3"]["knowledge_point"] == ["未提交知识点"]
        assert borrowed.execute("SELECT 1").fetchone()[0] == 1
    finally:
        borrowed.rollback()
        borrowed.close()
