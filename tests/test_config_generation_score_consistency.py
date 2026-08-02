from __future__ import annotations

import pytest

from backend.config_generation.score_allocation import (
    apply_score_allocation,
    collect_score_consistency_issues,
    normalize_score_allocation_payload,
    score_allocation_structure_summary,
    validate_score_allocation_payload,
)
from session_manager import _whole_generation_retry_prompt


def _payload() -> dict:
    return {
        "rubric": {
            "total_score": 6,
            "questions": [
                {
                    "question_id": "Q11",
                    "max_score": 6,
                    "parts": [
                        {
                            "part_id": "Q11(P1)",
                            "part_score": 1,
                            "max_score": 1,
                            "steps": [
                                {"step_id": "S1", "step_score": 1},
                            ],
                        }
                    ],
                }
            ],
        }
    }


def test_score_consistency_reports_exact_question_part_and_difference() -> None:
    payload = _payload()
    payload["rubric"]["questions"][0]["parts"][0]["part_score"] = 6

    issues = collect_score_consistency_issues(payload, expected_total=6)

    assert issues == [
        "Q11/Q11(P1) 的步骤分合计为 1 分，与小问分值 6 分不一致（相差 5 分）"
    ]


def test_applying_score_allocation_removes_legacy_part_max_score() -> None:
    payload = _payload()

    apply_score_allocation(
        payload,
        {
            "question_scores": [
                {
                    "question_id": "Q11",
                    "max_score": 6,
                    "parts": [
                        {
                            "part_id": "Q11(P1)",
                            "part_score": 6,
                            "steps": [
                                {"step_id": "S1", "step_score": 6},
                            ],
                        }
                    ],
                }
            ]
        },
    )

    part = payload["rubric"]["questions"][0]["parts"][0]
    assert part["part_score"] == 6
    assert "max_score" not in part


def test_whole_generation_retry_prompt_includes_exact_local_failure() -> None:
    prompt = _whole_generation_retry_prompt(
        "原始整卷提示词",
        ValueError("Q11(P1)：步骤分合计 1 与小问分值 6 不一致，相差 5"),
    )

    assert "原始整卷提示词" in prompt
    assert "Q11(P1)" in prompt
    assert "相差 5" in prompt
    assert "重新返回完整整卷评分标准" in prompt


def test_repairable_twelve_question_allocation_is_normalized_before_validation() -> None:
    question_types = ["choice"] * 5 + ["fill_blank"] * 4 + ["calculation"] * 3
    proposed_scores = [5, 5, 5, 5, 5, 7, 7, 8, 8, 15, 15, 15]
    payload = {"rubric": {"questions": []}}
    allocation = {"question_scores": []}
    for index, (question_type, score) in enumerate(
        zip(question_types, proposed_scores, strict=True),
        start=1,
    ):
        question_id = f"Q{index}"
        response_mode = (
            "exact_objective"
            if question_type in {"choice", "fill_blank"}
            else "structured_solution"
        )
        payload["rubric"]["questions"].append(
            {
                "question_id": question_id,
                "question_type": question_type,
                "parts": [
                    {
                        "part_id": question_id,
                        "response_mode": response_mode,
                        "steps": [
                            {
                                "step_id": "S1",
                                "core_goal": "合成测试",
                                "required_elements": [],
                            }
                        ],
                    }
                ],
            }
        )
        allocation["question_scores"].append(
            {
                "question_id": question_id,
                "max_score": score,
                "parts": [
                    {
                        "part_id": question_id,
                        "part_score": score,
                        "steps": [{"step_id": "S1", "step_score": score}],
                    }
                ],
            }
        )

    repairs = normalize_score_allocation_payload(
        allocation,
        score_allocation_structure_summary(payload),
    )
    validate_score_allocation_payload(
        allocation,
        score_allocation_structure_summary(payload),
    )

    scores = allocation["question_scores"]
    assert sum(item["max_score"] for item in scores) == 100
    assert len({item["max_score"] for item in scores[:5]}) == 1
    assert len({item["max_score"] for item in scores[5:9]}) == 1
    assert repairs


def test_impossible_objective_group_allocation_fails_without_mutating_model_output() -> None:
    structure = [
        {
            "question_id": question_id,
            "question_type": "choice",
            "parts": [
                {
                    "part_id": question_id,
                    "steps": [{"step_id": "S1"}],
                }
            ],
        }
        for question_id in ("Q1", "Q2")
    ]
    allocation = {
        "question_scores": [
            {
                "question_id": "Q1",
                "max_score": 1,
                "parts": [
                    {
                        "part_id": "Q1",
                        "part_score": 1,
                        "steps": [{"step_id": "S1", "step_score": 1}],
                    }
                ],
            },
            {
                "question_id": "Q2",
                "max_score": 2,
                "parts": [
                    {
                        "part_id": "Q2",
                        "part_score": 2,
                        "steps": [{"step_id": "S1", "step_score": 2}],
                    }
                ],
            },
        ]
    }
    original = {
        "question_scores": [
            {
                "question_id": "Q1",
                "max_score": 1,
                "parts": [
                    {
                        "part_id": "Q1",
                        "part_score": 1,
                        "steps": [{"step_id": "S1", "step_score": 1}],
                    }
                ],
            },
            {
                "question_id": "Q2",
                "max_score": 2,
                "parts": [
                    {
                        "part_id": "Q2",
                        "part_score": 2,
                        "steps": [{"step_id": "S1", "step_score": 2}],
                    }
                ],
            },
        ]
    }

    with pytest.raises(ValueError, match="Cannot allocate"):
        normalize_score_allocation_payload(
            allocation,
            structure,
            target_total=3,
        )

    assert allocation == original


def test_repairable_weight_above_question_cap_is_scaled_before_strict_validation() -> None:
    proposed_scores = [20, 16, 16, 16, 16, 16]
    structure = []
    allocation = {"question_scores": []}
    for index, score in enumerate(proposed_scores, start=1):
        question_id = f"Q{index}"
        structure.append(
            {
                "question_id": question_id,
                "question_type": "calculation",
                "parts": [
                    {
                        "part_id": question_id,
                        "steps": [{"step_id": "S1"}],
                    }
                ],
            }
        )
        allocation["question_scores"].append(
            {
                "question_id": question_id,
                "max_score": score,
                "parts": [
                    {
                        "part_id": question_id,
                        "part_score": score,
                        "steps": [{"step_id": "S1", "step_score": score}],
                    }
                ],
            }
        )

    repairs = normalize_score_allocation_payload(allocation, structure)
    validate_score_allocation_payload(allocation, structure)

    final_scores = [
        item["max_score"] for item in allocation["question_scores"]
    ]
    assert sum(final_scores) == 100
    assert max(final_scores) <= 18
    assert "Q1: 20→18" in repairs
