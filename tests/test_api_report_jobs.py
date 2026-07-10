from __future__ import annotations

import warnings
from pathlib import Path

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

    def report_handler(context: JobContext) -> dict[str, object]:
        session_id = int(context.payload["session_id"])
        context.report(0.5, "report_export", f"session {session_id}")
        report_path = tmp_path / "reports" / "report.xlsx"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(b"xlsx")
        return {
            "session_id": session_id,
            "file_path": str(report_path),
            "filename": report_path.name,
            "internal_debug": "must-not-be-public",
        }

    manager.register("report_export", report_handler)
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    with TestClient(app) as client:
        try:
            yield client, db, manager
        finally:
            manager.shutdown()


def test_session_report_export_route_queues_report_job(
    client_with_db_and_manager,
    tmp_path: Path,
) -> None:
    client, db, manager = client_with_db_and_manager
    session_id = db.create_grading_session("Exam A", "rubric.json", "answer.json")

    response = client.post(f"/api/sessions/{session_id}/reports/export")

    assert response.status_code == 202
    created = response.json()
    assert created["job_type"] == "report_export"
    assert created["payload"] == {"session_id": session_id}

    manager.wait(created["id"], timeout=5)
    loaded = client.get(f"/api/jobs/{created['id']}").json()
    assert loaded["status"] == "succeeded"
    assert loaded["result"] == {
        "session_id": session_id,
        "filename": "report.xlsx",
        "download_url": f"/api/jobs/{created['id']}/download",
    }
    assert "file_path" not in str(loaded["result"])
    assert str(tmp_path) not in str(loaded["result"])


def test_session_report_export_route_requires_existing_session(client_with_db_and_manager) -> None:
    client, _db, _manager = client_with_db_and_manager

    response = client.post(
        "/api/sessions/404/reports/export",
        headers={"x-request-id": "rid-report-missing"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    assert response.json()["error"]["request_id"] == "rid-report-missing"
