from __future__ import annotations

import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_db_and_manager(tmp_path):
    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db, get_job_manager
    from backend.jobs.manager import JobContext, JobManager
    from backend.jobs.store import JobStore
    from db_manager import DBManager

    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)

    def scan_handler(context: JobContext) -> dict[str, object]:
        session_id = int(context.payload["session_id"])
        context.report(0.5, "scan_analysis", f"session {session_id}")
        return {
            "session_id": session_id,
            "scan_analysis_path": str(tmp_path / "scan_analysis_latest.json"),
            "summary": {"auto_matched": 1, "issues": 0, "absent_candidates": 0, "total_pages": 2},
        }

    manager.register("scan_analysis", scan_handler)
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    with TestClient(app) as client:
        try:
            yield client, db, manager
        finally:
            manager.shutdown()


def test_session_scan_analysis_route_requires_confirmed_template(
    client_with_db_and_manager,
) -> None:
    client, db, manager = client_with_db_and_manager
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")

    response = client.post(
        f"/api/sessions/{session_id}/scan/analyze",
        json={"enhance_images": False, "ocr_workers": 3},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "scan_template_not_found"
    assert manager.list() == ([], 0)


def test_session_scan_analysis_route_requires_existing_session(client_with_db_and_manager) -> None:
    client, _db, _manager = client_with_db_and_manager

    response = client.post(
        "/api/sessions/404/scan/analyze",
        json={},
        headers={"x-request-id": "rid-scan-missing"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    assert response.json()["error"]["request_id"] == "rid-scan-missing"
