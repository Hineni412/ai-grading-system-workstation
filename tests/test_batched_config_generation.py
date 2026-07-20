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


class FakeBatchClient:
    def __init__(self, failed_batches: set[tuple[str, ...]] | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.prompts: list[str] = []
        self.failed_batches = set(failed_batches or set())

    def json_from_text_once(self, prompt: str, **_kwargs):
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


def test_twelve_questions_use_four_single_request_batches_and_local_scoring() -> None:
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
    assert len(checkpoints) == 4
    assert payload["meta"]["generation_mode"] == "batched"
    assert payload["meta"]["score_allocation_mode"] == "local_normalization"
    assert payload["meta"]["failed_batches"] == []
    assert payload["rubric"]["total_score"] == 100
    assert sum(item["max_score"] for item in payload["rubric"]["questions"]) == 100


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
