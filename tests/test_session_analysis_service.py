from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.analytics import SessionAnalysisService
from db_manager import DBManager


def _insert_result(
    conn: sqlite3.Connection,
    *,
    result_id: int,
    session_id: int,
    student_id: int,
    paper_id: int,
    details: list[tuple[int, str, float, str | None]],
) -> None:
    conn.execute(
        """
        INSERT INTO exam_papers (
            id, session_id, front_image, back_image, student_id,
            match_status, processing_status
        ) VALUES (?, ?, ?, ?, ?, 'matched', 'graded')
        """,
        (
            paper_id,
            session_id,
            f"front-{paper_id}.jpg",
            f"back-{paper_id}.jpg",
            student_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO session_results (
            id, session_id, student_id, paper_id, total_score, student_score,
            needs_human_review, raw_json
        ) VALUES (?, ?, ?, ?, 20, 0, 0, '{}')
        """,
        (result_id, session_id, student_id, paper_id),
    )
    conn.executemany(
        """
        INSERT INTO session_details (
            id, result_id, question_id, score_awarded, deduction_reason, knowledge_ids
        ) VALUES (?, ?, ?, ?, ?, '["K"]')
        """,
        [
            (detail_id, result_id, qid, score, reason)
            for detail_id, qid, score, reason in details
        ],
    )


@pytest.fixture
def seed_analysis(tmp_path: Path) -> tuple[DBManager, int]:
    db_path = tmp_path / "grading.db"
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1", "max_score": 10, "parts": []},
                    {
                        "question_id": "Q2",
                        "max_score": 10,
                        "parts": [
                            {"part_id": "Q2(1)", "part_score": 4},
                            {"part_id": "Q2(2)", "part_score": 6},
                        ],
                    },
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
        conn.executemany(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (?, ?, ?, ?)",
            [
                (1, "S-ONE", "甲", "七年级一班"),
                (2, "S-TWO", "乙", "七年级一班"),
                (3, "S-THREE", "丙", "七年级二班"),
                (4, "S-NO-CLASS", "丁", None),
            ],
        )
        _insert_result(
            conn,
            result_id=1,
            session_id=1,
            student_id=1,
            paper_id=1,
            details=[
                (1, "Q1", 10, None),
                (2, "Q2(1)", 4, None),
                (3, "Q2(2)", 6, None),
            ],
        )
        _insert_result(
            conn,
            result_id=2,
            session_id=1,
            student_id=2,
            paper_id=2,
            details=[
                (4, "Q1", 8, "计算错误"),
                (5, "Q2(1)", 2, "步骤缺失"),
                (6, "Q2(2)", 5, None),
            ],
        )
        _insert_result(
            conn,
            result_id=3,
            session_id=1,
            student_id=3,
            paper_id=3,
            details=[(7, "Q1", 12, None)],
        )
        _insert_result(
            conn,
            result_id=4,
            session_id=1,
            student_id=4,
            paper_id=4,
            details=[(8, "Q1", 0, "未作答")],
        )
        conn.commit()
    return db, 1


@pytest.fixture
def seed_missing_max_score(tmp_path: Path) -> tuple[DBManager, int]:
    db_path = tmp_path / "missing-max.db"
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text('{"questions": []}', encoding="utf-8")
    db = DBManager(db_path)
    db.initialize()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO grading_sessions (id, session_name, rubric_path, answer_key_path) VALUES (1, '缺满分', ?, '')",
            (str(rubric_path),),
        )
        conn.execute(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (1, 'S1', '甲', '一班')"
        )
        _insert_result(
            conn,
            result_id=1,
            session_id=1,
            student_id=1,
            paper_id=1,
            details=[(1, "QX", 3, None)],
        )
        conn.commit()
    return db, 1


@pytest.fixture
def seed_parent_parts(tmp_path: Path) -> tuple[DBManager, int]:
    db_path = tmp_path / "parent-parts.db"
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q2",
                        "max_score": 10,
                        "parts": [
                            {"part_id": "Q2(1)", "part_score": 4},
                            {"part_id": "Q2(2)", "part_score": 6},
                        ],
                    }
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
            "INSERT INTO grading_sessions (id, session_name, rubric_path, answer_key_path) VALUES (1, '父题', ?, '')",
            (str(rubric_path),),
        )
        conn.executemany(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (?, ?, ?, '一班')",
            [
                (1, "S-FULL", "满分生"),
                (2, "S-PARTIAL", "失分生"),
                (3, "S-DIRECT", "直接明细生"),
                (4, "S-MERGED", "合并明细生"),
                (5, "S-OVER", "超分明细生"),
            ],
        )
        _insert_result(
            conn,
            result_id=1,
            session_id=1,
            student_id=1,
            paper_id=1,
            details=[(1, "Q2", 10, None)],
        )
        _insert_result(
            conn,
            result_id=2,
            session_id=1,
            student_id=2,
            paper_id=2,
            details=[(2, "Q2", 9, "小题失分")],
        )
        _insert_result(
            conn,
            result_id=3,
            session_id=1,
            student_id=3,
            paper_id=3,
            details=[(3, "Q2(1)", 3, "需复核")],
        )
        _insert_result(
            conn,
            result_id=4,
            session_id=1,
            student_id=4,
            paper_id=4,
            details=[(4, "Q2-1", 6, None), (5, "Q2-2", 6, None)],
        )
        _insert_result(
            conn,
            result_id=5,
            session_id=1,
            student_id=5,
            paper_id=5,
            details=[(6, "Q2(1)", 5, None)],
        )
        conn.commit()
    return db, 1


def test_service_keeps_the_frozen_question_analysis_contract(seed_analysis):
    db, session_id = seed_analysis

    actual = SessionAnalysisService(db).list_questions(
        session_id,
        class_name="七年级一班",
    )

    assert [(row.class_name, row.question_id) for row in actual] == [
        ("七年级一班", "Q1"),
        ("七年级一班", "Q2(1)"),
        ("七年级一班", "Q2(2)"),
    ]
    assert actual[0].question_id == "Q1"
    assert actual[0].score_rate == 90.0
    assert actual[0].average_score == 9.0
    assert actual[0].deduction_count == 1
    assert actual[0].attempt_count == 2


def test_service_merges_all_classes_and_preserves_unassigned_students(seed_analysis):
    db, session_id = seed_analysis

    rows = SessionAnalysisService(db).list_questions(session_id)
    q1 = next(row for row in rows if row.question_id == "Q1")

    assert q1.class_name == "全部班级"
    assert q1.attempt_count == 4
    assert q1.deduction_count == 2
    assert q1.score_rate == 70.0
    assert q1.average_score == 7.0
    assert q1.max_score == 10


def test_service_uses_null_for_uncomputable_rate(seed_missing_max_score):
    db, session_id = seed_missing_max_score

    row = SessionAnalysisService(db).list_questions(session_id)[0]

    assert row.score_rate is None
    assert row.max_score is None
    assert row.metric_status == "missing_max_score"
    assert row.attempt_count == 1
    assert row.average_score == 3


def test_student_rows_return_direct_canonical_details_and_review_state(seed_parent_parts):
    db, session_id = seed_parent_parts

    rows = SessionAnalysisService(db).list_students(session_id, "Q2(1)")
    direct = next(row for row in rows if row.student_code == "S-DIRECT")

    assert direct.student_id == 3
    assert direct.question_id == "Q2(1)"
    assert direct.score_awarded == 3
    assert direct.max_score == 4
    assert direct.deduction_amount == 1
    assert direct.deduction_reason == "需复核"
    assert direct.needs_review is True


def test_student_rows_infer_child_full_score_only_from_full_parent(seed_parent_parts):
    db, session_id = seed_parent_parts

    rows = SessionAnalysisService(db).list_students(session_id, "Q2(1)")
    inferred = next(row for row in rows if row.student_code == "S-FULL")

    assert inferred.score_awarded == 4
    assert inferred.max_score == 4
    assert inferred.deduction_amount == 0
    assert all(row.student_code != "S-PARTIAL" for row in rows)


def test_student_rows_merge_raw_ids_that_share_one_canonical_question(seed_parent_parts):
    db, session_id = seed_parent_parts

    rows = SessionAnalysisService(db).list_students(session_id, "Q2")
    merged = [row for row in rows if row.student_code == "S-MERGED"]

    assert len(merged) == 1
    assert merged[0].question_id == "Q2"
    assert merged[0].score_awarded == 10
    assert merged[0].max_score == 10
    assert merged[0].deduction_amount == 0


def test_student_rows_cap_direct_score_at_rubric_maximum(seed_parent_parts):
    db, session_id = seed_parent_parts

    rows = SessionAnalysisService(db).list_students(session_id, "Q2(1)")
    over_max = next(row for row in rows if row.student_code == "S-OVER")

    assert over_max.score_awarded == 4
    assert over_max.max_score == 4
    assert over_max.deduction_amount == 0
