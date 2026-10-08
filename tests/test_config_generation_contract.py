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


def _open_generated_config(*, second_fixed_blank: bool = False) -> dict:
    from question_bank.solution_evidence import CoreResolution, QuestionSolutionEvidence
    from question_bank.training_criteria.analysis import grading_config_skeleton_from_solution_evidence
    from question_bank.training_criteria.combined_analysis import compose_generated_config_from_skeletons

    class UnmappedResolver:
        def resolve(self, _term_id):
            return CoreResolution(status="unmapped")

    points = [{
        "evidence_point_id": "part-1-step-1", "step_index": 1,
        "answer_kind": "conditions", "target": "给出一个大于 2 的整数",
        "justification": "题目要求整数且大于 2", "answer_anchor": "3",
        "observable_evidence": "答案是整数且大于 2", "depends_on": [], "fine_term_links": [],
        "equivalent_rules": ["任意满足全部条件的整数均正确"],
        "counterexamples": ["2 不大于 2", "2.5 不是整数"],
    }]
    if second_fixed_blank:
        points.append({
            "evidence_point_id": "part-1-step-2", "step_index": 2,
            "target": "给出 9 的算术平方根", "justification": "非负平方根", "answer_anchor": "3",
            "observable_evidence": "第二空为 3", "depends_on": [], "fine_term_links": [],
            "equivalent_rules": [], "counterexamples": ["-3 不是算术平方根"],
        })
    evidence = QuestionSolutionEvidence.from_model_dict({
        "schema_version": "question-solution-evidence-v2", "question_id": 1,
        "parts": [{"part_id": "part-1", "label": "", "response_mode": "short_answer_points" if second_fixed_blank else "exact_objective",
            "canonical_answer": "3；3" if second_fixed_blank else "3", "accepted_forms": [],
            "full_answer": "第一空例如 3，第二空为 3" if second_fixed_blank else "",
            "proof_obligations": [], "visual_requirements": [], "deduction_policy": ["逐空独立判定"],
            "allow_alternative_methods": True, "evidence_points": points}],
        "auxiliary_rules": [], "rationale": "合成开放答案", "confidence": 1,
    }, question_id=1, source_content_hash="0" * 64, resolver=UnmappedResolver())
    result = compose_generated_config_from_skeletons(
        [grading_config_skeleton_from_solution_evidence(evidence, question_ref="Q5")], exam_title="TEST-open-answer")
    question = result["rubric"]["questions"][0]
    question["question_type"] = "fill_blank"
    question["max_score"] = 2 if second_fixed_blank else 1
    question["parts"][0]["part_score"] = question["max_score"]
    for step in question["parts"][0]["steps"]:
        step["step_score"] = 1
    return result


def test_open_answer_survives_skeleton_local_facts_normalization_editor_and_prompt() -> None:
    from backend.config_workspace.editor import ConfigEditorEdit, apply_config_editor_changes, project_config_editor
    from backend.scan_grading.objective_batch_recognition_service import build_objective_question_specs, build_objective_paper_prompt

    payload = _open_generated_config()
    apply_local_question_facts(payload, [{"question_id": "Q5", "response_form_fact": "single_blank",
        "question_text": "写一个大于 2 的整数", "canonical_answer": "3", "answer_text": "3", "local_answer_trusted": True}])
    normalize_new_generated_config_payload(payload)
    part = payload["rubric"]["questions"][0]["parts"][0]
    step = part["steps"][0]
    assert step["answer_kind"] == "conditions"
    assert step["core_goal"] == "给出一个大于 2 的整数"
    assert step["required_elements"] == ["答案是整数且大于 2"]
    assert step["evidence_point_ids"] == ["part-1-step-1"]
    row = project_config_editor(payload)[0]
    assert row.answer_kind == "conditions"
    saved = apply_config_editor_changes(payload, edits=[ConfigEditorEdit(row_id=row.row_id, standard_answer="4")], commands=[])
    saved_step = saved["rubric"]["questions"][0]["parts"][0]["steps"][0]
    assert saved_step["answer_kind"] == "conditions"
    assert saved_step["required_elements"] == ["答案是整数且大于 2"]
    prompt = build_objective_paper_prompt(build_objective_question_specs("test", saved["rubric"], saved["answer_key"]), {})
    import json
    context = json.loads(prompt.rsplit("SCORING_CONTEXT_JSON:", 1)[1].split("BATCH_MANIFEST_JSON:", 1)[0])
    assert context[0]["rubric"]["parts"][0]["steps"][0]["answer_kind"] == "conditions"
    assert "答案是整数且大于 2" in prompt
    assert "ALL mathematical conditions" in prompt
    assert "legacy data omits answer_kind" in prompt


def test_multi_blank_kind_and_conditions_stay_on_their_own_point() -> None:
    payload = _open_generated_config(second_fixed_blank=True)
    answer_part = payload["answer_key"]["questions"][0]["parts"][0]
    answer_part["answer_values"] = ["3", "3"]
    normalize_new_generated_config_payload(payload)
    part = payload["rubric"]["questions"][0]["parts"][0]
    assert part["response_mode"] == "short_answer_points"
    steps = part["steps"]
    assert [step.get("answer_kind", "fixed") for step in steps] == ["conditions", "fixed"]
    assert [step["required_elements"] for step in steps] == [["答案是整数且大于 2"], ["3"]]
    assert [step["evidence_point_ids"] for step in steps] == [["part-1-step-1"], ["part-1-step-2"]]


def test_open_conditions_reach_training_projection_and_strict_model_schema() -> None:
    from question_bank.solution_evidence import QuestionSolutionEvidence
    from question_bank.training_criteria.analysis import (
        _solution_evidence_schema, criteria_from_confirmed_rubric,
        solution_evidence_source_content_hash,
        training_criteria_from_solution_evidence,
    )
    from question_bank.training_criteria.adapters import question_analysis_input_from_config_source
    from tests.training.test_solution_evidence_normalization import Resolver, _point, _part, _payload

    question = question_analysis_input_from_config_source({"question_id": "Q5", "question_type": "填空题",
        "question_text": "写一个大于 2 的整数", "answer_text": "3"}, question_id=1, curriculum_volume_id="pep-7-up")
    point = _point("part-1-step-1", index=1, target="给出一个大于 2 的整数", anchor="3")
    point.update(answer_kind="conditions", observable_evidence="答案是整数且大于 2")
    evidence = QuestionSolutionEvidence.from_model_dict(
        _payload(_part(mode="exact_objective", canonical_answer="3", full_answer="", points=[point])),
        question_id=1, source_content_hash=solution_evidence_source_content_hash(question), resolver=Resolver())
    draft = training_criteria_from_solution_evidence(evidence, question=question)
    assert draft.points[0].answer_kind == "conditions"
    assert draft.points[0].target == point["target"]
    assert draft.points[0].observable_evidence == point["observable_evidence"]
    config = _open_generated_config()
    normalize_new_generated_config_payload(config)
    converted = criteria_from_confirmed_rubric(question=question, rubric_question=config["rubric"]["questions"][0],
        answer_key=config["answer_key"]["questions"][0])
    assert converted.points[0].answer_kind == "conditions"
    assert converted.points[0].observable_evidence == point["observable_evidence"]
    schema = _solution_evidence_schema()["properties"]["parts"]["items"]["properties"]["evidence_points"]["items"]
    assert "answer_kind" in schema["required"]
    assert schema["properties"]["answer_kind"]["enum"] == ["fixed", "conditions"]


def test_fixed_numeric_normalization_does_not_add_different_valid_values() -> None:
    rubric, answer = _question("Q5", question_type="fill_blank", part_id="Q5", step_id="S1")
    answer.update(canonical_answer="3", accepted_forms=["3.0"])
    answer["parts"][0]["answer"] = "3"
    rubric["parts"][0]["steps"][0]["answer_kind"] = "fixed"
    payload = {"rubric": {"questions": [rubric]}, "answer_key": {"questions": [answer]}, "meta": {}}
    normalize_new_generated_config_payload(payload)
    step = payload["rubric"]["questions"][0]["parts"][0]["steps"][0]
    assert step["answer_kind"] == "fixed"
    assert "3" in step["required_elements"]
    assert "4" not in step["required_elements"]
    assert "4" not in payload["answer_key"]["questions"][0]["accepted_forms"]
