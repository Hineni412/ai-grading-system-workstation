from __future__ import annotations

from backend.config_generation.score_allocation import (
    apply_score_allocation,
    collect_score_consistency_issues,
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
