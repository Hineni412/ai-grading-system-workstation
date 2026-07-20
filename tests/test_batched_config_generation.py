from __future__ import annotations

import copy
import json
import re

import pytest

import session_manager


def _blocks(count: int) -> list[dict[str, object]]:
    return [
        {
            "question_id": f"Q{index}",
            "question_type": "choice" if index <= 6 else "calculation",
            "question_type_confirmed": True,
            "text": f"Question {index}",
            "answer_text": "A" if index <= 6 else str(index),
        }
        for index in range(1, count + 1)
    ]


def _batch_payload(question_ids: list[str]) -> dict:
    return {
        "rubric": {
            "questions": [
                {
                    "question_id": qid,
                    "question_type": "choice" if int(qid[1:]) <= 6 else "calculation",
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


def test_twelve_questions_use_four_batches_then_one_ai_score_allocation() -> None:
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
        ("Q10", "Q11", "Q12"),
    ]
    assert client.score_calls == 1
    assert len(checkpoints) == 6
    assert payload["meta"]["generation_mode"] == "batched"
    assert payload["meta"]["score_allocation_mode"] == "dedicated_ai_scoring"
    assert payload["meta"]["score_allocation_ai_success"] is True
    assert payload["meta"]["score_allocation_pending"] is False
    assert payload["meta"]["failed_batches"] == []
    assert payload["rubric"]["total_score"] == 100
    assert sum(item["max_score"] for item in payload["rubric"]["questions"]) == 100
    assert {
        item["question_id"]: item["max_score"]
        for item in payload["rubric"]["questions"]
    } == {
        **{f"Q{index}": 3 for index in range(1, 7)},
        "Q7": 14,
        "Q8": 14,
        "Q9": 14,
        "Q10": 14,
        "Q11": 13,
        "Q12": 13,
    }
    assert "Check Q12" in client.score_prompts[0]


def test_score_allocation_records_local_json_repair_without_raw_response() -> None:
    class RepairedScoreClient(FakeBatchClient):
        def json_from_text_once(self, prompt: str, **kwargs):
            payload = super().json_from_text_once(prompt, **kwargs)
            if "BATCH_QUESTION_IDS_JSON=" not in prompt:
                payload["meta"] = {
                    "local_json_repair": {
                        "repaired": True,
                        "operations": ["remove_trailing_comma"],
                        "response_chars": 1234,
                        "response_sha256": "a" * 64,
                        "raw_response": "must-not-be-persisted",
                    }
                }
            return payload

    payload = session_manager.generate_grading_config_in_batches(
        _blocks(12),
        "document",
        llm_client=RepairedScoreClient(),
    )

    assert payload["meta"]["score_allocation_local_json_repair"] == {
        "repaired": True,
        "operations": ["remove_trailing_comma"],
        "response_chars": 1234,
        "response_sha256": "a" * 64,
    }
    assert "raw_response" not in json.dumps(
        payload["meta"]["score_allocation_local_json_repair"]
    )


def test_score_allocation_failure_preserves_batches_and_retries_only_scoring() -> None:
    failed_client = FakeBatchClient(fail_score_allocation=True)
    checkpoints: list[dict] = []

    pending = session_manager.generate_grading_config_in_batches(
        _blocks(12),
        "document",
        llm_client=failed_client,
        checkpoint=lambda value: checkpoints.append(copy.deepcopy(value)),
    )

    assert failed_client.calls == [
        ("Q1", "Q2", "Q3"),
        ("Q4", "Q5", "Q6"),
        ("Q7", "Q8", "Q9"),
        ("Q10", "Q11", "Q12"),
    ]
    assert failed_client.score_calls == 1
    assert pending["meta"]["failed_batches"] == []
    assert pending["meta"]["score_allocation_ai_success"] is False
    assert pending["meta"]["score_allocation_pending"] is True
    assert pending["meta"]["score_allocation_failed"] is True
    assert sum(item["max_score"] for item in pending["rubric"]["questions"]) == 12
    assert checkpoints[-1]["meta"]["score_allocation_failed"] is True

    retry_client = FakeBatchClient()
    completed = session_manager.retry_failed_grading_config_batches(
        pending,
        _blocks(12),
        "document",
        llm_client=retry_client,
    )

    assert retry_client.calls == []
    assert retry_client.score_calls == 1
    assert completed["meta"]["score_allocation_ai_success"] is True
    assert completed["meta"]["score_allocation_pending"] is False
    assert completed["meta"]["score_allocation_failed"] is False
    assert sum(item["max_score"] for item in completed["rubric"]["questions"]) == 100


def test_incomplete_score_allocation_is_not_published_or_locally_replaced() -> None:
    class IncompleteScoreClient(FakeBatchClient):
        def json_from_text_once(self, prompt: str, **kwargs):
            if "BATCH_QUESTION_IDS_JSON=" in prompt:
                return super().json_from_text_once(prompt, **kwargs)
            self.score_calls += 1
            self.score_prompts.append(prompt)
            question_ids = _score_prompt_ids(prompt)
            payload = _score_payload(question_ids)
            payload["question_scores"].pop()
            return payload

    client = IncompleteScoreClient()

    pending = session_manager.generate_grading_config_in_batches(
        _blocks(12),
        "document",
        llm_client=client,
    )

    assert client.score_calls == 1
    assert pending["meta"]["score_allocation_ai_success"] is False
    assert pending["meta"]["score_allocation_pending"] is True
    assert pending["meta"]["score_allocation_failed"] is True
    assert sum(item["max_score"] for item in pending["rubric"]["questions"]) == 12


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
    assert payload["meta"]["score_allocation_ai_success"] is True


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
            "category": "invalid_json",
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
        assert after_map[qid]["knowledge_id"] == item["knowledge_id"]
        assert after_map[qid]["knowledge_name"] == item["knowledge_name"]
    assert completed["meta"]["failed_batches"] == []
    assert completed["meta"]["failed_question_ids"] == []


def test_already_ai_scored_checkpoint_resumes_without_another_model_call() -> None:
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
    assert resumed["meta"]["score_allocation_mode"] == "dedicated_ai_scoring"


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
        "schema_mismatch"
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
        ("Q7", "Q8", "Q9"), ("Q10", "Q11", "Q12"),
    ]
    assert all("PRIVATE_EXTRACTED_PDF_TEXT" not in prompt for prompt in client.prompts)
