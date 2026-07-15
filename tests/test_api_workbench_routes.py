from __future__ import annotations

import hashlib
import json
import sqlite3
import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)


@pytest.fixture
def workbench_client(tmp_path: Path):
    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db, get_job_manager
    from backend.api.routers.workbench import router as workbench_router
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from db_manager import DBManager

    db_path = tmp_path / "grading.db"
    db = DBManager(db_path)
    db.initialize()
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps({"questions": [{"question_id": "Q1", "max_score": 10}]}),
        encoding="utf-8",
    )

    with sqlite3.connect(db_path) as conn:
        session_ids = [
            int(
                conn.execute(
                    """
                    INSERT INTO grading_sessions (
                        session_name, rubric_path, answer_key_path, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 'completed', ?, ?)
                    """,
                    (
                        f"Exam {index}",
                        str(rubric_path),
                        str(tmp_path / "answer.json"),
                        f"2026-07-{index:02d} 08:00:00",
                        f"2026-07-{index:02d} 09:00:00",
                    ),
                ).lastrowid
            )
            for index in (1, 2, 3)
        ]
        session_id = session_ids[-1]
        students = []
        for code, name in (("S001", "Alice"), ("S002", "Bob"), ("S003", "Cara")):
            students.append(
                int(
                    conn.execute(
                        "INSERT INTO students (student_code, name, class_name) VALUES (?, ?, 'Class 1')",
                        (code, name),
                    ).lastrowid
                )
            )

        matched_paper_id = int(
            conn.execute(
                """
                INSERT INTO exam_papers (
                    session_id, front_image, back_image, ocr_name, student_id,
                    match_status, processing_status
                ) VALUES (?, ?, ?, 'Alice', ?, 'matched', 'graded')
                """,
                (session_id, "front-a.jpg", "back-a.jpg", students[0]),
            ).lastrowid
        )
        conn.execute(
            """
            INSERT INTO exam_papers (
                session_id, front_image, back_image, ocr_name, student_id,
                match_status, processing_status, error_message
            ) VALUES (?, ?, ?, 'Unknown paper', NULL, 'unmatched', 'pending', NULL)
            """,
            (session_id, "C:/private/front.jpg", "C:/private/back.jpg"),
        )
        conn.execute(
            """
            INSERT INTO exam_papers (
                session_id, front_image, back_image, ocr_name, student_id,
                match_status, processing_status, error_message
            ) VALUES (?, ?, ?, 'Cara', ?, 'matched', 'failed', ?)
            """,
            (
                session_id,
                "front-c.jpg",
                "back-c.jpg",
                students[2],
                "Cannot read C:/private/cara.png",
            ),
        )
        conn.execute(
            """
            INSERT INTO session_attendance (
                session_id, student_id, attendance_status, source_reason
            ) VALUES (?, ?, 'scan_issue', ?)
            """,
            (session_id, students[1], "See C:/private/scan.png"),
        )
        result_id = int(
            conn.execute(
                """
                INSERT INTO session_results (
                    session_id, student_id, paper_id, total_score, student_score,
                    needs_human_review, raw_json
                ) VALUES (?, ?, ?, 10, 8, 1, '{}')
                """,
                (session_id, students[0], matched_paper_id),
            ).lastrowid
        )
        conn.execute(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, deduction_reason,
                knowledge_id, error_category, error_summary, confidence_score
            ) VALUES (?, 'Q1', 8, '需复核：字迹不清', 'K1', '需复核', 'unclear', 55)
            """,
            (result_id,),
        )
        conn.commit()

    manager = JobManager(JobStore(db_path), max_workers=1)
    manager.store.create_job(
        "grading_run",
        {"session_id": session_id, "path": "C:/private/source"},
    )
    latest_job = manager.store.create_job(
        "report_export",
        {"session_id": session_id, "token": "private-token"},
    )

    app = create_app()
    app.include_router(workbench_router)
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    with TestClient(app) as client:
        try:
            yield client, session_id, db_path, latest_job.id
        finally:
            manager.shutdown()


def test_overview_returns_existing_counts_and_recent_sessions(workbench_client) -> None:
    client, session_id, _db_path, latest_job_id = workbench_client

    response = client.get(
        "/api/workbench/overview",
        params={"session_id": session_id, "recent_limit": 2},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_session"]["id"] == session_id
    assert payload["progress"]["unmatched_papers"] == 1
    assert payload["review"] == {"question_count": 1, "item_count": 1}
    assert payload["anomalies"] == {
        "unmatched_papers": 1,
        "scan_issue_students": 1,
        "failed_papers": 1,
    }
    assert len(payload["recent_sessions"]) == 2
    assert payload["recent_jobs"][0]["id"] == latest_job_id
    assert "rubric_path" not in response.text
    assert "answer_key_path" not in response.text
    assert "payload" not in response.text
    assert "result" not in response.text
    assert "private-token" not in response.text
    assert "C:/private" not in response.text


def test_overview_without_session_does_not_choose_one(workbench_client) -> None:
    client, _session_id, _db_path, _latest_job_id = workbench_client

    payload = client.get("/api/workbench/overview").json()

    assert payload["current_session"] is None
    assert payload["progress"] is None
    assert payload["review"] is None
    assert payload["anomalies"] is None
    assert payload["recent_jobs"] == []
    assert len(payload["recent_sessions"]) == 3


@pytest.mark.parametrize("recent_limit", [1, 20])
def test_overview_accepts_recent_limit_bounds(
    workbench_client,
    recent_limit: int,
) -> None:
    client, _session_id, _db_path, _latest_job_id = workbench_client

    response = client.get(
        "/api/workbench/overview",
        params={"recent_limit": recent_limit},
    )

    assert response.status_code == 200
    assert len(response.json()["recent_sessions"]) == min(recent_limit, 3)


@pytest.mark.parametrize("recent_limit", [0, 21])
def test_overview_rejects_recent_limit_outside_bounds(
    workbench_client,
    recent_limit: int,
) -> None:
    client, _session_id, _db_path, _latest_job_id = workbench_client

    response = client.get(
        "/api/workbench/overview",
        params={"recent_limit": recent_limit},
    )

    assert response.status_code == 422


def test_overview_returns_404_for_missing_session(workbench_client) -> None:
    client, _session_id, _db_path, _latest_job_id = workbench_client

    response = client.get(
        "/api/workbench/overview",
        params={"session_id": 9999},
        headers={"x-request-id": "rid-missing-workbench"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    assert response.json()["error"]["request_id"] == "rid-missing-workbench"


def test_empty_session_overview_uses_existing_zero_counts(workbench_client) -> None:
    client, _session_id, _db_path, _latest_job_id = workbench_client
    first_session_id = client.get("/api/sessions").json()["items"][-1]["id"]

    payload = client.get(
        "/api/workbench/overview",
        params={"session_id": first_session_id},
    ).json()

    assert payload["progress"]["total_papers"] == 0
    assert payload["review"] == {"question_count": 0, "item_count": 0}
    assert payload["anomalies"] == {
        "unmatched_papers": 0,
        "scan_issue_students": 0,
        "failed_papers": 0,
    }
    assert payload["recent_jobs"] == []


def test_anomalies_are_stable_paginated_filterable_and_sanitized(
    workbench_client,
) -> None:
    client, session_id, _db_path, _latest_job_id = workbench_client

    first_page = client.get(
        f"/api/sessions/{session_id}/anomalies",
        params={"page": 1, "page_size": 2},
    )
    second_page = client.get(
        f"/api/sessions/{session_id}/anomalies",
        params={"page": 2, "page_size": 2},
    )

    assert first_page.status_code == 200
    assert second_page.status_code == 200
    assert first_page.json()["total"] == 3
    assert first_page.json()["total_pages"] == 2
    items = first_page.json()["items"] + second_page.json()["items"]
    assert [(item["anomaly_type"], item["anomaly_id"]) for item in items] == sorted(
        (item["anomaly_type"], item["anomaly_id"]) for item in items
    )
    assert {item["anomaly_type"] for item in items} == {
        "unmatched_paper",
        "scan_issue",
        "grading_failed",
    }
    assert all(item["detail"] is None for item in items if item["anomaly_type"] != "unmatched_paper")
    combined_text = first_page.text + second_page.text
    assert "front_image" not in combined_text
    assert "back_image" not in combined_text
    assert "source_reason" not in combined_text
    assert "C:/private" not in combined_text

    filtered = client.get(
        f"/api/sessions/{session_id}/anomalies",
        params={"anomaly_type": "scan_issue"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["anomaly_type"] == "scan_issue"


@pytest.mark.parametrize(
    "unsafe_text, marker",
    [
        ("token=plain-secret", "plain-secret"),
        ('{"payload":{"result":"private-result","error":"boom"}}', "private-result"),
        ("diagnostic payload=private-payload", "private-payload"),
        ("Traceback (most recent call last): ValueError: private-stack", "private-stack"),
        ("Bearer eyJ...private-signature", "private-signature"),
        ('diagnostic: {"debug":"private-marker"}', "private-marker"),
        (
            "java.lang.IllegalStateException: private-java at app.Worker.java:12",
            "private-java",
        ),
    ],
)
def test_anomaly_detail_rejects_opaque_diagnostic_text(
    workbench_client,
    unsafe_text: str,
    marker: str,
) -> None:
    client, session_id, db_path, _latest_job_id = workbench_client
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE exam_papers SET error_message = ?
            WHERE session_id = ? AND processing_status = 'failed'
            """,
            (unsafe_text, session_id),
        )
        conn.commit()

    response = client.get(
        f"/api/sessions/{session_id}/anomalies",
        params={"anomaly_type": "grading_failed"},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["detail"] is None
    assert marker not in response.text


@pytest.mark.parametrize(
    "unsafe_text, marker",
    [
        ("secret=plain-secret", "plain-secret"),
        ('{"payload":{"result":"private-result","error":"boom"}}', "private-result"),
        ("diagnostic error=private-error", "private-error"),
        ("Traceback (most recent call last): ValueError: private-stack", "private-stack"),
        ("Bearer eyJ...private-signature", "private-signature"),
        ('diagnostic: {"debug":"private-marker"}', "private-marker"),
        (
            "java.lang.IllegalStateException: private-java at app.Worker.java:12",
            "private-java",
        ),
    ],
)
def test_recent_job_detail_rejects_opaque_diagnostic_text(
    workbench_client,
    unsafe_text: str,
    marker: str,
) -> None:
    client, session_id, db_path, latest_job_id = workbench_client
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET detail = ? WHERE id = ?",
            (unsafe_text, latest_job_id),
        )
        conn.commit()

    response = client.get(
        "/api/workbench/overview",
        params={"session_id": session_id},
    )

    assert response.status_code == 200
    assert response.json()["recent_jobs"][0]["detail"] == ""
    assert marker not in response.text


def test_workbench_preserves_short_non_sensitive_diagnostic_text(
    workbench_client,
) -> None:
    client, session_id, db_path, latest_job_id = workbench_client
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE exam_papers SET error_message = 'OCR 识别失败，请重试'
            WHERE session_id = ? AND processing_status = 'failed'
            """,
            (session_id,),
        )
        conn.execute(
            "UPDATE jobs SET detail = 'processing batch 2 of 5' WHERE id = ?",
            (latest_job_id,),
        )
        conn.commit()

    anomalies = client.get(
        f"/api/sessions/{session_id}/anomalies",
        params={"anomaly_type": "grading_failed"},
    ).json()
    overview = client.get(
        "/api/workbench/overview",
        params={"session_id": session_id},
    ).json()

    assert anomalies["items"][0]["detail"] == "OCR 识别失败，请重试"
    assert overview["recent_jobs"][0]["detail"] == "processing batch 2 of 5"


@pytest.mark.parametrize("page_size", [0, 101])
def test_anomalies_reject_invalid_page_size(workbench_client, page_size: int) -> None:
    client, session_id, _db_path, _latest_job_id = workbench_client

    response = client.get(
        f"/api/sessions/{session_id}/anomalies",
        params={"page_size": page_size},
    )

    assert response.status_code == 422


def test_anomalies_return_404_for_missing_session(workbench_client) -> None:
    client, _session_id, _db_path, _latest_job_id = workbench_client

    response = client.get("/api/sessions/9999/anomalies")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


def test_workbench_gets_do_not_change_database_fingerprint(workbench_client) -> None:
    client, session_id, db_path, _latest_job_id = workbench_client
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    overview = client.get(
        "/api/workbench/overview",
        params={"session_id": session_id},
    )
    anomalies = client.get(f"/api/sessions/{session_id}/anomalies")

    after = hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert overview.status_code == 200
    assert anomalies.status_code == 200
    assert after == before
