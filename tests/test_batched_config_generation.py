from __future__ import annotations

import copy
import json
import re
import threading
from types import SimpleNamespace

import pytest

import session_manager


def _blocks(count: int) -> list[dict[str, object]]:
    def question_type(index: int) -> str:
        if index <= 6:
            return "choice"
        if index <= 9:
            return "fill_blank"
        return "calculation"

    return [
        {
            "question_id": f"Q{index}",
            "question_type": question_type(index),
            "question_type_confirmed": True,
            "text": f"Question {index}",
            "answer_text": "A" if index <= 9 else str(index),
        }
        for index in range(1, count + 1)
    ]


def _batch_payload(question_ids: list[str]) -> dict:
    def question_type(question_id: str) -> str:
        index = int(question_id[1:])
        if index <= 6:
            return "choice"
        if index <= 9:
            return "fill_blank"
        return "calculation"

    return {
        "rubric": {
            "questions": [
                {
                    "question_id": qid,
                    "question_type": question_type(qid),
                    "knowledge_id": f"K-{qid}",
                    "knowledge_name": f"Knowledge {qid}",
                    "max_score": 1,
                    "parts": [
                        {
                            "part_id": qid,
                            "response_mode": "exact_objective",
                            "part_score": 1,
                            "steps": [
                                {
                                    "step_id": "S1",
                                    "step_score": 1,
                                    "core_goal": f"Check {qid}",
                                    "required_elements": [f"Answer {qid}"],
                                }
                            ],
                        }
                    ],
                }
                for qid in question_ids
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": qid,
                    "canonical_answer": "A",
                    "accepted_forms": ["A"],
                    "parts": [{"part_id": qid, "answer": "A"}],
                }
                for qid in question_ids
            ]
        },
        "meta": {},
    }


def _prompt_ids(prompt: str) -> list[str]:
    match = re.search(r"BATCH_QUESTION_IDS_JSON=(\[[^\n]+\])", prompt)
    assert match is not None
    return json.loads(match.group(1))


def _score_prompt_ids(prompt: str) -> list[str]:
    match = re.search(r"SCORE_QUESTION_IDS_JSON=(\[[^\n]+\])", prompt)
    assert match is not None
    return json.loads(match.group(1))


def _score_payload(question_ids: list[str]) -> dict:
    score_maps = {
        tuple(f"Q{index}" for index in range(1, 8)): {
            **{f"Q{index}": 14 for index in range(1, 7)},
            "Q7": 16,
        },
        tuple(f"Q{index}" for index in range(1, 13)): {
            **{f"Q{index}": 3 for index in range(1, 7)},
            "Q7": 14,
            "Q8": 14,
            "Q9": 14,
            "Q10": 14,
            "Q11": 13,
            "Q12": 13,
        },
    }
    scores = score_maps[tuple(question_ids)]
    return {
        "question_scores": [
            {
                "question_id": qid,
                "max_score": scores[qid],
                "parts": [
                    {
                        "part_id": qid,
                        "part_score": scores[qid],
                        "steps": [{"step_id": "S1", "step_score": scores[qid]}],
                    }
                ],
            }
            for qid in question_ids
        ]
    }


class FakeBatchClient:
    def __init__(
        self,
        failed_batches: set[tuple[str, ...]] | None = None,
        *,
        fail_score_allocation: bool = False,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.prompts: list[str] = []
        self.score_calls = 0
        self.score_prompts: list[str] = []
        self.failed_batches = set(failed_batches or set())
        self.fail_score_allocation = fail_score_allocation

    def json_from_text_once(self, prompt: str, **_kwargs):
        if "BATCH_QUESTION_IDS_JSON=" not in prompt:
            self.score_calls += 1
            self.score_prompts.append(prompt)
            if self.fail_score_allocation:
                raise RuntimeError("score allocation unavailable")
            return _score_payload(_score_prompt_ids(prompt))
        self.prompts.append(prompt)
        ids = tuple(_prompt_ids(prompt))
        self.calls.append(ids)
        if ids in self.failed_batches:
            raise ValueError("模型返回非 JSON；响应字符数: 10；响应摘要: " + "a" * 64)
        return _batch_payload(list(ids))

    def json_from_images_once(self, prompt: str, _images: list[bytes], **_kwargs):
        return self.json_from_text_once(prompt, **_kwargs)

    def json_from_text(self, *_args, **_kwargs):
        raise AssertionError("batch generation must not make an extra AI scoring/repair call")


def test_independent_batches_overlap_when_profile_allows_parallel_requests() -> None:
    class ConcurrentBatchClient(FakeBatchClient):
        def __init__(self) -> None:
            super().__init__()
            self.config_gateway = SimpleNamespace(
                execution_snapshot=SimpleNamespace(max_in_flight=3)
            )
            self._barrier = threading.Barrier(2, timeout=2)
            self._lock = threading.Lock()
            self.active = 0
            self.peak_active = 0
            self.batch_arrivals = 0

        def json_from_text_once(self, prompt: str, **kwargs):
            if "BATCH_QUESTION_IDS_JSON=" not in prompt:
                return super().json_from_text_once(prompt, **kwargs)
            with self._lock:
                self.batch_arrivals += 1
                should_wait = self.batch_arrivals <= 2
                self.active += 1
                self.peak_active = max(self.peak_active, self.active)
            try:
                if should_wait:
                    self._barrier.wait()
                return super().json_from_text_once(prompt, **kwargs)
            finally:
                with self._lock:
                    self.active -= 1

    client = ConcurrentBatchClient()
    payload = session_manager.generate_grading_config_in_batches(
        _blocks(7),
        "document",
        llm_client=client,
    )

    assert client.peak_active == 2
    assert set(client.calls) == {
        ("Q1", "Q2", "Q3"),
        ("Q4", "Q5", "Q6"),
        ("Q7",),
    }
    assert payload["meta"]["failed_batches"] == []
    assert payload["meta"]["score_allocation_mode"] == "local_step_weighted"
    assert payload["meta"]["score_allocation_ai_success"] is False


def test_unconfirmed_parser_type_does_not_override_model_reclassification() -> None:
    class ReclassifyingClient(FakeBatchClient):
        def json_from_text_once(self, prompt: str, **kwargs):
            ids = _prompt_ids(prompt)
            payload = _batch_payload(ids)
            for question in payload["rubric"]["questions"]:
                if question["question_id"] == "Q11":
                    question["question_type"] = "comprehensive"
            return payload

    block = {
        "question_id": "Q11",
        "question_type": "fill_blank",
        "question_type_confirmed": False,
        "text": "阅读材料并回答三个问题。",
        "answer_text": "略",
    }
    confirmed = [
        {
            "question_id": f"Q{index}",
            "question_type": "choice",
            "question_type_confirmed": True,
            "text": f"Question {index}",
            "answer_text": "A",
        }
        for index in range(1, 7)
    ]

    payload = session_manager.generate_grading_config_in_batches(
        [block, *confirmed],
        "document",
        llm_client=ReclassifyingClient(),
    )

    question = next(
        item for item in payload["rubric"]["questions"]
        if item["question_id"] == "Q11"
    )
    assert question["question_type"] == "comprehensive"
    assert question["question_type_confirmed"] is False


def test_twelve_questions_send_constructed_response_questions_one_per_batch() -> None:
    client = FakeBatchClient()
    checkpoints: list[dict] = []

    payload = session_manager.generate_grading_config_in_batches(
        _blocks(12),
        "document",
        llm_client=client,
        checkpoint=lambda value: checkpoints.append(copy.deepcopy(value)),
    )

    assert client.calls == [
        ("Q1", "Q2", "Q3"),
        ("Q4", "Q5", "Q6"),
        ("Q7", "Q8", "Q9"),
        ("Q10",),
        ("Q11",),
        ("Q12",),
    ]
    # 本地配分不再发送整卷 AI 配分请求。
    assert client.score_calls == 0
    assert len(checkpoints) == 7
    assert payload["meta"]["generation_mode"] == "batched"
    assert payload["meta"]["score_allocation_mode"] == "local_step_weighted"
    assert payload["meta"]["score_allocation_ai_success"] is False
    assert payload["meta"]["score_allocation_pending"] is False
    assert payload["meta"]["failed_batches"] == []
    assert payload["rubric"]["total_score"] == 100
    assert sum(item["max_score"] for item in payload["rubric"]["questions"]) == 100
    # 同型客观题同分；主观题按步骤权重取整数。
    scores = {
        item["question_id"]: item["max_score"]
        for item in payload["rubric"]["questions"]
    }
    assert len({scores[f"Q{index}"] for index in range(1, 7)}) == 1
    assert all(isinstance(score, int) and score > 0 for score in scores.values())


def test_local_score_allocation_needs_no_score_model_call() -> None:
    client = FakeBatchClient()

    payload = session_manager.generate_grading_config_in_batches(
        _blocks(12),
        "document",
        llm_client=client,
    )

    assert client.score_calls == 0
    assert payload["meta"]["score_allocation_mode"] == "local_step_weighted"
    assert payload["meta"]["score_allocation_ai_success"] is False
    assert "score_allocation_local_json_repair" not in payload["meta"]


def test_completed_batches_get_local_scores_and_retry_is_noop() -> None:
    client = FakeBatchClient()

    completed = session_manager.generate_grading_config_in_batches(
        _blocks(12),
        "document",
        llm_client=client,
    )

    assert client.score_calls == 0
    assert completed["meta"]["failed_batches"] == []
    assert completed["meta"]["score_allocation_pending"] is False
    assert completed["meta"]["score_allocation_failed"] is False
    assert sum(item["max_score"] for item in completed["rubric"]["questions"]) == 100

    retry_client = FakeBatchClient()
    resumed = session_manager.retry_failed_grading_config_batches(
        completed,
        _blocks(12),
        "document",
        llm_client=retry_client,
    )

    assert retry_client.calls == []
    assert retry_client.score_calls == 0
    assert resumed["meta"]["score_allocation_mode"] == "local_step_weighted"
    assert sum(item["max_score"] for item in resumed["rubric"]["questions"]) == 100


def test_batch_accepts_answer_aliases_through_local_normalization() -> None:
    class AnswerAliasClient(FakeBatchClient):
        def json_from_text_once(self, prompt: str, **kwargs):
            if "BATCH_QUESTION_IDS_JSON=" not in prompt:
                return super().json_from_text_once(prompt, **kwargs)
            ids = tuple(_prompt_ids(prompt))
            self.calls.append(ids)
            payload = _batch_payload(list(ids))
            for answer in payload["answer_key"]["questions"]:
                answer["answer"] = answer.pop("canonical_answer")
            return payload

    client = AnswerAliasClient()

    payload = session_manager.generate_grading_config_in_batches(
        _blocks(7), "document", llm_client=client
    )

    assert client.calls == [
        ("Q1", "Q2", "Q3"),
        ("Q4", "Q5", "Q6"),
        ("Q7",),
    ]
    assert payload["meta"]["failed_batches"] == []
    assert [
        item["canonical_answer"] for item in payload["answer_key"]["questions"]
    ] == ["A", "A", "A", "A", "A", "A", "A"]
    assert payload["meta"]["score_allocation_mode"] == "local_step_weighted"


def test_batched_generation_strips_knowledge_fields_without_extra_model_calls() -> None:
    class MultiKnowledgeClient(FakeBatchClient):
        def json_from_text_once(self, prompt: str, **_kwargs):
            payload = super().json_from_text_once(prompt, **_kwargs)
            for question in payload["rubric"]["questions"]:
                if question["question_id"] == "Q12":
                    question["knowledge_id"] = ["K-CONGRUENCE", "K-BISECTOR", "K-PROOF"]
                    question["knowledge_name"] = ["全等三角形", "角平分线性质", "几何证明"]
            return payload

    client = MultiKnowledgeClient()

    payload = session_manager.generate_grading_config_in_batches(
        _blocks(12), "document", llm_client=client
    )

    assert client.calls == [
        ("Q1", "Q2", "Q3"),
        ("Q4", "Q5", "Q6"),
        ("Q7", "Q8", "Q9"),
        ("Q10",),
        ("Q11",),
        ("Q12",),
    ]
    question = next(
        item for item in payload["rubric"]["questions"]
        if item["question_id"] == "Q12"
    )
    assert not any(key.startswith("knowledge") for key in question)
    warnings = session_manager.collect_generated_config_quality_warnings(payload)
    assert not any("knowledge" in warning.lower() for warning in warnings)


def test_failed_batch_is_retained_and_retry_only_calls_that_complete_batch() -> None:
    failed = {("Q4", "Q5", "Q6")}
    initial_client = FakeBatchClient(failed)

    partial = session_manager.generate_grading_config_in_batches(
        _blocks(12),
        "document",
        llm_client=initial_client,
    )

    assert partial["meta"]["failed_question_ids"] == ["Q4", "Q5", "Q6"]
    assert partial["meta"]["failed_batches"] == [
        {
            "batch_id": "B002",
            "question_ids": ["Q4", "Q5", "Q6"],
            "category": "model_response_parse",
            "error": "模型返回非 JSON；响应字符数: 10；响应摘要: " + "a" * 64,
        }
    ]
    assert partial["meta"]["score_allocation_pending"] is False
    assert partial["meta"]["score_allocation_failed"] is False
    successful_before = copy.deepcopy(partial["rubric"]["questions"])

    retry_client = FakeBatchClient()
    completed = session_manager.retry_failed_grading_config_batches(
        partial,
        _blocks(12),
        "document",
        llm_client=retry_client,
        retry_question_ids=["Q4", "Q5", "Q6"],
    )

    assert retry_client.calls == [("Q4", "Q5", "Q6")]
    before_map = {item["question_id"]: item for item in successful_before}
    after_map = {item["question_id"]: item for item in completed["rubric"]["questions"]}
    for qid, item in before_map.items():
        assert after_map[qid]["question_type"] == item["question_type"]
        assert not any(key.startswith("knowledge") for key in after_map[qid])
    assert completed["meta"]["failed_batches"] == []
    assert completed["meta"]["failed_question_ids"] == []



def test_legacy_failed_big_question_batch_resumes_as_three_single_question_batches() -> None:
    successful_ids = [f"Q{index}" for index in range(1, 10)]
    successful_payload = _batch_payload(successful_ids)
    legacy_partial = {
        "rubric": {
            "exam_title": "generated",
            "total_score": 100,
            "questions": successful_payload["rubric"]["questions"],
        },
        "answer_key": successful_payload["answer_key"],
        "meta": {
            "generation_mode": "batched",
            "batch_size": 3,
            "batches": [
                {
                    "batch_id": "B001",
                    "question_ids": ["Q1", "Q2", "Q3"],
                    "status": "succeeded",
                },
                {
                    "batch_id": "B002",
                    "question_ids": ["Q4", "Q5", "Q6"],
                    "status": "succeeded",
                },
                {
                    "batch_id": "B003",
                    "question_ids": ["Q7", "Q8", "Q9"],
                    "status": "succeeded",
                },
                {
                    "batch_id": "B004",
                    "question_ids": ["Q10", "Q11", "Q12"],
                    "status": "failed",
                    "category": "transient_network",
                    "error": "模型请求暂时失败（HTTP 502）",
                },
            ],
            "failed_batches": [
                {
                    "batch_id": "B004",
                    "question_ids": ["Q10", "Q11", "Q12"],
                    "category": "transient_network",
                    "error": "模型请求暂时失败（HTTP 502）",
                }
            ],
            "failed_question_ids": ["Q10", "Q11", "Q12"],
        },
    }
    before = {
        item["question_id"]: copy.deepcopy(item)
        for item in legacy_partial["rubric"]["questions"]
    }
    client = FakeBatchClient()

    completed = session_manager.retry_failed_grading_config_batches(
        legacy_partial,
        _blocks(12),
        "document",
        llm_client=client,
        retry_question_ids=["Q10", "Q11", "Q12"],
    )

    assert client.calls == [("Q10",), ("Q11",), ("Q12",)]
    assert client.score_calls == 0
    after = {
        item["question_id"]: item for item in completed["rubric"]["questions"]
    }
    for question_id, question in before.items():
        assert after[question_id]["question_type"] == question["question_type"]
        assert not any(key.startswith("knowledge") for key in after[question_id])
    assert completed["meta"]["failed_batches"] == []



def test_already_scored_checkpoint_resumes_without_another_model_call() -> None:
    initial_client = FakeBatchClient()
    checkpoint = session_manager.generate_grading_config_in_batches(
        _blocks(12), "document", llm_client=initial_client
    )
    resume_client = FakeBatchClient()

    resumed = session_manager.retry_failed_grading_config_batches(
        checkpoint,
        _blocks(12),
        "document",
        llm_client=resume_client,
    )

    assert resume_client.calls == []
    assert resume_client.score_calls == 0
    assert resumed["rubric"]["total_score"] == 100
    assert sum(item["max_score"] for item in resumed["rubric"]["questions"]) == 100
    assert resumed["meta"]["score_allocation_mode"] == "local_step_weighted"


def test_retry_rejects_partial_failed_batch_without_calling_model() -> None:
    client = FakeBatchClient({("Q4", "Q5", "Q6")})
    partial = session_manager.generate_grading_config_in_batches(
        _blocks(12), "document", llm_client=client
    )
    retry_client = FakeBatchClient()

    with pytest.raises(ValueError, match="完整失败批次"):
        session_manager.retry_failed_grading_config_batches(
            partial,
            _blocks(12),
            "document",
            llm_client=retry_client,
            retry_question_ids=["Q4"],
        )

    assert retry_client.calls == []


def test_batch_with_missing_or_extra_question_ids_fails_without_placeholder() -> None:
    class WrongIdsClient(FakeBatchClient):
        def json_from_text_once(self, prompt: str, **_kwargs):
            ids = tuple(_prompt_ids(prompt))
            self.calls.append(ids)
            return _batch_payload([ids[0], "Q999"])

    payload = session_manager.generate_grading_config_in_batches(
        _blocks(6), "document", llm_client=WrongIdsClient()
    )

    assert payload["rubric"]["questions"] == []
    assert payload["meta"]["failed_question_ids"] == [
        "Q1", "Q2", "Q3", "Q4", "Q5", "Q6"
    ]
    assert {item["category"] for item in payload["meta"]["failed_batches"]} == {
        "model_output_contract"
    }


def test_pdf_image_batch_does_not_send_extracted_text() -> None:
    import base64

    blocks = _blocks(12)
    for block in blocks:
        block["semantic_source"] = "images"
    images = {
        str(block["question_id"]): {
            "question": base64.b64encode(b"synthetic-image").decode("ascii"),
            "answer": None,
        }
        for block in blocks
    }
    client = FakeBatchClient()

    session_manager.generate_grading_config_in_batches(
        blocks,
        "PRIVATE_EXTRACTED_PDF_TEXT",
        llm_client=client,
        q_images=images,
    )

    assert client.calls == [
        ("Q1", "Q2", "Q3"), ("Q4", "Q5", "Q6"),
        ("Q7", "Q8", "Q9"), ("Q10",), ("Q11",), ("Q12",),
    ]
    assert all("PRIVATE_EXTRACTED_PDF_TEXT" not in prompt for prompt in client.prompts)
