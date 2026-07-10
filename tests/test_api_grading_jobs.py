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

    def grading_handler(context: JobContext) -> dict[str, object]:
        session_id = int(context.payload["session_id"])
        context.report(0.5, "grading_run", f"session {session_id}")
        return {
            "session_id": session_id,
            "state": "completed",
            "summary": {"graded": 1, "failed": 0, "skipped": 0, "conflicts": 0, "scan_issues": 0},
        }

    manager.register("grading_run", grading_handler)
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    with TestClient(app) as client:
        try:
            yield client, db, manager
        finally:
            manager.shutdown()


def test_session_grading_run_route_queues_grading_job(client_with_db_and_manager) -> None:
    client, db, manager = client_with_db_and_manager
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")

    response = client.post(
        f"/api/sessions/{session_id}/grading/run",
        json={
            "grading_mode": "full_paper",
            "failed_only": False,
            "enhance_images": False,
            "max_workers": 2,
            "requests_per_minute": 120,
        },
    )

    assert response.status_code == 202
    created = response.json()
    assert created["job_type"] == "grading_run"
    assert created["payload"] == {
        "session_id": session_id,
        "grading_mode": "full_paper",
        "failed_only": False,
        "enhance_images": False,
        "max_workers": 2,
        "requests_per_minute": 120,
    }
    assert "api_key" not in str(created["payload"]).lower()

    manager.wait(created["id"], timeout=5)
    loaded = client.get(f"/api/jobs/{created['id']}").json()
    assert loaded["status"] == "succeeded"
    assert loaded["result"]["summary"]["graded"] == 1


def test_session_grading_run_route_requires_existing_session(client_with_db_and_manager) -> None:
    client, _db, _manager = client_with_db_and_manager

    response = client.post(
        "/api/sessions/404/grading/run",
        json={},
        headers={"x-request-id": "rid-grading-missing"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    assert response.json()["error"]["request_id"] == "rid-grading-missing"
