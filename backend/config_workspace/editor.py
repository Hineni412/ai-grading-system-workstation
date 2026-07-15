from __future__ import annotations

import copy
import hashlib
import math
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from session_manager import (
    refresh_generated_config_quality_warnings,
    validate_generated_config,
)


_OBJECTIVE_TYPES = {"choice", "fill_blank", "judgement", "true_false", "direct_answer"}
_SOLUTION_TYPES = {"proof", "calculation", "comprehensive"}
_WHOLE_ID = "整题"
_UNSPLIT_ID = "未拆评分点"


@dataclass(frozen=True, slots=True)
class ConfigEditorRow:
    row_id: str
    question_id: str
    part_id: str
    step_id: str
    part_label: str
    question_type: str
    core_goal: str
    score: float
    standard_answer: str
    accepted_answers: tuple[str, ...]
    match_rule: str
    knowledge: str
    answer_only_max_score: float | None
    require_final_answer: bool | None
    required_elements: tuple[str, ...] = ()
    deduction_rules: tuple[str, ...] = ()
    final_answer_rule: str = ""


@dataclass(frozen=True, slots=True)
class ConfigEditorEdit:
    row_id: str
    score: float | None = None
    standard_answer: str | None = None
    accepted_answers: tuple[str, ...] | None = None
    answer_only_max_score: float | None = None
    require_final_answer: bool | None = None
    required_elements: tuple[str, ...] | None = None
    deduction_rules: tuple[str, ...] | None = None
    final_answer_rule: str | None = None


@dataclass(frozen=True, slots=True)
class ManualPartInput:
    part_id: str
    score: float
    core_goal: str


@dataclass(frozen=True, slots=True)
class SplitScoringUnitCommand:
    kind: Literal["split"]
    question_id: str
    count: int
    style: Literal["subquestion", "blank"]


@dataclass(frozen=True, slots=True)
class ReplaceScoringUnitsCommand:
    kind: Literal["replace_parts"]
    question_id: str
    parts: tuple[ManualPartInput, ...]


ConfigEditorCommand = SplitScoringUnitCommand | ReplaceScoringUnitsCommand
ConfigEditorIssue = dict[str, str | None]


class ConfigEditorValidationError(ValueError):
    def __init__(self, issues: Sequence[ConfigEditorIssue]) -> None:
        self.issues = tuple(dict(issue) for issue in issues)
        message = str(self.issues[0]["message"]) if self.issues else "Configuration editor input is invalid."
        super().__init__(message)


def project_config_editor(payload: dict[str, Any]) -> list[ConfigEditorRow]:
    issues = collect_config_editor_issues(payload, validate_publish=False)
    if issues:
        raise ConfigEditorValidationError(issues)

    rubric_questions = _questions(payload, "rubric")
    answer_questions = _questions(payload, "answer_key")
    answer_map = {str(item.get("question_id")): item for item in answer_questions}
    rows: list[ConfigEditorRow] = []

    for question in rubric_questions:
        question_id = str(question["question_id"])
        question_type = str(question.get("question_type") or "")
        answer_question = answer_map.get(question_id, {})
        parts = _dict_list(question.get("parts"))
        answer_parts = _dict_list(answer_question.get("parts"))
        knowledge = _knowledge_label(question)
        require_final = question.get("require_final_answer")
        if require_final is None:
            require_final = question_type == "comprehensive"
        max_score = _number(question.get("max_score"), 0.0)
        answer_only = _number_or_none(question.get("answer_only_max_score"))
        if answer_only is None:
            answer_only = float(max(1, round(max_score * 0.25))) if max_score > 0 else 1.0
        final_rule = _final_answer_rule(question)

        if not parts:
            rows.append(
                ConfigEditorRow(
                    row_id=_row_id(question_id, _WHOLE_ID, _WHOLE_ID),
                    question_id=question_id,
                    part_id=_WHOLE_ID,
                    step_id=_WHOLE_ID,
                    part_label=_WHOLE_ID,
                    question_type=question_type,
                    core_goal=_WHOLE_ID,
                    score=max_score,
                    standard_answer=_answer_text(answer_question),
                    accepted_answers=_unique_texts(answer_question.get("accepted_forms")),
                    match_rule=_match_rule(answer_question),
                    knowledge=knowledge,
                    answer_only_max_score=answer_only if question_type in _SOLUTION_TYPES else None,
                    require_final_answer=bool(require_final) if question_type in _SOLUTION_TYPES else None,
                    final_answer_rule=final_rule if question_type in _SOLUTION_TYPES else "",
                )
            )
            continue

        for part_index, part in enumerate(parts):
            part_id = str(part["part_id"])
            answer_part = _answer_part(answer_parts, part_id, part_index)
            first_answer = answer_part or (answer_question if len(parts) == 1 else {})
            part_label = _part_label(question_id, part_id, part_index + 1, len(parts))
            part_score = _number(_first_value(part, "part_score", "max_score"), 0.0)
            part_require_final = bool(part.get("require_final_answer", require_final))
            part_answer_only = _number_or_none(part.get("answer_only_max_score"))
            if part_answer_only is None:
                part_answer_only = _number_or_none(question.get("answer_only_max_score"))
            if part_answer_only is None:
                part_answer_only = float(max(1, round(part_score * 0.25))) if part_score > 0 else 1.0
            part_final_rule = _final_answer_rule(part) or final_rule
            part_knowledge = _part_knowledge(part, knowledge)
            steps = _dict_list(part.get("steps"))
            if not steps:
                rows.append(
                    ConfigEditorRow(
                        row_id=_row_id(question_id, part_id, _UNSPLIT_ID),
                        question_id=question_id,
                        part_id=part_id,
                        step_id=_UNSPLIT_ID,
                        part_label=part_label,
                        question_type=question_type,
                        core_goal=_UNSPLIT_ID,
                        score=part_score,
                        standard_answer=_answer_text(first_answer),
                        accepted_answers=_unique_texts(first_answer.get("accepted_forms")),
                        match_rule=_match_rule(first_answer, answer_question),
                        knowledge=part_knowledge,
                        answer_only_max_score=part_answer_only if question_type in _SOLUTION_TYPES else None,
                        require_final_answer=part_require_final if question_type in _SOLUTION_TYPES else None,
                        required_elements=_unique_texts(part.get("required_elements")),
                        final_answer_rule=part_final_rule if question_type in _SOLUTION_TYPES else "",
                    )
                )
                continue

            for step_index, step in enumerate(steps):
                is_first = step_index == 0
                step_id = str(step["step_id"])
                rows.append(
                    ConfigEditorRow(
                        row_id=_row_id(question_id, part_id, step_id),
                        question_id=question_id,
                        part_id=part_id,
                        step_id=step_id,
                        part_label=part_label,
                        question_type=question_type,
                        core_goal=_step_goal(step, step_index + 1),
                        score=_number(_first_value(step, "step_score", "score", "point_score", "max_score"), 0.0),
                        standard_answer=_answer_text(first_answer) if is_first else "",
                        accepted_answers=_unique_texts(first_answer.get("accepted_forms")) if is_first else (),
                        match_rule=_match_rule(first_answer, answer_question) if is_first else "",
                        knowledge=part_knowledge if is_first else "",
                        answer_only_max_score=(part_answer_only if question_type in _SOLUTION_TYPES else None) if is_first else None,
                        require_final_answer=(part_require_final if question_type in _SOLUTION_TYPES else None) if is_first else None,
                        required_elements=_unique_texts(step.get("required_elements")),
                        deduction_rules=_unique_texts(step.get("deduction_rules")),
                        final_answer_rule=(part_final_rule if question_type in _SOLUTION_TYPES else "") if is_first else "",
                    )
                )
    return rows


def apply_config_editor_changes(
    payload: dict[str, Any],
    *,
    edits: Sequence[ConfigEditorEdit],
    commands: Sequence[ConfigEditorCommand],
) -> dict[str, Any]:
    candidate = copy.deepcopy(payload)
    for command in commands:
        if isinstance(command, SplitScoringUnitCommand) and command.kind == "split":
            _apply_split(candidate, command)
        elif isinstance(command, ReplaceScoringUnitsCommand) and command.kind == "replace_parts":
            _apply_replace_parts(candidate, command)
        else:
            raise ConfigEditorValidationError((_issue("unknown_command", "commands", "Unsupported editor command."),))

    rows = project_config_editor(candidate)
    row_map = {row.row_id: row for row in rows}
    seen: set[str] = set()
    for edit in edits:
        if edit.row_id in seen:
            raise ConfigEditorValidationError(
                (_issue("duplicate_row_id", "edits.row_id", "Each editor row may be changed only once.", row_id=edit.row_id),)
            )
        seen.add(edit.row_id)
        row = row_map.get(edit.row_id)
        if row is None:
            raise ConfigEditorValidationError(
                (_issue("unknown_row_id", "edits.row_id", "The editor row no longer exists.", row_id=edit.row_id),)
            )
        _apply_edit(candidate, row, edit)

    _aggregate_scores(candidate)
    _enforce_objective_semantics(candidate)
    refresh_generated_config_quality_warnings(candidate)
    return candidate


def collect_config_editor_issues(
    payload: dict[str, Any],
    *,
    validate_publish: bool = True,
) -> list[ConfigEditorIssue]:
    issues = _identity_issues(payload)
    if issues or not validate_publish:
        return issues
    candidate = copy.deepcopy(payload)
    refresh_generated_config_quality_warnings(candidate)
    warning_issues = _warning_issues(candidate)
    try:
        validate_generated_config(candidate)
    except (TypeError, ValueError, KeyError):
        warning_issues.append(_issue("invalid_generated_config", "config", "The configuration is not ready to publish."))
    return warning_issues


def validate_config_editor_candidate(payload: dict[str, Any]) -> None:
    issues = collect_config_editor_issues(payload, validate_publish=False)
    if issues:
        raise ConfigEditorValidationError(issues)
    validate_generated_config(payload)


def editor_part_ids(payload: dict[str, Any]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    result: list[tuple[str, tuple[str, ...]]] = []
    for question in _questions(payload, "rubric"):
        question_id = str(question.get("question_id") or "")
        parts = tuple(str(part.get("part_id") or "") for part in _dict_list(question.get("parts")))
        result.append((question_id, parts))
    return tuple(result)


def editor_row_to_streamlit_dict(row: ConfigEditorRow) -> dict[str, Any]:
    return {
        "_row_id": row.row_id,
        "_question_id": row.question_id,
        "_part_id": row.part_id,
        "_step_id": row.step_id,
        "题号": row.question_id,
        "评分单元": row.part_label,
        "评分点": row.core_goal,
        "题型": row.question_type,
        "分值": row.score,
        "标准答案": row.standard_answer,
        "等价答案预案": "；".join(row.accepted_answers),
        "作答匹配规则": row.match_rule,
        "证据要求/关键步骤": "; ".join(row.required_elements),
        "扣分规则": "; ".join(row.deduction_rules),
        "知识点": row.knowledge,
        "需要单独写答": row.require_final_answer,
        "无过程结论分上限": row.answer_only_max_score,
        "未写答扣分说明": row.final_answer_rule,
    }


def streamlit_dataframe_to_editor_edits(
    payload: dict[str, Any], records: Sequence[dict[str, Any]]
) -> tuple[ConfigEditorEdit, ...]:
    projected = project_config_editor(payload)
    by_id = {row.row_id: row for row in projected}
    first_rows: set[str] = set()
    seen_units: set[tuple[str, str]] = set()
    for row in projected:
        unit = (row.question_id, row.part_id)
        if unit not in seen_units:
            first_rows.add(row.row_id)
            seen_units.add(unit)

    edits: list[ConfigEditorEdit] = []
    for index, record in enumerate(records):
        row_id = str(record.get("_row_id") or "").strip()
        if not row_id:
            question_id = str(record.get("_question_id") or "").strip()
            part_id = str(record.get("_part_id") or "").strip()
            step_id = str(record.get("_step_id") or "").strip()
            if question_id and part_id and step_id:
                row_id = _row_id(question_id, part_id, step_id)
            elif index < len(projected):
                # Streamlit may omit hidden columns from returned records. Its
                # editor does not reorder rows, so the original projection order
                # remains the only identity input; visible labels are never used.
                row_id = projected[index].row_id
        original = by_id.get(row_id)
        is_first = row_id in first_rows
        edits.append(
            ConfigEditorEdit(
                row_id=row_id,
                score=_optional_number(record.get("分值")),
                standard_answer=str(record.get("标准答案") or "").strip() if is_first else None,
                accepted_answers=_split_text(record.get("等价答案预案")) if is_first else None,
                answer_only_max_score=_optional_number(record.get("无过程结论分上限")) if is_first else None,
                require_final_answer=_optional_bool(record.get("需要单独写答")) if is_first else None,
                required_elements=_split_text(record.get("证据要求/关键步骤")),
                deduction_rules=_split_text(record.get("扣分规则")),
                final_answer_rule=str(record.get("未写答扣分说明") or "").strip() if is_first else None,
            )
        )
        if original is None:
            continue
    return tuple(edits)


def _apply_edit(payload: dict[str, Any], row: ConfigEditorRow, edit: ConfigEditorEdit) -> None:
    question = _question_by_id(payload, "rubric", row.question_id)
    if question is None:
        raise ConfigEditorValidationError((_issue("unknown_question_id", "question_id", "The question no longer exists."),))
    answer_question = _ensure_answer_question(payload, row.question_id)
    part: dict[str, Any] | None = None
    answer_part: dict[str, Any] | None = None
    step: dict[str, Any] | None = None
    if row.part_id != _WHOLE_ID:
        parts = _dict_list(question.get("parts"))
        part_index = next((idx for idx, item in enumerate(parts) if str(item.get("part_id")) == row.part_id), -1)
        if part_index < 0:
            raise ConfigEditorValidationError((_issue("unknown_part_id", "part_id", "The scoring unit no longer exists."),))
        part = parts[part_index]
        answer_part = _answer_part(_dict_list(answer_question.get("parts")), row.part_id, part_index)
        if answer_part is None:
            answer_part = _new_answer_part(row.part_id)
            answer_question.setdefault("parts", []).append(answer_part)
        if row.step_id != _UNSPLIT_ID:
            step = next(
                (item for item in _dict_list(part.get("steps")) if str(item.get("step_id")) == row.step_id),
                None,
            )

    if edit.score is not None:
        score = _valid_score(edit.score, row.row_id)
        if step is not None:
            step["step_score"] = score
            for alias in ("score", "max_score", "point_score"):
                if alias in step:
                    step[alias] = score
        elif part is not None:
            part["part_score"] = score
            if "max_score" in part:
                part["max_score"] = score
        else:
            question["max_score"] = score

    answer_node = answer_part if answer_part is not None else answer_question
    if edit.standard_answer is not None:
        answer_text = str(edit.standard_answer).strip()
        if answer_part is not None:
            answer_part["answer"] = answer_text
            if "standard_answer" in answer_part:
                answer_part["standard_answer"] = answer_text
        else:
            answer_question["canonical_answer"] = answer_text
    if edit.accepted_answers is not None:
        answer_node["accepted_forms"] = list(_unique_texts(edit.accepted_answers))
        answer_node["_manual_accepted_forms"] = True

    policy_node = part if part is not None else question
    if edit.require_final_answer is not None:
        policy_node["require_final_answer"] = bool(edit.require_final_answer)
    if edit.answer_only_max_score is not None:
        policy_node["answer_only_max_score"] = _valid_score(edit.answer_only_max_score, row.row_id)
    if step is not None:
        if edit.required_elements is not None:
            step["required_elements"] = list(_unique_texts(edit.required_elements))
        if edit.deduction_rules is not None:
            step["deduction_rules"] = list(_unique_texts(edit.deduction_rules))
    elif part is not None and edit.required_elements is not None:
        part["required_elements"] = list(_unique_texts(edit.required_elements))
    if edit.final_answer_rule is not None:
        _set_final_answer_rule(policy_node, edit.final_answer_rule)


def _apply_split(payload: dict[str, Any], command: SplitScoringUnitCommand) -> None:
    if isinstance(command.count, bool) or not isinstance(command.count, int) or not 2 <= command.count <= 20:
        raise ConfigEditorValidationError(
            (_issue("invalid_split_count", "commands.count", "Split count must be between 2 and 20."),)
        )
    if command.style not in {"subquestion", "blank"}:
        raise ConfigEditorValidationError((_issue("invalid_split_style", "commands.style", "Split style is invalid."),))
    question = _question_by_id(payload, "rubric", command.question_id)
    if question is None:
        raise ConfigEditorValidationError((_issue("unknown_question_id", "commands.question_id", "The question does not exist."),))
    answer = _ensure_answer_question(payload, command.question_id)
    total = int(round(_number(question.get("max_score"), float(command.count))))
    if total <= 0:
        total = command.count
    scores = _integer_even_split(total, command.count)
    qtype = str(question.get("question_type") or "comprehensive")
    direct = qtype in _OBJECTIVE_TYPES or command.style == "blank"
    rubric_parts: list[dict[str, Any]] = []
    answer_parts: list[dict[str, Any]] = []
    for index, score in enumerate(scores, start=1):
        part_id = f"{command.question_id}-B{index}" if command.style == "blank" else f"{command.question_id}({index})"
        rubric_parts.append(
            {
                "part_id": part_id,
                "part_score": score,
                "response_mode": "exact_objective" if direct else "process_required",
                "require_final_answer": False if direct else bool(question.get("require_final_answer", qtype == "comprehensive")),
                "answer_only_max_score": score if direct else min(score, max(1, round(score * 0.25))),
                "steps": [
                    {
                        "step_id": "S1",
                        "step_score": score,
                        "core_goal": f"完成 {command.question_id} 第 {index} 个填空/评分单元",
                        "required_elements": ["答案正确或与标准答案等价"]
                        if direct
                        else ["关键过程合理", "结论或证明目标成立"],
                        "allow_alternative_methods": qtype != "choice",
                    }
                ],
                "presentation_rules": [],
            }
        )
        answer_parts.append(
            {
                "part_id": part_id,
                "answer": "",
                "analysis": "用户手动拆分的评分单元，请 AI 基于题干与参考答案补全。",
                "step_milestones": [],
            }
        )
    question["parts"] = rubric_parts
    if command.style == "blank" and qtype in _SOLUTION_TYPES:
        question["grading_mode"] = "direct_answer"
    answer["parts"] = answer_parts
    _append_warning(payload, f"教师手动将 {command.question_id} 拆分为 {command.count} 个评分单元，AI 二次完善时必须保留 part_id。")


def _apply_replace_parts(payload: dict[str, Any], command: ReplaceScoringUnitsCommand) -> None:
    if not command.parts:
        raise ConfigEditorValidationError((_issue("empty_parts", "commands.parts", "Scoring units cannot be empty."),))
    part_ids = [str(item.part_id).strip() for item in command.parts]
    if any(not part_id for part_id in part_ids):
        raise ConfigEditorValidationError((_issue("missing_part_id", "commands.parts.part_id", "Scoring unit IDs cannot be empty."),))
    if len(set(part_ids)) != len(part_ids):
        raise ConfigEditorValidationError((_issue("duplicate_part_id", "commands.parts.part_id", "Scoring unit IDs must be unique."),))
    question = _question_by_id(payload, "rubric", command.question_id)
    if question is None:
        raise ConfigEditorValidationError((_issue("unknown_question_id", "commands.question_id", "The question does not exist."),))
    scores = [_valid_score(item.score, None) for item in command.parts]
    current_total = _number(question.get("max_score"), 0.0)
    if current_total > 0 and not math.isclose(sum(scores), current_total, abs_tol=1e-6):
        raise ConfigEditorValidationError(
            (_issue("score_total_mismatch", "commands.parts.score", "Scoring unit scores must equal the question score."),)
        )
    answer = _ensure_answer_question(payload, command.question_id)
    old_parts = _dict_list(question.get("parts"))
    old_answers = _dict_list(answer.get("parts"))
    old_part_map = {str(item.get("part_id")): item for item in old_parts}
    old_answer_map = {str(item.get("part_id")): item for item in old_answers}
    new_parts: list[dict[str, Any]] = []
    new_answers: list[dict[str, Any]] = []
    for index, (item, part_id, score) in enumerate(zip(command.parts, part_ids, scores)):
        old_part = old_part_map.get(part_id) or (old_parts[index] if index < len(old_parts) else {})
        old_answer = old_answer_map.get(part_id) or (old_answers[index] if index < len(old_answers) else {})
        steps = copy.deepcopy(_dict_list(old_part.get("steps")))
        if steps:
            steps[0]["core_goal"] = str(item.core_goal).strip() or f"完成 {command.question_id} 第 {index + 1} 个评分单元"
            steps[0]["step_score"] = score
            for extra in steps[1:]:
                extra["step_score"] = 0.0
        else:
            steps = [
                {
                    "step_id": "S1",
                    "step_score": score,
                    "core_goal": str(item.core_goal).strip() or f"完成 {command.question_id} 第 {index + 1} 个评分单元",
                    "required_elements": ["答案正确或过程目标完成"],
                    "allow_alternative_methods": True,
                }
            ]
        new_part = copy.deepcopy(old_part)
        new_part.update(
            {
                "part_id": part_id,
                "part_score": score,
                "steps": steps,
                "presentation_rules": copy.deepcopy(old_part.get("presentation_rules"))
                if isinstance(old_part.get("presentation_rules"), list)
                else [],
            }
        )
        new_answer = copy.deepcopy(old_answer)
        new_answer.update(
            {
                "part_id": part_id,
                "answer": str(old_answer.get("answer") or ""),
                "analysis": str(old_answer.get("analysis") or "用户手动编辑的评分单元，请 AI 补全。"),
                "step_milestones": copy.deepcopy(old_answer.get("step_milestones"))
                if isinstance(old_answer.get("step_milestones"), list)
                else [],
            }
        )
        new_parts.append(new_part)
        new_answers.append(new_answer)
    question["parts"] = new_parts
    question["max_score"] = current_total or sum(scores)
    answer["parts"] = new_answers
    _append_warning(payload, f"教师手动编辑了 {command.question_id} 的评分单元结构，AI 二次完善时必须保留这些 part_id。")


def _aggregate_scores(payload: dict[str, Any]) -> None:
    answer_map = {str(item.get("question_id")): item for item in _questions(payload, "answer_key")}
    total = 0.0
    for question in _questions(payload, "rubric"):
        parts = _dict_list(question.get("parts"))
        if parts:
            question_total = 0.0
            answer_only_total = 0.0
            for part in parts:
                steps = _dict_list(part.get("steps"))
                if steps:
                    part_score = sum(_number(step.get("step_score"), 0.0) for step in steps)
                    part["part_score"] = part_score
                    if "max_score" in part:
                        part["max_score"] = part_score
                else:
                    part_score = _number(_first_value(part, "part_score", "max_score"), 0.0)
                question_total += part_score
                raw_answer_only = _number_or_none(part.get("answer_only_max_score"))
                if raw_answer_only is None:
                    mode = str(part.get("response_mode") or "process_required")
                    raw_answer_only = (
                        part_score
                        if mode in {"exact_objective", "short_answer_points", "visual_construction"}
                        else float(max(1, round(part_score * 0.25)))
                    )
                answer_only_total += min(part_score, max(0.0, raw_answer_only))
            question["max_score"] = question_total
            if str(question.get("question_type") or "") in _SOLUTION_TYPES:
                question["answer_only_max_score"] = answer_only_total
                presentation = question.get("answer_presentation_policy")
                if isinstance(presentation, dict):
                    presentation["answer_only_max_score"] = answer_only_total
        total += _number(question.get("max_score"), 0.0)
        for policy in _dict_list(question.get("deduction_policy")):
            if str(policy.get("policy_id")) == "answer_only_process_missing":
                policy["max_deduction"] = max(
                    0.0,
                    _number(question.get("max_score"), 0.0)
                    - _number(question.get("answer_only_max_score"), 0.0),
                )
        answer = answer_map.get(str(question.get("question_id")))
        if answer is not None:
            answer["max_score"] = question.get("max_score", 0.0)
            rubric_part_map = {str(part.get("part_id")): part for part in parts}
            for index, answer_part in enumerate(_dict_list(answer.get("parts"))):
                rubric_part = rubric_part_map.get(str(answer_part.get("part_id")))
                if rubric_part is None and index < len(parts):
                    rubric_part = parts[index]
                if rubric_part is not None:
                    score = rubric_part.get("part_score", 0.0)
                    answer_part["part_score"] = score
                    if "max_score" in answer_part:
                        answer_part["max_score"] = score
    payload.setdefault("rubric", {})["total_score"] = total


def _enforce_objective_semantics(payload: dict[str, Any]) -> None:
    answer_map = {str(item.get("question_id")): item for item in _questions(payload, "answer_key")}
    for question in _questions(payload, "rubric"):
        qtype = str(question.get("question_type") or "").strip().lower()
        if qtype not in _OBJECTIVE_TYPES:
            continue
        question["require_final_answer"] = False
        question["answer_only_max_score"] = _number(question.get("max_score"), 0.0)
        answer = answer_map.get(str(question.get("question_id")))
        answer_parts = _dict_list(answer.get("parts")) if answer is not None else []
        all_exact = True
        for index, part in enumerate(_dict_list(question.get("parts"))):
            mode = str(part.get("response_mode") or "exact_objective")
            if mode not in {"exact_objective", "short_answer_points"}:
                mode = "exact_objective"
            part["response_mode"] = mode
            part["require_final_answer"] = False
            part["answer_only_max_score"] = _number(part.get("part_score"), 0.0)
            part["presentation_rules"] = []
            if mode != "exact_objective":
                all_exact = False
            if index < len(answer_parts) and mode == "exact_objective":
                answer_parts[index]["partial_credit"] = False
        if answer is not None and (all_exact or str(answer.get("match_mode")) == "complete_set"):
            answer["partial_credit"] = False


def _identity_issues(payload: Any) -> list[ConfigEditorIssue]:
    if not isinstance(payload, dict):
        return [_issue("invalid_payload", "config", "Configuration must be an object.")]
    rubric = payload.get("rubric")
    if not isinstance(rubric, dict):
        return [_issue("invalid_rubric", "rubric", "Rubric must be an object.")]
    raw_questions = rubric.get("questions")
    if not isinstance(raw_questions, list):
        return [_issue("invalid_questions", "rubric.questions", "Rubric questions must be a list.")]
    issues: list[ConfigEditorIssue] = []
    question_ids: set[str] = set()
    row_ids: set[str] = set()
    for question in raw_questions:
        if not isinstance(question, dict):
            issues.append(_issue("invalid_question", "rubric.questions", "Each rubric question must be an object."))
            continue
        question_id = str(question.get("question_id") or "").strip()
        if not question_id:
            issues.append(_issue("missing_question_id", "rubric.questions.question_id", "Question IDs cannot be empty."))
            continue
        if question_id in question_ids:
            issues.append(_issue("duplicate_question_id", "rubric.questions.question_id", "Question IDs must be unique."))
        question_ids.add(question_id)
        parts = question.get("parts")
        if parts is None or parts == []:
            row_ids.add(_row_id(question_id, _WHOLE_ID, _WHOLE_ID))
            continue
        if not isinstance(parts, list):
            issues.append(_issue("invalid_parts", "rubric.questions.parts", "Scoring units must be a list."))
            continue
        part_ids: set[str] = set()
        for part in parts:
            if not isinstance(part, dict):
                issues.append(_issue("invalid_part", "rubric.questions.parts", "Each scoring unit must be an object."))
                continue
            part_id = str(part.get("part_id") or "").strip()
            if not part_id:
                issues.append(_issue("missing_part_id", "rubric.questions.parts.part_id", "Scoring unit IDs cannot be empty."))
                continue
            if part_id in part_ids:
                issues.append(_issue("duplicate_part_id", "rubric.questions.parts.part_id", "Scoring unit IDs must be unique."))
            part_ids.add(part_id)
            steps = part.get("steps")
            if steps is None or steps == []:
                row_ids.add(_row_id(question_id, part_id, _UNSPLIT_ID))
                continue
            if not isinstance(steps, list):
                issues.append(_issue("invalid_steps", "rubric.questions.parts.steps", "Scoring steps must be a list."))
                continue
            step_ids: set[str] = set()
            for step in steps:
                if not isinstance(step, dict):
                    issues.append(_issue("invalid_step", "rubric.questions.parts.steps", "Each scoring step must be an object."))
                    continue
                step_id = str(step.get("step_id") or "").strip()
                if not step_id:
                    issues.append(_issue("missing_step_id", "rubric.questions.parts.steps.step_id", "Scoring step IDs cannot be empty."))
                    continue
                if step_id in step_ids:
                    issues.append(_issue("duplicate_step_id", "rubric.questions.parts.steps.step_id", "Scoring step IDs must be unique."))
                    continue
                step_ids.add(step_id)
                identity = _row_id(question_id, part_id, step_id)
                if identity in row_ids:
                    issues.append(_issue("duplicate_row_id", "row_id", "Editor row identities must be unique.", row_id=identity))
                row_ids.add(identity)
    return issues


def _issue(code: str, field: str, message: str, *, row_id: str | None = None) -> ConfigEditorIssue:
    return {"code": code, "severity": "error", "row_id": row_id, "field": field, "message": message}


def _warning_issues(payload: dict[str, Any]) -> list[ConfigEditorIssue]:
    meta = payload.get("meta")
    warnings = meta.get("warnings") if isinstance(meta, dict) else []
    if not isinstance(warnings, list):
        return []
    issues: list[ConfigEditorIssue] = []
    for warning in warnings:
        message = str(warning).strip()
        if not message:
            continue
        issue = _issue(
            "quality_blocking" if message.startswith("[质量检查-阻断]") else "config_warning",
            "meta.warnings",
            message,
        )
        if not message.startswith("[质量检查-阻断]"):
            issue["severity"] = "warning"
        issues.append(issue)
    return issues


def _questions(payload: Any, section: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    node = payload.get(section)
    if not isinstance(node, dict):
        return []
    return _dict_list(node.get("questions"))


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _question_by_id(payload: dict[str, Any], section: str, question_id: str) -> dict[str, Any] | None:
    return next((item for item in _questions(payload, section) if str(item.get("question_id")) == question_id), None)


def _ensure_answer_question(payload: dict[str, Any], question_id: str) -> dict[str, Any]:
    existing = _question_by_id(payload, "answer_key", question_id)
    if existing is not None:
        return existing
    answer_key = payload.setdefault("answer_key", {})
    questions = answer_key.setdefault("questions", [])
    answer = {
        "question_id": question_id,
        "canonical_answer": "",
        "accepted_forms": [],
        "method_variants": [],
        "parts": [],
    }
    questions.append(answer)
    return answer


def _new_answer_part(part_id: str) -> dict[str, Any]:
    return {"part_id": part_id, "answer": "", "analysis": "", "step_milestones": []}


def _answer_part(parts: list[dict[str, Any]], part_id: str, index: int) -> dict[str, Any] | None:
    exact = next((item for item in parts if str(item.get("part_id")) == part_id), None)
    if exact is not None:
        return exact
    return parts[index] if index < len(parts) else None


def _row_id(question_id: str, part_id: str, step_id: str) -> str:
    raw = f"{question_id}\0{part_id}\0{step_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _number(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _number_or_none(value: Any) -> float | None:
    if value is None:
        return None
    result = _number(value, math.nan)
    return result if math.isfinite(result) else None


def _optional_number(value: Any) -> float | None:
    return _number_or_none(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return bool(value)


def _valid_score(value: Any, row_id: str | None) -> float:
    score = _number_or_none(value)
    if score is None or score < 0:
        raise ConfigEditorValidationError(
            (_issue("invalid_score", "score", "Scores must be finite and non-negative.", row_id=row_id),)
        )
    return score


def _first_value(node: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in node and node[key] is not None:
            return node[key]
    return None


def _unique_texts(value: Any) -> tuple[str, ...]:
    values = value if isinstance(value, (list, tuple)) else []
    return tuple(dict.fromkeys(text for item in values if (text := str(item).strip())))


def _split_text(value: Any) -> tuple[str, ...]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ()
    text = str(value).replace("；", ";")
    return tuple(dict.fromkeys(part.strip() for part in text.split(";") if part.strip()))


def _answer_text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    return str(
        node.get("answer")
        or node.get("canonical_answer")
        or node.get("standard_answer")
        or node.get("correct_answer")
        or ""
    )


def _match_rule(node: Any, fallback: Any = None) -> str:
    source = node if isinstance(node, dict) else {}
    backup = fallback if isinstance(fallback, dict) else {}
    if str(source.get("match_mode") or backup.get("match_mode") or "") != "complete_set":
        return ""
    raw_values = source.get("required_values")
    if not isinstance(raw_values, list) or not raw_values:
        raw_values = backup.get("required_values")
    values = _unique_texts(raw_values)
    if not values:
        return "必须填写全部正确答案；顺序不限；少写、错写或多写均不得分"
    return f"必须全部填写：{'、'.join(values)}；顺序不限；少写、错写或多写均不得分"


def _knowledge_label(question: dict[str, Any]) -> str:
    name = str(question.get("knowledge_name") or "").strip()
    return name or str(question.get("knowledge_id") or "").strip()


def _part_knowledge(part: dict[str, Any], fallback: str) -> str:
    points = part.get("knowledge_points")
    labels: list[str] = []
    if isinstance(points, str) and points.strip():
        labels.append(points.strip())
    elif isinstance(points, list):
        for point in points:
            if isinstance(point, dict):
                label = str(point.get("knowledge_name") or point.get("name") or point.get("label") or "").strip()
            else:
                label = str(point or "").strip()
            if label:
                labels.append(label)
    return "；".join(dict.fromkeys(labels)) or str(part.get("knowledge_name") or "").strip() or fallback


def _part_label(question_id: str, part_id: str, index: int, count: int) -> str:
    if count <= 1:
        return question_id
    import re

    suffix = re.search(r"[\(（]([^)）]+)[\)）]$", part_id)
    return f"第({suffix.group(1)})问" if suffix else f"第({index})问"


def _step_goal(step: dict[str, Any], index: int) -> str:
    for key in ("core_goal", "goal", "criterion", "description"):
        value = str(step.get(key) or "").strip()
        if value:
            return value
    required = _unique_texts(step.get("required_elements"))
    return required[0] if required else f"踩分点{index}"


def _final_answer_rule(node: dict[str, Any]) -> str:
    for rule in _dict_list(node.get("presentation_rules")):
        if str(rule.get("rule_id")) == "final_answer_required":
            return str(rule.get("rule") or "")
    return ""


def _set_final_answer_rule(node: dict[str, Any], text: str) -> None:
    rules = node.setdefault("presentation_rules", [])
    if not isinstance(rules, list):
        rules = []
        node["presentation_rules"] = rules
    rules[:] = [rule for rule in rules if not isinstance(rule, dict) or rule.get("rule_id") != "final_answer_required"]
    if bool(node.get("require_final_answer")):
        rules.append(
            {
                "rule_id": "final_answer_required",
                "rule": str(text).strip()
                or "一般解答题需要写出最终答或明确结论；未写答/结论不完整，酌情扣1分",
                "max_deduction": 1,
            }
        )


def _append_warning(payload: dict[str, Any], warning: str) -> None:
    meta = payload.setdefault("meta", {})
    warnings = meta.setdefault("warnings", [])
    if isinstance(warnings, list) and warning not in warnings:
        warnings.append(warning)


def _integer_even_split(total: int, count: int) -> list[int]:
    base, remainder = divmod(int(total), int(count))
    return [base + (1 if index < remainder else 0) for index in range(count)]


__all__ = [
    "ConfigEditorCommand",
    "ConfigEditorEdit",
    "ConfigEditorIssue",
    "ConfigEditorRow",
    "ConfigEditorValidationError",
    "ManualPartInput",
    "ReplaceScoringUnitsCommand",
    "SplitScoringUnitCommand",
    "apply_config_editor_changes",
    "collect_config_editor_issues",
    "editor_part_ids",
    "editor_row_to_streamlit_dict",
    "project_config_editor",
    "streamlit_dataframe_to_editor_edits",
    "validate_config_editor_candidate",
]
