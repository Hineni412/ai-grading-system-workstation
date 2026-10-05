from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from backend.llm.llm_client import LLMResponseFormatError
from question_bank.knowledge_graph_release.loader import DEFAULT_TAXONOMY_PATH
from question_bank.services.ai_tagging_service import AITaggingService
from question_bank.services.taxonomy_review_suggestions import (
    TaxonomySuggestionService,
)
from question_bank.taxonomy.curriculum_catalog import (
    eligible_curriculum_knowledge_nodes,
)
from question_bank.taxonomy.governance import TaxonomyGovernance


CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "question_bank"
    / "taxonomy"
    / "catalogs"
    / "tag_vocabulary_v2.json"
)


def test_progress_callback_failure_keeps_original_snapshot_and_reports_type(caplog) -> None:
    from question_bank.services.taxonomy_review_suggestions import _safe_progress_callback

    snapshot = {"summary": {"completed": 1}}

    def broken_callback(payload):
        payload["summary"]["completed"] = 999
        raise RuntimeError("synthetic callback detail")

    _safe_progress_callback(broken_callback, snapshot)
    assert snapshot == {"summary": {"completed": 1}}
    assert "RuntimeError" in caplog.text
    assert "synthetic callback detail" not in caplog.text


def _governance(tmp_path: Path) -> TaxonomyGovernance:
    # 显式传入本测试私有库路径：缺省会指向会话级共享题库库，
    # 全量跑时被其他测试的应用启动装上 revision 4 的签入标准，与本文件词表 revision 冲突。
    return TaxonomyGovernance(
        catalog_path=CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "taxonomy-governance.db",
    )


def _persist(
    governance: TaxonomyGovernance,
    *,
    dimension: str,
    name: str,
    token: str,
    question_id: int,
) -> dict:
    result = governance.constrain(
        {
            "proposed_tags": [
                {
                    "dimension": dimension,
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
    proposal = result["proposals"][0]
    generation_id = f"test:{token}"
    governance.allocate_observation_sequences(
        generation_id=generation_id,
        question_ids=[str(question_id)],
    )
    governance.record_successful_observation(
        question_id=str(question_id),
        generation_id=generation_id,
        proposal_ids=[proposal["id"]],
        taxonomy_revision=int(result["taxonomy_revision"]),
    )
    return proposal


def _question_loader(question_ids):
    return [
        {
            "id": int(question_id),
            "question_number": f"Q{question_id}",
            "question_type": "解答题",
            "question_text": "用于核对归并建议的当前题目。",
            "answer_text": "当前题目的答案摘要。",
            "grade": "七年级",
            "semester": "下学期",
            "textbook_version": "北师大版（2024）",
        }
        for question_id in question_ids
    ]


class RecordingGateway:
    def __init__(self, responses=None, *, fail_names=()):
        self.responses = dict(responses or {})
        self.fail_names = set(fail_names)
        self.calls: list[list[dict]] = []

    def suggest_taxonomy_reviews(self, batch):
        copied = [dict(item) for item in batch]
        self.calls.append(copied)
        if any(item["proposed_name"] in self.fail_names for item in copied):
            raise RuntimeError("temporary gateway failure")
        return [
            {
                "proposal_id": item["proposal_id"],
                **self.responses.get(
                    item["proposal_id"],
                    {
                        "decision": "uncertain",
                        "target_term_ids": [],
                        "reason": "证据不足",
                        "confidence": 0.4,
                    },
                ),
            }
            for item in copied
        ]


def test_failed_batches_resume_after_restart_without_repeating_successes(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    first = _persist(
        governance,
        dimension="knowledge",
        name="待判断知识甲",
        token="3" * 32,
        question_id=51,
    )
    second = _persist(
        governance,
        dimension="knowledge",
        name="待判断知识乙",
        token="4" * 32,
        question_id=52,
    )
    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
        question_loader=lambda ids: [
            {
                "id": question_id,
                "question_number": f"Q{question_id}",
                "question_text": "根据题意完成证明。" * 80,
                "answer_text": "由已知条件可得结论。" * 80,
                "question_type": "解答题",
                "source_path": r"C:\private\paper.docx",
            }
            for question_id in ids
        ],
    )
    run = service.create_run(
        proposal_ids=[first["id"], second["id"]],
        expected_revision=governance.list_proposals(status="pending")["revision"],
        request_token="5" * 32,
    )
    failing = RecordingGateway(fail_names={"待判断知识乙"})

    partial = service.process_run(run["run_id"], failing, batch_size=1)

    assert partial["status"] == "partial"
    assert [item["status"] for item in partial["items"]] == [
        "suggested",
        "failed",
    ]
    successful_calls = [
        item["proposal_id"] for batch in failing.calls for item in batch
    ]
    assert successful_calls.count(first["id"]) == 1
    first_payload = failing.calls[0][0]
    assert first_payload["question_summaries"] == [
        {
            "id": 51,
            "question_number": "Q51",
            "question_type": "解答题",
            "question_text": ("根据题意完成证明。" * 80)[:600],
            "answer_text": ("由已知条件可得结论。" * 80)[:400],
        }
    ]
    assert "source_path" not in json.dumps(first_payload, ensure_ascii=False)

    restarted = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
        question_loader=_question_loader,
    )
    retry_gateway = RecordingGateway()
    retried = restarted.retry_failed(
        run["run_id"],
        retry_gateway,
        batch_size=1,
    )

    assert retried["status"] == "completed"
    assert [item["proposal_id"] for batch in retry_gateway.calls for item in batch] == [
        second["id"]
    ]


def test_thirty_nine_invalid_model_responses_finish_in_five_requests_without_repair(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    proposals = [
        _persist(
            governance,
            dimension="knowledge",
            name=f"格式错误回归候选{i}",
            token=f"{i + 1:032x}",
            question_id=200 + i,
        )
        for i in range(39)
    ]
    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
        question_loader=_question_loader,
    )
    run = service.create_run(
        proposal_ids=[proposal["id"] for proposal in proposals],
        expected_revision=governance.list_proposals(status="pending")["revision"],
        request_token="f" * 32,
    )

    class NonJsonSingleRequestClient:
        settings = SimpleNamespace(config_model="fake-tagging-model")

        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def json_from_text_once(self, prompt, **kwargs):
            self.calls.append((prompt, kwargs))
            raise LLMResponseFormatError("Model response is not valid JSON")

        def json_from_text(self, *_args, **_kwargs):
            raise AssertionError("invalid suggestion output must not use AI repair")

    llm_client = NonJsonSingleRequestClient()
    gateway = AITaggingService(
        env={"QUESTION_BANK_TAGGING_MODEL": "fake-tagging-model"},
        llm_client=llm_client,
    )

    failed = service.process_run(run["run_id"], gateway, batch_size=8)

    assert len(llm_client.calls) == 5
    first_prompt = llm_client.calls[0][0]
    first_payload = json.loads(first_prompt.split("\n\n")[-1])
    assert len(first_payload["shared_candidate_catalogs"]) == 1
    assert len(first_payload["shared_candidate_catalogs"][0]["terms"]) == len(
        eligible_curriculum_knowledge_nodes("bnu24-math-g7-lower")
    )
    assert all(
        "candidates" not in proposal
        and proposal["candidate_catalog_key"] == "catalog-1"
        for proposal in first_payload["proposals"]
    )
    assert failed["status"] == "failed"
    assert failed["progress"] == {
        "total": 39,
        "processed": 39,
        "completed": 0,
        "failed": 39,
        "pending": 0,
        "cancelled": 0,
    }
    assert {item["attempts"] for item in failed["items"]} == {1}
    assert {item["error"]["category"] for item in failed["items"]} == {"model_response"}


class ParallelRecordingGateway(RecordingGateway):
    def __init__(self, responses=None, *, fail_names=(), delay=0.05):
        super().__init__(responses, fail_names=fail_names)
        self.delay = delay
        self._lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0

    def suggest_taxonomy_reviews(self, batch):
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            time.sleep(self.delay)
            return super().suggest_taxonomy_reviews(batch)
        finally:
            with self._lock:
                self.in_flight -= 1


class BarrierRecordingGateway(RecordingGateway):
    """First calls rendezvous at a barrier, proving overlap without sleep."""

    def __init__(self, responses=None, *, fail_names=(), parties=2, timeout=30):
        super().__init__(responses, fail_names=fail_names)
        self._barrier = threading.Barrier(parties, timeout=timeout)
        self._waiters = parties
        self._lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0

    def suggest_taxonomy_reviews(self, batch):
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            wait = self._waiters > 0
            if wait:
                self._waiters -= 1
        try:
            if wait:
                # Serial processing would block here until the barrier
                # times out and this test fails deterministically.
                self._barrier.wait()
            return RecordingGateway.suggest_taxonomy_reviews(self, batch)
        finally:
            with self._lock:
                self.in_flight -= 1
