from __future__ import annotations

import copy
import hashlib
from dataclasses import FrozenInstanceError

import pytest

from backend.config_workspace.editor import (
    ConfigEditorEdit,
    ConfigEditorValidationError,
    ManualPartInput,
    ManualQuestionPartInput,
    ManualStepInput,
    ReplaceQuestionStructureCommand,
    ReplaceScoringUnitsCommand,
    SplitScoringUnitCommand,
    apply_config_editor_changes,
    collect_config_editor_issues,
    editor_part_ids,
    project_config_editor,
)


def _payload() -> dict:
    return {
        "rubric": {
            "total_score": 10,
            "questions": [
                {
                    "question_id": "Q12",
                    "question_type": "proof",
                    "max_score": 6,
                    "knowledge_id": "K1",
                    "knowledge_name": "全等三角形判定",
                    "parts": [
                        {
                            "part_id": "P1",
                            "part_score": 6,
                            "response_mode": "process_required",
                            "answer_only_max_score": 1,
                            "require_final_answer": True,
                            "presentation_rules": [
                                {
                                    "rule_id": "final_answer_required",
                                    "rule": "必须写出明确结论",
                                    "max_deduction": 1,
                                }
                            ],
                            "steps": [
                                {
                                    "step_id": "S1",
                                    "step_score": 3,
                                    "core_goal": "先证明全等",
                                    "required_elements": ["列出全等条件"],
                                    "deduction_rules": ["缺条件扣分"],
                                    "allow_alternative_methods": True,
                                },
                                {
                                    "step_id": "S2",
                                    "step_score": 3,
                                    "core_goal": "推出对应边相等",
                                    "required_elements": ["写出对应边相等"],
                                    "allow_alternative_methods": True,
                                },
                            ],
                        }
                    ],
                },
                {
                    "question_id": "Q13",
                    "question_type": "fill_blank",
                    "max_score": 4,
                    "knowledge_id": "K2",
                    "parts": [
                        {
                            "part_id": "P1",
                            "part_score": 4,
                            "response_mode": "exact_objective",
                            "steps": [
                                {
                                    "step_id": "S1",
                                    "step_score": 4,
                                    "core_goal": "填写全部正确答案",
                                    "required_elements": ["72°", "54°"],
                                    "allow_alternative_methods": True,
                                }
                            ],
                            "presentation_rules": [],
                        }
                    ],
                },
            ],
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q12",
                    "canonical_answer": "证明略",
                    "accepted_forms": ["等价证明"],
                    "method_variants": [],
                    "parts": [
                        {
                            "part_id": "P1",
                            "answer": "证明略",
                            "accepted_forms": ["等价证明"],
                            "analysis": "",
                            "step_milestones": [],
                        }
                    ],
                },
                {
                    "question_id": "Q13",
                    "canonical_answer": "72°或54°",
                    "accepted_forms": ["54°或72°"],
                    "method_variants": [],
                    "match_mode": "complete_set",
                    "required_values": ["72°", "54°"],
                    "order_sensitive": False,
                    "allow_extra_values": False,
                    "partial_credit": False,
                    "parts": [
                        {
                            "part_id": "P1",
                            "answer": "72°或54°",
                            "accepted_forms": ["54°或72°"],
                            "analysis": "",
                            "step_milestones": [],
                        }
                    ],
                },
            ]
        },
        "meta": {"warnings": []},
    }


def test_projection_preserves_stable_hidden_identity_and_immutable_values() -> None:
    rows = project_config_editor(_payload())

    assert [(row.question_id, row.part_id, row.step_id) for row in rows[:2]] == [
        ("Q12", "P1", "S1"),
        ("Q12", "P1", "S2"),
    ]
    assert rows[0].row_id == hashlib.sha256(b"Q12\0P1\0S1").hexdigest()[:24]
    assert rows[0].accepted_answers == ("等价证明",)
    assert rows[0].answer_only_max_score == 1
    assert rows[0].require_final_answer is True
    assert rows[2].match_rule == "必须全部填写：72°、54°；顺序不限；少写、错写或多写均不得分"
    with pytest.raises(FrozenInstanceError):
        rows[0].score = 9  # type: ignore[misc]


def test_part_level_deduction_policy_is_projected_and_edited_on_the_first_row() -> None:
    payload = _payload()
    payload["rubric"]["questions"][0]["parts"][0]["deduction_policy"] = (
        "缺少全等条件扣对应步骤分；没有最终结论扣 1 分"
    )
    rows = project_config_editor(payload)

    assert rows[0].part_deduction_rules == (
        "缺少全等条件扣对应步骤分",
        "没有最终结论扣 1 分",
    )
    assert rows[1].part_deduction_rules == ()

    updated = apply_config_editor_changes(
        payload,
        edits=(
            ConfigEditorEdit(
                row_id=rows[0].row_id,
                part_deduction_rules=("缺少对应顶点顺序扣 1 分",),
            ),
        ),
        commands=(),
    )

    assert updated["rubric"]["questions"][0]["parts"][0]["deduction_policy"] == [
        "缺少对应顶点顺序扣 1 分"
    ]


def test_apply_changes_uses_row_id_deep_copies_and_recomputes_score_totals() -> None:
    payload = _payload()
    original = copy.deepcopy(payload)
    row = project_config_editor(payload)[0]

    updated = apply_config_editor_changes(
        payload,
        edits=(
            ConfigEditorEdit(
                row_id=row.row_id,
                score=4,
                standard_answer="完整证明",
                accepted_answers=("证明方法甲", "证明方法甲", "证明方法乙"),
                answer_only_max_score=2,
                require_final_answer=False,
            ),
        ),
        commands=(),
    )

    assert payload == original
    question = updated["rubric"]["questions"][0]
    assert [step["step_score"] for step in question["parts"][0]["steps"]] == [4.0, 3]
    assert question["parts"][0]["part_score"] == 7.0
    assert question["max_score"] == 7.0
    assert updated["rubric"]["total_score"] == 11.0
    answer = updated["answer_key"]["questions"][0]["parts"][0]
    assert answer["answer"] == "完整证明"
    assert answer["accepted_forms"] == ["证明方法甲", "证明方法乙"]
    assert question["parts"][0]["answer_only_max_score"] == 2.0
    assert question["parts"][0]["require_final_answer"] is False


def test_objective_edit_keeps_all_or_nothing_semantics() -> None:
    payload = _payload()
    row = project_config_editor(payload)[-1]

    updated = apply_config_editor_changes(
        payload,
        edits=(ConfigEditorEdit(row_id=row.row_id, standard_answer="60°", accepted_answers=("60度",)),),
        commands=(),
    )

    question = updated["rubric"]["questions"][1]
    answer = updated["answer_key"]["questions"][1]
    assert question["require_final_answer"] is False
    assert question["answer_only_max_score"] == 4.0
    assert question["parts"][0]["response_mode"] == "exact_objective"
    assert question["parts"][0]["answer_only_max_score"] == 4.0
    assert answer["partial_credit"] is False
    assert answer["parts"][0]["partial_credit"] is False


def test_projection_reports_missing_and_duplicate_ids_without_internal_details() -> None:
    payload = _payload()
    payload["rubric"]["questions"][0]["parts"][0]["steps"][1]["step_id"] = "S1"
    payload["rubric"]["questions"][1]["parts"][0]["part_id"] = ""

    issues = collect_config_editor_issues(payload, validate_publish=False)

    assert {(issue["code"], issue["field"]) for issue in issues} == {
        ("duplicate_step_id", "rubric.questions.parts.steps.step_id"),
        ("missing_part_id", "rubric.questions.parts.part_id"),
    }
    assert all(set(issue) == {"code", "severity", "row_id", "field", "message"} for issue in issues)
    assert all("Traceback" not in issue["message"] for issue in issues)
    with pytest.raises(ConfigEditorValidationError):
        project_config_editor(payload)


def test_answer_question_and_part_identity_issues_are_stable() -> None:
    payload = _payload()
    payload["answer_key"]["questions"][0]["parts"].append(
        copy.deepcopy(payload["answer_key"]["questions"][0]["parts"][0])
    )
    payload["answer_key"]["questions"].append(copy.deepcopy(payload["answer_key"]["questions"][0]))
    payload["answer_key"]["questions"][1]["parts"][0]["part_id"] = "OTHER"

    issues = collect_config_editor_issues(payload, validate_publish=False)

    assert {issue["code"] for issue in issues} >= {
        "duplicate_answer_question_id",
        "duplicate_answer_part_id",
        "missing_answer_part_id",
    }


def test_missing_answer_question_is_a_stable_projection_issue() -> None:
    payload = _payload()
    payload["answer_key"]["questions"] = payload["answer_key"]["questions"][:1]

    with pytest.raises(ConfigEditorValidationError) as exc:
        project_config_editor(payload)

    assert exc.value.issues[0]["code"] == "missing_answer_question_id"


def test_unknown_or_duplicate_edit_row_id_is_rejected() -> None:
    row_id = project_config_editor(_payload())[0].row_id
    with pytest.raises(ConfigEditorValidationError) as unknown:
        apply_config_editor_changes(
            _payload(), edits=(ConfigEditorEdit(row_id="missing", score=1),), commands=()
        )
    assert unknown.value.issues[0]["code"] == "unknown_row_id"

    with pytest.raises(ConfigEditorValidationError) as duplicate:
        apply_config_editor_changes(
            _payload(),
            edits=(ConfigEditorEdit(row_id=row_id, score=1), ConfigEditorEdit(row_id=row_id, score=2)),
            commands=(),
        )
    assert duplicate.value.issues[0]["code"] == "duplicate_row_id"


@pytest.mark.parametrize("count", [2, 20])
def test_split_accepts_real_streamlit_range_and_preserves_total(count: int) -> None:
    updated = apply_config_editor_changes(
        _payload(),
        edits=(),
        commands=(SplitScoringUnitCommand(kind="split", question_id="Q12", count=count, style="blank"),),
    )

    question = updated["rubric"]["questions"][0]
    assert len(question["parts"]) == count
    assert sum(part["part_score"] for part in question["parts"]) == 6
    assert editor_part_ids(updated)[0] == (
        "Q12",
        tuple(f"Q12(P{i})" for i in range(1, count + 1)),
    )


@pytest.mark.parametrize("count", [1, 21])
def test_split_rejects_counts_outside_real_streamlit_range(count: int) -> None:
    with pytest.raises(ConfigEditorValidationError) as exc:
        apply_config_editor_changes(
            _payload(),
            edits=(),
            commands=(SplitScoringUnitCommand(kind="split", question_id="Q12", count=count, style="subquestion"),),
        )
    assert exc.value.issues[0]["code"] == "invalid_split_count"


def test_replace_parts_normalizes_legacy_ids_and_does_not_mutate_source() -> None:
    payload = _payload()
    original = copy.deepcopy(payload)
    command = ReplaceScoringUnitsCommand(
        kind="replace_parts",
        question_id="Q12",
        parts=(
            ManualPartInput(part_id="P1", score=2, core_goal="目标甲"),
            ManualPartInput(part_id="Q12_2", score=4, core_goal="目标乙"),
        ),
    )

    updated = apply_config_editor_changes(payload, edits=(), commands=(command,))

    assert payload == original
    assert editor_part_ids(updated)[0] == (
        "Q12",
        ("Q12(P1)", "Q12(P2)"),
    )
    assert [
        part["answer"]
        for part in updated["answer_key"]["questions"][0]["parts"]
    ] == ["证明略", ""]

    ambiguous_aliases = ReplaceScoringUnitsCommand(
        kind="replace_parts",
        question_id="Q12",
        parts=(
            ManualPartInput(part_id="P1", score=3, core_goal="甲"),
            ManualPartInput(part_id="Q12(1)", score=3, core_goal="乙"),
        ),
    )
    with pytest.raises(ConfigEditorValidationError) as exc:
        apply_config_editor_changes(
            payload,
            edits=(),
            commands=(ambiguous_aliases,),
        )
    assert exc.value.issues[0]["code"] == "duplicate_part_id"


def test_solution_structure_can_replace_subquestions_and_each_scoring_step() -> None:
    command = ReplaceQuestionStructureCommand(
        kind="replace_question_structure",
        question_id="Q12",
        parts=(
            ManualQuestionPartInput(
                part_id="P1",
                steps=(
                    ManualStepInput(step_id="S1", score=1, core_goal="写出已知条件"),
                    ManualStepInput(step_id="S2", score=2, core_goal="完成关键推理"),
                ),
            ),
            ManualQuestionPartInput(
                part_id="P2",
                steps=(
                    ManualStepInput(step_id="S1", score=3, core_goal="得出最终结论"),
                ),
            ),
        ),
    )

    updated = apply_config_editor_changes(_payload(), edits=(), commands=(command,))
    rows = [row for row in project_config_editor(updated) if row.question_id == "Q12"]

    assert [(row.part_id, row.step_id, row.score, row.core_goal) for row in rows] == [
        ("Q12(P1)", "S1", 1, "写出已知条件"),
        ("Q12(P1)", "S2", 2, "完成关键推理"),
        ("Q12(P2)", "S1", 3, "得出最终结论"),
    ]
    assert updated["rubric"]["questions"][0]["max_score"] == 6


def test_step_reorder_and_new_step_never_borrow_frozen_point_identity():
    payload = _payload()
    steps = payload['rubric']['questions'][0]['parts'][0]['steps']
    steps[0]['evidence_point_ids'] = ['p1']
    steps[1]['evidence_point_ids'] = ['p2']
    command = ReplaceQuestionStructureCommand(kind='replace_question_structure', question_id='Q12', parts=(
        ManualQuestionPartInput(part_id='P1', steps=(
            ManualStepInput(step_id='S2', score=2, core_goal='先处理原第二步'),
            ManualStepInput(step_id='S3', score=2, core_goal='新加步骤'),
            ManualStepInput(step_id='S1', score=2, core_goal='后处理原第一步'),
        )),
    ))
    updated = apply_config_editor_changes(payload, edits=(), commands=(command,))
    actual = updated['rubric']['questions'][0]['parts'][0]['steps']
    assert [step.get('evidence_point_ids') for step in actual] == [['p2'], None, ['p1']]


def test_replace_parts_rejects_opaque_internal_id_without_guessing() -> None:
    command = ReplaceScoringUnitsCommand(
        kind="replace_parts",
        question_id="Q12",
        parts=(
            ManualPartInput(part_id="老师-甲", score=2, core_goal="目标甲"),
            ManualPartInput(part_id="P2", score=4, core_goal="目标乙"),
        ),
    )

    with pytest.raises(ConfigEditorValidationError) as exc:
        apply_config_editor_changes(
            _payload(),
            edits=(),
            commands=(command,),
        )

    assert exc.value.issues[0]["code"] == "invalid_part_id"


def test_replace_parts_folds_one_part_to_parent_and_preserves_answer() -> None:
    command = ReplaceScoringUnitsCommand(
        kind="replace_parts",
        question_id="Q12",
        parts=(
            ManualPartInput(part_id="Q12(1)", score=6, core_goal="完整作答"),
        ),
    )

    updated = apply_config_editor_changes(
        _payload(),
        edits=(),
        commands=(command,),
    )

    assert editor_part_ids(updated)[0] == ("Q12", ("Q12",))
    assert updated["answer_key"]["questions"][0]["parts"][0]["part_id"] == "Q12"
    assert updated["answer_key"]["questions"][0]["parts"][0]["answer"] == "证明略"


def test_whole_solution_edit_restores_legacy_policy_semantics() -> None:
    payload = {
        "rubric": {
            "questions": [
                {
                    "question_id": "Q1",
                    "question_type": "proof",
                    "max_score": 6,
                    "knowledge_id": "K1",
                    "parts": [],
                }
            ]
        },
        "answer_key": {
            "questions": [
                {
                    "question_id": "Q1",
                    "canonical_answer": "证明略",
                    "accepted_forms": [],
                    "method_variants": [],
                    "parts": [],
                }
            ]
        },
        "meta": {"warnings": []},
    }
    row = project_config_editor(payload)[0]

    updated = apply_config_editor_changes(
        payload,
        edits=(
            ConfigEditorEdit(
                row_id=row.row_id,
                require_final_answer=True,
                answer_only_max_score=2,
                final_answer_rule="必须写出结论",
            ),
        ),
        commands=(),
    )

    question = updated["rubric"]["questions"][0]
    assert question["_manual_solution_rules"] is True
    assert question["answer_presentation_policy"] == {
        "require_final_answer": True,
        "answer_only_max_score": 2.0,
        "note": "教师在界面中手动确认的过程/写答规则。",
    }
    policies = {policy["policy_id"]: policy for policy in question["deduction_policy"]}
    assert policies["answer_only_process_missing"]["max_deduction"] == 4.0
    assert policies["core_process_missing"]["max_deduction"] == 6.0


def test_solution_projection_restores_default_final_answer_rule() -> None:
    payload = _payload()
    payload["rubric"]["questions"][0]["parts"][0]["presentation_rules"] = []

    row = project_config_editor(payload)[0]

    assert row.final_answer_rule == "一般解答题需要写出最终答或明确结论；未写答/结论不完整，酌情扣1分"
