from __future__ import annotations

from typing import Any

from backend.config_generation.orchestration import (
    ConfigGenerationOrchestrator,
    ConfigGenerationPolicy,
)


def _question_payload(question_id: str) -> dict[str, Any]:
    return {
        "rubric": {
            "exam_title": "generated",
            "total_score": 100,
            "questions": [
                {
                    "question_id": question_id,
                    "question_type": "choice",
                    "max_score": 1,
                    "parts": [
                        {
                            "part_id": question_id,
                            "part_score": 1,
                            "steps": [
                                {
                                    "step_id": "S1",
                                    "step_score": 1,
                                    "core_goal": "核对答案",
                                    "required_elements": ["A"],
                                    "allow_alternative_methods": True,
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": question_id,
                    "canonical_answer": "A",
                    "accepted_forms": [],
                    "parts": [
                        {
                            "part_id": question_id,
                            "answer": "A",
                            "analysis": "",
                            "step_milestones": [],
                        }
                    ],
                }
            ]
        },
        "meta": {"warnings": []},
    }


def _policy() -> ConfigGenerationPolicy:
    def apply_scores(payload: dict[str, Any], score_data: dict[str, Any]) -> None:
        score_map = {
            str(item["question_id"]): item for item in score_data["question_scores"]
        }
        for question in payload["rubric"]["questions"]:
            score = score_map[str(question["question_id"])]
            question["max_score"] = score["max_score"]
            question["parts"][0]["part_score"] = score["parts"][0]["part_score"]
            question["parts"][0]["steps"][0]["step_score"] = score["parts"][0]["steps"][
                0
            ]["step_score"]

    return ConfigGenerationPolicy(
        validate_image_inputs=lambda _blocks, _images: None,
        normalize_payload=lambda _payload: None,
        apply_local_question_facts=lambda _payload, _blocks: None,
        attach_reference_answer_images=lambda _payload, _images: None,
        refresh_quality_warnings=lambda _payload: None,
        validate_final_payload=lambda _payload: None,
        force_total_score=lambda payload, _target: payload["rubric"].update(
            {"total_score": 100.0}
        ),
        word_block_image_blobs=lambda _block, _limit: [],
        is_transient_error=lambda _exc: False,
        score_structure_summary=lambda payload: [
            {
                "question_id": str(question["question_id"]),
                "question_type": str(question["question_type"]),
                "parts": [
                    {
                        "part_id": str(question["parts"][0]["part_id"]),
                        "steps": [
                            {
                                "step_id": str(
                                    question["parts"][0]["steps"][0]["step_id"]
                                )
                            }
                        ],
                    }
                ],
            }
            for question in payload["rubric"]["questions"]
        ],
        validate_score_payload=lambda _score_data, _summary: None,
        apply_score_allocation=apply_scores,
    )


class _ScriptedGateway:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, str, int]] = []

    def request_text(self, prompt: str) -> dict[str, Any]:
        self.calls.append(("text", prompt, 0))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, dict)
        return outcome

    def request_images(self, prompt: str, image_blobs: list[bytes]) -> dict[str, Any]:
        self.calls.append(("images", prompt, len(image_blobs)))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, dict)
        return outcome


def test_partial_failure_retries_only_failed_batch_then_scores_once() -> None:
    blocks = [
        {"question_id": "Q1", "question_type": "choice", "text": "one"},
        {"question_id": "Q2", "question_type": "proof", "text": "two"},
    ]
    first_gateway = _ScriptedGateway(
        [_question_payload("Q1"), RuntimeError("provider unavailable")]
    )
    checkpoints: list[dict[str, Any]] = []
    first = ConfigGenerationOrchestrator(
        first_gateway,
        _policy(),
        batch_size=3,
    ).generate(
        blocks,
        "document",
        checkpoint=checkpoints.append,
    )

    assert [call[0] for call in first_gateway.calls] == ["text", "text"]
    assert first["meta"]["failed_question_ids"] == ["Q2"]
    assert len(checkpoints) == 2

    retry_gateway = _ScriptedGateway([_question_payload("Q2")])
    completed = ConfigGenerationOrchestrator(
        retry_gateway,
        _policy(),
        batch_size=3,
    ).retry(
        first,
        blocks,
        "document",
        retry_question_ids=["Q2"],
    )

    # 重试只补失败批次；整卷配分是本地计算，不再发模型请求。
    assert [call[0] for call in retry_gateway.calls] == ["text"]
    assert "Q2" in retry_gateway.calls[0][1]
    assert completed["meta"]["failed_question_ids"] == []
    assert completed["meta"]["score_allocation_ai_success"] is False
    assert completed["meta"]["score_allocation_mode"] == "local_step_weighted"
    assert completed["rubric"]["total_score"] == 100


def test_targeted_regeneration_replaces_selected_question_then_reallocates_scores() -> (
    None
):
    existing = _question_payload("Q1")
    q2 = _question_payload("Q2")
    existing["rubric"]["questions"].extend(q2["rubric"]["questions"])
    existing["answer_key"]["questions"].extend(q2["answer_key"]["questions"])
    existing["rubric"]["questions"][0]["max_score"] = 40
    existing["rubric"]["questions"][0]["parts"][0]["part_score"] = 40
    existing["rubric"]["questions"][0]["parts"][0]["steps"][0]["step_score"] = 40
    existing["rubric"]["questions"][1]["max_score"] = 60
    existing["rubric"]["questions"][1]["parts"][0]["part_score"] = 60
    existing["rubric"]["questions"][1]["parts"][0]["steps"][0]["step_score"] = 60
    original_q2 = existing["rubric"]["questions"][1].copy()
    replacement = _question_payload("Q1")
    replacement["rubric"]["questions"][0]["parts"][0]["steps"][0]["core_goal"] = (
        "采用最新返回核对 B"
    )
    replacement["answer_key"]["questions"][0]["canonical_answer"] = "B"
    replacement["answer_key"]["questions"][0]["parts"][0]["answer"] = "B"
    gateway = _ScriptedGateway([replacement])
    blocks = [
        {"question_id": "Q1", "question_type": "choice", "text": "one"},
        {"question_id": "Q2", "question_type": "choice", "text": "two"},
    ]

    completed = ConfigGenerationOrchestrator(
        gateway,
        _policy(),
        batch_size=3,
    ).regenerate_questions(
        existing,
        blocks,
        "document",
        regenerate_question_ids=["Q1"],
    )

    # 只重新生成被选题目；整卷配分是本地计算，不发模型请求。
    assert len(gateway.calls) == 1
    assert 'BATCH_QUESTION_IDS_JSON=["Q1"]' in gateway.calls[0][1]
    q1 = completed["rubric"]["questions"][0]
    assert q1["parts"][0]["steps"][0]["core_goal"] == "采用最新返回核对 B"
    assert completed["answer_key"]["questions"][0]["canonical_answer"] == "B"
    assert (
        completed["rubric"]["questions"][1]["question_id"] == original_q2["question_id"]
    )
    assert completed["rubric"]["questions"][1]["max_score"] == 60
    assert completed["rubric"]["total_score"] == 100
    assert completed["meta"]["score_allocation_mode"] == "local_step_weighted"
