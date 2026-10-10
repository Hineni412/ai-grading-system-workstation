from __future__ import annotations

import copy

import pytest

from backend.config_workspace.editor import (
    ConfigEditorEdit,
    ConfigEditorValidationError,
    ManualQuestionPartInput,
    ManualStepInput,
    ReplaceQuestionStructureCommand,
    apply_config_editor_changes,
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


def test_objective_edit_keeps_all_or_nothing_semantics() -> None:
    payload = _payload()
    row = project_config_editor(payload)[-1]

    updated = apply_config_editor_changes(
        payload,
        edits=(
            ConfigEditorEdit(
                row_id=row.row_id, standard_answer="60°", accepted_answers=("60度",)
            ),
        ),
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


def test_step_goal_and_equivalent_rules_edit_round_trip() -> None:
    payload = _payload()
    step_row = next(
        row for row in project_config_editor(payload) if row.step_id == "S1"
    )
    updated = apply_config_editor_changes(
        payload,
        edits=(
            ConfigEditorEdit(
                row_id=step_row.row_id,
                core_goal=" 证明三角形全等 ",
                equivalent_rules=("用其他判定同样给分", "", "用其他判定同样给分"),
            ),
        ),
        commands=(),
    )
    step = updated["rubric"]["questions"][0]["parts"][0]["steps"][0]
    assert step["core_goal"] == "证明三角形全等"
    assert step["equivalent_rules"] == ["用其他判定同样给分"]
    projected = {row.row_id: row for row in project_config_editor(updated)}
    assert projected[step_row.row_id].core_goal == "证明三角形全等"
    assert projected[step_row.row_id].equivalent_rules == ("用其他判定同样给分",)


def test_core_goal_blank_or_unsplit_row_rejected() -> None:
    payload = _payload()
    step_row = next(
        row for row in project_config_editor(payload) if row.step_id == "S1"
    )
    with pytest.raises(ConfigEditorValidationError) as blank:
        apply_config_editor_changes(
            payload,
            edits=(ConfigEditorEdit(row_id=step_row.row_id, core_goal="  "),),
            commands=(),
        )
    assert blank.value.issues[0]["code"] == "missing_core_goal"

    unsplit = _payload()
    unsplit["rubric"]["questions"][0]["parts"][0]["steps"] = []
    unsplit_row = next(
        row for row in project_config_editor(unsplit) if row.step_id == "未拆评分点"
    )
    with pytest.raises(ConfigEditorValidationError) as rejected:
        apply_config_editor_changes(
            unsplit,
            edits=(ConfigEditorEdit(row_id=unsplit_row.row_id, core_goal="新目标"),),
            commands=(),
        )
    assert rejected.value.issues[0]["code"] == "core_goal_not_editable"

    updated = apply_config_editor_changes(
        unsplit,
        edits=(
            ConfigEditorEdit(
                row_id=unsplit_row.row_id,
                equivalent_rules=("等价写法同样给分",),
            ),
        ),
        commands=(),
    )
    part = updated["rubric"]["questions"][0]["parts"][0]
    assert part["equivalent_rules"] == ["等价写法同样给分"]
    assert next(
        row for row in project_config_editor(updated) if row.step_id == "未拆评分点"
    ).equivalent_rules == ("等价写法同样给分",)


def test_step_reorder_and_new_step_never_borrow_frozen_point_identity():
    payload = _payload()
    steps = payload["rubric"]["questions"][0]["parts"][0]["steps"]
    steps[0]["evidence_point_ids"] = ["p1"]
    steps[1]["evidence_point_ids"] = ["p2"]
    command = ReplaceQuestionStructureCommand(
        kind="replace_question_structure",
        question_id="Q12",
        parts=(
            ManualQuestionPartInput(
                part_id="P1",
                steps=(
                    ManualStepInput(step_id="S2", score=2, core_goal="先处理原第二步"),
                    ManualStepInput(step_id="S3", score=2, core_goal="新加步骤"),
                    ManualStepInput(step_id="S1", score=2, core_goal="后处理原第一步"),
                ),
            ),
        ),
    )
    updated = apply_config_editor_changes(payload, edits=(), commands=(command,))
    actual = updated["rubric"]["questions"][0]["parts"][0]["steps"]
    assert [step.get("evidence_point_ids") for step in actual] == [["p2"], None, ["p1"]]


def test_replacing_mixed_solution_steps_preserves_existing_open_result_kind_only() -> None:
    payload = _payload()
    part = payload["rubric"]["questions"][0]["parts"][0]
    part["response_mode"] = "short_answer_points"
    part["steps"][0].update(answer_kind="conditions", core_goal="给出一个大于 2 的整数",
        required_elements=["答案是整数且大于 2"], evidence_point_ids=["original-open"])
    command = ReplaceQuestionStructureCommand(kind="replace_question_structure", question_id="Q12", parts=(
        ManualQuestionPartInput(part_id="P1", steps=(
            ManualStepInput(step_id="S2", score=2, core_goal="原固定结果"),
            ManualStepInput(step_id="S3", score=2, core_goal="新增结果"),
            ManualStepInput(step_id="S1", score=2, core_goal="给出一个大于 2 的整数"),
        )),
    ))
    saved = apply_config_editor_changes(payload, edits=(), commands=(command,))
    actual = saved["rubric"]["questions"][0]["parts"][0]
    assert actual["response_mode"] == "short_answer_points"
    assert [step.get("answer_kind", "fixed") for step in actual["steps"]] == ["fixed", "fixed", "conditions"]
    assert [step.get("evidence_point_ids") for step in actual["steps"]] == [None, None, ["original-open"]]
    assert actual["steps"][2]["required_elements"] == ["答案是整数且大于 2"]
    assert payload["rubric"]["questions"][0]["parts"][0]["steps"][0]["step_score"] == 3
