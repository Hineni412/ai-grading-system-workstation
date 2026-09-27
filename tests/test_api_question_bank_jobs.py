from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import (
    get_job_manager,
    get_question_bank_write_service,
)
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from question_bank.services.question_write_service import QuestionBankWriteService


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
    assert (
        first.json()["payload"]
        == second.json()["payload"]
        == {
            "question_ids": [12],
            "retry_of_job_id": source.id,
            "curriculum_volume_id": VOLUME_ID,
        }
    )
