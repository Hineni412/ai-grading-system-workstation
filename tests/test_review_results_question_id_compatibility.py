from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from backend.results_center.service import ResultsCenterService
from backend.review.service import ReviewApplicationService
from db_manager import DBManager


def _seed_review_result(
    tmp_path: Path,
    *,
    stored_details: list[tuple[str, float]],
    rubric_parts: list[dict[str, Any]] | None = None,
) -> tuple[DBManager, int, dict[str, Any], dict[str, Any]]:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    rubric_path = tmp_path / "rubric.json"
    configured_parts = rubric_parts or [
        {
            "part_id": "Q1(P1)",
            "part_score": 10,
        }
    ]
    rubric_total = sum(
        float(part.get("part_score") or 0)
        for part in configured_parts
    )
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "max_score": rubric_total,
                        "parts": configured_parts,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    session_id = db.create_grading_session(
        "Historical question ID",
        str(rubric_path),
        "answer.json",
    )
    connection = sqlite3.connect(db.db_path)
    try:
        student_id = int(
            connection.execute(
                """
                INSERT INTO students (student_code, name, class_name)
                VALUES ('001', 'Student A', 'Class 1')
                """
            ).lastrowid
        )
        paper_id = int(
            connection.execute(
                """
                INSERT INTO exam_papers (
                    session_id, front_image, back_image, ocr_name, student_id,
                    match_status, processing_status
                ) VALUES (?, 'front.png', 'back.png', 'Student A', ?,
                          'matched', 'graded')
                """,
                (session_id, student_id),
            ).lastrowid
        )
        result_id = int(
            connection.execute(
                """
                INSERT INTO session_results (
                    session_id, student_id, paper_id, total_score,
                    student_score, needs_human_review, raw_json
                ) VALUES (?, ?, ?, ?, ?, 0, '{}')
                """,
                (
                    session_id,
                    student_id,
                    paper_id,
                    rubric_total,
                    sum(score for _question_id, score in stored_details),
                ),
            ).lastrowid
        )
        for question_id, score in stored_details:
            connection.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, deduction_reason,
                    knowledge_ids, error_category, error_summary,
                    confidence_score
                ) VALUES (?, ?, ?, 'AI scored', '["K1"]', '', '', 95)
                """,
                (result_id, question_id, score),
            )
        connection.commit()
    finally:
        connection.close()

    session = db.get_grading_session(session_id)
    assert session is not None
    manual_context = {
        "scan_batch_id": "current-batch",
        "papers": [
            {
                "student_id": student_id,
                "student_name": "Student A",
                "target_type": "paper",
                "target_id": str(paper_id),
                "front_media_url": "/front",
                "back_media_url": "/back",
            }
        ],
    }
    return db, session_id, session, manual_context


def test_single_part_parent_id_from_ai_keeps_score_in_review_and_results(
    tmp_path: Path,
) -> None:
    db, session_id, session, manual_context = _seed_review_result(
        tmp_path,
        stored_details=[("Q1", 7.0)],
    )
    review = ReviewApplicationService(db)

    items = review.list_items(
        session_id,
        session,
        scope="all",
        manual_context=manual_context,
    )
    snapshot = ResultsCenterService(review).get_snapshot(
        session_id,
        session,
        manual_context=manual_context,
    )

    assert [
        (item.question_id, item.score_status, item.score_awarded)
        for item in items
    ] == [("Q1", "ai_ready", 7.0)]
    assert [
        (student.current_score, student.ungraded_count)
        for student in snapshot.students
    ] == [(7.0, 0)]


def test_multi_part_parent_score_never_crosses_into_a_child(
    tmp_path: Path,
) -> None:
    db, session_id, session, manual_context = _seed_review_result(
        tmp_path,
        stored_details=[("Q1", 7.0)],
        rubric_parts=[
            {"part_id": "Q1(P1)", "part_score": 4},
            {"part_id": "Q1(P2)", "part_score": 6},
        ],
    )
    review = ReviewApplicationService(db)

    items = review.list_items(
        session_id,
        session,
        scope="all",
        manual_context=manual_context,
    )

    assert [
        (item.question_id, item.score_status, item.score_awarded)
        for item in items
    ] == [
        ("Q1(P1)", "ungraded", None),
        ("Q1(P2)", "ungraded", None),
    ]


def test_historical_subquestion_id_keeps_ai_score_in_review_and_results(
    tmp_path: Path,
) -> None:
    db, session_id, session, manual_context = _seed_review_result(
        tmp_path,
        stored_details=[("Q1(1)", 7.0)],
    )
    review = ReviewApplicationService(db)

    items = review.list_items(
        session_id,
        session,
        scope="all",
        manual_context=manual_context,
    )
    snapshot = ResultsCenterService(review).get_snapshot(
        session_id,
        session,
        manual_context=manual_context,
    )

    assert [
        (item.question_id, item.score_status, item.score_awarded)
        for item in items
    ] == [("Q1", "ai_ready", 7.0)]
    assert [
        (
            student.current_score,
            student.ungraded_count,
            student.failed_count,
        )
        for student in snapshot.students
    ] == [(7.0, 0, 0)]


def test_single_part_explicit_subquestion_alias_keeps_ai_score(
    tmp_path: Path,
) -> None:
    db, session_id, session, manual_context = _seed_review_result(
        tmp_path,
        stored_details=[("Q1(P1)", 7.0)],
    )
    review = ReviewApplicationService(db)

    items = review.list_items(
        session_id,
        session,
        scope="all",
        manual_context=manual_context,
    )
    snapshot = ResultsCenterService(review).get_snapshot(
        session_id,
        session,
        manual_context=manual_context,
    )

    assert [
        (item.question_id, item.score_status, item.score_awarded)
        for item in items
    ] == [("Q1", "ai_ready", 7.0)]
    assert [
        (student.current_score, student.ungraded_count)
        for student in snapshot.students
    ] == [(7.0, 0)]


def test_different_historical_subquestion_never_crosses_into_current_item(
    tmp_path: Path,
) -> None:
    db, session_id, session, manual_context = _seed_review_result(
        tmp_path,
        stored_details=[("Q1(P2)", 7.0)],
    )
    review = ReviewApplicationService(db)

    items = review.list_items(
        session_id,
        session,
        scope="all",
        manual_context=manual_context,
    )
    snapshot = ResultsCenterService(review).get_snapshot(
        session_id,
        session,
        manual_context=manual_context,
    )

    assert [
        (item.question_id, item.score_status, item.score_awarded)
        for item in items
    ] == [("Q1", "ungraded", None)]
    assert [
        (student.current_score, student.ungraded_count)
        for student in snapshot.students
    ] == [(0, 1)]


def test_alias_collision_is_failed_instead_of_silently_overwriting_score(
    tmp_path: Path,
) -> None:
    db, session_id, session, manual_context = _seed_review_result(
        tmp_path,
        stored_details=[
            ("Q1(1)", 7.0),
            ("Q1(P1)", 8.0),
        ],
    )
    review = ReviewApplicationService(db)

    items = review.list_items(
        session_id,
        session,
        scope="all",
        manual_context=manual_context,
    )
    snapshot = ResultsCenterService(review).get_snapshot(
        session_id,
        session,
        manual_context=manual_context,
    )

    assert [
        (
            item.question_id,
            item.score_status,
            item.score_awarded,
            item.result_id,
            item.detail_id,
        )
        for item in items
    ] == [("Q1", "failed", None, None, None)]
    assert [
        (
            student.current_score,
            student.ungraded_count,
            student.failed_count,
            student.status,
        )
        for student in snapshot.students
    ] == [(0, 0, 1, "failed")]


def test_historical_review_without_manual_context_keeps_legacy_projection(
    tmp_path: Path,
) -> None:
    db, session_id, session, _manual_context = _seed_review_result(
        tmp_path,
        stored_details=[("Q1(1)", 7.0)],
    )
    review = ReviewApplicationService(db)

    items = review.list_items(
        session_id,
        session,
        scope="all",
        manual_context=None,
    )
    snapshot = ResultsCenterService(review).get_snapshot(
        session_id,
        session,
        manual_context=None,
    )

    assert [
        (item.question_id, item.score_status, item.score_awarded)
        for item in items
    ] == [("Q1(1)", "ai_ready", 7.0)]
    assert [
        (student.current_score, student.ungraded_count, student.failed_count)
        for student in snapshot.students
    ] == [(7.0, 0, 0)]
