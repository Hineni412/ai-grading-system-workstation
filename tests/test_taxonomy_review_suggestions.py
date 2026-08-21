from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from llm_client import LLMResponseFormatError
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


def _governance(tmp_path: Path) -> TaxonomyGovernance:
    # 显式传入本测试私有库路径：缺省会指向会话级共享题库库，
    # 全量跑时被其他测试的应用启动装上 revision 4 的签入标准，与本文件词表 revision 冲突。
    return TaxonomyGovernance(
        catalog_path=CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "taxonomy-governance.db",
    )


def _release_governance(tmp_path: Path) -> TaxonomyGovernance:
    # 显式传入本测试私有库路径：缺省会指向会话级共享题库库，
    # 全量跑时被其他测试的应用启动装上 revision 4 的签入标准，与本文件词表 revision 冲突。
    return TaxonomyGovernance(
        catalog_path=DEFAULT_TAXONOMY_PATH,
        state_path=tmp_path / "taxonomy-release-state.json",
        knowledge_graph_db_path=tmp_path / "taxonomy-release-governance.db",
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


def test_lower_seventh_grade_sends_the_complete_upper_and_lower_knowledge_tree(
    tmp_path: Path,
) -> None:
    governance = _release_governance(tmp_path)
    proposal = _persist(
        governance,
        dimension="knowledge",
        name="待匹配的七下知识",
        token="20" * 16,
        question_id=201,
    )
    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
        question_loader=_question_loader,
    )
    run = service.create_run(
        proposal_ids=[proposal["id"]],
        expected_revision=governance.list_proposals(status="pending")["revision"],
        request_token="21" * 16,
    )
    gateway = RecordingGateway()

    completed = service.process_run(run["run_id"], gateway, batch_size=1)

    assert completed["status"] == "completed"
    payload = gateway.calls[0][0]
    expected = eligible_curriculum_knowledge_nodes("bnu24-math-g7-lower")
    assert [item["id"] for item in payload["candidates"]] == [
        item["id"] for item in expected
    ]
    assert {item["volume_id"] for item in payload["candidates"]} == {
        "bnu24-math-g7-upper",
        "bnu24-math-g7-lower",
    }
    assert payload["curriculum_volume_ids"] == ["bnu24-math-g7-lower"]


def test_exact_composite_knowledge_is_split_locally_without_model_call(
    tmp_path: Path,
) -> None:
    governance = _release_governance(tmp_path)
    proposal = _persist(
        governance,
        dimension="knowledge",
        name="临时复合知识词",
        token="22" * 16,
        question_id=202,
    )
    scoped = eligible_curriculum_knowledge_nodes("bnu24-math-g7-lower")
    leaves = [item for item in scoped if int(item["level"]) == 3]
    first, second = leaves[0], leaves[1]
    _rewrite_as_legacy_composite(
        governance,
        proposal_id=proposal["id"],
        name=f'{first["name"]}、{second["name"]}',
    )
    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
        question_loader=_question_loader,
    )
    run = service.create_run(
        proposal_ids=[proposal["id"]],
        expected_revision=governance.list_proposals(status="pending")["revision"],
        request_token="23" * 16,
    )
    gateway = RecordingGateway()

    completed = service.process_run(run["run_id"], gateway, batch_size=1)

    assert gateway.calls == []
    assert completed["items"][0]["suggestion"]["target_term_ids"] == [
        first["id"],
        second["id"],
    ]
    assert "本地拆分" in completed["items"][0]["suggestion"]["reason"]


def test_local_composite_split_does_not_escape_the_paper_volume_scope(
    tmp_path: Path,
) -> None:
    governance = _release_governance(tmp_path)
    proposal = _persist(
        governance,
        dimension="knowledge",
        name="临时跨册复合知识词",
        token="24" * 16,
        question_id=203,
    )
    allowed_ids = {
        str(item["id"])
        for item in eligible_curriculum_knowledge_nodes(
            "bnu24-math-g7-lower"
        )
    }
    out_of_scope = [
        item
        for item in eligible_curriculum_knowledge_nodes(
            "bnu24-math-g8-upper"
        )
        if int(item["level"]) == 3
        and str(item["id"]) not in allowed_ids
    ]
    first, second = out_of_scope[0], out_of_scope[1]
    _rewrite_as_legacy_composite(
        governance,
        proposal_id=proposal["id"],
        name=f'{first["name"]}、{second["name"]}',
    )
    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
        question_loader=_question_loader,
    )
    run = service.create_run(
        proposal_ids=[proposal["id"]],
        expected_revision=governance.list_proposals(status="pending")["revision"],
        request_token="25" * 16,
    )
    gateway = RecordingGateway()

    completed = service.process_run(run["run_id"], gateway, batch_size=1)

    assert len(gateway.calls) == 1
    assert completed["items"][0]["suggestion"]["source"] == "ai"
    assert {
        item["volume_id"]
        for item in gateway.calls[0][0]["candidates"]
    } == {"bnu24-math-g7-upper", "bnu24-math-g7-lower"}


def _rewrite_as_legacy_composite(
    governance: TaxonomyGovernance,
    *,
    proposal_id: str,
    name: str,
) -> None:
    payload = json.loads(governance.state_path.read_text(encoding="utf-8"))
    proposal = next(
        item for item in payload["proposals"] if item["id"] == proposal_id
    )
    proposal["proposed_name"] = name
    proposal["normalized_name"] = re.sub(r"[\s\W_]+", "", name.casefold())
    governance.state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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


def test_ai_reason_is_stored_as_one_short_chinese_sentence(tmp_path: Path) -> None:
    governance = _governance(tmp_path)
    proposal = _persist(
        governance,
        dimension="knowledge",
        name="待精简的新知识点",
        token="0" * 32,
        question_id=40,
    )
    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
        question_loader=_question_loader,
    )
    run = service.create_run(
        proposal_ids=[proposal["id"]],
        expected_revision=governance.list_proposals(status="pending")["revision"],
        request_token="f" * 32,
    )
    gateway = RecordingGateway(responses={
        proposal["id"]: {
            "relation_kind": "uncertain",
            "target_term_ids": [],
            "reason": "The candidate is semantically close, but the available evidence is not sufficient for a safe merge.",
            "confidence": 0.55,
        }
    })

    completed = service.process_run(run["run_id"], gateway)

    reason = completed["items"][0]["suggestion"]["reason"]
    assert re.search(r"[\u3400-\u9fff]", reason)
    assert len(reason) <= 48
    assert reason.endswith("。")


def test_legacy_composite_curriculum_gets_a_local_multi_target_suggestion(
    tmp_path: Path,
) -> None:
    governance = _release_governance(tmp_path)
    proposal = _persist(
        governance,
        dimension="curriculum",
        name="临时跨章节旧词",
        token="1" * 32,
        question_id=41,
    )
    first_name = "七年级下册 第二章 相交线与平行线"
    second_name = "七年级下册 第四章 三角形"
    _rewrite_as_legacy_composite(
        governance,
        proposal_id=proposal["id"],
        name=f"{first_name}，{second_name}",
    )
    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
    )
    run = service.create_run(
        proposal_ids=[proposal["id"]],
        expected_revision=governance.list_proposals(status="pending")["revision"],
        request_token="2" * 32,
    )
    gateway = RecordingGateway()

    completed = service.process_run(run["run_id"], gateway, batch_size=1)

    first = governance.resolve_term("curriculum", first_name)
    second = governance.resolve_term("curriculum", second_name)
    assert first and second
    assert gateway.calls == []
    assert completed["status"] == "completed"
    assert completed["items"][0]["suggestion"] == {
        "relation_kind": "related",
        "target_term_ids": [first["id"], second["id"]],
        "reason": "名称可安全拆分并精确匹配到多个现有教材章节。",
        "confidence": 1.0,
        "source": "local_exact",
        "legacy_format": False,
        "evidence_question_ids": [41],
        "taxonomy_revision": 1,
        "graph_release_id": completed["graph_release_id"],
    }
    assert governance.get_proposal(proposal["id"])["status"] == "pending"


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
    assert "source_path" not in json.dumps(
        first_payload, ensure_ascii=False
    )

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
    assert [
        item["proposal_id"]
        for batch in retry_gateway.calls
        for item in batch
    ] == [second["id"]]


def test_running_state_is_resumable_and_cancel_preserves_finished_suggestions(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    first = _persist(
        governance,
        dimension="knowledge",
        name="中断恢复知识甲",
        token="6" * 32,
        question_id=61,
    )
    second = _persist(
        governance,
        dimension="knowledge",
        name="中断恢复知识乙",
        token="7" * 32,
        question_id=62,
    )
    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
        question_loader=_question_loader,
    )
    run = service.create_run(
        proposal_ids=[first["id"], second["id"]],
        expected_revision=governance.list_proposals(status="pending")["revision"],
        request_token="8" * 32,
    )
    state = json.loads(service.state_path.read_text(encoding="utf-8"))
    stored = state["runs"][run["run_id"]]
    stored["status"] = "running"
    stored["items"][0]["status"] = "running"
    service.state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    class CancellingGateway(RecordingGateway):
        def suggest_taxonomy_reviews(self, batch):
            result = super().suggest_taxonomy_reviews(batch)
            service.cancel_run(run["run_id"])
            return result

    resumed = service.process_run(
        run["run_id"],
        CancellingGateway(),
        batch_size=1,
    )

    assert resumed["status"] == "cancelled"
    assert resumed["items"][0]["status"] == "suggested"
    assert resumed["items"][1]["status"] == "cancelled"


def test_taxonomy_revision_change_marks_unfinished_run_stale_without_gateway_call(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    proposal = _persist(
        governance,
        dimension="knowledge",
        name="版本变化前候选",
        token="9" * 32,
        question_id=71,
    )
    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
        question_loader=_question_loader,
    )
    run = service.create_run(
        proposal_ids=[proposal["id"]],
        expected_revision=governance.list_proposals(status="pending")["revision"],
        request_token="a" * 32,
    )
    _persist(
        governance,
        dimension="method",
        name="触发词表版本变化",
        token="b" * 32,
        question_id=72,
    )
    gateway = RecordingGateway()

    stale = service.process_run(run["run_id"], gateway)

    assert stale["status"] == "stale"
    assert stale["stale"] is True
    assert gateway.calls == []


def test_progress_callback_and_external_cancel_stop_before_the_next_batch(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    first = _persist(
        governance,
        dimension="knowledge",
        name="进度回调知识甲",
        token="c" * 32,
        question_id=81,
    )
    second = _persist(
        governance,
        dimension="knowledge",
        name="进度回调知识乙",
        token="d" * 32,
        question_id=82,
    )
    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
        question_loader=_question_loader,
    )
    run = service.create_run(
        proposal_ids=[first["id"], second["id"]],
        expected_revision=governance.list_proposals(status="pending")[
            "revision"
        ],
        request_token="e" * 32,
    )
    gateway = RecordingGateway()
    progress: list[dict] = []

    result = service.process_run(
        run["run_id"],
        gateway,
        batch_size=1,
        progress_callback=lambda snapshot: progress.append(snapshot),
        cancel_requested=lambda: bool(gateway.calls),
    )

    assert len(gateway.calls) == 1
    assert result["status"] == "cancelled"
    assert [item["status"] for item in result["items"]] == [
        "suggested",
        "cancelled",
    ]
    assert progress[0]["progress"]["total"] == 2
    assert any(
        snapshot["progress"]["completed"] == 1
        for snapshot in progress
    )
    assert result["progress"]["processed"] == 2


def test_current_question_context_is_derived_without_rewriting_historical_refs(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    proposal = _persist(
        governance,
        dimension="knowledge",
        name="保留历史引用的候选",
        token="10" * 16,
        question_id=111,
    )
    _persist(
        governance,
        dimension="knowledge",
        name="保留历史引用的候选",
        token="11" * 16,
        question_id=112,
    )

    def load_current(question_ids):
        return _question_loader(
            [question_id for question_id in question_ids if question_id == 112]
        )

    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
        question_loader=load_current,
    )
    page = service.contextualize_proposal_page(
        governance.list_proposals(status="pending")
    )
    item = page["items"][0]

    assert item["question_refs"] == [112]
    assert item["active_question_refs"] == [112]
    assert item["unavailable_question_ref_count"] == 0
    assert item["actionable"] is True
    assert page["counts"]["actionable"] == 1
    assert "historical_unavailable" not in page["counts"]

    run = service.create_run(
        proposal_ids=[proposal["id"]],
        expected_revision=page["revision"],
        request_token="12" * 16,
    )
    gateway = RecordingGateway()
    completed = service.process_run(run["run_id"], gateway, batch_size=1)

    assert completed["status"] == "completed"
    assert [
        summary["id"]
        for summary in gateway.calls[0][0]["question_summaries"]
    ] == [112]
    assert governance.get_proposal(proposal["id"])["question_refs"] == [111, 112]


def test_proposal_summary_counts_only_candidates_with_current_questions(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    _persist(
        governance,
        dimension="knowledge",
        name="只读摘要候选",
        token="31" * 16,
        question_id=111,
    )
    _persist(
        governance,
        dimension="knowledge",
        name="只读摘要候选",
        token="32" * 16,
        question_id=112,
    )
    loaded_ids: list[int] = []

    def load_current(question_ids):
        loaded_ids.extend(question_ids)
        return _question_loader(
            [question_id for question_id in question_ids if question_id == 112]
        )

    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
        question_loader=load_current,
    )
    governance.list_proposals = lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("summary must not build the full proposal page")
    )

    summary = service.proposal_summary()

    assert loaded_ids == [111, 112]
    assert summary["items"] == []
    assert summary["counts"] == {
        "pending": 1,
        "actionable": 1,
        "historical_unavailable": 0,
    }


def test_unavailable_question_context_costs_no_request_and_can_retry_after_restore(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    proposal = _persist(
        governance,
        dimension="knowledge",
        name="等待原题恢复的候选",
        token="13" * 16,
        question_id=121,
    )
    active_question_ids: set[int] = {121}

    def load_current(question_ids):
        return _question_loader(
            [
                question_id
                for question_id in question_ids
                if question_id in active_question_ids
            ]
        )

    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
        question_loader=load_current,
    )
    run = service.create_run(
        proposal_ids=[proposal["id"]],
        expected_revision=governance.list_proposals(status="pending")["revision"],
        request_token="14" * 16,
    )
    active_question_ids.clear()
    first_gateway = RecordingGateway()

    blocked = service.process_run(run["run_id"], first_gateway, batch_size=1)

    assert first_gateway.calls == []
    assert blocked["status"] == "failed"
    assert blocked["items"][0]["error"]["category"] == "question_context"
    assert governance.get_proposal(proposal["id"])["question_refs"] == [121]

    active_question_ids.add(121)
    retry_gateway = RecordingGateway()
    retried = service.retry_failed(run["run_id"], retry_gateway, batch_size=1)

    assert retried["status"] == "completed"
    assert len(retry_gateway.calls) == 1


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
    assert {
        item["error"]["category"] for item in failed["items"]
    } == {"model_response"}


def test_interrupted_run_becomes_retryable_without_losing_finished_items(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    first = _persist(
        governance,
        dimension="knowledge",
        name="中断已完成知识",
        token="1a" * 16,
        question_id=91,
    )
    second = _persist(
        governance,
        dimension="knowledge",
        name="中断未完成知识",
        token="2b" * 16,
        question_id=92,
    )
    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
    )
    run = service.create_run(
        proposal_ids=[first["id"], second["id"]],
        expected_revision=governance.list_proposals(status="pending")[
            "revision"
        ],
        request_token="3c" * 16,
    )
    state = json.loads(service.state_path.read_text(encoding="utf-8"))
    stored = state["runs"][run["run_id"]]
    stored["status"] = "running"
    stored["items"][0]["status"] = "suggested"
    stored["items"][0]["suggestion"] = {
        "relation_kind": "uncertain",
        "target_term_ids": [],
        "reason": "证据不足",
        "confidence": 0.4,
        "source": "ai",
        "legacy_format": False,
        "evidence_question_ids": [91],
        "taxonomy_revision": run["taxonomy_revision"],
        "graph_release_id": run["graph_release_id"],
    }
    stored["items"][1]["status"] = "running"
    service.state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    recovered = service.recover_interrupted(run["run_id"])

    assert recovered["status"] == "partial"
    assert recovered["retryable"] is True
    assert recovered["items"][0]["status"] == "suggested"
    assert recovered["items"][1]["status"] == "failed"
    assert recovered["items"][1]["error"]["category"] == "interrupted"


def test_same_revision_and_selection_reuse_one_run_across_new_tokens(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    proposal = _persist(
        governance,
        dimension="knowledge",
        name="并发防重复候选",
        token="4d" * 16,
        question_id=101,
    )
    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
    )
    revision = governance.list_proposals(status="pending")["revision"]

    def create(token: str) -> dict:
        return service.create_run(
            proposal_ids=[proposal["id"]],
            expected_revision=revision,
            request_token=token,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        runs = list(executor.map(create, ["5e" * 16, "6f" * 16]))

    assert runs[0]["run_id"] == runs[1]["run_id"]
    state = json.loads(service.state_path.read_text(encoding="utf-8"))
    assert len(state["runs"]) == 1
    assert {
        receipt["run_id"] for receipt in state["requests"].values()
    } == {runs[0]["run_id"]}


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


def _persist_many(
    governance: TaxonomyGovernance,
    *,
    count: int,
    name_prefix: str,
    token_prefix: str,
    first_question_id: int,
) -> list[dict]:
    return [
        _persist(
            governance,
            dimension="knowledge",
            name=f"{name_prefix}{index}",
            token=f"{token_prefix}{index:030x}"[:32],
            question_id=first_question_id + index,
        )
        for index in range(count)
    ]


def test_concurrent_workers_finish_every_batch_with_accurate_counts(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    proposals = _persist_many(
        governance,
        count=4,
        name_prefix="并发完成知识",
        token_prefix="a1",
        first_question_id=301,
    )
    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
        question_loader=_question_loader,
    )
    run = service.create_run(
        proposal_ids=[proposal["id"] for proposal in proposals],
        expected_revision=governance.list_proposals(status="pending")[
            "revision"
        ],
        request_token="a2" + "0" * 30,
    )
    gateway = BarrierRecordingGateway()
    progress: list[dict] = []

    completed = service.process_run(
        run["run_id"],
        gateway,
        batch_size=1,
        concurrency=2,
        progress_callback=lambda snapshot: progress.append(snapshot),
    )

    assert gateway.max_in_flight == 2
    assert len(gateway.calls) == 4
    assert completed["status"] == "completed"
    assert [item["status"] for item in completed["items"]] == [
        "suggested"
    ] * 4
    assert {item["attempts"] for item in completed["items"]} == {1}
    assert completed["progress"] == {
        "total": 4,
        "processed": 4,
        "completed": 4,
        "failed": 0,
        "pending": 0,
        "cancelled": 0,
    }
    assert progress[-1]["progress"]["completed"] == 4


def test_concurrent_batch_failure_is_isolated_from_other_batches(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    proposals = _persist_many(
        governance,
        count=3,
        name_prefix="并发隔离知识",
        token_prefix="b3",
        first_question_id=311,
    )
    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
        question_loader=_question_loader,
    )
    run = service.create_run(
        proposal_ids=[proposal["id"] for proposal in proposals],
        expected_revision=governance.list_proposals(status="pending")[
            "revision"
        ],
        request_token="b4" + "0" * 30,
    )
    gateway = ParallelRecordingGateway(
        fail_names={"并发隔离知识1"},
        delay=0.02,
    )

    partial = service.process_run(
        run["run_id"],
        gateway,
        batch_size=1,
        concurrency=2,
    )

    assert partial["status"] == "partial"
    statuses = {
        item["proposal_id"]: item["status"] for item in partial["items"]
    }
    assert statuses[proposals[0]["id"]] == "suggested"
    assert statuses[proposals[1]["id"]] == "failed"
    assert statuses[proposals[2]["id"]] == "suggested"
    failed_item = partial["items"][1]
    assert failed_item["error"]["category"] == "model_gateway"
    assert partial["progress"] == {
        "total": 3,
        "processed": 3,
        "completed": 2,
        "failed": 1,
        "pending": 0,
        "cancelled": 0,
    }
    assert partial["retryable"] is True

    retry_gateway = RecordingGateway()
    retried = service.retry_failed(
        run["run_id"],
        retry_gateway,
        batch_size=1,
        concurrency=2,
    )
    assert retried["status"] == "completed"
    assert [
        item["proposal_id"]
        for batch in retry_gateway.calls
        for item in batch
    ] == [proposals[1]["id"]]


def test_concurrent_workers_stop_claiming_once_cancel_is_requested(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    proposals = _persist_many(
        governance,
        count=4,
        name_prefix="并发取消知识",
        token_prefix="c5",
        first_question_id=321,
    )
    service = TaxonomySuggestionService(
        state_path=tmp_path / "suggestions.json",
        governance=governance,
        question_loader=_question_loader,
    )
    run = service.create_run(
        proposal_ids=[proposal["id"] for proposal in proposals],
        expected_revision=governance.list_proposals(status="pending")[
            "revision"
        ],
        request_token="c6" + "0" * 30,
    )
    barrier = threading.Barrier(2, timeout=30)
    calls: list[list[dict]] = []
    cancel_event = threading.Event()

    class BarrierGateway:
        def suggest_taxonomy_reviews(self, batch):
            copied = [dict(item) for item in batch]
            barrier.wait()
            calls.append(copied)
            cancel_event.set()
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

    result = service.process_run(
        run["run_id"],
        BarrierGateway(),
        batch_size=1,
        concurrency=2,
        cancel_requested=cancel_event.is_set,
    )

    # Both in-flight batches finish; no further batch is claimed after cancel.
    assert len(calls) == 2
    assert result["status"] == "cancelled"
    assert [item["status"] for item in result["items"]].count(
        "suggested"
    ) == 2
    assert [item["status"] for item in result["items"]].count(
        "cancelled"
    ) == 2
    assert result["progress"]["processed"] == 4
    assert result["progress"]["cancelled"] == 2
