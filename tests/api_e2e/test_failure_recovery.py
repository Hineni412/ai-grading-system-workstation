from __future__ import annotations
from pathlib import Path
import pytest
import path_manager

from types import SimpleNamespace

from fastapi.testclient import TestClient
from backend.api.app import create_app
from backend.api import dependencies
from backend.api.dependencies import get_job_manager, get_ops_write_service
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore


@pytest.fixture(autouse=True)
def _controlled_synthetic_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # tests/conftest.py isolates the global PathManager into its own temporary
    # directory, so the harness data root (tmp_path / "synthetic_data") falls
    # outside every controlled root and resolve_stored_file_path rejects the
    # session's stored rubric/template paths during region commit. Point the
    # data root at tmp_path so the whole synthetic tree is controlled here.
    monkeypatch.setattr(path_manager.get_path_manager(), "_data_root", tmp_path)


def test_scan_failure_can_be_retried_without_half_published_state(
    api_e2e,
    prepared_session_id,
) -> None:
    api_e2e.controls.scan_failures_remaining = 1
    failed = api_e2e.client.post(
        f"/api/sessions/{prepared_session_id}/scan/analyze",
        json={"enhance_images": False},
    )
    assert failed.status_code == 202
    first_job = api_e2e.poll_job(failed.json()["id"], "failed")
    assert first_job["error"] == "synthetic scan failure"
    assert not (
        api_e2e.paths.templates_dir
        / f"session_{prepared_session_id}"
        / "scan_analysis_latest.json"
    ).exists()
    assert list(
        (
            api_e2e.paths.templates_dir
            / f"session_{prepared_session_id}"
        ).glob(".scan_analysis_latest.*.tmp")
    ) == []
    assert api_e2e.paper_statuses(prepared_session_id) == []

    retried = api_e2e.client.post(
        f"/api/sessions/{prepared_session_id}/scan/analyze",
        json={"enhance_images": False},
    )
    assert retried.status_code == 202
    second_job = api_e2e.poll_job(retried.json()["id"], "succeeded")
    assert second_job["result"]["summary"] == {
        "auto_matched": 2,
        "issues": 0,
        "absent_candidates": 0,
        "total_pages": 4,
    }
    assert api_e2e.controls.scan_runner_calls == 2
    assert api_e2e.controls.scanner_analyze_calls == 2


def test_partial_grading_uses_completed_semantics_and_failed_only_recovery(
    api_e2e,
    scanned_session_id,
) -> None:
    assert api_e2e.paths.qb_db_path.is_file()
    api_e2e.paths.qb_db_path.resolve().relative_to(api_e2e.paths.data_root.resolve())
    submitted = api_e2e.client.post(
        f"/api/sessions/{scanned_session_id}/grading/run",
        json={"grading_mode": "ai", "enhance_images": False},
    )
    assert submitted.status_code == 202
    job = api_e2e.poll_job(submitted.json()["id"], "succeeded")
    assert job["result"]["state"] == "completed"
    assert job["result"]["summary"]["graded"] == 1
    assert job["result"]["summary"]["failed"] == 1
    assert api_e2e.paper_statuses(scanned_session_id) == ["failed", "graded"]
    assert api_e2e.result_scores(scanned_session_id) == {"SYN-001": 85.0}
    assert api_e2e.result_review_flags(scanned_session_id) == {
        "SYN-001": True,
    }
    assert api_e2e.result_details(scanned_session_id) == {
        "SYN-001": [
            ("Q1", 12.0),
            ("Q2", 17.0),
            ("Q3", 17.0),
            ("Q4", 17.0),
            ("Q5", 17.0),
            ("Q6", 5.0),
        ]
    }
    successful_result_ids = api_e2e.result_ids(scanned_session_id)

    retried = api_e2e.client.post(
        f"/api/sessions/{scanned_session_id}/grading/run",
        json={"failed_only": True, "enhance_images": False},
    )
    assert retried.status_code == 202
    recovered = api_e2e.poll_job(retried.json()["id"], "succeeded")
    assert recovered["result"]["state"] == "completed"
    assert recovered["result"]["summary"]["graded"] == 1
    assert recovered["result"]["summary"]["failed"] == 0
    assert api_e2e.paper_statuses(scanned_session_id) == ["graded", "graded"]
    assert api_e2e.result_scores(scanned_session_id) == {
        "SYN-001": 85.0,
        "SYN-002": 70.0,
    }
    assert api_e2e.result_ids(scanned_session_id)["SYN-001"] == (
        successful_result_ids["SYN-001"]
    )
    details = api_e2e.result_details(scanned_session_id)
    assert details["SYN-001"] == [
        ("Q1", 12.0),
        ("Q2", 17.0),
        ("Q3", 17.0),
        ("Q4", 17.0),
        ("Q5", 17.0),
        ("Q6", 5.0),
    ]
    assert [question_id for question_id, _score in details["SYN-002"]] == [
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "Q5",
        "Q6",
    ]
    assert sum(score for _question_id, score in details["SYN-002"]) == 70.0
    assert api_e2e.controls.grading_qbank_paths == [
        api_e2e.paths.qb_db_path,
        api_e2e.paths.qb_db_path,
    ]


def test_restart_marks_inflight_jobs_failed_and_preserves_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ops_factory_calls = 0

    def fail_if_ops_service_is_created():
        nonlocal ops_factory_calls
        ops_factory_calls += 1
        raise AssertionError("restart test must not create a root-bound ops service")

    monkeypatch.setattr(
        dependencies,
        "create_ops_write_service",
        fail_if_ops_service_is_created,
    )
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
        app.dependency_overrides[get_ops_write_service] = lambda: SimpleNamespace()
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
        for response in (queued_response, running_response):
            assert response.json()["error"] == stored_running.error
        assert ops_factory_calls == 0
    finally:
        restarted.shutdown()
