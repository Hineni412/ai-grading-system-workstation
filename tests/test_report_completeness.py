from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from db_manager import DBManager
from report import ReportGenerator


RUBRIC = {
    "questions": [
        {
            "question_id": "Q11",
            "max_score": 10,
            "parts": [
                {"part_id": "Q11(1)", "part_score": 4},
                {"part_id": "Q11(2)", "part_score": 6},
            ],
        }
    ]
}


def _seed_session(tmp_path: Path) -> Path:
    databases_dir = tmp_path / "databases"
    databases_dir.mkdir(parents=True, exist_ok=True)
    db_path = databases_dir / "grading.db"
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(json.dumps(RUBRIC, ensure_ascii=False), encoding="utf-8")

    DBManager(db_path).initialize()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO grading_sessions (id, session_name, rubric_path, answer_key_path) VALUES (1, '期末测试', ?, '')",
            (str(rubric_path),),
        )
        conn.executemany(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (?, ?, ?, ?)",
            [
                (1, "001", "张三", "一班"),
                (2, "002", "李四", "一班"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO exam_papers (
                id, session_id, student_id, front_image, back_image,
                match_status, processing_status
            ) VALUES (?, 1, ?, 'front.png', 'back.png', 'matched', 'graded')
            """,
            [(1, 1), (2, 2)],
        )
        conn.executemany(
            """
            INSERT INTO session_results (
                id, session_id, student_id, paper_id, total_score, student_score,
                needs_human_review, raw_json, graded_at
            ) VALUES (?, 1, ?, ?, 10, ?, 0, ?, '2026-06-20 10:00:00')
            """,
            [
                (1, 1, 1, 6, json.dumps({}, ensure_ascii=False)),
                (
                    2,
                    2,
                    2,
                    10,
                    json.dumps(
                        {
                            "grading_completeness": {
                                "status": "complete",
                                "missing_question_ids": [],
                                "duplicate_question_ids": [],
                                "unexpected_question_ids": [],
                                "score_out_of_range": [],
                                "affected_major_question_ids": [],
                            }
                        },
                        ensure_ascii=False,
                    ),
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, deduction_reason,
                knowledge_ids, error_category, error_summary
            ) VALUES (?, ?, ?, '', ?, '', '')
            """,
            [
                (1, "Q11(2)", 6, '["K11-2"]'),
                (2, "Q11(1)", 4, '["K11-1"]'),
                (2, "Q11(2)", 6, '["K11-2"]'),
            ],
        )
        conn.commit()
    return db_path


def test_export_session_marks_incomplete_and_complete_rows_with_completeness_columns(tmp_path: Path) -> None:
    db_path = _seed_session(tmp_path)

    export_path = ReportGenerator(db_path, tmp_path / "reports").export_session(1)
    exported = pd.read_excel(export_path, sheet_name="成绩与小题明细")

    assert "批改完整性" in exported.columns
    assert "缺失题目" in exported.columns
    by_name = {row["学生姓名"]: row for _, row in exported.iterrows()}
    assert by_name["张三"]["批改完整性"] == "不完整"
    assert by_name["张三"]["缺失题目"] == "Q11(P1)"
    assert by_name["李四"]["批改完整性"] == "完整"
    assert pd.isna(by_name["李四"]["缺失题目"]) or by_name["李四"]["缺失题目"] == ""


def test_completeness_fields_maps_supported_statuses() -> None:
    generator = ReportGenerator(Path("grading.db"), Path("reports"))

    assert generator._completeness_fields(
        {
            "grading_completeness": {
                "status": "complete",
                "missing_question_ids": [],
            }
        }
    ) == ("完整", "")
    assert generator._completeness_fields(
        {
            "grading_completeness": {
                "status": "incomplete",
                "missing_question_ids": ["Q11(1)", "Q11(2)"],
            }
        }
    ) == ("不完整", "Q11(1)、Q11(2)")
    assert generator._completeness_fields(
        {
            "grading_completeness": {
                "status": "invalid",
                "missing_question_ids": [],
            }
        }
    ) == ("无效", "")
