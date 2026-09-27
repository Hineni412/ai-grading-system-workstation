from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from db_manager import DBManager
from manual_review_service import ManualReviewService


def _seed_two_review_results(tmp_path: Path) -> tuple[DBManager, int, dict[str, int]]:
    db = DBManager(tmp_path / "manual-review-atomic.db")
    db.initialize()
    session_id = db.create_grading_session(
        "Atomic review", "rubric.json", "answer.json"
    )
    foreign_session_id = db.create_grading_session(
        "Foreign atomic review",
        "rubric.json",
        "answer.json",
    )
    ids: dict[str, int] = {"foreign_session_id": foreign_session_id}

    with sqlite3.connect(db.db_path) as conn:
        for label, target_score, other_score in (
            ("first", 4.0, 2.0),
            ("second", 3.0, 1.0),
        ):
            student_id = int(
                conn.execute(
                    "INSERT INTO students (student_code, name) VALUES (?, ?)",
                    (f"S-{label}", f"Student {label}"),
                ).lastrowid
            )
            paper_id = int(
                conn.execute(
                    """
                    INSERT INTO exam_papers (
                        session_id, front_image, back_image, ocr_name, student_id,
                        match_status, processing_status
                    ) VALUES (?, ?, ?, ?, ?, 'matched', 'graded')
                    """,
                    (
                        session_id,
                        f"{label}-front.jpg",
                        f"{label}-back.jpg",
                        f"Student {label}",
                        student_id,
                    ),
                ).lastrowid
            )
            result_id = int(
                conn.execute(
                    """
                    INSERT INTO session_results (
                        session_id, student_id, paper_id, total_score, student_score,
                        needs_human_review, raw_json
                    ) VALUES (?, ?, ?, 15, ?, 1, '{}')
                    """,
                    (session_id, student_id, paper_id, target_score + other_score),
                ).lastrowid
            )
            target_detail_id = int(
                conn.execute(
                    """
                    INSERT INTO session_details (
                        result_id, question_id, score_awarded, deduction_reason,
                        knowledge_ids, error_category, error_summary
                    ) VALUES (?, 'Q1', ?, ?, '["K1"]', ?, ?)
                    """,
                    (
                        result_id,
                        target_score,
                        f"{label} original reason",
                        f"{label} original category",
                        f"{label} original summary",
                    ),
                ).lastrowid
            )
            conn.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, deduction_reason,
                    knowledge_ids, error_category, error_summary
                ) VALUES (?, 'Q2', ?, 'untouched reason', '["K2"]', 'untouched category', 'untouched summary')
                """,
                (result_id, other_score),
            )
            ids[f"{label}_result_id"] = result_id
            ids[f"{label}_detail_id"] = target_detail_id
        conn.commit()

    return db, session_id, ids


def _adjustments(session_id: int, ids: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {
            "session_id": session_id,
            "result_id": ids["first_result_id"],
            "detail_id": ids["first_detail_id"],
            "question_id": "Q1",
            "score_awarded": 8.0,
            "deduction_reason": "first reviewed reason",
            "error_category": "first reviewed category",
            "error_summary": "first reviewed summary",
        },
        {
            "session_id": session_id,
            "result_id": ids["second_result_id"],
            "detail_id": ids["second_detail_id"],
            "question_id": "Q1",
            "score_awarded": 7.0,
            "deduction_reason": "second reviewed reason",
            "error_category": "second reviewed category",
            "error_summary": "second reviewed summary",
        },
    ]


def _review_state(
    db: DBManager,
    ids: dict[str, int],
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    detail_ids = (ids["first_detail_id"], ids["second_detail_id"])
    result_ids = (ids["first_result_id"], ids["second_result_id"])
    with sqlite3.connect(db.db_path) as conn:
        details = conn.execute(
            """
            SELECT id, score_awarded, deduction_reason, error_category, error_summary
            FROM session_details
            WHERE id IN (?, ?)
            ORDER BY id
            """,
            detail_ids,
        ).fetchall()
        results = conn.execute(
            """
            SELECT id, student_score
            FROM session_results
            WHERE id IN (?, ?)
            ORDER BY id
            """,
            result_ids,
        ).fetchall()
    return details, results


@pytest.mark.parametrize("forged_owner", ["session_id", "result_id", "question_id"])
def test_atomic_write_revalidates_session_result_and_question_ownership(
    tmp_path: Path,
    forged_owner: str,
) -> None:
    db, session_id, ids = _seed_two_review_results(tmp_path)
    adjustment = _adjustments(session_id, ids)[0]
    requested_session_id = session_id
    if forged_owner == "session_id":
        requested_session_id = ids["foreign_session_id"]
        adjustment["session_id"] = requested_session_id
    elif forged_owner == "result_id":
        adjustment["result_id"] = ids["second_result_id"]
    else:
        adjustment["question_id"] = "Q2"
    before = _review_state(db, ids)

    with pytest.raises(ValueError, match="does not belong"):
        db.apply_session_review_adjustments(requested_session_id, [adjustment])

    assert _review_state(db, ids) == before


def test_second_result_detail_failure_rolls_back_entire_review_batch(
    tmp_path: Path,
) -> None:
    db, session_id, ids = _seed_two_review_results(tmp_path)
    before = _review_state(db, ids)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            f"""
            CREATE TRIGGER fail_second_review_update
            BEFORE UPDATE ON session_details
            WHEN OLD.id = {ids["second_detail_id"]}
            BEGIN
                SELECT RAISE(ABORT, 'forced second review write failure');
            END;
            """
        )
        conn.commit()

    with pytest.raises(
        sqlite3.IntegrityError, match="forced second review write failure"
    ):
        db.apply_session_review_adjustments(session_id, _adjustments(session_id, ids))

    assert _review_state(db, ids) == before


def test_review_adjustments_commit_before_annotation_and_allow_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, session_id, ids = _seed_two_review_results(tmp_path)
    service = ManualReviewService(db, tmp_path / "annotated")
    rendered_result_ids: list[int] = []
    private_path = r"C:\Users\teacher\private\first-front.jpg"
    raw_error = f"renderer failed for {private_path}"

    def fail_first_annotation(
        result_id: int,
        highlight_qids: list[str] | None = None,
    ) -> dict[str, str] | None:
        rendered_result_ids.append(result_id)
        assert highlight_qids == ["Q1"]
        if result_id == ids["first_result_id"]:
            raise RuntimeError(raw_error)
        return {"front": "second-front.jpg", "back": "second-back.jpg"}

    monkeypatch.setattr(service, "render_result_annotation", fail_first_annotation)

    outcome = service.apply_review_adjustments(
        session_id,
        _adjustments(session_id, ids),
        highlight_qids=["Q1"],
    )

    assert _review_state(db, ids)[0] == [
        (
            ids["first_detail_id"],
            8.0,
            "first reviewed reason",
            "first reviewed category",
            "first reviewed summary",
        ),
        (
            ids["second_detail_id"],
            7.0,
            "second reviewed reason",
            "second reviewed category",
            "second reviewed summary",
        ),
    ]
    assert outcome["updated_details"] == 2
    assert outcome["updated_results"] == 2
    assert outcome["annotation_outcomes"] == [
        {
            "result_id": ids["first_result_id"],
            "status": "retry_required",
            "message": outcome["annotation_outcomes"][0]["message"],
        },
        {"result_id": ids["second_result_id"], "status": "succeeded"},
    ]
    failure_message = outcome["annotation_outcomes"][0]["message"]
    assert failure_message
    assert failure_message != raw_error
    assert raw_error not in failure_message
    assert private_path not in failure_message
    assert "annotated_paths" not in outcome
    assert rendered_result_ids == sorted(
        [ids["first_result_id"], ids["second_result_id"]]
    )

    monkeypatch.setattr(
        service,
        "render_result_annotation",
        lambda result_id, highlight_qids=None: {
            "front": f"retry-{result_id}-front.jpg",
            "back": f"retry-{result_id}-back.jpg",
        },
    )

    retried = service.apply_review_adjustments(
        session_id,
        _adjustments(session_id, ids),
        highlight_qids=["Q1"],
    )

    assert retried["annotation_outcomes"] == [
        {"result_id": ids["first_result_id"], "status": "succeeded"},
        {"result_id": ids["second_result_id"], "status": "succeeded"},
    ]
