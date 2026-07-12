from __future__ import annotations

import warnings

import pytest
from fastapi.testclient import TestClient

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)


class _TaskService:
    def get_task(self, task_id: int):
        if int(task_id) != 7:
            raise KeyError(task_id)
        return {"id": 7, "variants": [{"id": 11}, {"id": 12}]}


@pytest.fixture
def training_export_client(tmp_path):
    from backend.api.app import create_app
    from backend.api.dependencies import get_job_manager, get_training_task_service
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    manager.register(
        "training_export",
        lambda context: {
            "task_id": context.payload["task_id"],
            "variant_id": context.payload.get("variant_id"),
            "export_ids": [31],
            "file_path": str(tmp_path / "private" / "training.md"),
            "filename": "training.md",
        },
    )
    app = create_app()
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_training_task_service] = _TaskService
    with TestClient(app) as client:
        try:
            yield client, manager
        finally:
            manager.shutdown()


def test_submit_variant_export_persists_only_server_identifiers(
    training_export_client,
) -> None:
    client, manager = training_export_client

    response = client.post(
        "/api/training/tasks/7/exports",
        json={"variant_id": 11, "format": "markdown", "audience": "teacher"},
    )

    assert response.status_code == 202
    payload = response.json()["payload"]
    assert payload == {
        "task_id": 7,
        "variant_id": 11,
        "format": "markdown",
        "audience": "teacher",
    }
    manager.wait(response.json()["id"], timeout=5)
    loaded = client.get(f"/api/jobs/{response.json()['id']}").json()
    assert loaded["result"] == {
        "task_id": 7,
        "variant_id": 11,
        "export_ids": [31],
        "filename": "training.md",
        "download_url": f"/api/jobs/{response.json()['id']}/download",
    }
    assert "private" not in str(loaded)


def test_submit_task_bundle_rejects_audience(training_export_client) -> None:
    client, _manager = training_export_client

    response = client.post(
        "/api/training/tasks/7/exports",
        json={"format": "docx", "audience": "student"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_submit_training_export_rejects_missing_task_and_variant(
    training_export_client,
) -> None:
    client, _manager = training_export_client

    missing_task = client.post(
        "/api/training/tasks/404/exports",
        json={"format": "docx"},
    )
    missing_variant = client.post(
        "/api/training/tasks/7/exports",
        json={"variant_id": 99, "format": "docx", "audience": "student"},
    )

    assert missing_task.status_code == 404
    assert missing_task.json()["error"]["code"] == "training_task_not_found"
    assert missing_variant.status_code == 422
    assert missing_variant.json()["error"]["code"] == "training_export_request_invalid"


def test_failed_training_export_job_can_be_retried(training_export_client) -> None:
    client, manager = training_export_client
    source = manager.store.create_job(
        "training_export",
        {"task_id": 7, "variant_id": 11, "format": "docx", "audience": "teacher"},
    )
    assert manager.store.mark_running(source.id)
    manager.store.finish(source.id, "failed", error="private failure")

    response = client.post(f"/api/training/exports/jobs/{source.id}/retry")

    assert response.status_code == 202
    assert response.json()["payload"] == {
        "task_id": 7,
        "variant_id": 11,
        "format": "docx",
        "audience": "teacher",
        "retry_of_job_id": source.id,
    }


def test_training_export_retry_rejects_nonfailed_or_wrong_job(
    training_export_client,
) -> None:
    client, manager = training_export_client
    succeeded = manager.store.create_job("training_export", {"task_id": 7, "format": "docx"})
    assert manager.store.mark_running(succeeded.id)
    manager.store.finish(succeeded.id, "succeeded", result={})
    wrong = manager.store.create_job("report_export", {"session_id": 1})
    assert manager.store.mark_running(wrong.id)
    manager.store.finish(wrong.id, "failed", error="failed")

    not_retryable = client.post(f"/api/training/exports/jobs/{succeeded.id}/retry")
    wrong_type = client.post(f"/api/training/exports/jobs/{wrong.id}/retry")

    assert not_retryable.status_code == 409
    assert not_retryable.json()["error"]["code"] == "training_export_retry_not_available"
    assert wrong_type.status_code == 404
    assert wrong_type.json()["error"]["code"] == "training_export_job_not_found"


def test_generic_job_endpoint_rejects_training_export(training_export_client) -> None:
    client, _manager = training_export_client

    response = client.post(
        "/api/jobs/training_export",
        json={"payload": {"task_id": 7, "format": "docx"}},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "dedicated_job_endpoint_required"
