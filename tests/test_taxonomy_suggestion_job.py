from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.jobs.default_handlers import register_default_job_handlers
from backend.jobs.manager import (
    JobCancellationRequested,
    JobContext,
    JobManager,
)
from backend.jobs.store import JobStore
from backend.jobs.taxonomy_suggestions import run_taxonomy_suggestion_job
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


class SuggestionGateway:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, object]]] = []

    def suggest_taxonomy_reviews(
        self,
        batch,
    ) -> list[dict[str, object]]:
        copied = [dict(item) for item in batch]
        self.calls.append(copied)
        return [
            {
                "proposal_id": item["proposal_id"],
                "decision": "uncertain",
                "target_term_ids": [],
                "reason": "需要教师判断",
                "confidence": 0.4,
            }
            for item in copied
        ]


class FailingSuggestionGateway:
    def suggest_taxonomy_reviews(self, _batch):
        raise RuntimeError("temporary model failure")


class CancellingSuggestionGateway(SuggestionGateway):
    def __init__(self, cancel) -> None:
        super().__init__()
        self.cancel = cancel

    def suggest_taxonomy_reviews(
        self,
        batch,
    ) -> list[dict[str, object]]:
        result = super().suggest_taxonomy_reviews(batch)
        self.cancel()
        return result


def _governance(tmp_path: Path) -> TaxonomyGovernance:
    return TaxonomyGovernance(
        catalog_path=CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
    )


def _proposal(
    governance: TaxonomyGovernance,
    *,
    name: str,
    token: str,
    question_id: int,
) -> dict[str, object]:
    result = governance.constrain(
        {
            "proposed_tags": [
                {
                    "dimension": "knowledge",
                    "name": name,
                    "reason": "正式词表中没有直接匹配",
                }
            ]
        },
        context={
            "persist_proposals": True,
            "request_token": token,
            "question_ref": str(question_id),
        },
    )
    return dict(result["proposals"][0])


def _context(
    tmp_path: Path,
    payload: dict[str, object],
) -> tuple[JobContext, JobStore]:
    store = JobStore(tmp_path / "jobs.db")
    job = store.create_job("taxonomy_suggestion", payload)
    assert store.mark_running(job.id)
    return (
        JobContext(
            job_id=job.id,
            job_type=job.job_type,
            payload=job.payload,
            store=store,
        ),
        store,
    )


def test_process_job_reports_counts_and_returns_only_a_safe_summary(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    proposal = _proposal(
        governance,
        name="模型临时知识表达",
        token="1" * 32,
        question_id=41,
    )
    state_path = tmp_path / "taxonomy-state.suggestions.json"
    service = TaxonomySuggestionService(
        state_path=state_path,
        governance=governance,
    )
    run = service.create_run(
        proposal_ids=[str(proposal["id"])],
        expected_revision=governance.list_proposals(status="pending")[
            "revision"
        ],
        request_token="2" * 32,
    )
    context, store = _context(
        tmp_path,
        {
            "run_id": run["run_id"],
            "operation": "process",
        },
    )
    gateway = SuggestionGateway()

    result = run_taxonomy_suggestion_job(
        context=context,
        suggestion_state_path=state_path,
        taxonomy_governance=governance,
        question_loader=lambda _ids: [],
        ai_service_factory=lambda: gateway,
        batch_size=1,
    )

    assert result == {
        "run_id": run["run_id"],
        "operation": "process",
        "outcome": "completed",
        "status": "completed",
        "taxonomy_revision": run["taxonomy_revision"],
        "progress": {
            "total": 1,
            "completed": 1,
            "failed": 0,
            "pending": 0,
            "cancelled": 0,
        },
        "stale": False,
        "total_count": 1,
        "completed_count": 1,
        "failed_count": 0,
        "pending_count": 0,
        "cancelled_count": 0,
        "retryable": False,
    }
    assert len(gateway.calls) == 1
    stored_job = store.get_job(context.job_id)
    assert stored_job is not None
    assert stored_job.progress == 1.0
    assert stored_job.stage == "生成归并建议"
    serialized = json.dumps(
        {"result": result, "detail": stored_job.detail},
        ensure_ascii=False,
    )
    assert "模型临时知识表达" not in serialized
    assert str(tmp_path) not in serialized


def test_retry_job_only_reprocesses_the_service_run_failures(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    proposal = _proposal(
        governance,
        name="等待重试知识表达",
        token="3" * 32,
        question_id=42,
    )
    state_path = tmp_path / "taxonomy-state.suggestions.json"
    service = TaxonomySuggestionService(
        state_path=state_path,
        governance=governance,
    )
    run = service.create_run(
        proposal_ids=[str(proposal["id"])],
        expected_revision=governance.list_proposals(status="pending")[
            "revision"
        ],
        request_token="4" * 32,
    )
    failed = service.process_run(
        run["run_id"],
        FailingSuggestionGateway(),
        batch_size=1,
    )
    assert failed["status"] == "failed"
    assert failed["items"][0]["attempts"] == 1
    context, _store = _context(
        tmp_path,
        {
            "run_id": run["run_id"],
            "operation": "retry",
        },
    )

    result = run_taxonomy_suggestion_job(
        context=context,
        suggestion_state_path=state_path,
        taxonomy_governance=governance,
        question_loader=lambda _ids: [],
        ai_service_factory=SuggestionGateway,
        batch_size=1,
    )

    assert result["operation"] == "retry"
    assert result["outcome"] == "completed"
    retried = service.get_run(run["run_id"])
    assert retried["items"][0]["status"] == "suggested"
    assert retried["items"][0]["attempts"] == 2


def test_cancelled_job_stops_before_the_next_model_batch(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    proposals = [
        _proposal(
            governance,
            name=name,
            token=token * 32,
            question_id=question_id,
        )
        for name, token, question_id in (
            ("取消前知识表达", "5", 51),
            ("不应发送知识表达", "6", 52),
        )
    ]
    state_path = tmp_path / "taxonomy-state.suggestions.json"
    service = TaxonomySuggestionService(
        state_path=state_path,
        governance=governance,
    )
    run = service.create_run(
        proposal_ids=[str(item["id"]) for item in proposals],
        expected_revision=governance.list_proposals(status="pending")[
            "revision"
        ],
        request_token="7" * 32,
    )
    context, store = _context(
        tmp_path,
        {
            "run_id": run["run_id"],
            "operation": "process",
        },
    )
    gateway = CancellingSuggestionGateway(
        lambda: store.request_cancel(context.job_id)
    )

    with pytest.raises(JobCancellationRequested):
        run_taxonomy_suggestion_job(
            context=context,
            suggestion_state_path=state_path,
            taxonomy_governance=governance,
            question_loader=lambda _ids: [],
            ai_service_factory=lambda: gateway,
            batch_size=1,
        )

    assert len(gateway.calls) == 1
    cancelled = service.get_run(run["run_id"])
    assert cancelled["status"] == "cancelled"
    assert [item["status"] for item in cancelled["items"]] == [
        "suggested",
        "cancelled",
    ]
    stored_job = store.get_job(context.job_id)
    assert stored_job is not None
    assert stored_job.progress < 1.0
    assert "取消前知识表达" not in stored_job.detail


def test_default_handlers_register_the_injected_taxonomy_suggestion_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    governance = _governance(tmp_path)
    monkeypatch.setattr(
        "backend.jobs.default_handlers.get_taxonomy_governance",
        lambda: governance,
    )
    gateway = object()
    captured: dict[str, object] = {}

    def runner(**kwargs):
        captured.update(kwargs)
        kwargs["context"].report(
            1.0,
            "taxonomy_suggestion",
            "completed=1 failed=0 pending=0 cancelled=0",
        )
        return {
            "run_id": "a" * 32,
            "operation": "process",
            "outcome": "completed",
            "status": "completed",
            "total_count": 1,
            "completed_count": 1,
            "failed_count": 0,
            "pending_count": 0,
            "cancelled_count": 0,
            "retryable": False,
        }

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    try:
        register_default_job_handlers(
            manager,
            db_path=tmp_path / "grading.db",
            reports_dir=tmp_path / "reports",
            data_root=tmp_path / "data",
            question_bank_db_path=(
                tmp_path / "data" / "databases" / "question_bank.db"
            ),
            taxonomy_suggestion_runner=runner,
            tagging_ai_service_factory=lambda: gateway,
        )

        job, created = manager.submit_idempotent_taxonomy_suggestion(
            {
                "run_id": "a" * 32,
                "operation": "process",
                "client_request_token": "b" * 32,
            }
        )
        assert created is True
        manager.wait(job.id, timeout=5)

        stored = manager.get(job.id)
        assert stored is not None
        assert stored.status == "succeeded"
        assert stored.result["outcome"] == "completed"
        assert captured["taxonomy_governance"] is governance
        assert captured["suggestion_state_path"] == (
            tmp_path / "taxonomy-state.suggestions.json"
        )
        assert captured["ai_service_factory"]() is gateway
        assert captured["question_loader"]([]) == []
    finally:
        manager.shutdown()
