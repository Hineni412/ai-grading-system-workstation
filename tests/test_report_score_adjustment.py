from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from db_manager import DBManager
from manual_review_service import ManualReviewService
from report import ReportGenerator
from web_app import _build_report_score_review_rows, _report_persisted_score, _report_score_input_key


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
            ) VALUES (1, 1, 'front.jpg', 'back.jpg', 1, 'matched', 'completed')
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
                id, result_id, question_id, score_awarded, deduction_reason, knowledge_id
            ) VALUES
                (1, 1, 'Q1', 2, '过程不完整', 'K1'),
                (2, 1, 'Q2', 3, '计算错误', 'K2')
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
                id, result_id, question_id, score_awarded, deduction_reason, knowledge_id
            ) VALUES (3, 1, 'UNKNOWN', 0, NULL, 'UNKNOWN')
            """
        )
        conn.commit()

    service = ManualReviewService(db, tmp_path / "annotated")
    service.apply_batch_score_adjustments(1, [{"detail_id": 1, "score_awarded": 4}])

    assert db.get_session_results(1)[0]["student_score"] == 7.0


def test_report_review_rows_include_parent_full_score_as_inferred_part_score(tmp_path: Path) -> None:
    db, db_path = _seed_session(tmp_path)
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q11",
                        "max_score": 11,
                        "parts": [{"part_id": "Q11(3)", "part_score": 5}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE grading_sessions SET rubric_path = ? WHERE id = 1", (str(rubric_path),))
        conn.execute("DELETE FROM session_details WHERE result_id = 1")
        conn.execute(
            """
            INSERT INTO session_details (
                id, result_id, question_id, score_awarded, deduction_reason, knowledge_id
            ) VALUES (11, 1, 'Q11', 11, NULL, 'K11')
            """
        )
        conn.execute("UPDATE session_results SET total_score = 11, student_score = 11 WHERE id = 1")
        conn.commit()

    rows = _build_report_score_review_rows(db, 1, "一班", "Q11(3)")

    assert len(rows) == 1
    assert rows[0]["question_id"] == "Q11(3)"
    assert rows[0]["source_question_id"] == "Q11"
    assert rows[0]["score_awarded"] == 5
    assert rows[0]["max_score"] == 5
    assert rows[0]["is_inferred_full_score"] is True
    assert _report_persisted_score(rows[0], 3) == 9
    assert "Q11(3)" in _report_score_input_key(1, rows[0])


def test_report_export_page_has_editable_scores_and_bottom_browser_downloads() -> None:
    source = Path("web_app.py").read_text(encoding="utf-8")
    start = source.index("def render_export_tab")
    end = source.index("def _render_selectable_question_matrix", start)
    section = source[start:end]

    assert "_render_report_score_review_grid" in section
    assert section.rindex("_render_report_export_controls") > section.index("_render_report_score_review_grid")
    assert '"下载 Excel 成绩报表"' in source
    assert 'mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"' in source
    assert "_report_score_revision" in source
    assert '"revision": score_revision' in source
    assert 'cached.get("revision") != score_revision' in source
    assert "st.session_state.pop(_report_score_input_key" not in section
    assert "_reset_report_score_input_state(selected_session_id)" in section
    assert "_mark_report_score_inputs_for_reset(session_id)" in section
    assert "查看满分同学复核" in section
    assert 'expanded=False' in section
