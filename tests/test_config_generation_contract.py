from __future__ import annotations

from backend.config_generation.local_facts import apply_local_question_facts
from backend.config_generation.normalization import (
    normalize_new_generated_config_payload,
)


def _question(
    question_id: str,
    *,
    question_type: str,
    part_id: str,
    step_id: str,
    confirmed: bool = False,
) -> tuple[dict, dict]:
    rubric = {
        "question_id": question_id,
        "question_type": question_type,
        "question_type_confirmed": confirmed,
        "max_score": 1,
        "parts": [
            {
                "part_id": part_id,
                "part_score": 1,
                "response_mode": "exact_objective",
                "steps": [
                    {
                        "step_id": step_id,
                        "step_score": 1,
                        "core_goal": "核对答案",
                        "required_elements": ["答案"],
                    }
                ],
            }
        ],
    }
    answer = {
        "question_id": question_id,
        "canonical_answer": "A",
        "accepted_forms": ["A"],
        "parts": [{"part_id": part_id, "answer": "A"}],
    }
    return rubric, answer


def test_multi_blank_question_creates_one_answer_only_step_per_blank() -> None:
    rubric, answer = _question(
        "Q5",
        question_type="fill_blank",
        part_id="Q5",
        step_id="model-process",
    )
    rubric["max_score"] = 2
    rubric["parts"][0]["part_score"] = 2
    rubric["parts"][0]["steps"][0]["core_goal"] = "先计算再推导"
    answer["canonical_answer"] = "3；5"
    answer["accepted_forms"] = ["3；5"]
    answer["parts"][0].update(
        {
            "answer": "3；5",
            "answer_values": ["3", "5"],
        }
    )
    payload = {
        "rubric": {
            "exam_title": "generated",
            "total_score": 2,
            "questions": [rubric],
        },
        "answer_key": {"questions": [answer]},
        "meta": {},
    }

    normalize_new_generated_config_payload(payload)

    steps = payload["rubric"]["questions"][0]["parts"][0]["steps"]
    assert [step["step_id"] for step in steps] == ["S1", "S2"]
    assert [step["step_score"] for step in steps] == [1, 1]
    assert [step["required_elements"] for step in steps] == [["3"], ["5"]]
    assert all("过程" not in step["core_goal"] for step in steps)
    assert all(step["deduction_rules"] == [] for step in steps)


def test_teacher_confirmed_answer_overrides_a_conflicting_model_answer() -> None:
    rubric, _answer = _question(
        "Q5",
        question_type="fill_blank",
        part_id="Q5",
        step_id="S1",
    )
    payload = {
        "rubric": {
            "exam_title": "generated",
            "total_score": 1,
            "questions": [rubric],
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q5",
                    "parts": [
                        {
                            "part_id": "Q5",
                            "steps": [{"step_id": "S1", "correct_value": "70"}],
                        }
                    ],
                }
            ]
        },
        "meta": {},
    }
    teacher_blocks = [
        {
            "question_id": "Q5",
            "question_type": "fill_blank",
            "question_type_confirmed": True,
            "canonical_answer": "72",
            "accepted_forms": ["72"],
            "local_answer_trusted": True,
            "answer_confirmed_by_teacher": True,
            "semantic_source": "text",
        }
    ]

    normalize_new_generated_config_payload(payload)
    apply_local_question_facts(payload, teacher_blocks)

    answer = payload["answer_key"]["questions"][0]
    assert answer["canonical_answer"] == "72"
    assert answer["parts"][0]["answer"] == "72"
    assert "70" not in answer["accepted_forms"]
