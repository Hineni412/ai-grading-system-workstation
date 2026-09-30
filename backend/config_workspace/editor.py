from __future__ import annotations

import copy
import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from backend.config_generation.normalization import validate_generated_config
from backend.config_generation.quality import (
    refresh_generated_config_quality_warnings,
)
from question_id_contract import (
    canonical_parent_id,
    canonical_part_id,
    question_id_coordinates,
)

_OBJECTIVE_TYPES = {"choice", "fill_blank", "judgement", "true_false", "direct_answer"}
_SOLUTION_TYPES = {"proof", "calculation", "comprehensive"}
_WHOLE_ID = "整题"
_UNSPLIT_ID = "未拆评分点"
_DEFAULT_FINAL_ANSWER_RULE = "一般解答题需要写出最终答或明确结论；未写答/结论不完整，酌情扣1分"


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
    answer_only_max_score: float | None
    require_final_answer: bool | None
    required_elements: tuple[str, ...] = ()
    deduction_rules: tuple[str, ...] = ()
    part_deduction_rules: tuple[str, ...] = ()
    final_answer_rule: str = ""
    response_mode: str = ""
    allow_alternative_methods: bool = True
    equivalent_rules: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConfigEditorEdit:
    row_id: str
    score: float | None = None
    standard_answer: str | None = None
    accepted_answers: tuple[str, ...] | None = None
    answer_only_max_score: float | None = None
    answer_only_max_score_provided: bool | None = None
    require_final_answer: bool | None = None
    required_elements: tuple[str, ...] | None = None
    deduction_rules: tuple[str, ...] | None = None
    part_deduction_rules: tuple[str, ...] | None = None
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


@dataclass(frozen=True, slots=True)
class ManualStepInput:
    step_id: str
    score: float
    core_goal: str


@dataclass(frozen=True, slots=True)
class ManualQuestionPartInput:
    part_id: str
    steps: tuple[ManualStepInput, ...]


@dataclass(frozen=True, slots=True)
class ReplaceQuestionStructureCommand:
    kind: Literal["replace_question_structure"]
    question_id: str
    parts: tuple[ManualQuestionPartInput, ...]


ConfigEditorCommand = (
    SplitScoringUnitCommand
    | ReplaceScoringUnitsCommand
    | ReplaceQuestionStructureCommand
)
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
        require_final = question.get("require_final_answer")
        if require_final is None:
            require_final = question_type == "comprehensive"
        max_score = _number(question.get("max_score"), 0.0)
        answer_only = _number_or_none(question.get("answer_only_max_score"))
        if "answer_only_max_score" not in question:
            answer_only = 1.0 if max_score > 0 else 0.0
        final_rule = _final_answer_rule(question) or (
            _DEFAULT_FINAL_ANSWER_RULE if question_type in _SOLUTION_TYPES else ""
        )

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
                    response_mode=str(question.get("response_mode") or ("exact_objective" if question_type in _OBJECTIVE_TYPES else "process_required")),
                    allow_alternative_methods=bool(question.get("allow_alternative_methods", True)),
                    score=max_score,
                    standard_answer=_answer_text(answer_question),
                    accepted_answers=_unique_texts(answer_question.get("accepted_forms")),
                    match_rule=_match_rule(answer_question),
                    answer_only_max_score=answer_only if question_type in _SOLUTION_TYPES else None,
                    require_final_answer=bool(require_final) if question_type in _SOLUTION_TYPES else None,
                    final_answer_rule=final_rule if question_type in _SOLUTION_TYPES else "",
                )
            )
            continue

        for part_index, part in enumerate(parts):
            part_id = str(part["part_id"])
            answer_part = _answer_part(answer_question, part_id, len(parts))
            first_answer = answer_part or (answer_question if len(parts) == 1 else {})
            part_label = _part_label(question_id, part_id, part_index + 1, len(parts))
            part_score = _number(_first_value(part, "part_score", "max_score"), 0.0)
            part_require_final = bool(part.get("require_final_answer", require_final))
            part_answer_only = _number_or_none(part.get("answer_only_max_score"))
            if "answer_only_max_score" not in part:
                part_answer_only = _number_or_none(question.get("answer_only_max_score"))
            if "answer_only_max_score" not in part and "answer_only_max_score" not in question:
                part_answer_only = 1.0 if part_score > 0 else 0.0
            part_final_rule = _final_answer_rule(part) or final_rule
            part_deduction_rules = _deduction_policy_texts(
                part.get("deduction_policy")
            )
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
                        response_mode=str(part.get("response_mode") or ("exact_objective" if question_type in _OBJECTIVE_TYPES else "process_required")),
                        allow_alternative_methods=bool(part.get("allow_alternative_methods", True)),
                        equivalent_rules=_unique_texts(part.get("equivalent_rules")),
                        score=part_score,
                        standard_answer=_answer_text(first_answer),
                        accepted_answers=_unique_texts(first_answer.get("accepted_forms")),
                        match_rule=_match_rule(first_answer, answer_question),
                        answer_only_max_score=part_answer_only if question_type in _SOLUTION_TYPES else None,
                        require_final_answer=part_require_final if question_type in _SOLUTION_TYPES else None,
                        required_elements=_unique_texts(part.get("required_elements")),
                        part_deduction_rules=part_deduction_rules,
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
                        response_mode=str(part.get("response_mode") or ("exact_objective" if question_type in _OBJECTIVE_TYPES else "process_required")),
                        allow_alternative_methods=bool(step.get("allow_alternative_methods", part.get("allow_alternative_methods", True))),
                        equivalent_rules=_unique_texts(step.get("equivalent_rules")),
                        score=_number(_first_value(step, "step_score", "score", "point_score", "max_score"), 0.0),
                        standard_answer=_answer_text(first_answer) if is_first else "",
                        accepted_answers=_unique_texts(first_answer.get("accepted_forms")) if is_first else (),
                        match_rule=_match_rule(first_answer, answer_question) if is_first else "",
                        answer_only_max_score=(part_answer_only if question_type in _SOLUTION_TYPES else None) if is_first else None,
                        require_final_answer=(part_require_final if question_type in _SOLUTION_TYPES else None) if is_first else None,
                        required_elements=_unique_texts(step.get("required_elements")),
                        deduction_rules=_unique_texts(step.get("deduction_rules")),
                        part_deduction_rules=(
                            part_deduction_rules if is_first else ()
                        ),
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
        elif (
            isinstance(command, ReplaceQuestionStructureCommand)
            and command.kind == "replace_question_structure"
        ):
            _apply_replace_question_structure(candidate, command)
        else:
            raise ConfigEditorValidationError((_issue("unknown_command", "commands", "Unsupported editor command."),))

    rows = project_config_editor(candidate)
    row_map = {row.row_id: row for row in rows}
    seen: set[str] = set()
    policy_question_ids = {
        str(question.get("question_id"))
        for question in _questions(candidate, "rubric")
        if bool(question.get("_manual_solution_rules"))
    }
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
        if row.question_type in _SOLUTION_TYPES and (
            _answer_only_edit_provided(edit)
            or any(
                value is not None
                for value in (
                    edit.require_final_answer,
                    edit.final_answer_rule,
                )
            )
        ):
            policy_question_ids.add(row.question_id)
        _apply_edit(candidate, row, edit)

    _aggregate_scores(candidate)
    for question_id in policy_question_ids:
        question = _question_by_id(candidate, "rubric", question_id)
        if question is not None:
            _sync_solution_policy(question)
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
        validate_generated_config(candidate, normalize=False, enforce_score_policy=False)
    except (TypeError, ValueError, KeyError):
        warning_issues.append(_issue("invalid_generated_config", "config", "The configuration is not ready to publish."))
    return warning_issues + _score_policy_warnings(candidate)


def validate_config_editor_candidate(payload: dict[str, Any]) -> None:
    issues = collect_config_editor_issues(payload, validate_publish=False)
    if issues:
        raise ConfigEditorValidationError(issues)
    try:
        validate_generated_config(payload, normalize=False, enforce_score_policy=False)
    except (TypeError, ValueError, KeyError) as exc:
        raise ConfigEditorValidationError((
            _issue("invalid_generated_config", "config", "评分依据的数据结构不完整，请检查题目、小问和步骤的对应关系；人工分值不受自动配分规则限制。"),
        )) from exc


def editor_part_ids(payload: dict[str, Any]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    result: list[tuple[str, tuple[str, ...]]] = []
    for question in _questions(payload, "rubric"):
        question_id = str(question.get("question_id") or "")
        parts = tuple(str(part.get("part_id") or "") for part in _dict_list(question.get("parts")))
        result.append((question_id, parts))
    return tuple(result)


def editor_identity_signature(payload: dict[str, Any]) -> tuple[Any, ...]:
    rubric_identity = tuple(
        (
            str(question.get("question_id") or ""),
            tuple(
                (
                    str(part.get("part_id") or ""),
                    tuple(
                        str(step.get("step_id") or "")
                        for step in _dict_list(part.get("steps"))
                    ),
                )
                for part in _dict_list(question.get("parts"))
            ),
        )
        for question in _questions(payload, "rubric")
    )
    answer_identity = tuple(
        (
            str(question.get("question_id") or ""),
            tuple(
                str(part.get("part_id") or "")
                for part in _dict_list(question.get("parts"))
            ),
        )
        for question in _questions(payload, "answer_key")
    )
    return rubric_identity, answer_identity


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
        "需要单独写答": row.require_final_answer,
        "无过程结论分上限": row.answer_only_max_score,
        "未写答扣分说明": row.final_answer_rule,
    }


def streamlit_dataframe_to_editor_edits(
    payload: dict[str, Any], records: Sequence[dict[str, Any]]
) -> tuple[ConfigEditorEdit, ...]:
    projected = project_config_editor(payload)
    first_rows: set[str] = set()
    seen_units: set[tuple[str, str]] = set()
    for row in projected:
        unit = (row.question_id, row.part_id)
        if unit not in seen_units:
            first_rows.add(row.row_id)
            seen_units.add(unit)

    edits: list[ConfigEditorEdit] = []
    positional_fallback = any(
        not str(record.get("_row_id") or "").strip()
        and not all(
            str(record.get(key) or "").strip()
            for key in ("_question_id", "_part_id", "_step_id")
        )
        for record in records
    )
    if positional_fallback and len(records) != len(projected):
        raise ConfigEditorValidationError(
            (_issue("visible_row_identity_mismatch", "rows", "Visible editor rows no longer match the projection."),)
        )
    for index, record in enumerate(records):
        row_id = str(record.get("_row_id") or "").strip()
        if not row_id:
            question_id = str(record.get("_question_id") or "").strip()
            part_id = str(record.get("_part_id") or "").strip()
            step_id = str(record.get("_step_id") or "").strip()
            if question_id and part_id and step_id:
                row_id = _row_id(question_id, part_id, step_id)
            elif index < len(projected):
                if not _visible_identity_matches(record, projected[index]):
                    raise ConfigEditorValidationError(
                        (
                            _issue(
                                "visible_row_identity_mismatch",
                                "rows",
                                "Visible editor rows no longer match the projection.",
                                row_id=projected[index].row_id,
                            ),
                        )
                    )
                row_id = projected[index].row_id
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
    return tuple(edits)


def _answer_only_edit_provided(edit: ConfigEditorEdit) -> bool:
    if edit.answer_only_max_score_provided is not None:
        return edit.answer_only_max_score_provided
    return edit.answer_only_max_score is not None


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
        answer_part = _answer_part(answer_question, row.part_id, len(parts))
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
    if _answer_only_edit_provided(edit):
        policy_node["answer_only_max_score"] = (
            None
            if edit.answer_only_max_score is None
            else _valid_score(edit.answer_only_max_score, row.row_id)
        )
    if step is not None:
        if edit.required_elements is not None:
            step["required_elements"] = list(_unique_texts(edit.required_elements))
        if edit.deduction_rules is not None:
            step["deduction_rules"] = list(_unique_texts(edit.deduction_rules))
    elif part is not None and edit.required_elements is not None:
        part["required_elements"] = list(_unique_texts(edit.required_elements))
    if edit.part_deduction_rules is not None:
        policy_target = part if part is not None else question
        policy_target["deduction_policy"] = list(
            _unique_texts(edit.part_deduction_rules)
        )
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
    parent_id = canonical_parent_id(command.question_id)
    if parent_id is None:
        raise ConfigEditorValidationError(
            (
                _issue(
                    "invalid_question_id",
                    "commands.question_id",
                    "Question IDs must use the Qn format.",
                ),
            )
        )
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
        part_id = canonical_part_id(parent_id, index)
        rubric_parts.append(
            {
                "part_id": part_id,
                "part_score": score,
                "response_mode": "exact_objective" if direct else "process_required",
                "require_final_answer": False if direct else bool(question.get("require_final_answer", qtype == "comprehensive")),
                "answer_only_max_score": score if direct else min(score, 1),
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
    raw_part_ids = [str(item.part_id).strip() for item in command.parts]
    if any(not part_id for part_id in raw_part_ids):
        raise ConfigEditorValidationError((_issue("missing_part_id", "commands.parts.part_id", "Scoring unit IDs cannot be empty."),))
    question = _question_by_id(payload, "rubric", command.question_id)
    if question is None:
        raise ConfigEditorValidationError((_issue("unknown_question_id", "commands.question_id", "The question does not exist."),))
    parent_id = canonical_parent_id(command.question_id)
    if parent_id is None:
        raise ConfigEditorValidationError(
            (
                _issue(
                    "invalid_question_id",
                    "commands.question_id",
                    "Question IDs must use the Qn format.",
                ),
            )
        )
    part_ids: list[str] = []
    for raw_part_id in raw_part_ids:
        canonical = _canonical_editor_part_id(
            raw_part_id,
            parent_id,
            part_count=len(raw_part_ids),
        )
        if canonical is None:
            raise ConfigEditorValidationError(
                (
                    _issue(
                        "invalid_part_id",
                        "commands.parts.part_id",
                        "Scoring unit IDs must identify a sub-question of "
                        f"{parent_id}.",
                    ),
                )
            )
        part_ids.append(canonical)
    if len(set(part_ids)) != len(part_ids):
        raise ConfigEditorValidationError((_issue("duplicate_part_id", "commands.parts.part_id", "Scoring unit IDs must be unique."),))
    scores = [_valid_score(item.score, None) for item in command.parts]
    answer = _ensure_answer_question(payload, command.question_id)
    old_parts = _dict_list(question.get("parts"))
    old_part_map = _parts_by_canonical_id(old_parts, parent_id)
    old_answer_parts = _dict_list(answer.get("parts"))
    old_answer_map = _parts_by_canonical_id(
        old_answer_parts,
        parent_id,
    )
    new_parts: list[dict[str, Any]] = []
    new_answers: list[dict[str, Any]] = []
    for index, (item, part_id, score) in enumerate(zip(command.parts, part_ids, scores)):
        old_part = old_part_map.get(part_id) or (old_parts[index] if index < len(old_parts) else {})
        old_answer = old_answer_map.get(part_id) or (
            old_answer_parts[index] if index < len(old_answer_parts) else {}
        )
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
    question["max_score"] = sum(scores)
    answer["parts"] = new_answers
    _append_warning(payload, f"教师手动编辑了 {command.question_id} 的评分单元结构，AI 二次完善时必须保留这些 part_id。")


def _apply_replace_question_structure(
    payload: dict[str, Any],
    command: ReplaceQuestionStructureCommand,
) -> None:
    if not command.parts:
        raise ConfigEditorValidationError(
            (_issue("empty_parts", "commands.parts", "A solution question must keep at least one sub-question."),)
        )
    question = _question_by_id(payload, "rubric", command.question_id)
    if question is None:
        raise ConfigEditorValidationError(
            (_issue("unknown_question_id", "commands.question_id", "The question does not exist."),)
        )
    if str(question.get("question_type") or "") not in _SOLUTION_TYPES:
        raise ConfigEditorValidationError(
            (_issue("question_type_not_solution", "commands.question_id", "Only solution questions support nested scoring steps."),)
        )
    parent_id = canonical_parent_id(command.question_id)
    if parent_id is None:
        raise ConfigEditorValidationError(
            (_issue("invalid_question_id", "commands.question_id", "Question IDs must use the Qn format."),)
        )

    raw_part_ids = [str(item.part_id).strip() for item in command.parts]
    part_ids = [
        _canonical_editor_part_id(raw_id, parent_id, part_count=len(command.parts))
        for raw_id in raw_part_ids
    ]
    if any(part_id is None for part_id in part_ids) or len(set(part_ids)) != len(part_ids):
        raise ConfigEditorValidationError(
            (_issue("invalid_part_id", "commands.parts.part_id", "Sub-question identities must be unique."),)
        )
    for part in command.parts:
        if not part.steps:
            raise ConfigEditorValidationError(
                (_issue("empty_steps", "commands.parts.steps", "Each sub-question must keep at least one scoring step."),)
            )
        step_ids = [str(step.step_id).strip() for step in part.steps]
        if any(not step_id for step_id in step_ids) or len(set(step_ids)) != len(step_ids):
            raise ConfigEditorValidationError(
                (_issue("invalid_step_id", "commands.parts.steps.step_id", "Scoring-step identities must be unique within a sub-question."),)
            )
        if any(not str(step.core_goal).strip() for step in part.steps):
            raise ConfigEditorValidationError(
                (_issue("missing_core_goal", "commands.parts.steps.core_goal", "Each scoring step needs a scoring target."),)
            )

    scores = [
        _valid_score(step.score, None)
        for part in command.parts
        for step in part.steps
    ]

    answer = _ensure_answer_question(payload, command.question_id)
    old_parts = _dict_list(question.get("parts"))
    old_part_map = _parts_by_canonical_id(old_parts, parent_id)
    old_answer_parts = _dict_list(answer.get("parts"))
    old_answer_map = _parts_by_canonical_id(old_answer_parts, parent_id)
    new_parts: list[dict[str, Any]] = []
    new_answers: list[dict[str, Any]] = []
    score_index = 0
    for part_index, (part_input, maybe_part_id) in enumerate(zip(command.parts, part_ids)):
        part_id = str(maybe_part_id)
        old_part = old_part_map.get(part_id) or {}
        old_steps = _dict_list(old_part.get("steps"))
        old_steps_by_id = {str(step.get("step_id") or ""): step for step in old_steps}
        new_steps: list[dict[str, Any]] = []
        for step_index, step_input in enumerate(part_input.steps):
            step_id = str(step_input.step_id).strip()
            old_step = old_steps_by_id.get(step_id) or {}
            new_step = copy.deepcopy(old_step)
            new_step.update(
                {
                    "step_id": step_id,
                    "step_score": scores[score_index],
                    "core_goal": str(step_input.core_goal).strip(),
                    "required_elements": copy.deepcopy(old_step.get("required_elements"))
                    if isinstance(old_step.get("required_elements"), list)
                    else ["完成该步骤的关键过程或结论"],
                    "deduction_rules": copy.deepcopy(old_step.get("deduction_rules"))
                    if isinstance(old_step.get("deduction_rules"), list)
                    else [],
                    "allow_alternative_methods": bool(old_step.get("allow_alternative_methods", True)),
                }
            )
            score_index += 1
            new_steps.append(new_step)
        part_score = sum(step["step_score"] for step in new_steps)
        new_part = copy.deepcopy(old_part)
        new_part.update(
            {
                "part_id": part_id,
                "part_score": part_score,
                "response_mode": "process_required",
                "steps": new_steps,
                "presentation_rules": copy.deepcopy(old_part.get("presentation_rules"))
                if isinstance(old_part.get("presentation_rules"), list)
                else [],
            }
        )
        old_answer = old_answer_map.get(part_id) or (
            old_answer_parts[part_index] if part_index < len(old_answer_parts) else {}
        )
        new_answer = copy.deepcopy(old_answer)
        new_answer.update(
            {
                "part_id": part_id,
                "answer": str(old_answer.get("answer") or ""),
                "analysis": str(old_answer.get("analysis") or "教师手动调整了解答题结构。"),
                "step_milestones": [step["core_goal"] for step in new_steps],
            }
        )
        new_parts.append(new_part)
        new_answers.append(new_answer)
    question["parts"] = new_parts
    question["max_score"] = sum(scores)
    answer["parts"] = new_answers
    _append_warning(payload, f"教师手动调整了 {command.question_id} 的小问和步骤点结构。")


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
                if raw_answer_only is None and "answer_only_max_score" in part:
                    continue
                if raw_answer_only is None:
                    mode = str(part.get("response_mode") or "process_required")
                    raw_answer_only = (
                        part_score
                        if mode in {"exact_objective", "short_answer_points", "visual_construction"}
                        else 1.0
                    )
                answer_only_total += raw_answer_only
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
            for rubric_part in parts:
                answer_part = _answer_part(answer, str(rubric_part.get("part_id")), len(parts))
                if answer_part is None:
                    continue
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
        all_exact = True
        parts = _dict_list(question.get("parts"))
        for part in parts:
            mode = str(part.get("response_mode") or "exact_objective")
            if mode not in {"exact_objective", "short_answer_points"}:
                mode = "exact_objective"
            part["response_mode"] = mode
            part["require_final_answer"] = False
            part["answer_only_max_score"] = _number(part.get("part_score"), 0.0)
            part["presentation_rules"] = []
            if mode != "exact_objective":
                all_exact = False
            if answer is not None and mode == "exact_objective":
                answer_part = _answer_part(answer, str(part.get("part_id")), len(parts))
                if answer_part is not None:
                    answer_part["partial_credit"] = False
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
    issues.extend(_answer_identity_issues(payload, raw_questions))
    return issues


def _answer_identity_issues(
    payload: dict[str, Any],
    rubric_questions: list[Any],
) -> list[ConfigEditorIssue]:
    answer_key = payload.get("answer_key")
    if not isinstance(answer_key, dict):
        return [_issue("invalid_answer_key", "answer_key", "Answer key must be an object.")]
    raw_answers = answer_key.get("questions")
    if not isinstance(raw_answers, list):
        return [_issue("invalid_answer_questions", "answer_key.questions", "Answer questions must be a list.")]

    issues: list[ConfigEditorIssue] = []
    answers_by_id: dict[str, list[dict[str, Any]]] = {}
    for answer in raw_answers:
        if not isinstance(answer, dict):
            issues.append(_issue("invalid_answer_question", "answer_key.questions", "Each answer question must be an object."))
            continue
        question_id = str(answer.get("question_id") or "").strip()
        if not question_id:
            issues.append(
                _issue(
                    "missing_answer_question_id",
                    "answer_key.questions.question_id",
                    "Answer question IDs cannot be empty.",
                )
            )
            continue
        matches = answers_by_id.setdefault(question_id, [])
        if matches:
            issues.append(
                _issue(
                    "duplicate_answer_question_id",
                    "answer_key.questions.question_id",
                    "Answer question IDs must be unique.",
                )
            )
        matches.append(answer)
        part_ids: set[str] = set()
        raw_parts = answer.get("parts")
        if raw_parts is None:
            raw_parts = []
        if not isinstance(raw_parts, list):
            issues.append(_issue("invalid_answer_parts", "answer_key.questions.parts", "Answer parts must be a list."))
            continue
        for part in raw_parts:
            if not isinstance(part, dict):
                issues.append(_issue("invalid_answer_part", "answer_key.questions.parts", "Each answer part must be an object."))
                continue
            part_id = str(part.get("part_id") or "").strip()
            if not part_id:
                issues.append(
                    _issue(
                        "missing_answer_part_id",
                        "answer_key.questions.parts.part_id",
                        "Answer part IDs cannot be empty.",
                    )
                )
                continue
            if part_id in part_ids:
                issues.append(
                    _issue(
                        "duplicate_answer_part_id",
                        "answer_key.questions.parts.part_id",
                        "Answer part IDs must be unique.",
                    )
                )
            part_ids.add(part_id)

    for rubric_question in rubric_questions:
        if not isinstance(rubric_question, dict):
            continue
        question_id = str(rubric_question.get("question_id") or "").strip()
        if not question_id:
            continue
        matches = answers_by_id.get(question_id, [])
        if not matches:
            issues.append(
                _issue(
                    "missing_answer_question_id",
                    "answer_key.questions.question_id",
                    "Each rubric question needs one matching answer question.",
                )
            )
            continue
        if len(matches) != 1:
            continue
        rubric_parts = _dict_list(rubric_question.get("parts"))
        if not rubric_parts:
            continue
        answer_question = matches[0]
        answer_parts = _dict_list(answer_question.get("parts"))
        legacy_whole = len(rubric_parts) == 1 and (
            not answer_parts
            or (
                len(answer_parts) == 1
                and str(answer_parts[0].get("part_id") or "") == question_id
            )
        )
        for rubric_part in rubric_parts:
            part_id = str(rubric_part.get("part_id") or "").strip()
            if not part_id:
                continue
            if any(str(answer_part.get("part_id") or "") == part_id for answer_part in answer_parts):
                continue
            if legacy_whole:
                continue
            issues.append(
                _issue(
                    "missing_answer_part_id",
                    "answer_key.questions.parts.part_id",
                    "Each rubric part needs one matching answer part.",
                    row_id=_row_id(question_id, part_id, _UNSPLIT_ID),
                )
            )
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
        issue["severity"] = "warning"
        issue["message"] = message.replace("[质量检查-阻断]", "[质量提醒]")
        issues.append(issue)
    return issues


def _score_policy_warnings(payload: dict[str, Any]) -> list[ConfigEditorIssue]:
    from score_policy import MAX_QUESTION_SCORE

    issues: list[ConfigEditorIssue] = []

    def warn(code: str, message: str) -> None:
        issue = _issue(code, "score", message + "；按人工设置保存。")
        issue["severity"] = "warning"
        issues.append(issue)

    questions = _questions(payload, "rubric")
    total = sum(_number(question.get("max_score"), 0.0) for question in questions)
    if not math.isclose(total, 100.0, abs_tol=1e-6):
        warn("manual_total_score", f"当前总分为 {total:g} 分，与自动配分的 100 分不同")
    objective_scores: dict[str, set[float]] = {}
    for question in questions:
        qid = str(question.get("question_id"))
        score = _number(question.get("max_score"), 0.0)
        if score > MAX_QUESTION_SCORE:
            warn("manual_question_score", f"{qid} 共 {score:g} 分，超过自动配分的单题 {MAX_QUESTION_SCORE} 分上限")
        qtype = str(question.get("question_type"))
        if qtype in _OBJECTIVE_TYPES:
            objective_scores.setdefault(qtype, set()).add(score)
        steps = [step for part in _dict_list(question.get("parts")) for step in _dict_list(part.get("steps"))]
        if any((value := _number(step.get("step_score"), 0.0)) < 0 or not value.is_integer() for step in steps):
            warn("manual_step_score", f"{qid} 含小数或负数分值，超出自动配分的非负整数规则")
    if any(len(scores) > 1 for scores in objective_scores.values()):
        warn("manual_objective_scores", "同类客观题的分值不完全相同")
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


def _parts_by_canonical_id(
    parts: list[dict[str, Any]],
    parent_id: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for part in parts:
        canonical = _canonical_editor_part_id(
            part.get("part_id"),
            parent_id,
            part_count=len(parts),
        )
        if canonical is None:
            continue
        result.setdefault(canonical, part)
    return result


def _canonical_editor_part_id(
    raw_part_id: object,
    parent_id: str,
    *,
    part_count: int,
) -> str | None:
    coordinates = question_id_coordinates(
        raw_part_id,
        parent_id=parent_id,
    )
    if coordinates is None or coordinates[0] != int(parent_id[1:]):
        return None
    part_number = coordinates[1]
    if part_count == 1:
        return parent_id if part_number in (None, 1) else None
    if part_number is None:
        return None
    return canonical_part_id(parent_id, part_number)


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


def _answer_part(
    answer_question: dict[str, Any],
    part_id: str,
    rubric_part_count: int,
) -> dict[str, Any] | None:
    parts = _dict_list(answer_question.get("parts"))
    exact = next((item for item in parts if str(item.get("part_id")) == part_id), None)
    if exact is not None:
        return exact
    question_id = str(answer_question.get("question_id") or "")
    if rubric_part_count == 1 and not parts:
        return answer_question
    if rubric_part_count == 1 and len(parts) == 1 and str(parts[0].get("part_id") or "") == question_id:
        return parts[0]
    return None


def _visible_identity_matches(record: dict[str, Any], row: ConfigEditorRow) -> bool:
    projected = editor_row_to_streamlit_dict(row)
    for key in ("题号", "评分单元", "评分点", "题型", "作答匹配规则"):
        if key not in record:
            return False
        if str(record.get(key) or "").strip() != str(projected.get(key) or "").strip():
            return False
    return True


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
    if isinstance(value, bool) or score is None:
        raise ConfigEditorValidationError(
            (_issue("invalid_score", "score", "请填写有效数字分值。", row_id=row_id),)
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


def _deduction_policy_texts(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return _split_text(value.replace("\r\n", "\n").replace("\n", ";"))
    if not isinstance(value, (list, tuple)):
        return ()
    texts: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = next(
                (
                    str(item.get(key) or "").strip()
                    for key in (
                        "description",
                        "rule",
                        "issue",
                        "deduction",
                        "condition",
                    )
                    if str(item.get(key) or "").strip()
                ),
                "",
            )
        else:
            text = str(item).strip()
        if text:
            texts.append(text)
    return tuple(dict.fromkeys(texts))


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
                or _DEFAULT_FINAL_ANSWER_RULE,
                "max_deduction": 1,
            }
        )


def _sync_solution_policy(question: dict[str, Any]) -> None:
    max_score = _number(question.get("max_score"), 0.0)
    explicit_null = (
        "answer_only_max_score" in question
        and question.get("answer_only_max_score") is None
    )
    effective_answer_only = _number(question.get("answer_only_max_score"), 0.0)
    answer_only: float | None = None if explicit_null else effective_answer_only
    require_final = bool(question.get("require_final_answer"))
    question["require_final_answer"] = require_final
    question["answer_only_max_score"] = answer_only
    question["_manual_solution_rules"] = True
    question["answer_presentation_policy"] = {
        "require_final_answer": require_final,
        "answer_only_max_score": answer_only,
        "note": "教师在界面中手动确认的过程/写答规则。",
    }
    policies = question.setdefault("deduction_policy", [])
    if not isinstance(policies, list):
        policies = []
        question["deduction_policy"] = policies
    answer_only_text = (
        str(int(effective_answer_only))
        if effective_answer_only.is_integer()
        else str(effective_answer_only)
    )
    _upsert_policy(
        policies,
        {
            "policy_id": "answer_only_process_missing",
            "issue": f"只写最终答案但没有有效过程，最多给 {answer_only_text} 分，主要过程分不得给分",
            "max_deduction": max(0.0, max_score - effective_answer_only),
            "severity": "major",
        },
    )
    _upsert_policy(
        policies,
        {
            "policy_id": "core_process_missing",
            "issue": "关键过程、证明义务或推理链缺失，应扣除对应过程分",
            "max_deduction": max_score,
            "severity": "fatal",
        },
    )


def _upsert_policy(policies: list[Any], policy: dict[str, Any]) -> None:
    policy_id = str(policy["policy_id"])
    for index, existing in enumerate(policies):
        if isinstance(existing, dict) and str(existing.get("policy_id") or "") == policy_id:
            policies[index] = policy
            return
    policies.append(policy)


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
    "ManualQuestionPartInput",
    "ManualStepInput",
    "ReplaceQuestionStructureCommand",
    "ReplaceScoringUnitsCommand",
    "SplitScoringUnitCommand",
    "apply_config_editor_changes",
    "collect_config_editor_issues",
    "editor_part_ids",
    "editor_identity_signature",
    "editor_row_to_streamlit_dict",
    "project_config_editor",
    "streamlit_dataframe_to_editor_edits",
    "validate_config_editor_candidate",
]
