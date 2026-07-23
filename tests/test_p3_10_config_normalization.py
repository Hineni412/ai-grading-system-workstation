from __future__ import annotations

import copy
import hashlib
import inspect
import json

import pytest

from backend.config_generation import (
    local_facts,
    normalization,
    policy as policy_module,
    quality,
    score_allocation as score_allocation_module,
)
from backend.config_generation.compat import _policy
from backend.config_generation.score_allocation import (
    apply_score_allocation,
    score_allocation_structure_summary,
    validate_score_allocation_payload,
)


def _golden_input() -> dict[str, object]:
    return {
        "rubric": {
            "exam_title": "golden",
            "total_score": 15,
            "questions": [
                {
                    "question_id": "1",
                    "question_type": "select",
                    "max_score": 5,
                    "knowledge_id": "K1",
                    "knowledge_name": "linear equation",
                    "parts": [
                        {
                            "part_id": "1",
                            "part_score": 5,
                            "steps": [
                                {
                                    "step_id": "S1",
                                    "step_score": 5,
                                    "desc": "match option A",
                                }
                            ],
                        }
                    ],
                },
                {
                    "question_id": "Q2",
                    "question_type": "proof",
                    "max_score": 10,
                    "knowledge_points": [
                        {
                            "knowledge_id": "K2",
                            "knowledge_name": "triangle congruence",
                        }
                    ],
                    "parts": [
                        {
                            "part_id": "Q2",
                            "part_score": 10,
                            "steps": [
                                {
                                    "step_id": "S1",
                                    "step_score": 10,
                                    "core_goal": "prove equal angles",
                                    "required_elements": ["congruence"],
                                    "allow_alternative_methods": True,
                                }
                            ],
                        }
                    ],
                },
            ],
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q1",
                    "answer": "A",
                    "accepted_forms": ["A", " a "],
                },
                {
                    "question_id": "2",
                    "canonical_answer": "established",
                    "parts": [
                        {
                            "part_id": "Q2",
                            "answer_content": "follows from congruence",
                            "analysis": "proof",
                        }
                    ],
                },
            ]
        },
        "meta": {"warnings": ["legacy warning"]},
    }


def _payload_digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_normalization_preserves_first_and_second_pass_golden_payloads() -> None:
    payload = _golden_input()

    normalization.normalize_generated_config_schema(payload)
    assert _payload_digest(payload) == (
        "4b53f4114ae31f4bfda78b30af9bf084426bd244e4f13dbfb1f41238a7265bee"
    )

    normalization.normalize_generated_config_schema(payload)
    assert _payload_digest(payload) == (
        "a6c4a4b8eaaa76554a904e4dde789ab551003a5b0a57d65921d0ae5f1d853878"
    )
    third = copy.deepcopy(payload)
    normalization.normalize_generated_config_schema(third)
    assert third == payload


def test_quality_warning_order_and_text_are_exact() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q1",
                    "question_type": "fill_blank",
                    "stem_summary": "如图，��平分��",
                    "knowledge_id": "UNKNOWN",
                    "knowledge_name": "如图，��平分��",
                    "parts": [
                        {
                            "part_id": "Q1",
                            "response_mode": "exact_objective",
                            "steps": [
                                {"step_id": "S1", "core_goal": "写出答案"}
                            ],
                        }
                    ],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q1",
                    "canonical_answer": "",
                    "parts": [],
                }
            ]
        },
    }

    assert quality.collect_generated_config_quality_warnings(payload) == [
        "[质量检查-提醒] Q1 缺少明确知识点",
        "[质量检查-提醒] Q1 知识点疑似直接复制题干",
        "[质量检查-阻断] Q1 的题干、公式、答案或踩分点中存在疑似乱码",
        "[质量检查-阻断] Q1 缺少可评分的标准答案",
        "[质量检查-阻断] Q1/Q1 缺少可评分的标准答案或答案图",
    ]


def test_score_allocation_structure_validation_and_application_are_exact() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q1",
                    "question_type": "choice",
                    "max_score": 1,
                    "knowledge_name": "arithmetic",
                    "parts": [
                        {
                            "part_id": "Q1",
                            "part_score": 1,
                            "response_mode": "exact_objective",
                            "steps": [
                                {
                                    "step_id": "S1",
                                    "step_score": 1,
                                    "core_goal": "match A",
                                    "required_elements": ["A"],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    }
    expected_summary = [
        {
            "question_id": "Q1",
            "question_type": "choice",
            "knowledge_name": "arithmetic",
            "parts": [
                {
                    "part_id": "Q1",
                    "response_mode": "exact_objective",
                    "steps": [
                        {
                            "step_id": "S1",
                            "core_goal": "match A",
                            "required_elements": ["A"],
                        }
                    ],
                }
            ],
        }
    ]
    score_data = {
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

    summary = score_allocation_structure_summary(payload)
    assert summary == expected_summary
    with pytest.raises(ValueError, match="单题上限"):
        validate_score_allocation_payload(score_data, summary)

    score_data["question_scores"][0]["max_score"] = 18
    score_data["question_scores"][0]["parts"][0]["part_score"] = 18
    score_data["question_scores"][0]["parts"][0]["steps"][0]["step_score"] = 18
    with pytest.raises(ValueError, match="总分不是 100"):
        validate_score_allocation_payload(score_data, summary)

    apply_score_allocation(payload, score_data)
    question = payload["rubric"]["questions"][0]
    assert question["max_score"] == 18
    assert question["parts"][0]["part_score"] == 18
    assert question["parts"][0]["steps"][0]["step_score"] == 18


def test_new_policy_modules_are_session_manager_free_and_facade_is_exact() -> None:
    import session_manager

    modules = (
        normalization,
        quality,
        local_facts,
        score_allocation_module,
        policy_module,
    )
    for module in modules:
        assert "session_manager" not in inspect.getsource(module)

    policy = _policy()
    assert policy.normalize_payload is normalization.normalize_generated_config_schema
    assert (
        policy.apply_local_question_facts
        is local_facts.apply_local_question_facts
    )
    assert (
        policy.refresh_quality_warnings
        is quality.refresh_generated_config_quality_warnings
    )
    assert (
        session_manager.normalize_generated_config_schema
        is normalization.normalize_generated_config_schema
    )
    assert (
        session_manager.refresh_generated_config_quality_warnings
        is quality.refresh_generated_config_quality_warnings
    )
