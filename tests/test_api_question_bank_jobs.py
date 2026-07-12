from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import (
    get_job_manager,
    get_question_bank_write_service,
)
from backend.jobs.default_handlers import register_default_job_handlers
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from question_bank.services.question_write_service import QuestionBankWriteService


def _client(tmp_path: Path):
    service = QuestionBankWriteService(
        tmp_path / "data" / "databases" / "question_bank.db",
        data_root=tmp_path / "data",
    )
    upload = service.stage_upload(filename="测试试卷.docx", content=b"fake-docx")
    request = service.create_import_request(upload_id=upload.upload_id)
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)

    def import_handler(context):
        return {
            "request_id": context.payload["request_id"],
            "outcome": "complete",
            "imported_papers": 1,
            "question_count": 2,
            "failed_count": 0,
            "successful_question_ids": [11, 12],
            "failed_question_ids": [],
            "failure_category": "",
            "retryable": False,
            "internal_path": str(tmp_path / "must-not-leak.docx"),
        }

    def tagging_handler(context):
        ids = [int(item) for item in context.payload["question_ids"]]
        failed = [item for item in ids if item == 12]
        succeeded = [item for item in ids if item != 12]
        return {
            "outcome": "partial" if failed else "complete",
            "requested_count": len(ids),
            "skipped_complete_count": 0,
            "tagged_count": len(succeeded),
            "failed_count": len(failed),
            "successful_question_ids": succeeded,
            "failed_question_ids": failed,
            "failures": [
                {
                    "question_id": item,
                    "category": "timeout",
                    "message": "AI tagging request timed out.",
                }
                for item in failed
            ],
            "retryable": bool(failed),
            "question_text": "must not leak",
        }

    manager.register("question_import", import_handler)
    manager.register("tagging_sync", tagging_handler)
    app = create_app()
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_question_bank_write_service] = lambda: service
    return TestClient(app), manager, service, request


def test_question_import_route_submits_safe_queryable_job(tmp_path: Path) -> None:
    client, manager, _service, request = _client(tmp_path)

    response = client.post(
        f"/api/question-bank/import-requests/{request.request_id}/jobs"
    )

    assert response.status_code == 202
    body = response.json()
    assert body["job_type"] == "question_import"
    assert body["payload"] == {"request_id": request.request_id}
    manager.wait(body["id"], timeout=5)
    queried = client.get(f"/api/jobs/{body['id']}").json()
    assert queried["result"]["successful_question_ids"] == [11, 12]
    assert "internal_path" not in queried["result"]
    assert str(tmp_path) not in json.dumps(queried)


def test_question_import_route_rejects_missing_server_request(tmp_path: Path) -> None:
    client, _manager, _service, _request = _client(tmp_path)

    response = client.post(
        "/api/question-bank/import-requests/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/jobs"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "question_import_request_not_found"


def test_tagging_route_submits_safe_batch_and_projects_partial_result(
    tmp_path: Path,
) -> None:
    client, manager, _service, _request = _client(tmp_path)

    response = client.post(
        "/api/question-bank/tagging-jobs",
        json={"question_ids": [11, 12, 11]},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["payload"] == {"question_ids": [11, 12]}
    manager.wait(body["id"], timeout=5)
    queried = client.get(f"/api/jobs/{body['id']}").json()
    assert queried["result"]["outcome"] == "partial"
    assert queried["result"]["failed_question_ids"] == [12]
    assert "question_text" not in queried["result"]


def test_tagging_retry_accepts_only_source_failed_ids(tmp_path: Path) -> None:
    client, manager, _service, _request = _client(tmp_path)
    initial = client.post(
        "/api/question-bank/tagging-jobs",
        json={"question_ids": [11, 12]},
    ).json()
    manager.wait(initial["id"], timeout=5)

    rejected = client.post(
        f"/api/question-bank/tagging-jobs/{initial['id']}/retry",
        json={"question_ids": [11]},
    )
    accepted = client.post(
        f"/api/question-bank/tagging-jobs/{initial['id']}/retry",
        json={"question_ids": [12]},
    )

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "tagging_sync_retry_not_available"
    assert accepted.status_code == 202
    assert accepted.json()["payload"] == {
        "question_ids": [12],
        "retry_of_job_id": initial["id"],
    }


def test_question_import_retry_reuses_safe_request_id(tmp_path: Path) -> None:
    client, manager, _service, request = _client(tmp_path)
    source = manager.store.create_job(
        "question_import", {"request_id": request.request_id}
    )
    assert manager.store.mark_running(source.id)
    manager.store.finish(source.id, "failed", error="private failure")

    response = client.post(
        f"/api/question-bank/question-import-jobs/{source.id}/retry"
    )

    assert response.status_code == 202
    assert response.json()["payload"] == {
        "request_id": request.request_id,
        "retry_of_job_id": source.id,
    }


def test_question_bank_retry_routes_reject_wrong_source_job_types(tmp_path: Path) -> None:
    client, manager, _service, _request = _client(tmp_path)
    wrong = manager.store.create_job("report_export", {"session_id": 1})

    import_retry = client.post(
        f"/api/question-bank/question-import-jobs/{wrong.id}/retry"
    )
    tagging_retry = client.post(
        f"/api/question-bank/tagging-jobs/{wrong.id}/retry",
        json={},
    )

    assert import_retry.status_code == 404
    assert import_retry.json()["error"]["code"] == "question_import_job_not_found"
    assert tagging_retry.status_code == 404
    assert tagging_retry.json()["error"]["code"] == "tagging_sync_job_not_found"


def test_tagging_retry_uses_original_ids_after_failed_or_cancelled_job(
    tmp_path: Path,
) -> None:
    client, manager, _service, _request = _client(tmp_path)
    failed = manager.store.create_job("tagging_sync", {"question_ids": [11, 12]})
    assert manager.store.mark_running(failed.id)
    manager.store.finish(failed.id, "failed", error="private")
    cancelled = manager.store.create_job("tagging_sync", {"question_ids": [12]})
    assert manager.store.request_cancel(cancelled.id)

    failed_retry = client.post(
        f"/api/question-bank/tagging-jobs/{failed.id}/retry",
        json={},
    )
    cancelled_retry = client.post(
        f"/api/question-bank/tagging-jobs/{cancelled.id}/retry",
        json={},
    )

    assert failed_retry.status_code == 202
    assert failed_retry.json()["payload"]["question_ids"] == [11, 12]
    assert cancelled_retry.status_code == 202
    assert cancelled_retry.json()["payload"]["question_ids"] == [12]


def test_generic_job_route_rejects_dedicated_question_bank_jobs(tmp_path: Path) -> None:
    client, _manager, _service, _request = _client(tmp_path)

    for job_type in ("question_import", "tagging_sync"):
        response = client.post(
            f"/api/jobs/{job_type}",
            json={"payload": {"api_key": "must-not-persist"}},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "dedicated_job_endpoint_required"
        assert response.json()["error"]["message"] == (
            "Use the dedicated endpoint for this job type"
        )


def test_default_handlers_register_both_question_bank_job_types(tmp_path: Path) -> None:
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    register_default_job_handlers(
        manager,
        db_path=tmp_path / "grading.db",
        reports_dir=tmp_path / "reports",
        data_root=tmp_path / "data",
        question_bank_db_path=tmp_path / "data" / "databases" / "question_bank.db",
        question_import_runner=lambda **_kwargs: {},
        tagging_sync_runner=lambda **_kwargs: {},
        tagging_ai_service_factory=lambda: object(),
    )

    assert {"question_import", "tagging_sync"}.issubset(manager._handlers)
    manager.shutdown()
