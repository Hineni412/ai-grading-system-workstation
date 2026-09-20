"""Report export: AI-vs-teacher comparison sheet and manual-only sessions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from db_manager import DBManager
from report import ReportGenerator


def _write_rubric(tmp_path: Path) -> Path:
    from path_manager import get_path_manager

    controlled_dir = (
        get_path_manager().data_root / "config" / "uploaded" / "report-test"
    )
    controlled_dir.mkdir(parents=True, exist_ok=True)
    rubric_path = controlled_dir / f"rubric-{tmp_path.name}.json"
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
    return rubric_path


def _seed_mixed_session(tmp_path: Path) -> Path:
    """AI-graded session where Q1 was overridden by a teacher lock."""
    db_path = tmp_path / "grading.db"
    rubric_path = _write_rubric(tmp_path)
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
            "INSERT INTO students (id, student_code, name, class_name) "
            "VALUES (1, '001', '张三', '一班')"
        )
        conn.execute(
            """
            INSERT INTO exam_papers (
                id, session_id, front_image, back_image, student_id,
                match_status, processing_status
            ) VALUES (1, 1, 'front.jpg', 'back.jpg', 1, 'matched', 'graded')
            """
        )
        conn.execute(
            """
            INSERT INTO session_results (
                id, session_id, student_id, paper_id, total_score, student_score,
                ai_student_score, needs_human_review, raw_json
            ) VALUES (1, 1, 1, 1, 10, 8, 7, 0, '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO session_details (
                id, result_id, question_id, score_awarded, ai_score_awarded,
                deduction_reason, knowledge_ids, error_category, error_summary
            ) VALUES
                (1, 1, 'Q1', 5, 4, '教师认定满分', '["K1"]', '教师已确认', 'teacher_score_locked'),
                (2, 1, 'Q2', 3, 3, '计算错误', '["K2"]', NULL, NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO teacher_score_locks (
                session_id, scan_batch_id, student_id, question_id,
                score_awarded, max_score, deduction_reason,
                source_target_type, source_target_id
            ) VALUES (1, 'batch-1', 1, 'Q1', 5, 5, '教师认定满分', 'session_detail', 1)
            """
        )
        conn.commit()
    return db_path


def _seed_manual_only_session(tmp_path: Path) -> Path:
    """No AI results at all; only teacher score locks exist."""
    db_path = tmp_path / "grading.db"
    rubric_path = _write_rubric(tmp_path)
    db = DBManager(db_path)
    db.initialize()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO grading_sessions (id, session_name, rubric_path, answer_key_path)
            VALUES (1, '纯人工测试', ?, '')
            """,
            (str(rubric_path),),
        )
        conn.execute(
            "INSERT INTO students (id, student_code, name, class_name) "
            "VALUES (1, '001', '张三', '一班')"
        )
        conn.execute(
            """
            INSERT INTO exam_papers (
                id, session_id, front_image, back_image, student_id,
                match_status, processing_status
            ) VALUES (1, 1, 'front.jpg', 'back.jpg', 1, 'matched', 'graded')
            """
        )
        conn.execute(
            """
            INSERT INTO teacher_score_locks (
                session_id, scan_batch_id, student_id, question_id,
                score_awarded, max_score, deduction_reason,
                source_target_type, source_target_id
            ) VALUES
                (1, 'batch-1', 1, 'Q1', 4, 5, '步骤分', 'exam_paper', 1),
                (1, 'batch-1', 1, 'Q2', 3, 5, NULL, 'exam_paper', 1)
            """
        )
        conn.commit()
    return db_path


def test_comparison_sheet_shows_ai_teacher_and_final_scores(tmp_path: Path) -> None:
    db_path = _seed_mixed_session(tmp_path)

    export_path = ReportGenerator(db_path, tmp_path / "reports").export_session(1)
    compare = pd.read_excel(export_path, sheet_name="AI与人工分对比", header=2)

    assert list(compare.columns) == [
        "班级",
        "学号",
        "学生姓名",
        "题号",
        "满分",
        "AI 得分",
        "人工得分",
        "最终得分",
    ]
    rows = {str(row["题号"]): row for _, row in compare.iterrows()}
    # 只保留教师实际打过分的行；Q2 没有人工得分，不再出现在对比页。
    assert set(rows) == {"Q1"}
    assert rows["Q1"]["AI 得分"] == 4
    assert rows["Q1"]["人工得分"] == 5
    assert rows["Q1"]["最终得分"] == 5


def test_manual_only_session_exports_with_empty_ai_track(tmp_path: Path) -> None:
    db_path = _seed_manual_only_session(tmp_path)

    export_path = ReportGenerator(db_path, tmp_path / "reports").export_session(1)

    compare = pd.read_excel(export_path, sheet_name="AI与人工分对比", header=2)
    assert len(compare) == 2
    rows = {str(row["题号"]): row for _, row in compare.iterrows()}
    assert pd.isna(rows["Q1"]["AI 得分"])
    assert rows["Q1"]["人工得分"] == 4
    assert rows["Q1"]["最终得分"] == 4
    assert rows["Q2"]["人工得分"] == 3
    assert rows["Q2"]["最终得分"] == 3

    scores = pd.read_excel(export_path, sheet_name="班级成绩总表", header=None)
    assert "张三" in scores.astype(str).values
    assert 7 in scores.values


def test_session_without_results_and_locks_still_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "grading.db"
    rubric_path = _write_rubric(tmp_path)
    db = DBManager(db_path)
    db.initialize()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO grading_sessions (id, session_name, rubric_path, answer_key_path)
            VALUES (1, '空场次', ?, '')
            """,
            (str(rubric_path),),
        )
        conn.commit()

    with pytest.raises(ValueError, match="暂无批改结果"):
        ReportGenerator(db_path, tmp_path / "reports").export_session(1)
