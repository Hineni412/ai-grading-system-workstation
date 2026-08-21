from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from backend.results_center.service import ResultsCenterService
from backend.review.service import (
    ReviewApplicationService,
    ReviewConfirmationInput,
)
from db_manager import DBManager


def _seed_review_result(
    tmp_path: Path,
    *,
    stored_details: list[tuple[str, float]],
    rubric_parts: list[dict[str, Any]] | None = None,
) -> tuple[DBManager, int, dict[str, Any], dict[str, Any]]:
    db_path = tmp_path / "databases" / "grading.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = DBManager(db_path)
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


def test_single_part_uses_parent_max_when_legacy_part_score_is_missing(
    tmp_path: Path,
) -> None:
    db, session_id, session, manual_context = _seed_review_result(
        tmp_path,
        stored_details=[("Q1(1)", 7.0)],
    )
    rubric_path = Path(str(session["rubric_path"]))
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    del rubric["questions"][0]["parts"][0]["part_score"]
    rubric_path.write_text(
        json.dumps(rubric, ensure_ascii=False),
        encoding="utf-8",
    )

    items = ReviewApplicationService(db).list_items(
        session_id,
        session,
        scope="all",
        manual_context=manual_context,
    )

    assert [(item.question_id, item.max_score) for item in items] == [
        ("Q1", 10.0),
    ]


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

    # The stored parent detail leaves both rubric parts missing, so the
    # completeness audit marks them failed; the parent score must still
    # never cross into a child.
    assert [
        (item.question_id, item.score_status, item.score_awarded)
        for item in items
    ] == [
        ("Q1(P1)", "failed", None),
        ("Q1(P2)", "failed", None),
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


def test_teacher_can_save_current_parent_score_over_one_historical_detail(
    tmp_path: Path,
) -> None:
    db, session_id, session, manual_context = _seed_review_result(
        tmp_path,
        stored_details=[("Q1(1)", 7.0)],
    )
    template_id = db.upsert_session_template(
        session_id,
        "front-template.png",
        "back-template.png",
    )
    db.add_answer_region(
        session_id,
        template_id,
        {
            "page": "front",
            "region_order": 1,
            "x": 10,
            "y": 20,
            "w": 100,
            "h": 80,
            "mapped_question_id": "Q1(P1)",
            "is_confirmed": True,
        },
    )
    review = ReviewApplicationService(db)
    item = review.list_items(
        session_id,
        session,
        scope="all",
        manual_context=manual_context,
    )[0]

    outcome = review.confirm(
        session_id,
        session,
        "Q1",
        [
            ReviewConfirmationInput(
                result_id=item.result_id,
                detail_id=item.detail_id,
                score_awarded=9.0,
                review_item_id=item.review_item_id,
                expected_revision=item.revision,
                student_id=item.student_id,
            )
        ],
        manual_context=manual_context,
    )

    assert (outcome.updated_details, outcome.updated_results) == (1, 1)
    stored_detail = db.get_result_details(int(item.result_id or 0))[0]
    assert stored_detail["question_id"] == "Q1(1)"
    assert stored_detail["score_awarded"] == 9.0
    locks = db.review_repository.list_teacher_score_locks(
        session_id,
        "current-batch",
    )
    assert [lock["question_id"] for lock in locks] == ["Q1"]


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

    # The stored Q1(P2) detail leaves the rubric part missing, so the
    # completeness audit marks the item failed; the alien score must still
    # never cross into the current item.
    assert [
        (item.question_id, item.score_status, item.score_awarded)
        for item in items
    ] == [("Q1", "failed", None)]
    assert [
        (
            student.current_score,
            student.ungraded_count,
            student.failed_count,
        )
        for student in snapshot.students
    ] == [(0, 0, 1)]


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
