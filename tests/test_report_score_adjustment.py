from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.repositories.db_manager import DBManager
from backend.review.manual_review_service import ManualReviewService
from backend.repositories.grading_database import open_grading_repositories


def _seed_session(tmp_path: Path) -> tuple[DBManager, Path]:
    # db 放在 "databases" 子目录下,使 tmp_path 被推断为受控 data root
    # (见 manual_review_service._data_root / report 中的同名逻辑),
    # 从而 tmp_path 下的 rubric.json 等存储路径能通过受控根校验。
    db_path = tmp_path / "databases" / "grading.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1", "max_score": 5, "parts": []},
                    {"question_id": "Q2", "max_score": 5, "parts": []},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    db = open_grading_repositories(db_path)
    db.initialize()

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO grading_sessions (id, session_name, rubric_path, answer_key_path)
            VALUES (1, '期末测试', ?, '')
            """,
            (str(rubric_path),),
        )
        conn.execute(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (1, '001', '张三', '一班')"
        )
        conn.execute(
            """
            INSERT INTO exam_papers (
                id, session_id, front_image, back_image, student_id, match_status, processing_status
            ) VALUES (1, 1, 'front.jpg', 'back.jpg', 1, 'matched', 'graded')
            """
        )
        conn.execute(
            """
            INSERT INTO session_results (
                id, session_id, student_id, paper_id, total_score, student_score,
                needs_human_review, raw_json
            ) VALUES (1, 1, 1, 1, 10, 5, 0, '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO session_details (
                id, result_id, question_id, score_awarded, deduction_reason, knowledge_ids
            ) VALUES
                (1, 1, 'Q1', 2, '过程不完整', '["K1"]'),
                (2, 1, 'Q2', 3, '计算错误', '["K2"]')
            """
        )
        conn.commit()
    return db, db_path


def test_batch_score_adjustment_checks_a_legacy_part_against_its_own_maximum(
    tmp_path: Path,
) -> None:
    db, db_path = _seed_session(tmp_path)
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "max_score": 5,
                        "parts": [
                            {"part_id": "Q1(1)", "part_score": 3},
                            {"part_id": "Q1_2", "part_score": 2},
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE session_details SET question_id = 'Q1-1', score_awarded = 2 WHERE id = 1"
        )
        conn.execute(
            "UPDATE session_details SET question_id = 'Q1(2)', score_awarded = 1 WHERE id = 2"
        )
        conn.commit()

    service = ManualReviewService(db, tmp_path / "annotated")
    with pytest.raises(ValueError, match=r"Q1\(P1\)"):
        service.apply_batch_score_adjustments(
            1,
            [{"detail_id": 1, "score_awarded": 4}],
        )

    assert [row["score_awarded"] for row in db.results.get_result_details(1)] == [2.0, 1.0]
