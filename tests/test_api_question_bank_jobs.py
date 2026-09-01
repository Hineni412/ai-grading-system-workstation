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
from question_bank.taxonomy.curriculum_catalog import curriculum_volume


VOLUME_ID = "bnu24-math-g7-lower"


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


def test_question_import_submit_carries_curriculum_volume_defaults(
    tmp_path: Path,
) -> None:
    client, manager, _service, request = _client(tmp_path)

    response = client.post(
        f"/api/question-bank/import-requests/{request.request_id}/jobs",
        json={"curriculum_volume_id": VOLUME_ID},
    )

    assert response.status_code == 202
    # 公开响应只投影白名单字段，完整 payload 以任务存储为准。
    stored = manager.get(response.json()["id"])
    assert stored is not None
    volume = curriculum_volume(volume_id=VOLUME_ID)
    assert volume is not None
    assert stored.payload == {
        "request_id": request.request_id,
        "paper_defaults": {
            "grade": str(volume["grade"]),
            "semester": str(volume["semester"]),
            "textbook_version": str(volume["textbook_version"]),
        },
    }


def test_question_import_submit_rejects_unknown_curriculum_volume(
    tmp_path: Path,
) -> None:
    client, _manager, _service, request = _client(tmp_path)

    response = client.post(
        f"/api/question-bank/import-requests/{request.request_id}/jobs",
        json={"curriculum_volume_id": "not-a-real-volume"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "curriculum_volume_invalid"


def test_question_import_result_exposes_duplicate_reuse_fields(tmp_path: Path) -> None:
    client, manager, _service, _request = _client(tmp_path)
    job = manager.store.create_job("question_import", {"request_id": "b" * 32})
    assert manager.store.mark_running(job.id)
    manager.store.finish(
        job.id,
        "succeeded",
        result={
            "request_id": "b" * 32,
            "outcome": "complete",
            "imported_papers": 1,
            "question_count": 2,
            "failed_count": 0,
            "successful_question_ids": [21, 22],
            "failed_question_ids": [],
            "failure_category": "",
            "retryable": False,
            "exact_duplicate_count": 1,
            "analysis_reused_count": 1,
            "near_duplicate_hints": [
                {
                    "question_number": "2",
                    "matched_question_id": 11,
                    "matched_paper_title": "旧卷",
                    "similarity": 0.87,
                    "high": True,
                }
            ],
        },
    )

    result = client.get(f"/api/jobs/{job.id}").json()["result"]
    assert result["exact_duplicate_count"] == 1
    assert result["analysis_reused_count"] == 1
    assert result["near_duplicate_hints"] == [
        {
            "question_number": "2",
            "matched_question_id": 11,
            "matched_paper_title": "旧卷",
            "similarity": 0.87,
            "high": True,
        }
    ]


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
        json={"question_ids": [11, 12, 11], "curriculum_volume_id": VOLUME_ID},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["payload"] == {
        "question_ids": [11, 12],
        "curriculum_volume_id": VOLUME_ID,
    }
    manager.wait(body["id"], timeout=5)
    queried = client.get(f"/api/jobs/{body['id']}").json()
    assert queried["result"]["outcome"] == "partial"
    assert queried["result"]["failed_question_ids"] == [12]
    assert "question_text" not in queried["result"]


def test_tagging_result_keeps_each_projection_status_visible(tmp_path: Path) -> None:
    client, manager, _service, _request = _client(tmp_path)
    job = manager.store.create_job(
        "tagging_sync",
        {"question_ids": [8, 9, 10, 11, 12], "curriculum_volume_id": VOLUME_ID},
    )
    assert manager.store.mark_running(job.id)
    manager.store.finish(
        job.id,
        "succeeded",
        result={
            "outcome": "partial",
            "tagged_count": 5,
            "complete_tagged_count": 12,
            "evidence_count": 11,
            "evidence_succeeded_question_ids": [8, 9, 10, 11],
            "evidence_failed_question_ids": [12],
            "criteria_count": 10,
            "criteria_succeeded_question_ids": [8, 9, 10],
            "criteria_failed_question_ids": [11],
            "criteria_needs_review_count": 1,
            "criteria_needs_review_question_ids": [10],
            "review_count": 1,
            "review_question_ids": [9],
            "failed_count": 2,
            "failed_question_ids": [11, 12],
            "retryable": True,
        },
    )

    result = client.get(f"/api/jobs/{job.id}").json()["result"]

    assert result["tagged_count"] == 5
    assert result["complete_tagged_count"] == 12
    assert result["evidence_failed_question_ids"] == [12]
    assert result["criteria_failed_question_ids"] == [11]
    assert result["criteria_needs_review_count"] == 1
    assert result["criteria_needs_review_question_ids"] == [10]
    assert result["review_question_ids"] == [9]


def test_tagging_route_refuses_to_guess_a_missing_curriculum_volume(
    tmp_path: Path,
) -> None:
    client, _manager, _service, _request = _client(tmp_path)

    response = client.post(
        "/api/question-bank/tagging-jobs",
        json={"question_ids": [11]},
    )

    assert response.status_code == 422


def test_tagging_route_marks_explicit_retag_scope(tmp_path: Path) -> None:
    client, _manager, _service, _request = _client(tmp_path)

    response = client.post(
        "/api/question-bank/tagging-jobs",
        json={
            "question_ids": [11, 12, 11],
            "force_retag": True,
            "curriculum_volume_id": VOLUME_ID,
        },
    )

    assert response.status_code == 202
    assert response.json()["payload"] == {
        "question_ids": [11, 12],
        "force_retag_question_ids": [11, 12],
        "curriculum_volume_id": VOLUME_ID,
    }


def test_tagging_route_reuses_client_request_token_without_duplicate_work(
    tmp_path: Path,
) -> None:
    client, manager, _service, _request = _client(tmp_path)
    payload = {
        "question_ids": [11, 12],
        "force_retag": True,
        "curriculum_volume_id": VOLUME_ID,
        "client_request_token": "a" * 32,
    }

    first = client.post("/api/question-bank/tagging-jobs", json=payload)
    repeated = client.post("/api/question-bank/tagging-jobs", json=payload)

    assert first.status_code == 202
    assert repeated.status_code == 202
    assert repeated.json()["id"] == first.json()["id"]
    jobs, total = manager.store.list_jobs(job_types=("tagging_sync",))
    assert total == 1
    assert jobs[0].payload["client_request_token"] == "a" * 32


def test_tagging_route_rejects_reusing_token_for_other_questions(
    tmp_path: Path,
) -> None:
    client, _manager, _service, _request = _client(tmp_path)
    token = "b" * 32
    first = client.post(
        "/api/question-bank/tagging-jobs",
        json={
            "question_ids": [11],
            "curriculum_volume_id": VOLUME_ID,
            "client_request_token": token,
        },
    )
    conflict = client.post(
        "/api/question-bank/tagging-jobs",
        json={
            "question_ids": [12],
            "curriculum_volume_id": VOLUME_ID,
            "client_request_token": token,
        },
    )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "question_tagging_request_conflict"


def test_tagging_route_validates_question_ids_against_source_import_job(
    tmp_path: Path,
) -> None:
    client, manager, _service, _request = _client(tmp_path)
    source = manager.store.create_job("question_import", {"request_id": "a" * 32})
    assert manager.store.mark_running(source.id)
    manager.store.finish(
        source.id,
        "succeeded",
        result={
            "outcome": "complete",
            "successful_question_ids": [11],
            "failed_question_ids": [],
            "retryable": False,
        },
    )

    accepted = client.post(
        "/api/question-bank/tagging-jobs",
        json={
            "question_ids": [11],
            "source_job_id": source.id,
            "curriculum_volume_id": VOLUME_ID,
        },
    )
    rejected = client.post(
        "/api/question-bank/tagging-jobs",
        json={
            "question_ids": [12],
            "source_job_id": source.id,
            "curriculum_volume_id": VOLUME_ID,
        },
    )

    assert accepted.status_code == 202
    assert accepted.json()["payload"]["source_job_id"] == source.id
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "question_tagging_request_invalid"


def test_tagging_retry_accepts_only_source_failed_ids(tmp_path: Path) -> None:
    client, manager, _service, _request = _client(tmp_path)
    initial = client.post(
        "/api/question-bank/tagging-jobs",
        json={"question_ids": [11, 12], "curriculum_volume_id": VOLUME_ID},
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
        "curriculum_volume_id": VOLUME_ID,
    }


def test_tagging_retry_marks_evidence_only_ids_without_losing_tag_success(
    tmp_path: Path,
) -> None:
    client, manager, _service, _request = _client(tmp_path)
    source = manager.store.create_job(
        "tagging_sync",
        {"question_ids": [11], "curriculum_volume_id": VOLUME_ID},
    )
    assert manager.store.mark_running(source.id)
    manager.store.finish(
        source.id,
        "succeeded",
        result={
            "outcome": "partial",
            "successful_question_ids": [11],
            "failed_question_ids": [11],
            "evidence_failed_question_ids": [11],
            "retryable": True,
        },
    )

    response = client.post(
        f"/api/question-bank/tagging-jobs/{source.id}/retry",
        json={"question_ids": [11]},
    )

    assert response.status_code == 202
    assert response.json()["payload"] == {
        "question_ids": [11],
        "retry_evidence_question_ids": [11],
        "retry_of_job_id": source.id,
        "curriculum_volume_id": VOLUME_ID,
    }


def test_tagging_retry_marks_relation_only_ids_for_local_replay(
    tmp_path: Path,
) -> None:
    client, manager, _service, _request = _client(tmp_path)
    source = manager.store.create_job(
        "tagging_sync",
        {"question_ids": [11], "curriculum_volume_id": VOLUME_ID},
    )
    assert manager.store.mark_running(source.id)
    manager.store.finish(
        source.id,
        "succeeded",
        result={
            "outcome": "partial",
            "successful_question_ids": [11],
            "failed_question_ids": [],
            "relation_governance_failed_question_ids": [11],
            "retryable": True,
        },
    )

    response = client.post(
        f"/api/question-bank/tagging-jobs/{source.id}/retry",
        json={"question_ids": [11]},
    )

    assert response.status_code == 202
    assert response.json()["payload"] == {
        "question_ids": [11],
        "retry_relation_question_ids": [11],
        "retry_of_job_id": source.id,
        "curriculum_volume_id": VOLUME_ID,
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


def test_question_import_retry_preserves_curriculum_volume_defaults(
    tmp_path: Path,
) -> None:
    client, manager, _service, request = _client(tmp_path)
    paper_defaults = {
        "grade": "八年级",
        "semester": "上学期",
        "textbook_version": "北师大版（2024）",
    }
    source = manager.store.create_job(
        "question_import",
        {"request_id": request.request_id, "paper_defaults": paper_defaults},
    )
    assert manager.store.mark_running(source.id)
    manager.store.finish(source.id, "failed", error="private failure")

    response = client.post(
        f"/api/question-bank/question-import-jobs/{source.id}/retry"
    )

    assert response.status_code == 202
    stored = manager.get(response.json()["id"])
    assert stored is not None
    assert stored.payload == {
        "request_id": request.request_id,
        "retry_of_job_id": source.id,
        "paper_defaults": paper_defaults,
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


def test_question_bank_retry_routes_reject_missing_and_nonretryable_jobs(
    tmp_path: Path,
) -> None:
    client, manager, _service, request = _client(tmp_path)
    completed_import = manager.store.create_job(
        "question_import", {"request_id": request.request_id}
    )
    assert manager.store.mark_running(completed_import.id)
    manager.store.finish(
        completed_import.id,
        "succeeded",
        result={"outcome": "complete", "retryable": False},
    )
    completed_tagging = manager.store.create_job(
        "tagging_sync", {"question_ids": [11]}
    )
    assert manager.store.mark_running(completed_tagging.id)
    manager.store.finish(
        completed_tagging.id,
        "succeeded",
        result={"outcome": "complete", "retryable": False},
    )

    cases = [
        (
            f"/api/question-bank/question-import-jobs/{completed_import.id}/retry",
            None,
            409,
            "question_import_retry_not_available",
        ),
        (
            f"/api/question-bank/tagging-jobs/{completed_tagging.id}/retry",
            {},
            409,
            "tagging_sync_retry_not_available",
        ),
        (
            "/api/question-bank/question-import-jobs/999999/retry",
            None,
            404,
            "question_import_job_not_found",
        ),
        (
            "/api/question-bank/tagging-jobs/999999/retry",
            {},
            404,
            "tagging_sync_job_not_found",
        ),
    ]
    for url, body, status, code in cases:
        response = client.post(url) if body is None else client.post(url, json=body)
        assert response.status_code == status
        assert response.json()["error"]["code"] == code


def test_duplicate_tagging_retry_requests_keep_same_safe_logical_payload(
    tmp_path: Path,
) -> None:
    client, manager, _service, _request = _client(tmp_path)
    source = manager.store.create_job(
        "tagging_sync",
        {"question_ids": [11, 12], "curriculum_volume_id": VOLUME_ID},
    )
    assert manager.store.mark_running(source.id)
    manager.store.finish(
        source.id,
        "succeeded",
        result={
            "outcome": "partial",
            "failed_question_ids": [12],
            "retryable": True,
        },
    )

    first = client.post(
        f"/api/question-bank/tagging-jobs/{source.id}/retry",
        json={"question_ids": [12]},
    )
    second = client.post(
        f"/api/question-bank/tagging-jobs/{source.id}/retry",
        json={"question_ids": [12]},
    )

    assert first.status_code == second.status_code == 202
    assert first.json()["id"] != second.json()["id"]
    assert first.json()["payload"] == second.json()["payload"] == {
        "question_ids": [12],
        "retry_of_job_id": source.id,
        "curriculum_volume_id": VOLUME_ID,
    }


def test_tagging_retry_uses_original_ids_after_failed_or_cancelled_job(
    tmp_path: Path,
) -> None:
    client, manager, _service, _request = _client(tmp_path)
    failed = manager.store.create_job(
        "tagging_sync",
        {"question_ids": [11, 12], "curriculum_volume_id": VOLUME_ID},
    )
    assert manager.store.mark_running(failed.id)
    manager.store.finish(failed.id, "failed", error="private")
    cancelled = manager.store.create_job(
        "tagging_sync",
        {"question_ids": [12], "curriculum_volume_id": VOLUME_ID},
    )
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

    for job_type in ("question_import", "tagging_sync", "question_bank_sync"):
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

    assert {
        "question_import",
        "tagging_sync",
        "question_bank_sync",
    }.issubset(manager._handlers)
    manager.shutdown()
