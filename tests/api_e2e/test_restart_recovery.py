from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import get_job_manager
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore


def test_restart_marks_inflight_jobs_failed_and_preserves_terminal(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "restart.db")
    queued = store.create_job("scan_analysis", {"session_id": 1})
    running = store.create_job("grading_run", {"session_id": 1})
    succeeded = store.create_job("report_export", {"session_id": 1})
    assert store.mark_running(running.id) is True
    store.finish(succeeded.id, "succeeded", result={"session_id": 1})

    restarted = JobManager(
        JobStore(store.db_path),
        max_workers=1,
        cleanup_interrupted=True,
    )
    try:
        app = create_app()
        app.dependency_overrides[get_job_manager] = lambda: restarted
        with TestClient(app) as client:
            queued_response = client.get(f"/api/jobs/{queued.id}")
            running_response = client.get(f"/api/jobs/{running.id}")
            succeeded_response = client.get(f"/api/jobs/{succeeded.id}")

        assert queued_response.status_code == 200
        assert running_response.status_code == 200
        assert succeeded_response.status_code == 200
        assert queued_response.json()["status"] == "failed"
        assert running_response.json()["status"] == "failed"
        assert succeeded_response.json()["status"] == "succeeded"

        stored_running = restarted.get(running.id)
        assert stored_running is not None
        assert stored_running.error == "interrupted by process restart"
        public_error = "Job failed; see local logs for details."
        for response in (queued_response, running_response):
            exposed_error = response.json()["error"]
            assert exposed_error == public_error
            assert str(tmp_path) not in exposed_error
            assert stored_running.error not in exposed_error
    finally:
        restarted.shutdown()
