from __future__ import annotations

import json
import inspect
import sqlite3
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient


def _seed_review_db(tmp_path: Path, *, result_count: int = 1):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_annotated_dir,
        get_grading_db,
        get_job_manager,
    )
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from db_manager import DBManager

    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1", "max_score": 10},
                    {"question_id": "Q2", "max_score": 5},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    session_id = db.create_grading_session("Exam A", str(rubric_path), "answer.json")
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        result_id = 0
        q1_detail_id = 0
        for index in range(result_count):
            student_id = int(
                conn.execute(
                    "INSERT INTO students (student_code, name, class_name) VALUES (?, ?, 'Class 1')",
                    (f"S{index + 1:03d}", "Alice" if index == 0 else f"Student {index + 1:03d}"),
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
                        f"front-{index}.jpg",
                        f"back-{index}.jpg",
                        "Alice" if index == 0 else f"Student {index + 1:03d}",
                        student_id,
                    ),
                ).lastrowid
            )
            current_result_id = int(
                conn.execute(
                    """
                    INSERT INTO session_results (
                        session_id, student_id, paper_id, total_score, student_score,
                        needs_human_review, raw_json
                    ) VALUES (?, ?, ?, 15, 10, 1, ?)
                    """,
                    (
                        session_id,
                        student_id,
                        paper_id,
                        json.dumps(
                            {
                                "detail_metadata": {
                                    "Q1": {
                                        "question_id": "Q1",
                                        "evidence_steps": [
                                            "student step",
                                            str(tmp_path / "private" / "evidence.png"),
                                        ],
                                        "missing_steps": ["final conclusion"],
                                        "candidate_scores": [
                                            {
                                                "score": 8,
                                                "confidence": 0.55,
                                                "reason": "unclear handwriting",
                                                "image_path": str(
                                                    tmp_path / "private" / "candidate.png"
                                                ),
                                            }
                                        ],
                                        "source": "legacy-import",
                                        "image_path": str(
                                            tmp_path / "private" / "metadata.png"
                                        ),
                                        "configApiKey": "metadata-secret",
                                    }
                                }
                            },
                            ensure_ascii=False,
                        ),
                    ),
                ).lastrowid
            )
            current_detail_id = int(
                conn.execute(
                    """
                    INSERT INTO session_details (
                        result_id, question_id, score_awarded, deduction_reason, knowledge_id,
                        error_category, error_summary, confidence_score
                    ) VALUES (?, 'Q1', 8, '需复核：字迹不清', 'K1', '需复核', 'unclear', 55)
                    """,
                    (current_result_id,),
                ).lastrowid
            )
            conn.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, deduction_reason, knowledge_id,
                    error_category, error_summary, confidence_score
                ) VALUES (?, 'Q2', 2, '计算错误', 'K2', '计算错误', 'wrong', 92)
                """,
                (current_result_id,),
            )
            if index == 0:
                result_id = current_result_id
                q1_detail_id = current_detail_id
        conn.commit()

    app = create_app()
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    app.dependency_overrides[get_annotated_dir] = (
        lambda: tmp_path / "annotated"
    )
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    return TestClient(app), db, session_id, result_id, q1_detail_id


def test_review_question_summary_and_items(tmp_path: Path) -> None:
    client, _db, session_id, result_id, detail_id = _seed_review_db(tmp_path)

    summary_response = client.get(f"/api/sessions/{session_id}/review/questions")

    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["total"] == 2
    q1 = next(item for item in summary["items"] if item["question_id"] == "Q1")
    q2 = next(item for item in summary["items"] if item["question_id"] == "Q2")
    assert q1["total_count"] == 1
    assert q1["needs_review_count"] == 1
    assert q1["max_score"] == 10
    assert q2["needs_review_count"] == 0

    items_response = client.get(f"/api/sessions/{session_id}/review/questions/Q1/items")

    assert items_response.status_code == 200
    items = items_response.json()
    assert items["total"] == 1
    row = items["items"][0]
    assert row["result_id"] == result_id
    assert row["detail_id"] == detail_id
    assert row["student_name"] == "Alice"
    assert row["needs_review"] is True
    assert row["candidate_scores"][0]["score"] == 8
    assert row["metadata"] == {
        "question_id": "Q1",
        "evidence_steps": ["student step"],
        "missing_steps": ["final conclusion"],
        "candidate_scores": [
            {
                "score": 8,
                "confidence": 0.55,
                "reason": "unclear handwriting",
            }
        ],
    }
    assert "metadata-secret" not in items_response.text
    assert str(tmp_path / "private") not in items_response.text
    assert row["media"] == {
        "crop_url": (
            f"/api/sessions/{session_id}/results/{result_id}"
            f"/details/{detail_id}/crop"
        ),
        "original_front_url": (
            f"/api/sessions/{session_id}/results/{result_id}/pages/front"
        ),
        "original_back_url": (
            f"/api/sessions/{session_id}/results/{result_id}/pages/back"
        ),
        "annotated_front_url": (
            f"/api/sessions/{session_id}/results/{result_id}"
            "/pages/front?variant=annotated"
        ),
        "annotated_back_url": (
            f"/api/sessions/{session_id}/results/{result_id}"
            "/pages/back?variant=annotated"
        ),
    }
    assert "front_image" not in row
    assert "back_image" not in row


def test_review_confirm_question_keeps_request_and_response_compatibility(tmp_path: Path) -> None:
    from backend.api.dependencies import get_review_application_service

    client, _db, session_id, result_id, detail_id = _seed_review_db(tmp_path)
    calls: list[tuple[int, dict[str, Any], str, list[Any]]] = []

    class FakeReviewApplicationService:
        def confirm(
            self,
            requested_session_id: int,
            session: dict[str, Any],
            question_id: str,
            items: list[Any],
        ) -> SimpleNamespace:
            calls.append((requested_session_id, session, question_id, items))
            return SimpleNamespace(
                updated_details=1,
                updated_results=1,
                annotation_outcomes=[
                    SimpleNamespace(result_id=result_id, status="succeeded", message=None)
                ],
            )

    client.app.dependency_overrides[get_review_application_service] = (
        lambda: FakeReviewApplicationService()
    )

    response = client.post(
        f"/api/sessions/{session_id}/review/questions/Q1/confirm",
        json={"items": [{"result_id": result_id, "detail_id": detail_id, "score_awarded": 9}]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["updated_details"] == 1
    assert payload["updated_results"] == 1
    assert payload["annotation_outcomes"] == [
        {"result_id": result_id, "status": "succeeded"}
    ]
    assert len(calls) == 1
    called_session_id, called_session, called_question_id, called_items = calls[0]
    assert called_session_id == session_id
    assert called_session["id"] == session_id
    assert called_question_id == "Q1"
    assert len(called_items) == 1
    assert called_items[0].result_id == result_id
    assert called_items[0].detail_id == detail_id
    assert called_items[0].score_awarded == 9.0


def test_review_routes_require_existing_session(tmp_path: Path) -> None:
    client, _db, _session_id, _result_id, _detail_id = _seed_review_db(tmp_path)

    response = client.get(
        "/api/sessions/404/review/questions",
        headers={"x-request-id": "rid-review-missing"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    assert response.json()["error"]["request_id"] == "rid-review-missing"


@pytest.mark.parametrize("invalid_score", [-0.01, 10.01])
def test_review_confirm_maps_invalid_score_to_stable_domain_error(
    tmp_path: Path,
    invalid_score: float,
) -> None:
    client, _db, session_id, result_id, detail_id = _seed_review_db(tmp_path)

    response = client.post(
        f"/api/sessions/{session_id}/review/questions/Q1/confirm",
        headers={"x-request-id": "rid-review-score"},
        json={
            "items": [
                {
                    "result_id": result_id,
                    "detail_id": detail_id,
                    "score_awarded": invalid_score,
                }
            ]
        },
    )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "review_validation_error",
        "message": "Invalid review confirmation",
        "details": {"session_id": session_id, "question_id": "Q1"},
        "request_id": "rid-review-score",
    }


def test_review_confirm_maps_wrong_detail_ownership_to_existing_not_found_error(
    tmp_path: Path,
) -> None:
    client, _db, session_id, result_id, detail_id = _seed_review_db(tmp_path)

    response = client.post(
        f"/api/sessions/{session_id}/review/questions/Q1/confirm",
        json={
            "items": [
                {
                    "result_id": result_id + 999,
                    "detail_id": detail_id,
                    "score_awarded": 9,
                }
            ]
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "review_detail_not_found"
    assert response.json()["error"]["message"] == "Review detail not found"
    assert response.json()["error"]["details"] == {
        "session_id": session_id,
        "question_id": "Q1",
        "result_id": result_id + 999,
        "detail_id": detail_id,
    }


def test_review_confirm_maps_atomic_ownership_revalidation_failure_to_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db, session_id, result_id, detail_id = _seed_review_db(tmp_path)
    original_apply = db.review_repository.apply_session_review_adjustments

    def invalidate_then_apply(
        requested_session_id: int,
        adjustments: list[dict[str, Any]],
    ) -> dict[str, int]:
        with sqlite3.connect(db.db_path) as conn:
            conn.execute(
                "UPDATE session_details SET question_id = 'Q2' WHERE id = ?",
                (detail_id,),
            )
            conn.commit()
        return original_apply(requested_session_id, adjustments)

    monkeypatch.setattr(
        db.review_repository,
        "apply_session_review_adjustments",
        invalidate_then_apply,
    )

    response = client.post(
        f"/api/sessions/{session_id}/review/questions/Q1/confirm",
        json={
            "items": [
                {
                    "result_id": result_id,
                    "detail_id": detail_id,
                    "score_awarded": 9,
                }
            ]
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "review_detail_not_found"
    assert response.json()["error"]["details"] == {
        "session_id": session_id,
        "question_id": "Q1",
        "result_id": result_id,
        "detail_id": detail_id,
    }


def test_review_confirm_exposes_stable_annotation_retry_outcome(tmp_path: Path) -> None:
    from backend.api.dependencies import get_manual_review_service

    client, _db, session_id, result_id, detail_id = _seed_review_db(tmp_path)

    class RetryManualReviewService:
        def apply_review_adjustments(
            self,
            requested_session_id: int,
            adjustments: list[dict[str, Any]],
            highlight_qids: list[str] | None = None,
        ) -> dict[str, Any]:
            assert requested_session_id == session_id
            assert highlight_qids == ["Q1"]
            return {
                "updated_details": len(adjustments),
                "updated_results": 1,
                "annotation_outcomes": [
                    {
                        "result_id": result_id,
                        "status": "retry_required",
                        "message": "Annotation rendering failed; retry required.",
                    }
                ],
            }

    client.app.dependency_overrides[get_manual_review_service] = (
        lambda: RetryManualReviewService()
    )

    response = client.post(
        f"/api/sessions/{session_id}/review/questions/Q1/confirm",
        json={
            "items": [
                {
                    "result_id": result_id,
                    "detail_id": detail_id,
                    "score_awarded": 9,
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["annotation_outcomes"] == [
        {
            "result_id": result_id,
            "status": "retry_required",
            "message": "Annotation rendering failed; retry required.",
        }
    ]


def test_large_review_api_get_uses_at_most_two_database_connections(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, db, session_id, _result_id, _detail_id = _seed_review_db(
        tmp_path,
        result_count=60,
    )
    original_connect = db._connect
    connection_count = 0

    def counted_connect():
        nonlocal connection_count
        connection_count += 1
        return original_connect()

    monkeypatch.setattr(db, "_connect", counted_connect)

    response = client.get(f"/api/sessions/{session_id}/review/questions/Q1/items")

    assert response.status_code == 200
    assert response.json()["total"] == 60
    assert connection_count <= 2


def test_review_router_remains_a_thin_application_service_shell() -> None:
    import backend.api.routers.review as review_router_module

    source = inspect.getsource(review_router_module)

    assert "_load_score_map" not in source
    assert "_build_review_rows" not in source
    assert "_find_review_row" not in source
    assert "_is_substantive_review_reason" not in source
    assert "get_session_results" not in source
    assert "get_result_details" not in source
    assert "apply_manual_adjustments" not in source
    assert "grouped" not in source
