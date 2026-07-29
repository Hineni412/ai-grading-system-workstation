from __future__ import annotations

from backend.config_generation.contract import (
    align_generated_question_ids,
    align_score_allocation_ids,
    is_simple_objective_question,
)
from backend.config_generation.local_facts import apply_local_question_facts
from backend.config_generation.normalization import (
    normalize_new_generated_config_payload,
)
from backend.config_generation.quality import (
    collect_generated_config_quality_warnings,
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


def test_new_generation_repairs_model_owned_part_and_step_ids() -> None:
    rubric_questions = []
    answer_questions = []
    for question_id in ("Q4", "Q5", "Q6"):
        number = question_id[1:]
        rubric, answer = _question(
            question_id,
            question_type="choice",
            part_id=f"P{number}_1",
            step_id=f"S{number}_1",
        )
        rubric_questions.append(rubric)
        answer_questions.append(answer)
    payload = {
        "rubric": {
            "exam_title": "generated",
            "total_score": 3,
            "questions": rubric_questions,
        },
        "answer_key": {"questions": answer_questions},
        "meta": {},
    }

    align_generated_question_ids(payload, ["Q4", "Q5", "Q6"])
    normalize_new_generated_config_payload(payload)

    assert [
        (question["question_id"], question["parts"][0]["part_id"])
        for question in payload["rubric"]["questions"]
    ] == [("Q4", "Q4"), ("Q5", "Q5"), ("Q6", "Q6")]
    assert [
        question["parts"][0]["steps"][0]["step_id"]
        for question in payload["rubric"]["questions"]
    ] == ["S1", "S1", "S1"]
    assert [
        question["parts"][0]["part_id"]
        for question in payload["answer_key"]["questions"]
    ] == ["Q4", "Q5", "Q6"]
    assert payload["meta"]["local_structure_repairs"]


def test_unconfirmed_local_type_cannot_override_model_reclassification() -> None:
    rubric, answer = _question(
        "Q11",
        question_type="comprehensive_question",
        part_id="Q11",
        step_id="S1",
        confirmed=True,
    )
    payload = {
        "rubric": {
            "exam_title": "generated",
            "total_score": 1,
            "questions": [rubric],
        },
        "answer_key": {"questions": [answer]},
        "meta": {},
    }
    local_blocks = [
        {
            "question_id": "Q11",
            "question_type": "fill_blank",
            "question_type_confirmed": False,
        }
    ]

    normalize_new_generated_config_payload(payload)
    apply_local_question_facts(payload, local_blocks)
    normalize_new_generated_config_payload(payload)
    apply_local_question_facts(payload, local_blocks)

    question = payload["rubric"]["questions"][0]
    assert question["question_type"] == "comprehensive"
    assert question["question_type_confirmed"] is False


def test_new_generation_promotes_step_answer_aliases_for_objective_questions() -> None:
    rubric_questions = []
    answer_questions = []
    for question_id, answer_field, answer_value in (
        ("Q5", "correct_value", "70"),
        ("Q7", "answer_value", "150°"),
    ):
        rubric, _answer = _question(
            question_id,
            question_type="fill_blank",
            part_id=question_id,
            step_id="S1",
        )
        rubric_questions.append(rubric)
        answer_questions.append(
            {
                "question_id": question_id,
                "parts": [
                    {
                        "part_id": question_id,
                        "steps": [
                            {"step_id": "S1", answer_field: answer_value},
                        ],
                    }
                ],
            }
        )
    payload = {
        "rubric": {
            "exam_title": "generated",
            "total_score": 2,
            "questions": rubric_questions,
        },
        "answer_key": {"questions": answer_questions},
        "meta": {},
    }

    normalize_new_generated_config_payload(payload)

    normalized_answers = payload["answer_key"]["questions"]
    assert [
        (answer["canonical_answer"], answer["parts"][0]["answer"])
        for answer in normalized_answers
    ] == [("70", "70"), ("150°", "150°")]


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


def test_unconfirmed_local_candidate_does_not_override_a_model_answer() -> None:
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
    unconfirmed_blocks = [
        {
            "question_id": "Q5",
            "question_type": "fill_blank",
            "question_type_confirmed": False,
            "canonical_answer": "72",
            "accepted_forms": ["72"],
            "local_answer_trusted": False,
            "semantic_source": "text",
        }
    ]

    normalize_new_generated_config_payload(payload)
    apply_local_question_facts(payload, unconfirmed_blocks)

    answer = payload["answer_key"]["questions"][0]
    assert answer["canonical_answer"] == "70"
    assert answer["parts"][0]["answer"] == "70"
    assert "72" not in answer["accepted_forms"]


def test_new_generation_normalizes_subjective_answer_aliases_at_the_boundary() -> None:
    def rubric_question(question_id: str) -> dict:
        return {
            "question_id": question_id,
            "question_type": "comprehensive",
            "max_score": 2,
            "parts": [
                {
                    "part_id": question_id,
                    "part_score": 2,
                    "response_mode": "process_required",
                    "steps": [
                        {
                            "step_id": "S1",
                            "step_score": 1,
                            "core_goal": "写出第一步",
                            "required_elements": ["第一步"],
                        },
                        {
                            "step_id": "S2",
                            "step_score": 1,
                            "core_goal": "写出结论",
                            "required_elements": ["结论"],
                        },
                    ],
                }
            ],
        }

    payload = {
        "rubric": {
            "exam_title": "generated",
            "total_score": 4,
            "questions": [rubric_question("Q10"), rubric_question("Q11")],
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q10",
                    "parts": [
                        {
                            "part_id": "Q10",
                            "steps": [
                                {"step_id": "S1", "correct_value": "由条件得到中间结论"},
                                {"step_id": "S2", "correct_value": "所以最终结论成立"},
                            ],
                        }
                    ],
                },
                {
                    "question_id": "Q11",
                    "parts": [
                        {
                            "part_id": "Q11",
                            "final_answer": "45°",
                            "steps": [
                                {"step_id": "S1", "step_content": "先求出两个相关角"},
                                {"step_id": "S2", "step_content": "相减得到45°"},
                            ],
                        }
                    ],
                },
            ]
        },
        "meta": {},
    }

    normalize_new_generated_config_payload(payload)

    answers = payload["answer_key"]["questions"]
    assert answers[0]["parts"][0]["answer"] == (
        "由条件得到中间结论\n所以最终结论成立"
    )
    assert answers[0]["parts"][0]["step_milestones"] == [
        "由条件得到中间结论",
        "所以最终结论成立",
    ]
    assert answers[1]["parts"][0]["answer"] == "45°"
    assert answers[1]["parts"][0]["step_milestones"] == [
        "先求出两个相关角",
        "相减得到45°",
    ]


def test_multi_part_subjective_questions_do_not_join_simple_fill_blank_group() -> None:
    simple_rubric, _simple_answer = _question(
        "Q5",
        question_type="fill_blank",
        part_id="Q5",
        step_id="S1",
    )
    multi_part = {
        "question_id": "Q11",
        "question_type": "fill_blank",
        "parts": [
            {
                "part_id": f"Q11(P{index})",
                "response_mode": "short_answer_points",
                "steps": [{"step_id": "S1"}],
            }
            for index in range(1, 4)
        ],
    }

    assert is_simple_objective_question(simple_rubric) is True
    assert is_simple_objective_question(multi_part) is False


def test_score_allocation_reuses_retained_part_and_step_ids_by_order() -> None:
    structure = [
        {
            "question_id": "Q4",
            "parts": [
                {
                    "part_id": "Q4(P1)",
                    "steps": [{"step_id": "S1"}, {"step_id": "S2"}],
                },
                {
                    "part_id": "Q4(P2)",
                    "steps": [{"step_id": "S1"}],
                },
            ],
        }
    ]
    score_data = {
        "question_scores": [
            {
                "question_id": "第4题",
                "parts": [
                    {
                        "part_id": "P4_1",
                        "steps": [{"step_id": "S4_1"}, {"step_id": "S4_2"}],
                    },
                    {
                        "part_id": "P4_2",
                        "steps": [{"step_id": "S4_1"}],
                    },
                ],
            }
        ]
    }

    repairs = align_score_allocation_ids(score_data, structure)

    question = score_data["question_scores"][0]
    assert question["question_id"] == "Q4"
    assert [part["part_id"] for part in question["parts"]] == [
        "Q4(P1)",
        "Q4(P2)",
    ]
    assert [
        [step["step_id"] for step in part["steps"]]
        for part in question["parts"]
    ] == [["S1", "S2"], ["S1"]]
    assert repairs


def _model_choice_payload(
    response_mode: str,
    *,
    question_type: str | None = None,
) -> dict:
    question = {
        "question_id": "Q4",
        "max_score": 4,
        "parts": [
            {
                "part_id": "Q4",
                "part_score": 4,
                "response_mode": response_mode,
                "steps": [
                    {
                        "step_id": "S1",
                        "step_score": 4,
                        "core_goal": "选择正确选项",
                        "required_elements": ["C"],
                    }
                ],
            }
        ],
    }
    if question_type is not None:
        question["question_type"] = question_type
    return {
        "rubric": {
            "exam_title": "generated",
            "total_score": 4,
            "questions": [question],
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q4",
                    "canonical_answer": "C",
                    "accepted_forms": ["C"],
                    "parts": [{"part_id": "Q4", "answer": "C"}],
                }
            ]
        },
        "meta": {},
    }


def test_missing_question_type_uses_explicit_choice_response_mode() -> None:
    for model_response_mode in ("single_choice", "multiple_choice"):
        payload = _model_choice_payload(model_response_mode)

        normalize_new_generated_config_payload(payload)

        question = payload["rubric"]["questions"][0]
        assert question["question_type"] == "choice"
        assert question["parts"][0]["response_mode"] == "exact_objective"
        assert not any(
            warning.startswith("[质量检查-阻断] Q4")
            for warning in collect_generated_config_quality_warnings(payload)
        )


def test_explicit_comprehensive_type_is_not_overridden_by_choice_mode_alias() -> None:
    payload = _model_choice_payload(
        "single_choice",
        question_type="comprehensive",
    )

    normalize_new_generated_config_payload(payload)

    question = payload["rubric"]["questions"][0]
    assert question["question_type"] == "comprehensive"
