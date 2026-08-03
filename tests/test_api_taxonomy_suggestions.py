from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import (
    get_job_manager,
    get_taxonomy_suggestion_service,
)
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from question_bank.services.taxonomy_review_suggestions import (
    TaxonomySuggestionService,
)
from question_bank.taxonomy.governance import TaxonomyGovernance


CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "question_bank"
    / "taxonomy"
    / "catalogs"
    / "tag_vocabulary_v2.json"
)


class _SuggestionGateway:
    def suggest_taxonomy_reviews(self, batch):
        return [
            {
                "proposal_id": item["proposal_id"],
                "decision": "uncertain",
                "target_term_ids": [],
                "reason": "需要教师结合题干确认。",
                "confidence": 0.45,
            }
            for item in batch
        ]


def _client(
    tmp_path: Path,
) -> tuple[
    TestClient,
    JobManager,
    TaxonomySuggestionService,
    TaxonomyGovernance,
    str,
]:
    governance = TaxonomyGovernance(
        catalog_path=CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
    )
    created = governance.constrain(
        {
            "proposed_tags": [
                {
                    "dimension": "knowledge",
                    "name": "需要人工判断的新知识词",
                    "reason": "没有直接命中现有词",
                }
            ]
        },
        context={
            "persist_proposals": True,
            "question_ref": "41",
            "request_token": "1" * 32,
        },
    )
    proposal_id = str(created["proposals"][0]["id"])
    service = TaxonomySuggestionService(
        state_path=tmp_path / "taxonomy-suggestions.json",
        governance=governance,
        question_loader=lambda question_ids: [
            {
                "id": int(question_id),
                "question_number": f"Q{question_id}",
                "question_type": "解答题",
                "question_text": "用于核对归并建议的当前题目。",
                "answer_text": "当前题目的答案摘要。",
            }
            for question_id in question_ids
        ],
    )
    manager = JobManager(
        JobStore(tmp_path / "jobs.db"),
        max_workers=1,
        cleanup_interrupted=False,
    )
    gateway = _SuggestionGateway()

    def handler(context):
        def report(snapshot):
            progress = snapshot["progress"]
            total = max(1, int(progress["total"]))
            context.report(
                (
                    int(progress["processed"])
                )
                / total,
                "taxonomy_suggestion",
                "正在生成归并建议",
            )

        if context.payload["operation"] == "retry":
            run = service.retry_failed(
                context.payload["run_id"],
                gateway,
                progress_callback=report,
                cancel_requested=context.is_cancel_requested,
            )
        else:
            run = service.process_run(
                context.payload["run_id"],
                gateway,
                progress_callback=report,
                cancel_requested=context.is_cancel_requested,
            )
        return {
            "run_id": run["run_id"],
            "status": run["status"],
            "taxonomy_revision": run["taxonomy_revision"],
            "progress": run["progress"],
            "stale": run["stale"],
            "retryable": run.get("retryable", False),
        }

    manager.register("taxonomy_suggestion", handler)
    app = create_app()
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[
        get_taxonomy_suggestion_service
    ] = lambda: service
    return TestClient(app), manager, service, governance, proposal_id


def test_suggestion_start_is_idempotent_persistent_and_never_auto_applies(
    tmp_path: Path,
) -> None:
    client, manager, _service, governance, proposal_id = _client(tmp_path)
    request = {
        "proposal_ids": [proposal_id],
        "expected_revision": governance.list_proposals(status="pending")[
            "revision"
        ],
        "request_token": "2" * 32,
    }

    first = client.post(
        "/api/question-bank/taxonomy/suggestions",
        json=request,
    )
    repeated = client.post(
        "/api/question-bank/taxonomy/suggestions",
        json=request,
    )

    assert first.status_code == 202
    assert repeated.status_code == 202
    assert repeated.json()["job"]["id"] == first.json()["job"]["id"]
    assert repeated.json()["run"]["run_id"] == first.json()["run"]["run_id"]

    job_id = int(first.json()["job"]["id"])
    run_id = str(first.json()["run"]["run_id"])
    manager.wait(job_id, timeout=5)
    run = client.get(
        f"/api/question-bank/taxonomy/suggestions/{run_id}"
    )
    assert run.status_code == 200
    assert run.json()["status"] == "completed"
    assert run.json()["items"][0]["suggestion"]["source"] == "ai"
    assert governance.get_proposal(proposal_id)["status"] == "pending"

    job = client.get(f"/api/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["payload"] == {
        "run_id": run_id,
        "operation": "process",
    }
    assert "client_request_token" not in job.text
    assert job.json()["result"]["run_id"] == run_id

    direct = client.post(
        "/api/jobs/taxonomy_suggestion",
        json={"payload": {"run_id": run_id}},
    )
    assert direct.status_code == 422
    manager.shutdown()


def test_cancelled_suggestion_run_can_resume_without_discarding_state(
    tmp_path: Path,
) -> None:
    client, manager, service, governance, proposal_id = _client(tmp_path)
    run = service.create_run(
        proposal_ids=[proposal_id],
        expected_revision=governance.list_proposals(status="pending")[
            "revision"
        ],
        request_token="3" * 32,
    )

    cancelled = client.post(
        f"/api/question-bank/taxonomy/suggestions/{run['run_id']}/cancel"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["progress"]["cancelled"] == 1

    retried = client.post(
        f"/api/question-bank/taxonomy/suggestions/{run['run_id']}/retry",
        json={"request_token": "4" * 32},
    )
    assert retried.status_code == 202
    manager.wait(retried.json()["job"]["id"], timeout=5)
    resumed = client.get(
        f"/api/question-bank/taxonomy/suggestions/{run['run_id']}"
    ).json()
    assert resumed["status"] == "completed"
    assert resumed["progress"]["completed"] == 1
    assert resumed["items"][0]["attempts"] == 1
    manager.shutdown()


def test_suggestion_start_reports_taxonomy_revision_conflict(
    tmp_path: Path,
) -> None:
    client, manager, _service, governance, proposal_id = _client(tmp_path)

    response = client.post(
        "/api/question-bank/taxonomy/suggestions",
        json={
            "proposal_ids": [proposal_id],
            "expected_revision": (
                governance.list_proposals(status="pending")["revision"] - 1
            ),
            "request_token": "5" * 32,
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "taxonomy_revision_conflict"
    manager.shutdown()


def test_get_run_converts_an_interrupted_outer_job_into_retryable_items(
    tmp_path: Path,
) -> None:
    client, manager, service, governance, proposal_id = _client(tmp_path)
    run = service.create_run(
        proposal_ids=[proposal_id],
        expected_revision=governance.list_proposals(status="pending")[
            "revision"
        ],
        request_token="6" * 32,
    )
    job = manager.store.create_job(
        "taxonomy_suggestion",
        {
            "run_id": run["run_id"],
            "operation": "process",
            "client_request_token": "7" * 32,
        },
    )
    assert manager.store.mark_running(job.id)
    manager.store.finish(job.id, "failed", "interrupted")

    recovered = client.get(
        f"/api/question-bank/taxonomy/suggestions/{run['run_id']}"
    )

    assert recovered.status_code == 200
    assert recovered.json()["status"] == "failed"
    assert recovered.json()["retryable"] is True
    assert recovered.json()["items"][0]["error"] == {
        "category": "interrupted",
        "message": "应用在处理期间退出，可继续未完成项目。",
    }
    manager.shutdown()
