from __future__ import annotations

import pytest

from backend.config_generation.score_allocation import (
    collect_score_consistency_issues,
    normalize_score_allocation_payload,
    score_allocation_structure_summary,
    validate_score_allocation_payload,
)


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


def test_impossible_objective_group_allocation_fails_without_mutating_model_output() -> (
    None
):
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
