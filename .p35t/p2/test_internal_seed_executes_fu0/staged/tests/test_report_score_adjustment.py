from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from db_manager import DBManager
from manual_review_service import ManualReviewService
from report import ReportGenerator


def _seed_session(tmp_path: Path) -> tuple[DBManager, Path]:
    db_path = tmp_path / "grading.db"
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
    db = DBManager(db_path)
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


def test_batch_score_adjustment_updates_details_and_recalculates_total(tmp_path: Path) -> None:
    db, _db_path = _seed_session(tmp_path)
    service = ManualReviewService(db, tmp_path / "annotated")

    result = service.apply_batch_score_adjustments(
        1,
        [
            {"detail_id": 1, "score_awarded": 4},
            {"detail_id": 2, "score_awarded": 5},
        ],
    )

    assert result == {"updated_details": 2, "updated_results": 1}
    assert [row["score_awarded"] for row in db.get_result_details(1)] == [4.0, 5.0]
    assert db.get_session_results(1)[0]["student_score"] == 9.0


def test_batch_score_adjustment_rejects_entire_batch_when_score_exceeds_max(tmp_path: Path) -> None:
    db, _db_path = _seed_session(tmp_path)
    service = ManualReviewService(db, tmp_path / "annotated")

    with pytest.raises(ValueError, match="Q2"):
        service.apply_batch_score_adjustments(
            1,
            [
                {"detail_id": 1, "score_awarded": 4},
                {"detail_id": 2, "score_awarded": 6},
            ],
        )

    assert [row["score_awarded"] for row in db.get_result_details(1)] == [2.0, 3.0]
    assert db.get_session_results(1)[0]["student_score"] == 5.0


def test_excel_export_uses_latest_adjusted_scores(tmp_path: Path) -> None:
    db, db_path = _seed_session(tmp_path)
    service = ManualReviewService(db, tmp_path / "annotated")
    service.apply_batch_score_adjustments(1, [{"detail_id": 1, "score_awarded": 5}])

    export_path = ReportGenerator(db_path, tmp_path / "reports").export_session(1)
    exported = pd.read_excel(export_path, sheet_name="成绩与小题明细")

    assert exported.loc[0, "总分"] == 8
    assert exported.loc[0, "Q1得分"] == "5/5"


def test_batch_score_adjustment_ignores_unrelated_unmapped_detail(tmp_path: Path) -> None:
    db, db_path = _seed_session(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO session_details (
                id, result_id, question_id, score_awarded, deduction_reason, knowledge_ids
            ) VALUES (3, 1, 'UNKNOWN', 0, NULL, '["UNKNOWN"]')
            """
        )
        conn.commit()

    service = ManualReviewService(db, tmp_path / "annotated")
    service.apply_batch_score_adjustments(1, [{"detail_id": 1, "score_awarded": 4}])

    assert db.get_session_results(1)[0]["student_score"] == 7.0
