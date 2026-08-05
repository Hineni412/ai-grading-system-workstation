from __future__ import annotations

import inspect
from typing import Any

from backend.config_generation.gateway import LLMConfigGenerationGateway
from backend.config_generation.contract import GENERATED_ID_CONTRACT_PROMPT
from backend.config_generation.orchestration import (
    ConfigGenerationOrchestrator,
    ConfigGenerationPolicy,
)
from backend.config_generation.prompts import (
    build_batch_generation_prompt,
    build_manual_structure_refinement_prompt,
    build_score_allocation_prompt,
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
            str(item["question_id"]): item
            for item in score_data["question_scores"]
        }
        for question in payload["rubric"]["questions"]:
            score = score_map[str(question["question_id"])]
            question["max_score"] = score["max_score"]
            question["parts"][0]["part_score"] = score["parts"][0]["part_score"]
            question["parts"][0]["steps"][0]["step_score"] = score["parts"][0][
                "steps"
            ][0]["step_score"]

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

    def request_images(
        self, prompt: str, image_blobs: list[bytes]
    ) -> dict[str, Any]:
        self.calls.append(("images", prompt, len(image_blobs)))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, dict)
        return outcome


def test_active_batch_prompt_keeps_current_scoring_contract() -> None:
    prompt = build_batch_generation_prompt(
        ["Q1"],
        [
            {
                "question_id": "Q1",
                "question_type": "choice",
                "question_text": "1+1=?",
                "answer_text": "2",
                "analysis": "直接计算",
            }
        ],
        [],
        "",
    )

    assert 'BATCH_QUESTION_IDS_JSON=["Q1"]' in prompt
    assert "question_type_confirmed" in prompt
    assert "response_mode" in prompt
    assert "knowledge_id" not in prompt
    assert "knowledge_name" not in prompt


def test_score_allocation_prompt_snapshot_is_exact() -> None:
    summary = [
        {
            "question_id": "Q1",
            "question_type": "choice",
            "parts": [{"part_id": "Q1", "steps": [{"step_id": "S1"}]}],
        }
    ]

    prompt = build_score_allocation_prompt(
        summary,
        "原卷文字",
        include_document_text=True,
    )

    assert prompt == (
        "请仅为下列已确认题目结构分配分值，总分必须精确等于100。\n"
        "优先继承原卷可识别的分值比例；没有可靠原卷分值时，再按题型、有效评分步骤数量、难度和工作量分配。"
        "相同类型客观题必须同分；选择题单题分值不得高于填空题，且不得低于填空题的一半；"
        "选择、填空、判断等简单客观题单题分值不得高于任一道过程题。"
        "解答题之间允许不同分，但有效评分步骤更多的题不得反而更低。"
        "同一个小问内部，最高评分步骤与最低评分步骤的分值差不得超过2分；"
        "这条规则不限制不同小问或不同大题的总分差。"
        "所有分值均为正整数；单题不超过18分。\n"
        f"{GENERATED_ID_CONTRACT_PROMPT}"
        "保持所有给定的 question_id、part_id、step_id 和小问结构不变。"
        "只有原卷分值缺失或与总分100、整数及上述教学约束冲突时，才可调整原卷比例；"
        "不得改变小问作答要求或 response_mode。\n"
        "\n原始文档文本（仅用于识别原卷分值提示）：\n原卷文字\n"
        '仅返回 JSON：{"question_scores":[{"question_id":"Q1","max_score":1,'
        '"parts":[{"part_id":"Q1","part_score":1,"steps":[{"step_id":"S1",'
        '"step_score":1}]}]}]}\n'
        '待分值结构：\n[{"question_id": "Q1", "question_type": "choice", '
        '"parts": [{"part_id": "Q1", "steps": [{"step_id": "S1"}]}]}]'
    )


def test_manual_refinement_prompt_keeps_ids_and_excludes_knowledge() -> None:
    prompt = build_manual_structure_refinement_prompt({"rubric": {"questions": []}})

    assert "question_id" in prompt
    assert "part_id" in prompt
    assert "knowledge" in prompt
    assert "不要输出" in prompt
    assert "教师创建的 parts[] 小问结构" in prompt
    assert "教师创建 the parts 部分" not in prompt


def test_gateway_adapter_preserves_single_request_methods_and_parameters() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

        def json_from_text_once(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(("text", args, kwargs))
            return {"kind": "text"}

        def json_from_images_once(
            self, *args: Any, **kwargs: Any
        ) -> dict[str, Any]:
            self.calls.append(("images", args, kwargs))
            return {"kind": "images"}

    client = Client()
    gateway = LLMConfigGenerationGateway(
        client,
        model_name="config-model",
        extra_kwargs={"timeout": 321.0},
    )

    assert gateway.request_text("TEXT") == {"kind": "text"}
    assert gateway.request_images("IMAGE", [b"a"]) == {"kind": "images"}
    assert client.calls == [
        (
            "text",
            ("TEXT",),
            {"model": "config-model", "extra_kwargs": {"timeout": 321.0}},
        ),
        (
            "images",
            ("IMAGE", [b"a"]),
            {
                "model": "config-model",
                "extra_kwargs": {"timeout": 321.0},
                "use_config_client": True,
            },
        ),
    ]


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

    score_payload = {
        "question_scores": [
            {
                "question_id": "Q1",
                "max_score": 50,
                "parts": [
                    {
                        "part_id": "Q1",
                        "part_score": 50,
                        "steps": [{"step_id": "S1", "step_score": 50}],
                    }
                ],
            },
            {
                "question_id": "Q2",
                "max_score": 50,
                "parts": [
                    {
                        "part_id": "Q2",
                        "part_score": 50,
                        "steps": [{"step_id": "S1", "step_score": 50}],
                    }
                ],
            },
        ]
    }
    retry_gateway = _ScriptedGateway([_question_payload("Q2"), score_payload])
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

    assert [call[0] for call in retry_gateway.calls] == ["text", "text"]
    assert "Q2" in retry_gateway.calls[0][1]
    assert "SCORE_QUESTION_IDS_JSON" in retry_gateway.calls[1][1]
    assert completed["meta"]["failed_question_ids"] == []
    assert completed["meta"]["score_allocation_ai_success"] is True


def test_solution_evidence_structure_uses_only_one_score_request() -> None:
    structure = _question_payload("Q1")
    structure["rubric"]["questions"][0]["max_score"] = 1
    structure["rubric"]["questions"][0]["parts"][0]["part_score"] = 1
    structure["rubric"]["questions"][0]["parts"][0]["steps"][0][
        "step_score"
    ] = 1
    score_payload = {
        "question_scores": [
            {
                "question_id": "Q1",
                "max_score": 100,
                "parts": [
                    {
                        "part_id": "Q1",
                        "part_score": 100,
                        "steps": [{"step_id": "S1", "step_score": 100}],
                    }
                ],
            }
        ]
    }
    gateway = _ScriptedGateway([score_payload])

    completed = ConfigGenerationOrchestrator(
        gateway,
        _policy(),
    ).allocate_scores_for_structure(
        structure,
        [{"question_id": "Q1", "question_type": "choice", "text": "one"}],
        "document",
    )

    assert len(gateway.calls) == 1
    assert gateway.calls[0][0] == "text"
    assert "SCORE_QUESTION_IDS_JSON" in gateway.calls[0][1]
    assert "BATCH_QUESTION_IDS_JSON" not in gateway.calls[0][1]
    assert completed["rubric"]["questions"][0]["max_score"] == 100
    assert completed["meta"]["structure_source"] == "solution_evidence"
    assert completed["meta"]["structure_generation_model_requests"] == 0
    assert completed["meta"]["score_allocation_ai_success"] is True


def test_targeted_regeneration_replaces_selected_question_then_reallocates_scores() -> None:
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
    replacement["rubric"]["questions"][0]["parts"][0]["steps"][0][
        "core_goal"
    ] = "采用最新返回核对 B"
    replacement["answer_key"]["questions"][0]["canonical_answer"] = "B"
    replacement["answer_key"]["questions"][0]["parts"][0]["answer"] = "B"
    score_payload = {
        "question_scores": [
            {
                "question_id": question_id,
                "max_score": 50,
                "parts": [{
                    "part_id": question_id,
                    "part_score": 50,
                    "steps": [{"step_id": "S1", "step_score": 50}],
                }],
            }
            for question_id in ("Q1", "Q2")
        ],
    }
    gateway = _ScriptedGateway([replacement, score_payload])
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

    assert len(gateway.calls) == 2
    assert 'BATCH_QUESTION_IDS_JSON=["Q1"]' in gateway.calls[0][1]
    assert "SCORE_QUESTION_IDS_JSON" in gateway.calls[1][1]
    q1 = completed["rubric"]["questions"][0]
    assert q1["parts"][0]["steps"][0]["core_goal"] == "采用最新返回核对 B"
    assert q1["max_score"] == 50
    assert q1["parts"][0]["part_score"] == 50
    assert q1["parts"][0]["steps"][0]["step_score"] == 50
    assert completed["answer_key"]["questions"][0]["canonical_answer"] == "B"
    assert completed["rubric"]["questions"][1]["question_id"] == original_q2["question_id"]
    assert completed["rubric"]["questions"][1]["max_score"] == 50
    assert completed["rubric"]["total_score"] == 100
    assert completed["meta"]["score_allocation_mode"] == "dedicated_ai_scoring"


def test_production_job_no_longer_imports_config_orchestration_from_session_manager() -> None:
    import backend.config_generation.orchestration as orchestration
    from backend.jobs import config_generation

    orchestration_source = inspect.getsource(orchestration)
    assert "session_manager" not in orchestration_source
    assert "os.getenv" not in orchestration_source
    source = inspect.getsource(config_generation)
    assert "ConfigGenerationOrchestrator" not in source
