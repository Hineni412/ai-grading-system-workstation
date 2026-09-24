from __future__ import annotations

import ast
import copy
import math
import re
from typing import Any

from answer_normalizer import complete_answer_set_values
from equivalence_engine import merge_equivalent_forms
from question_id_contract import canonicalize_grading_config_payload
from score_policy import (
    MAX_QUESTION_SCORE,
    OBJECTIVE_TYPES,
    _normalize_type,
    enforce_integer_scores_by_type,
)
from question_id_contract import canonical_parent_id

from .contract import (
    attach_structure_repairs,
    canonicalize_new_generated_structure_ids,
    is_simple_objective_question,
)

_GRADING_CONFIG_KNOWLEDGE_KEYS = frozenset(
    {"knowledge_id", "knowledge_ids", "knowledge_name", "knowledge_points"}
)
_LEGACY_KNOWLEDGE_ALIASES = frozenset(
    {"knowledge", "knowledge_text", "knowledge_label"}
)


def strip_generated_config_knowledge_fields(payload: dict[str, Any]) -> None:
    """Remove scoring-rubric knowledge metadata from a newly generated payload."""

    def strip(value: Any) -> None:
        if isinstance(value, dict):
            for key in tuple(value):
                if str(key) in (
                    _GRADING_CONFIG_KNOWLEDGE_KEYS | _LEGACY_KNOWLEDGE_ALIASES
                ):
                    value.pop(key, None)
                    continue
                strip(value[key])
        elif isinstance(value, list):
            for item in value:
                strip(item)

    if isinstance(payload, dict):
        strip(payload)


def _has_legacy_knowledge_metadata(question: dict[str, Any]) -> bool:
    keys = _GRADING_CONFIG_KNOWLEDGE_KEYS | _LEGACY_KNOWLEDGE_ALIASES
    if any(key in question for key in keys):
        return True
    parts = question.get("parts")
    return isinstance(parts, list) and any(
        isinstance(part, dict) and any(key in part for key in keys)
        for part in parts
    )


def _looks_like_serialized_answer_list(value: Any) -> bool:
    return bool(re.fullmatch(r"\s*\[[\s\S]*\]\s*", str(value or "")))


def _serialized_answer_values(value: Any) -> list[str] | None:
    text = str(value or "").strip()
    if not _looks_like_serialized_answer_list(text):
        return None
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(parsed, (list, tuple)) or not parsed:
        return None
    values = [str(item).strip() for item in parsed if str(item).strip()]
    return values or None


def _looks_like_serialized_knowledge_sequence(value: Any) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(?:\[[\s\S]*\]|\([\s\S]*\))\s*",
            str(value or ""),
        )
    )

def _normalize_serialized_answer_list(value: Any) -> str:
    text = str(value or "").strip()
    values = _serialized_answer_values(text)
    return "、".join(values) if values else text

def normalize_generated_config_schema(payload: dict[str, Any]) -> None:
    """Convert common LLM shorthand fields into the strict internal schema."""
    if not isinstance(payload, dict):
        return

    rubric = payload.setdefault("rubric", {})
    answer_key = payload.setdefault("answer_key", {})
    payload.setdefault("meta", {}).setdefault("warnings", [])
    if not isinstance(rubric, dict) or not isinstance(answer_key, dict):
        return

    rubric_questions = rubric.setdefault("questions", [])
    answer_questions = answer_key.setdefault("questions", [])
    if not isinstance(rubric_questions, list):
        rubric["questions"] = []
        rubric_questions = rubric["questions"]
    if not isinstance(answer_questions, list):
        answer_key["questions"] = []
        answer_questions = answer_key["questions"]

    for answer in answer_questions:
        if isinstance(answer, dict):
            answer["question_id"] = _canonical_question_id(
                answer.get("question_id")
                or answer.get("id")
                or answer.get("number")
                or answer.get("棰樺彿")
                or "",
                "",
            )
            _coerce_answer_item_aliases(answer)

    answer_map = {
        _canonical_question_id(q.get("question_id") or q.get("id") or q.get("number") or "", ""): q
        for q in answer_questions
        if isinstance(q, dict)
    }

    for idx, question in enumerate(rubric_questions, start=1):
        if not isinstance(question, dict):
            continue
        qid = _canonical_question_id(
            question.get("question_id")
            or question.get("id")
            or question.get("number")
            or f"Q{idx}",
            f"Q{idx}",
        )
        question["question_id"] = qid
        has_legacy_knowledge = _has_legacy_knowledge_metadata(question)
        if has_legacy_knowledge:
            _promote_nested_question_knowledge(question)
        raw_question_type = (
            question.get("question_type")
            or question.get("type")
        )
        if not str(raw_question_type or "").strip():
            raw_question_type = _infer_missing_question_type_from_parts(question)
        qtype = _normalize_question_type(raw_question_type)
        question["question_type"] = qtype
        question["max_score"] = _safe_float(
            question.get("max_score")
            or question.get("score")
            or question.get("points"),
            0.0,
        )
        if has_legacy_knowledge:
            question["knowledge_id"] = (
                question.get("knowledge_id")
                or question.get("knowledge")
                or "UNKNOWN"
            )
            question["knowledge_name"] = (
                question.get("knowledge_name")
                or question.get("knowledge_text")
                or question.get("knowledge_label")
                or question.get("knowledge")
                or ""
            )
            _normalize_question_knowledge_fields(question)
        question["stem_summary"] = str(question.get("stem_summary") or question.get("棰樺共鎽樿") or "").strip()
        question["grading_mode"] = str(
            question.get("grading_mode")
            or ("deductive_obligation" if qtype in {"proof", "calculation", "comprehensive"} else "direct_answer")
        )

        answer_item = answer_map.get(qid)
        if not isinstance(answer_item, dict):
            answer_item = {"question_id": qid}
            answer_questions.append(answer_item)
            answer_map[qid] = answer_item
        _normalize_answer_item(answer_item, question)
        if (
            not bool(question.get("question_type_confirmed"))
            and _should_treat_as_direct_answer_question(qtype, question, answer_item)
        ):
            qtype = "fill_blank"
            question["question_type"] = qtype
            question["grading_mode"] = "direct_answer"
        _augment_answer_equivalences(answer_item, qtype)
        _normalize_rubric_question(question, answer_item)
        _align_answer_parts_to_rubric_parts(question, answer_item)
        _align_step_required_elements_with_answer_values(question, answer_item)
        _enforce_objective_question_rules(question, answer_item)
        _ensure_solution_hard_rules(question)

    rubric["total_score"] = _safe_float(
        rubric.get("total_score"),
        sum(_safe_float(q.get("max_score"), 0.0) for q in rubric_questions if isinstance(q, dict)),
    )


def normalize_new_generated_config_payload(payload: dict[str, Any]) -> None:
    """Normalize a new AI result without carrying scoring-side knowledge tags."""
    if not isinstance(payload, dict):
        return
    strip_generated_config_knowledge_fields(payload)
    normalize_generated_config_schema(payload)
    rubric = payload.get("rubric")
    questions = rubric.get("questions") if isinstance(rubric, dict) else None
    for question in questions or []:
        if isinstance(question, dict):
            # Only local teacher decisions may later change this to true.
            question["question_type_confirmed"] = False
    repairs = canonicalize_new_generated_structure_ids(payload)
    canonical = canonicalize_grading_config_payload(payload)
    payload.clear()
    payload.update(canonical)
    attach_structure_repairs(payload, repairs)
    # Future schema repairs must not reintroduce question-bank-owned fields.
    strip_generated_config_knowledge_fields(payload)


def _canonical_question_id(value: Any, fallback: str = "") -> str:
    raw = str(value or "").strip()
    if not raw:
        raw = str(fallback or "").strip()
    return canonical_parent_id(raw) or raw

def _safe_knowledge_sequence(value: Any) -> list[str] | None:
    candidate = value
    if isinstance(value, str):
        text = value.strip()
        if not _looks_like_serialized_knowledge_sequence(text):
            return None
        try:
            candidate = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            return None
    if not isinstance(candidate, (list, tuple)) or not candidate:
        return None

    values: list[str] = []
    for item in candidate:
        if isinstance(item, bool) or not isinstance(item, (str, int, float)):
            return None
        text = str(item).strip()
        if not text:
            return None
        values.append(text)
    return values

def _redundant_knowledge_fields_match_pairs(
    question: dict[str, Any],
    paired_ids: list[str],
    paired_names: list[str],
) -> bool:
    raw_ids = question.get("knowledge_ids")
    if raw_ids not in (None, "", []):
        ids_match = _safe_knowledge_sequence(raw_ids) == paired_ids
        if (
            not ids_match
            and isinstance(raw_ids, (list, tuple))
            and len(raw_ids) == 1
        ):
            ids_match = _safe_knowledge_sequence(raw_ids[0]) == paired_ids
        if not ids_match:
            return False

    raw_points = question.get("knowledge_points")
    if raw_points in (None, "", []):
        return True
    if not isinstance(raw_points, (list, tuple)):
        return False

    expected_names = dict(zip(paired_ids, paired_names))
    canonical_ids: set[str] = set()
    for point in raw_points:
        if isinstance(point, dict):
            raw_id = point.get("knowledge_id") or point.get("id") or ""
            raw_name = (
                point.get("knowledge_name")
                or point.get("name")
                or point.get("label")
                or ""
            )
        else:
            raw_id = point
            raw_name = ""

        point_ids = _safe_knowledge_sequence(raw_id)
        if point_ids is not None:
            if point_ids != paired_ids:
                return False
            if raw_name and _safe_knowledge_sequence(raw_name) != paired_names:
                return False
            continue
        if _looks_like_serialized_knowledge_sequence(raw_id):
            return False
        if raw_name and _looks_like_serialized_knowledge_sequence(raw_name):
            return False

        kid = str(raw_id or "").strip()
        name = str(raw_name or "").strip()
        if not kid or kid not in expected_names:
            return False
        if name and name != expected_names[kid]:
            return False
        canonical_ids.add(kid)

    return not canonical_ids or canonical_ids == set(paired_ids)

def _normalize_question_knowledge_fields(question: dict[str, Any]) -> None:
    raw_primary_id = question.get("knowledge_id") or "UNKNOWN"
    raw_primary_name = question.get("knowledge_name") or ""
    paired_ids = _safe_knowledge_sequence(raw_primary_id)
    paired_names = _safe_knowledge_sequence(raw_primary_name)
    if (
        paired_ids is not None
        and paired_names is not None
        and len(paired_ids) == len(paired_names)
        and len(set(paired_ids)) == len(paired_ids)
        and _redundant_knowledge_fields_match_pairs(
            question,
            paired_ids,
            paired_names,
        )
    ):
        paired_points = [
            {"knowledge_id": kid, "knowledge_name": name}
            for kid, name in zip(paired_ids, paired_names)
        ]
        question["knowledge_points"] = paired_points
        question["knowledge_ids"] = list(paired_ids)
        question["knowledge_id"] = paired_ids[0]
        question["knowledge_name"] = paired_names[0]
        return

    raw_points = question.get("knowledge_points")
    points: list[dict[str, str]] = []
    if isinstance(raw_points, list):
        for item in raw_points:
            if isinstance(item, dict):
                kid = str(item.get("knowledge_id") or item.get("id") or "").strip()
                name = str(item.get("knowledge_name") or item.get("name") or item.get("label") or "").strip()
            else:
                kid = str(item or "").strip()
                name = ""
            if kid:
                points.append({"knowledge_id": kid, "knowledge_name": name})
    elif isinstance(raw_points, str) and raw_points.strip():
        points.append(
            {
                "knowledge_id": str(question.get("knowledge_id") or question.get("knowledge_name") or "UNKNOWN").strip(),
                "knowledge_name": raw_points.strip(),
            }
        )

    raw_ids = question.get("knowledge_ids")
    if isinstance(raw_ids, list):
        for raw_id in raw_ids:
            kid = str(raw_id or "").strip()
            if kid:
                points.append({"knowledge_id": kid, "knowledge_name": ""})

    primary_id = str(raw_primary_id).strip()
    primary_name = str(raw_primary_name).strip()
    if primary_id:
        points.append({"knowledge_id": primary_id, "knowledge_name": primary_name})

    useful_points = [point for point in points if point["knowledge_id"] not in {"", "UNKNOWN"}]
    if useful_points:
        points = useful_points

    normalized: list[dict[str, str]] = []
    for point in points:
        kid = point["knowledge_id"]
        name = point.get("knowledge_name", "")
        if not kid:
            continue
        same_id = [
            index for index, existing in enumerate(normalized)
            if existing["knowledge_id"] == kid
        ]
        if any(normalized[index].get("knowledge_name", "") == name for index in same_id):
            continue
        if not name and same_id:
            continue
        empty_index = next(
            (index for index in same_id if not normalized[index].get("knowledge_name")),
            None,
        )
        if name and empty_index is not None:
            normalized[empty_index]["knowledge_name"] = name
        else:
            normalized.append({"knowledge_id": kid, "knowledge_name": name})

    if not normalized:
        normalized = [{"knowledge_id": "UNKNOWN", "knowledge_name": ""}]

    question["knowledge_points"] = normalized
    question["knowledge_ids"] = list(dict.fromkeys(point["knowledge_id"] for point in normalized))
    question["knowledge_id"] = normalized[0]["knowledge_id"]
    if normalized[0].get("knowledge_name"):
        question["knowledge_name"] = normalized[0]["knowledge_name"]

def normalize_generated_config_knowledge_fields(payload: dict[str, Any]) -> bool:
    """Normalize only rubric knowledge metadata and report whether it changed."""
    rubric = payload.get("rubric") if isinstance(payload, dict) else None
    questions = rubric.get("questions") if isinstance(rubric, dict) else None
    if not isinstance(questions, list):
        return False

    changed = False
    tracked = ("knowledge_id", "knowledge_name", "knowledge_ids", "knowledge_points")
    for question in questions:
        if not isinstance(question, dict):
            continue
        raw_points = question.get("knowledge_points")
        point_ids = [
            str(point.get("knowledge_id") or point.get("id") or "").strip()
            for point in raw_points
            if isinstance(point, dict)
        ] if isinstance(raw_points, list) else []
        sequence_values: list[Any] = [
            question.get("knowledge_id"),
            question.get("knowledge_name"),
        ]
        raw_ids = question.get("knowledge_ids")
        if isinstance(raw_ids, list):
            sequence_values.extend(raw_ids)
        elif raw_ids is not None:
            sequence_values.append(raw_ids)
        if isinstance(raw_points, list):
            for point in raw_points:
                if isinstance(point, dict):
                    sequence_values.extend(
                        (point.get("knowledge_id"), point.get("knowledge_name"))
                    )
                else:
                    sequence_values.append(point)
        needs_compatibility = (
            any(
                isinstance(value, (list, tuple))
                or (
                    isinstance(value, str)
                    and _looks_like_serialized_knowledge_sequence(value)
                )
                for value in sequence_values
            )
            or len([kid for kid in point_ids if kid])
            != len(set(kid for kid in point_ids if kid))
        )
        if not needs_compatibility:
            continue
        before = {key: copy.deepcopy(question.get(key)) for key in tracked}
        _normalize_question_knowledge_fields(question)
        after = {key: question.get(key) for key in tracked}
        changed = changed or before != after
    return changed

def _promote_nested_question_knowledge(question: dict[str, Any]) -> None:
    parts = question.get("parts")
    if not isinstance(parts, list):
        return

    current_id = str(question.get("knowledge_id") or "").strip()
    current_name = str(question.get("knowledge_name") or "").strip()
    collected: list[dict[str, str]] = []
    raw_top_points = question.get("knowledge_points")
    if isinstance(raw_top_points, list):
        collected.extend(item for item in raw_top_points if isinstance(item, dict))

    for part in parts:
        if not isinstance(part, dict):
            continue
        part_id = str(part.get("knowledge_id") or part.get("knowledge_name") or "").strip()
        part_name = str(part.get("knowledge_name") or "").strip()
        if part_id and part_name:
            collected.append({"knowledge_id": part_id, "knowledge_name": part_name})
        raw_points = part.get("knowledge_points")
        if isinstance(raw_points, str) and raw_points.strip():
            collected.append(
                {
                    "knowledge_id": part_id or part_name or "DETAIL",
                    "knowledge_name": raw_points.strip(),
                }
            )
        elif isinstance(raw_points, list):
            for raw_point in raw_points:
                if isinstance(raw_point, dict):
                    collected.append(raw_point)
                elif str(raw_point or "").strip():
                    collected.append(
                        {
                            "knowledge_id": part_id or part_name or "DETAIL",
                            "knowledge_name": str(raw_point).strip(),
                        }
                    )

        if (not current_name or current_id in {"", "UNKNOWN"}) and part_name:
            current_name = part_name
            current_id = part_id or part_name

    if current_name:
        question["knowledge_name"] = current_name
    if current_id:
        question["knowledge_id"] = current_id
    if collected:
        question["knowledge_points"] = collected

def force_payload_total_score(payload: dict[str, Any], target_total: float = 100.0) -> None:
    rubric = payload.get("rubric") if isinstance(payload, dict) else None
    if not isinstance(rubric, dict):
        return
    questions = rubric.get("questions")
    if not isinstance(questions, list) or not questions:
        rubric["total_score"] = target_total
        return

    # 原卷分值不进入本场配分：先按每题有效评分步骤数播种本地权重，
    # 再由整数分配器在题型约束下把总分落到每道题。
    for question in questions:
        if isinstance(question, dict):
            _seed_question_scores_from_steps(question)
    enforce_integer_scores_by_type(
        questions,
        target_total=int(target_total),
        max_question_score=MAX_QUESTION_SCORE,
    )
    # Second pass: fix any +-1 rounding drift produced by integer allocation
    _fix_question_sum(questions, target_total)
    for question in questions:
        if isinstance(question, dict):
            _ensure_solution_hard_rules(question)
    rubric["total_score"] = target_total
    payload.setdefault("meta", {}).setdefault("warnings", [])


def _seed_question_scores_from_steps(question: dict[str, Any]) -> None:
    """Seed local score weights from the rubric's own step structure.

    分值权重只来自本卷判分结构：每个评分步骤贡献一份权重，
    来源卷面残留的分值前缀不作为分配依据。
    """

    parts = [
        part
        for part in question.get("parts") or []
        if isinstance(part, dict)
    ]
    total_steps = 0
    for part in parts:
        steps = [
            step
            for step in part.get("steps") or []
            if isinstance(step, dict)
        ]
        weight = max(1, len(steps))
        part["part_score"] = float(weight)
        for step in steps:
            step["step_score"] = 1.0
        total_steps += weight
    question["max_score"] = float(max(1, total_steps))

def _scale_question_scores(question: dict[str, Any], ratio: float) -> None:
    new_score = round(_safe_float(question.get("max_score"), 0.0) * ratio, 2)
    _scale_question_to_score(question, new_score)

def _scale_question_to_score(question: dict[str, Any], new_score: float) -> None:
    old_score = _safe_float(question.get("max_score"), 0.0)
    question["max_score"] = round(float(new_score), 2)
    parts = question.get("parts")
    if not isinstance(parts, list) or not parts:
        _normalize_rubric_question(question, {})
        return

    ratio = (float(new_score) / old_score) if old_score > 0 else (1.0 / len(parts))
    for part in parts:
        if not isinstance(part, dict):
            continue
        part["part_score"] = round(_safe_float(part.get("part_score"), 0.0) * ratio, 2)
        steps = part.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict):
                    step["step_score"] = round(_safe_float(step.get("step_score"), 0.0) * ratio, 2)
        _force_step_total(part)
    _force_part_total(question)
    _scale_deduction_policy(question, ratio)

def _scale_deduction_policy(question: dict[str, Any], ratio: float) -> None:
    policies = question.get("deduction_policy")
    if isinstance(policies, list):
        for policy in policies:
            if isinstance(policy, dict) and "max_deduction" in policy:
                policy["max_deduction"] = round(_safe_float(policy.get("max_deduction"), 0.0) * ratio, 2)

def _fix_question_sum(questions: list[Any], target_total: float) -> None:
    valid_questions = [q for q in questions if isinstance(q, dict)]
    if not valid_questions:
        return
    total = sum(_safe_float(q.get("max_score"), 0.0) for q in valid_questions)
    diff = round(target_total - total, 2)
    if abs(diff) > 0.001:
        last = valid_questions[-1]
        last["max_score"] = round(_safe_float(last.get("max_score"), 0.0) + diff, 2)
        _force_part_total(last)

def _normalize_answer_item(answer_item: dict[str, Any], rubric_question: dict[str, Any]) -> None:
    _coerce_answer_item_aliases(answer_item)
    qid = str(rubric_question.get("question_id") or answer_item.get("question_id") or "")
    qtype = str(rubric_question.get("question_type") or "").strip().lower()
    answer_item["question_id"] = qid
    direct_answers = _extract_direct_answer_values(answer_item)
    canonical = (
        answer_item.get("canonical_answer")
        or answer_item.get("answer")
        or answer_item.get("standard_answer")
        or answer_item.get("correct_answer")
        or answer_item.get("绛旀")
        or (direct_answers[0] if direct_answers else "")
        or rubric_question.get("canonical_answer")
        or rubric_question.get("answer")
        or rubric_question.get("correct_answer")
        or ""
    )
    answer_item["canonical_answer"] = str(canonical)
    if bool(answer_item.get("_manual_accepted_forms")):
        answer_item["accepted_forms"] = _string_list(answer_item.get("accepted_forms"))
    else:
        answer_item["accepted_forms"] = _string_list(
            answer_item.get("accepted_forms")
            or answer_item.get("equivalent_answers")
            or answer_item.get("aliases")
            or direct_answers
            or rubric_question.get("accepted_forms")
            or rubric_question.get("equivalent_answers")
            or [canonical]
        )
    answer_item["method_variants"] = _method_variants(
        answer_item.get("method_variants") or rubric_question.get("method_variants") or []
    )
    if not isinstance(answer_item.get("parts"), list) or not answer_item["parts"]:
        answer_item["parts"] = [
            {
                "part_id": qid,
                "answer": str(canonical),
                "analysis": str(answer_item.get("analysis") or rubric_question.get("analysis") or ""),
                "step_milestones": _string_list(answer_item.get("step_milestones") or rubric_question.get("step_milestones")),
            }
        ]
    else:
        for idx, part in enumerate(answer_item["parts"], start=1):
            if not isinstance(part, dict):
                continue
            _coerce_answer_part_aliases(part)
            part_direct_answers = _extract_direct_answer_values(part)
            step_solution_texts = (
                _extract_step_solution_texts(part)
                if qtype in {"calculation", "proof", "comprehensive"}
                else []
            )
            if (
                not part_direct_answers
                and qtype
                in {
                    "choice",
                    "fill_blank",
                    "judgement",
                    "true_false",
                    "direct_answer",
                }
            ):
                part_direct_answers = _extract_step_answer_values(part)
            part["answer_values"] = _string_list(part_direct_answers)
            part.setdefault("part_id", qid if len(answer_item["parts"]) == 1 else f"{qid}({idx})")
            part_answer = (
                part.get("answer")
                or part.get("canonical_answer")
                or part.get("standard_answer")
                or ("；".join(part["answer_values"]) if part["answer_values"] else "")
                or ("\n".join(step_solution_texts) if step_solution_texts else "")
                or canonical
            )
            part["answer"] = str(part_answer)
            if len(part["answer_values"]) == 1 and not part.get("accepted_forms"):
                part["accepted_forms"] = _string_list(part_direct_answers)
            part.setdefault("analysis", "")
            existing_milestones = _string_list(part.get("step_milestones"))
            part["step_milestones"] = list(
                dict.fromkeys([*existing_milestones, *step_solution_texts])
            )

    if not str(answer_item.get("canonical_answer") or "").strip():
        if qtype in {"choice", "fill_blank", "judgement", "true_false", "direct_answer"}:
            first_part_answer = next(
                (
                    str(part.get("answer") or "").strip()
                    for part in answer_item.get("parts", [])
                    if isinstance(part, dict) and str(part.get("answer") or "").strip()
                ),
                "",
            )
            if first_part_answer:
                answer_item["canonical_answer"] = first_part_answer
                answer_item["accepted_forms"] = _string_list(answer_item.get("accepted_forms")) or [first_part_answer]

def _coerce_answer_item_aliases(answer_item: dict[str, Any]) -> None:
    if not isinstance(answer_item.get("parts"), list) or not answer_item.get("parts"):
        for key in ("answer_parts", "sub_answers", "subquestions"):
            value = answer_item.get(key)
            if isinstance(value, list) and value:
                answer_item["parts"] = value
                break

def _coerce_answer_part_aliases(part: dict[str, Any]) -> None:
    if not str(part.get("answer") or "").strip():
        for key in (
            "answer_content",
            "standard_answer",
            "canonical_answer",
            "correct_answer",
            "final_answer",
        ):
            value = part.get(key)
            if value is not None and str(value).strip():
                part["answer"] = str(value).strip()
                break

def _extract_direct_answer_values(item: dict[str, Any]) -> list[Any]:
    normalized = item.get("answer_values")
    if isinstance(normalized, list) and normalized:
        # answer_values 是归一化写回键；已归一化的 payload 只读它，
        # 避免与 answers/answer_content 等原始别名键重复累计（幂等）。
        unwrapped: list[Any] = []
        for value in normalized:
            if isinstance(value, dict):
                nested = next(
                    (
                        value.get(alias)
                        for alias in ("value", "answer", "answer_content", "standard_answer", "canonical_answer")
                        if value.get(alias) is not None
                    ),
                    None,
                )
                if nested is not None:
                    unwrapped.append(nested)
            elif value is not None:
                unwrapped.append(value)
        if unwrapped:
            return unwrapped
    values: list[Any] = []
    direct = item.get("direct_answer")
    if isinstance(direct, dict):
        for key in ["accepted_forms", "answers", "answer", "value", "values"]:
            raw = direct.get(key)
            if raw is None:
                continue
            if isinstance(raw, list):
                values.extend(raw)
            else:
                values.append(raw)
    elif isinstance(direct, list):
        values.extend(direct)
    elif direct is not None:
        values.append(direct)
    for key in ("answers", "values", "answer_values"):
        raw = item.get(key)
        if isinstance(raw, list):
            for value in raw:
                if isinstance(value, dict):
                    nested = next(
                        (
                            value.get(alias)
                            for alias in ("value", "answer", "answer_content", "standard_answer", "canonical_answer")
                            if value.get(alias) is not None
                        ),
                        None,
                    )
                    if nested is not None:
                        values.append(nested)
                elif value is not None:
                    values.append(value)
        elif raw is not None:
            values.append(raw)
    answer_content = item.get("answer_content")
    if answer_content is not None:
        values.append(answer_content)
    return values

def _extract_step_answer_values(part: dict[str, Any]) -> list[Any]:
    values: list[Any] = []
    steps = part.get("steps")
    if not isinstance(steps, list):
        return values
    for step in steps:
        if not isinstance(step, dict):
            continue
        value = next(
            (
                step.get(alias)
                for alias in (
                    "answer_value",
                    "correct_value",
                    "answer",
                    "standard_answer",
                    "canonical_answer",
                )
                if step.get(alias) is not None
                and str(step.get(alias)).strip()
            ),
            None,
        )
        if value is not None:
            values.append(value)
    return values


def _extract_step_solution_texts(part: dict[str, Any]) -> list[str]:
    values: list[str] = []
    steps = part.get("steps")
    if not isinstance(steps, list):
        return values
    for step in steps:
        if not isinstance(step, dict):
            continue
        value = next(
            (
                str(step.get(alias)).strip()
                for alias in (
                    "step_content",
                    "correct_value",
                    "answer_value",
                    "answer",
                    "standard_answer",
                    "canonical_answer",
                )
                if step.get(alias) is not None
                and str(step.get(alias)).strip()
            ),
            "",
        )
        if value:
            values.append(value)
    return list(dict.fromkeys(values))


def _should_treat_as_direct_answer_question(
    qtype: str,
    question: dict[str, Any],
    answer_item: dict[str, Any],
) -> bool:
    normalized_type = str(qtype or "").strip().lower()
    if normalized_type in {"choice", "fill_blank", "judgement", "true_false"}:
        return False
    mode_text = " ".join(
        str(question.get(key) or "")
        for key in ["grading_mode", "scoring_type", "scoring_policy", "scoring_rule", "description"]
    ).lower()
    has_direct_mode = "direct_answer" in mode_text or "鍏ㄥ鍏ㄩ敊" in mode_text
    has_direct_answer = bool(_extract_direct_answer_values(answer_item) or _string_list(answer_item.get("accepted_forms")))
    has_solution_structure = bool(question.get("proof_obligations")) or any(
        isinstance(part, dict) and len(part.get("steps") or []) > 1
        for part in (question.get("parts") if isinstance(question.get("parts"), list) else [])
    )
    return has_direct_answer and has_direct_mode and not has_solution_structure

def _augment_answer_equivalences(answer_item: dict[str, Any], qtype: str) -> None:
    """Add deterministic local equivalents for objective/short-answer items."""
    if qtype == "choice":
        return

    top_level_manually_edited = bool(answer_item.get("_manual_accepted_forms"))
    parts = answer_item.get("parts")
    subjective_multi_part = (
        qtype in {"proof", "calculation", "comprehensive"}
        and isinstance(parts, list)
        and len([part for part in parts if isinstance(part, dict)]) > 1
    )
    answer_parts = (
        [part for part in parts if isinstance(part, dict)]
        if isinstance(parts, list)
        else []
    )
    serialized_canonical = _serialized_answer_values(
        answer_item.get("canonical_answer")
    )
    part_answers = [
        str(part.get("answer") or "").strip()
        for part in answer_parts
    ]
    if (
        subjective_multi_part
        and serialized_canonical is not None
        and len(serialized_canonical) == len(answer_parts)
        and all(
            answer
            and not _looks_like_serialized_answer_list(answer)
            for answer in part_answers
        )
    ):
        answer_item["canonical_answer"] = "；".join(
            f"（{index}）{answer}"
            for index, answer in enumerate(part_answers, start=1)
        )
    canonical = str(answer_item.get("canonical_answer") or "").strip()
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_answer = str(part.get("answer") or "").strip()
            if part_answer:
                normalized_part_answer = _normalize_serialized_answer_list(part_answer)
                serialized_part_answer = normalized_part_answer != part_answer
                if serialized_part_answer:
                    part["answer"] = normalized_part_answer
                    part_answer = normalized_part_answer
                if not bool(part.get("_manual_accepted_forms")):
                    existing_part_forms = [] if serialized_part_answer else [
                        value
                        for value in _string_list(part.get("accepted_forms"))
                        if not _looks_like_serialized_answer_list(value)
                    ]
                    part["accepted_forms"] = merge_equivalent_forms(existing_part_forms, part_answer, max_forms=16)

    if not top_level_manually_edited:
        existing_top_forms = [] if subjective_multi_part else [
            value
            for value in _string_list(answer_item.get("accepted_forms"))
            if not _looks_like_serialized_answer_list(value)
        ]
        answer_item["accepted_forms"] = merge_equivalent_forms(existing_top_forms, canonical, max_forms=32)

def _record_complete_answer_set_rule(answer_item: dict[str, Any], qtype: str) -> None:
    if qtype != "fill_blank":
        return
    canonical = str(answer_item.get("canonical_answer") or "").strip()
    required_values = complete_answer_set_values(canonical)
    if not required_values:
        return
    rule = {
        "match_mode": "complete_set",
        "required_values": required_values,
        "order_sensitive": False,
        "allow_extra_values": False,
        "partial_credit": False,
    }
    answer_item.update(rule)
    parts = answer_item.get("parts")
    if isinstance(parts, list) and len(parts) == 1 and isinstance(parts[0], dict):
        parts[0].update(rule)

def _sanitize_choice_answer_forms(answer_item: dict[str, Any]) -> None:
    canonical = str(answer_item.get("canonical_answer") or "").strip().upper()
    if not re.fullmatch(r"[A-D]", canonical):
        return
    answer_item["canonical_answer"] = canonical
    answer_item["accepted_forms"] = [canonical]
    parts = answer_item.get("parts")
    if not isinstance(parts, list):
        return
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_answer = str(part.get("answer") or canonical).strip().upper()
        if not re.fullmatch(r"[A-D]", part_answer):
            part_answer = canonical
        part["answer"] = part_answer
        part["accepted_forms"] = [part_answer]

def _normalize_rubric_question(question: dict[str, Any], answer_item: dict[str, Any]) -> None:
    qid = str(question.get("question_id") or "")
    qtype = str(question.get("question_type") or "comprehensive")
    max_score = _safe_float(question.get("max_score"), 0.0)
    _align_solution_parts_with_answer_parts(question, answer_item, qtype, max_score)
    if not isinstance(question.get("parts"), list) or not question["parts"]:
        question["parts"] = [
            {
                "part_id": qid,
                "part_score": max_score,
                "steps": [
                    {
                        "step_id": "S1",
                        "step_score": max_score,
                        "core_goal": _default_core_goal(qtype, answer_item),
                        "required_elements": _default_required_elements(qtype, answer_item),
                        "allow_alternative_methods": qtype not in {"choice"},
                    }
                ],
                "presentation_rules": [],
            }
        ]
    else:
        part_count = len(question["parts"])
        for idx, part in enumerate(question["parts"], start=1):
            if not isinstance(part, dict):
                part = {"part_id": qid if part_count == 1 else f"{qid}({idx})", "answer": str(part)}
                question["parts"][idx - 1] = part
            part_score = _safe_float(
                part.get("part_score")
                or part.get("score")
                or part.get("points"),
                max_score / max(part_count, 1),
            )
            part["part_id"] = str(part.get("part_id") or (qid if part_count == 1 else f"{qid}({idx})"))
            part["part_score"] = part_score
            # part_score is the single canonical full score for a subquestion.
            # A stale nested max_score previously survived whole-paper scoring
            # and could override the visible part_score during grading.
            part.pop("max_score", None)
            step_alias = _best_step_alias(part)
            if not step_alias:
                answer_part = _answer_part_for_rubric(
                    answer_item,
                    part,
                    idx - 1,
                )
                step_alias = _milestone_step_alias(answer_part)
            if step_alias is not part.get("steps"):
                part["steps"] = step_alias

            if not isinstance(part.get("steps"), list) or not part["steps"]:
                part["steps"] = [
                    {
                        "step_id": "S1",
                        "step_score": part_score,
                        "core_goal": _default_core_goal(qtype, answer_item),
                        "required_elements": _default_required_elements(qtype, answer_item),
                        "allow_alternative_methods": qtype not in {"choice"},
                    }
                ]
            else:
                for sidx, step in enumerate(part["steps"], start=1):
                    if not isinstance(step, dict):
                        step = {"core_goal": str(step)}
                        part["steps"][sidx - 1] = step
                    step["step_id"] = str(step.get("step_id") or f"S{sidx}")
                    # Avoid overriding explicit 0.0 with fallback by using explicit None checks
                    raw_score = step.get("step_score")
                    if raw_score is None:
                        raw_score = step.get("score")
                    if raw_score is None:
                        raw_score = step.get("points")

                    step["step_score"] = _safe_float(
                        raw_score,
                        part_score / max(len(part["steps"]), 1),
                    )
                    step["core_goal"] = _specific_step_goal(
                        step,
                        _default_core_goal(qtype, answer_item),
                    )
                    required_elements = _string_list(step.get("required_elements"))
                    if not required_elements:
                        goal = str(step.get("core_goal") or "").strip()
                        step["required_elements"] = (
                            [goal]
                            if goal
                            else _default_required_elements(qtype, answer_item)
                        )
                    else:
                        step["required_elements"] = required_elements
                    step["allow_alternative_methods"] = bool(step.get("allow_alternative_methods", qtype not in {"choice"}))
            if not isinstance(part.get("presentation_rules"), list):
                part["presentation_rules"] = []
            _force_step_total(part)
    _force_part_total(question)

def _first_list_value(node: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = node.get(key)
        if isinstance(value, list) and value:
            return value
    return []

def _best_step_alias(part: dict[str, Any]) -> list[Any]:
    existing = _first_list_value(part, "steps")
    candidates = [
        value
        for key in ("scoring_steps", "criteria", "rubric", "score_points", "points")
        if isinstance((value := part.get(key)), list) and value
    ]
    visual_requirements = _string_list(part.get("visual_requirements"))
    visual_steps = [
        {"core_goal": value, "required_elements": [value]}
        for value in visual_requirements
    ]
    if not existing:
        return next(iter(candidates), visual_steps)
    return existing


def _answer_part_for_rubric(
    answer_item: dict[str, Any],
    rubric_part: dict[str, Any],
    index: int,
) -> dict[str, Any] | None:
    answer_parts = answer_item.get("parts")
    if not isinstance(answer_parts, list):
        return None
    part_id = str(rubric_part.get("part_id") or "").strip()
    for answer_part in answer_parts:
        if (
            isinstance(answer_part, dict)
            and part_id
            and str(answer_part.get("part_id") or "").strip() == part_id
        ):
            return answer_part
    candidate = answer_parts[index] if index < len(answer_parts) else None
    return candidate if isinstance(candidate, dict) else None


def _milestone_step_alias(answer_part: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(answer_part, dict):
        return []
    milestones = answer_part.get("step_milestones")
    if not isinstance(milestones, list) or not milestones:
        return []
    steps: list[dict[str, Any]] = []
    for index, raw in enumerate(milestones, start=1):
        if isinstance(raw, dict):
            goal = _specific_step_goal(raw, "")
            required = _string_list(raw.get("required_elements"))
            if not required:
                required = _string_list(
                    raw.get("observable_evidence")
                    or raw.get("justification")
                    or raw.get("answer_anchor")
                )
            step = {
                "step_id": str(raw.get("step_id") or f"S{index}"),
                "core_goal": goal or str(raw.get("target") or "").strip(),
                "required_elements": required,
                "allow_alternative_methods": bool(
                    raw.get("allow_alternative_methods", True)
                ),
                "deduction_rules": _string_list(raw.get("deduction_rules")),
            }
        else:
            text = str(raw or "").strip()
            step = {
                "step_id": f"S{index}",
                "core_goal": text,
                "required_elements": [text] if text else [],
                "allow_alternative_methods": True,
                "deduction_rules": [],
            }
        if step["core_goal"] and step["required_elements"]:
            steps.append(step)
    return steps

def _specific_step_goal(step: dict[str, Any], default: str) -> str:
    values = [
        str(step.get(key) or "").strip()
        for key in ("core_goal", "goal", "criterion", "description", "step_description", "desc", "title", "requirement")
    ]
    return next((value for value in values if value), default)

def _enforce_objective_question_rules(question: dict[str, Any], answer_item: dict[str, Any]) -> None:
    qtype = str(question.get("question_type") or "").strip().lower()
    if qtype not in {"choice", "fill_blank", "judgement", "true_false", "direct_answer"}:
        return

    if qtype == "choice":
        _sanitize_choice_answer_forms(answer_item)
    _record_complete_answer_set_rule(answer_item, qtype)

    top_answers = _string_list(answer_item.get("accepted_forms"))
    canonical = str(answer_item.get("canonical_answer") or "").strip()
    if canonical and canonical not in top_answers:
        top_answers.insert(0, canonical)
    answer_parts = answer_item.get("parts")
    if not isinstance(answer_parts, list):
        answer_parts = []

    parts = question.get("parts")
    if not isinstance(parts, list):
        return
    mixed_fill = qtype == "fill_blank" and len(parts) > 1 and any(
        isinstance(part, dict)
        and (
            str(part.get("response_mode") or "").strip()
            in {"process_required", "visual_construction"}
            or bool(_string_list(part.get("proof_obligations")))
            or bool(_string_list(part.get("visual_requirements")))
        )
        for part in parts
    )
    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            continue
        if mixed_fill and (
            str(part.get("response_mode") or "").strip()
            in {"process_required", "visual_construction"}
            or bool(_string_list(part.get("proof_obligations")))
            or bool(_string_list(part.get("visual_requirements")))
        ):
            continue
        answer_part = answer_parts[index] if index < len(answer_parts) and isinstance(answer_parts[index], dict) else {}
        independent_answer_values = _string_list(answer_part.get("answer_values"))
        is_independent_fill = (
            qtype == "fill_blank"
            and len(independent_answer_values) > 1
        )
        part["response_mode"] = "short_answer_points" if is_independent_fill else "exact_objective"
        part["presentation_rules"] = []
        part["deduction_policy"] = [
            "仅按标准答案或等价答案判分，不要求书写过程。"
        ]
        part["proof_obligations"] = []
        part["visual_requirements"] = []
        part["require_final_answer"] = False
        part["answer_only_max_score"] = int(round(_safe_float(part.get("part_score"), 0.0)))

        part_answers: list[str] = []
        if answer_part:
            part_answers = _string_list(answer_part.get("accepted_forms"))
            part_answer = str(answer_part.get("answer") or "").strip()
            if part_answer and part_answer not in part_answers:
                part_answers.insert(0, part_answer)
        required_answers = list(dict.fromkeys([*part_answers, *top_answers]))

        steps = part.get("steps")
        if not isinstance(steps, list) or not steps:
            continue
        if not is_independent_fill:
            first_step = next((step for step in steps if isinstance(step, dict)), {})
            point_ids = list(dict.fromkeys(
                point_id for step in steps if isinstance(step, dict)
                for point_id in _string_list(step.get("evidence_point_ids"))
            ))
            if point_ids:
                first_step["evidence_point_ids"] = point_ids
            first_step["step_id"] = str(first_step.get("step_id") or "S1")
            first_step["step_score"] = int(round(_safe_float(part.get("part_score"), 0.0)))
            first_step["core_goal"] = _default_core_goal(qtype, answer_item)
            first_step["required_elements"] = required_answers
            first_step["allow_alternative_methods"] = qtype != "choice"
            first_step["deduction_rules"] = []
            part["steps"] = [first_step]
            continue
        step_scores = _allocate_scores(
            _safe_float(part.get("part_score"), 0.0),
            len(independent_answer_values),
        )
        template_steps = [step for step in steps if isinstance(step, dict)]
        if not template_steps:
            continue
        part["steps"] = []
        for answer_index, (answer_value, step_score) in enumerate(
            zip(independent_answer_values, step_scores),
            start=1,
        ):
            template = (
                template_steps[answer_index - 1]
                if answer_index <= len(template_steps)
                else template_steps[0]
            )
            step = dict(template)
            step["step_id"] = f"S{answer_index}"
            step["step_score"] = step_score
            step["core_goal"] = f"填写第 {answer_index} 个正确或等价答案"
            step["required_elements"] = [answer_value]
            step["allow_alternative_methods"] = True
            step["deduction_rules"] = []
            part["steps"].append(step)
        continue

    if mixed_fill:
        return
    question["require_final_answer"] = False
    question["answer_only_max_score"] = int(round(_safe_float(question.get("max_score"), 0.0)))
    question["deduction_policy"] = [
        "仅按标准答案或等价答案判分，不要求书写过程。"
    ]
    question["answer_presentation_policy"] = {
        "require_final_answer": False,
        "answer_only_max_score": question["answer_only_max_score"],
        "note": "客观题仅按标准答案或等价答案判分，不要求过程证据。",
    }

def _align_answer_parts_to_rubric_parts(
    question: dict[str, Any],
    answer_item: dict[str, Any],
) -> None:
    rubric_parts = question.get("parts")
    answer_parts = answer_item.get("parts")
    if not isinstance(rubric_parts, list) or not isinstance(answer_parts, list):
        return
    valid_rubric_parts = [part for part in rubric_parts if isinstance(part, dict)]
    valid_answer_parts = [part for part in answer_parts if isinstance(part, dict)]
    if not valid_rubric_parts or len(valid_rubric_parts) != len(valid_answer_parts):
        return

    rubric_ids = [str(part.get("part_id") or "") for part in valid_rubric_parts]
    answer_ids = [str(part.get("part_id") or "") for part in valid_answer_parts]
    if rubric_ids == answer_ids:
        return
    if len(rubric_ids) > 1 and set(rubric_ids) & set(answer_ids):
        return
    for rubric_part, answer_part in zip(valid_rubric_parts, valid_answer_parts):
        answer_part["part_id"] = str(rubric_part.get("part_id") or answer_part.get("part_id") or "")

def _align_step_required_elements_with_answer_values(
    question: dict[str, Any],
    answer_item: dict[str, Any],
) -> None:
    rubric_parts = question.get("parts")
    answer_parts = answer_item.get("parts")
    if not isinstance(rubric_parts, list) or not isinstance(answer_parts, list):
        return
    answer_part_map = {
        str(part.get("part_id") or ""): part
        for part in answer_parts
        if isinstance(part, dict)
    }
    for part_index, rubric_part in enumerate(rubric_parts):
        if not isinstance(rubric_part, dict):
            continue
        answer_part = answer_part_map.get(str(rubric_part.get("part_id") or ""))
        if not isinstance(answer_part, dict) and part_index < len(answer_parts):
            candidate = answer_parts[part_index]
            answer_part = candidate if isinstance(candidate, dict) else None
        if not isinstance(answer_part, dict):
            continue

        raw_answers = answer_part.get("answers")
        answer_values = _string_list(answer_part.get("answer_values"))
        steps = rubric_part.get("steps")
        if not answer_values or not isinstance(steps, list):
            continue

        values_by_id: dict[str, str] = {}
        if isinstance(raw_answers, list):
            for raw_answer in raw_answers:
                if not isinstance(raw_answer, dict):
                    continue
                score_point_id = str(raw_answer.get("score_point_id") or raw_answer.get("step_id") or "").strip()
                value = str(raw_answer.get("value") or raw_answer.get("answer") or "").strip()
                if score_point_id and value:
                    values_by_id[score_point_id] = value

        for step_index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            step_key = str(step.get("score_point_id") or step.get("step_id") or "").strip()
            value = values_by_id.get(step_key)
            if not value and step_index < len(answer_values):
                value = answer_values[step_index]
            if value:
                step["required_elements"] = merge_equivalent_forms([], value, max_forms=16)

def _align_solution_parts_with_answer_parts(
    question: dict[str, Any],
    answer_item: dict[str, Any],
    qtype: str,
    max_score: float,
) -> None:
    if qtype not in {"calculation", "proof", "comprehensive"}:
        return
    answer_parts = answer_item.get("parts")
    if not isinstance(answer_parts, list) or len(answer_parts) <= 1:
        return
    rubric_parts = question.get("parts")
    if isinstance(rubric_parts, list) and len(rubric_parts) > 1:
        return
    part_scores = _allocate_scores(max_score, len(answer_parts))
    next_parts: list[dict[str, Any]] = []
    for idx, (answer_part, part_score) in enumerate(zip(answer_parts, part_scores), start=1):
        if not isinstance(answer_part, dict):
            continue
        part_id = str(answer_part.get("part_id") or f"{question.get('question_id')}({idx})")
        answer = str(answer_part.get("answer") or "").strip()
        milestone_steps = _milestone_step_alias(answer_part)
        next_parts.append(
            {
                "part_id": part_id,
                "part_score": part_score,
                "steps": milestone_steps or [
                    {
                        "step_id": "S1",
                        "step_score": part_score,
                        "core_goal": f"完成 {part_id} 的答题要求",
                        "required_elements": [answer] if answer else ["合理的推理过程", "正确的结论"],
                        "allow_alternative_methods": True,
                        "deduction_rules": [],
                    }
                ],
                "presentation_rules": [],
            }
        )
    if next_parts:
        question["parts"] = next_parts

def _allocate_scores(total: float, count: int) -> list[float]:
    if count <= 0:
        return []
    total_int = int(round(total))
    base = total_int // count
    remainder = total_int - base * count
    return [base + (1 if idx < remainder else 0) for idx in range(count)]

_VALID_RESPONSE_MODES = {
    "exact_objective",
    "short_answer_points",
    "process_required",
    "visual_construction",
}

_CHOICE_RESPONSE_MODE_ALIASES = {
    "choice",
    "single_choice",
    "multiple_choice",
    "single_select",
    "multiple_select",
    "select_one",
    "select_many",
}

_NON_PROCESS_RESPONSE_MODES = {
    "exact_objective",
    "short_answer_points",
    "visual_construction",
}


def _normalize_response_mode_token(value: Any) -> str:
    return re.sub(r"[\s-]+", "_", str(value or "").strip().lower())


def _infer_missing_question_type_from_parts(question: dict[str, Any]) -> str:
    """Use only unanimous, explicit choice-mode evidence to repair a missing type."""
    parts = question.get("parts")
    if not isinstance(parts, list) or not parts:
        return ""

    response_modes: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            return ""
        response_mode = _normalize_response_mode_token(part.get("response_mode"))
        if not response_mode:
            return ""
        response_modes.append(response_mode)

    if all(mode in _CHOICE_RESPONSE_MODE_ALIASES for mode in response_modes):
        return "choice"
    return ""


def _infer_part_response_mode(question: dict[str, Any], part: dict[str, Any]) -> str:
    explicit = _normalize_response_mode_token(part.get("response_mode"))
    aliases = {
        "direct_answer": "short_answer_points",
        "answer_only": "short_answer_points",
        "objective": "exact_objective",
        "construction": "visual_construction",
        "proof": "process_required",
    }
    explicit = aliases.get(explicit, explicit)
    if explicit in _VALID_RESPONSE_MODES:
        return explicit

    qtype = str(question.get("question_type") or "").strip().lower()
    if qtype in {"choice", "fill_blank", "judgement", "true_false", "direct_answer"}:
        return "exact_objective"

    text_parts = [
        str(part.get(key) or "")
        for key in ("core_goal", "description", "part_title", "response_requirement", "answer_requirement")
    ]
    steps = part.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            text_parts.extend(
                str(step.get(key) or "")
                for key in ("core_goal", "goal", "criterion", "description")
            )
            required = step.get("required_elements")
            if isinstance(required, list):
                text_parts.extend(str(item or "") for item in required)
    text = " ".join(text_parts)
    if re.search(r"尺规|作图|画出|绘制|保留.{0,6}(?:痕迹|过程)", text):
        return "visual_construction"
    if re.search(r"直接写出|只需.{0,6}答案|无需.{0,6}过程|填空|填写|选择|判断|写出.{0,8}(?:结果|关系|答案|数值)", text):
        return "short_answer_points"
    return "process_required"

def _ensure_solution_hard_rules(question: dict[str, Any]) -> None:
    """Persist process rules without applying them to direct-answer subquestions."""
    qtype = str(question.get("question_type") or "")
    if qtype not in {"proof", "calculation", "comprehensive"}:
        return

    max_score = _safe_float(question.get("max_score"), 0.0)
    default_answer_only = 1

    if "require_final_answer" not in question:
        # Proof and pure calculation questions usually do not need an extra "绛?;
        # general comprehensive answers often do.
        question["require_final_answer"] = qtype == "comprehensive"
    question["require_final_answer"] = bool(question.get("require_final_answer"))

    parts = question.get("parts")
    part_answer_only_total = 0
    has_process_part = False
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue

            part_score = _safe_float(part.get("part_score"), max_score)
            response_mode = _infer_part_response_mode(question, part)
            part["response_mode"] = response_mode

            rules = part.get("presentation_rules")
            if not isinstance(rules, list):
                rules = []
                part["presentation_rules"] = rules
            _remove_rule_by_id(rules, "final_answer_required")

            if response_mode in _NON_PROCESS_RESPONSE_MODES:
                part["require_final_answer"] = False
                part["answer_only_max_score"] = int(round(part_score))
            else:
                has_process_part = True
                # 过程评分单元只有正确最终答案且没有有效过程时统一给 1 分；
                # 错误答案或无有效作答为 0，不再按小问分的 25% 计算。
                if "require_final_answer" not in part:
                    part["require_final_answer"] = question["require_final_answer"]
                part["require_final_answer"] = bool(part.get("require_final_answer"))
                part["answer_only_max_score"] = min(
                    1,
                    max(0, int(round(part_score))),
                )
                if part["require_final_answer"]:
                    rules.append(
                        {
                            "rule_id": "final_answer_required",
                            "rule": "启用此策略时，必须包含最终答案或明确的结论；如果缺失或不完整则扣分。",
                            "max_deduction": 1,
                        }
                    )
            part_answer_only_total += int(part["answer_only_max_score"])

    if isinstance(parts, list) and parts:
        answer_only_max = part_answer_only_total
    else:
        answer_only_max = default_answer_only
        has_process_part = True
    if max_score > 0:
        answer_only_max = max(0, min(answer_only_max, int(round(max_score))))
    question["answer_only_max_score"] = answer_only_max

    policies = question.get("deduction_policy")
    if not isinstance(policies, list):
        policies = []
        question["deduction_policy"] = policies

    policies[:] = [
        policy
        for policy in policies
        if not (
            isinstance(policy, dict)
            and str(policy.get("policy_id") or "") in {"answer_only_process_missing", "core_process_missing"}
        )
    ]
    if has_process_part:
        _upsert_policy(
            policies,
            {
                "policy_id": "answer_only_process_missing",
                "issue": f"需要过程的小问仅有最终答案时，整题最高只给 {answer_only_max} 分",
                "max_deduction": max(0, int(round(max_score)) - answer_only_max),
                "severity": "major",
            },
        )
        _upsert_policy(
            policies,
            {
                "policy_id": "core_process_missing",
                "issue": "需要过程的小问缺失关键步骤、证明逻辑或推理链条；扣除对应步骤分",
                "max_deduction": int(round(max_score)),
                "severity": "fatal",
            },
        )

    question["answer_presentation_policy"] = {
        "require_final_answer": question["require_final_answer"],
        "answer_only_max_score": answer_only_max,
        "note": "仅 response_mode=process_required 的小问需要过程证据；其他小问按正确答案项或图形要求给分。",
    }

def _upsert_policy(policies: list[Any], new_policy: dict[str, Any]) -> None:
    policy_id = str(new_policy.get("policy_id") or "")
    for idx, policy in enumerate(policies):
        if isinstance(policy, dict) and str(policy.get("policy_id") or "") == policy_id:
            policies[idx] = {**policy, **new_policy}
            return
    policies.append(new_policy)

def _remove_rule_by_id(rules: list[Any], rule_id: str) -> None:
    rules[:] = [
        rule
        for rule in rules
        if not (isinstance(rule, dict) and str(rule.get("rule_id") or "") == rule_id)
    ]

def _default_core_goal(qtype: str, answer_item: dict[str, Any]) -> str:
    if qtype == "choice":
        return "选择正确的选项"
    if qtype == "fill_blank":
        return "填写正确或等价的答案"
    return "完成必要的推理或计算步骤"

def _default_required_elements(qtype: str, answer_item: dict[str, Any]) -> list[str]:
    canonical = str(answer_item.get("canonical_answer") or "").strip()
    if qtype in {"choice", "fill_blank"} and canonical:
        return [canonical]
    return ["合理的推理过程", "正确的结论"]

def _force_step_total(part: dict[str, Any]) -> None:
    steps = part.get("steps")
    if not isinstance(steps, list) or not steps:
        return
    part_score = _safe_float(part.get("part_score"), 0.0)
    step_sum = sum(_safe_float(step.get("step_score"), 0.0) for step in steps if isinstance(step, dict))
    diff = round(part_score - step_sum, 2)
    if abs(diff) > 0.01 and isinstance(steps[-1], dict):
        steps[-1]["step_score"] = round(_safe_float(steps[-1].get("step_score"), 0.0) + diff, 2)

def _force_part_total(question: dict[str, Any]) -> None:
    parts = question.get("parts")
    if not isinstance(parts, list) or not parts:
        return
    max_score = _safe_float(question.get("max_score"), 0.0)
    part_sum = sum(_safe_float(part.get("part_score"), 0.0) for part in parts if isinstance(part, dict))
    diff = round(max_score - part_sum, 2)
    if abs(diff) > 0.01 and isinstance(parts[-1], dict):
        parts[-1]["part_score"] = round(_safe_float(parts[-1].get("part_score"), 0.0) + diff, 2)
        _force_step_total(parts[-1])

def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)

def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        if not value.strip():
            return []
        return [item.strip() for item in value.replace(",", ";").split(";") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []

def _method_variants(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for idx, item in enumerate(value, start=1):
        if isinstance(item, dict):
            result.append(
                {
                    "name": str(item.get("name") or f"鏂规硶{idx}"),
                    "outline": str(item.get("outline") or item.get("description") or ""),
                }
            )
        elif str(item).strip():
            result.append({"name": f"鏂规硶{idx}", "outline": str(item).strip()})
    return result

def _normalize_question_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "comprehensive"
    if raw in {"fill_blank", "fill-in", "fill_in_blank", "blank", "short_answer", "short-answer"}:
        return "fill_blank"
    if raw in {"choice", "single_choice", "multiple_choice", "select", "option"}:
        return "choice"
    if raw in {"calculation", "calculate", "solve", "solution", "problem_solving"}:
        return "calculation"
    if raw in {"proof", "prove"}:
        return "proof"
    if raw in {"comprehensive", "subjective", "constructed_response"}:
        return "comprehensive"
    if any(token in raw for token in ["choice", "select", "option"]):
        return "choice"
    if any(token in raw for token in ["blank", "short_answer", "fill"]):
        return "fill_blank"
    if any(token in raw for token in ["proof", "prove"]):
        return "proof"
    if any(token in raw for token in ["calculation", "calculate", "solve", "solution"]):
        return "calculation"
    return "comprehensive"

def validate_generated_config(
    payload: dict[str, Any], *, normalize: bool = True, enforce_score_policy: bool = True,
) -> None:
    if normalize:
        normalize_generated_config_schema(payload)
        force_payload_total_score(payload, target_total=100.0)
    if not isinstance(payload, dict):
        raise ValueError("Generated config must be a JSON object")
    for top_key in ["rubric", "answer_key", "meta"]:
        if top_key not in payload:
            raise ValueError(f"Generated config is missing top-level field: {top_key}")

    rubric = payload["rubric"]
    answer_key = payload["answer_key"]
    meta = payload["meta"]
    if not isinstance(rubric, dict):
        raise ValueError("rubric must be an object")
    if not isinstance(answer_key, dict):
        raise ValueError("answer_key must be an object")
    if not isinstance(meta, dict):
        raise ValueError("meta must be an object")

    rubric_questions = rubric.get("questions")
    answer_questions = answer_key.get("questions")
    if not isinstance(rubric_questions, list) or not rubric_questions:
        raise ValueError("rubric.questions must be a non-empty list")
    if not isinstance(answer_questions, list) or not answer_questions:
        raise ValueError("answer_key.questions must be a non-empty list")

    rubric_q_map: dict[str, dict[str, Any]] = {}
    scores_by_type: dict[str, float] = {}
    for idx, item in enumerate(rubric_questions, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"rubric.questions[{idx - 1}] must be an object, got {type(item).__name__}: {item!r}")
        for key in ["question_id", "question_type", "max_score", "parts"]:
            if key not in item:
                raise ValueError(f"rubric.questions[{idx - 1}] is missing field: {key}")
        qid = str(item["question_id"])
        rubric_q_map[qid] = item
        qtype = str(item.get("question_type") or "comprehensive")
        max_score = float(item["max_score"])
        if not math.isfinite(max_score):
            raise ValueError("Question score must be finite")
        if enforce_score_policy and not max_score.is_integer():
            raise ValueError(f"rubric.questions[{idx - 1}].max_score must be an integer")
        if enforce_score_policy and max_score > MAX_QUESTION_SCORE:
            raise ValueError(
                f"rubric.questions[{idx - 1}].max_score must not exceed {MAX_QUESTION_SCORE}"
            )
        # 同类同分仅约束客观题（choice/fill_blank/judgement/true_false）；
        # 解答类大题（calculation/proof/comprehensive）允许各题分值不同。
        if (
            enforce_score_policy and _normalize_type(qtype) in OBJECTIVE_TYPES
            and is_simple_objective_question(item)
        ):
            if qtype in scores_by_type and abs(scores_by_type[qtype] - max_score) > 1e-6:
                raise ValueError(f"All questions with question_type={qtype} must use the same max_score")
            scores_by_type[qtype] = max_score

        parts = item.get("parts")
        if not isinstance(parts, list) or not parts:
            raise ValueError(f"rubric.questions[{idx - 1}].parts must be a non-empty list")
        part_total = 0.0
        for pidx, part in enumerate(parts, start=1):
            if not isinstance(part, dict):
                raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}] must be an object")
            for key in ["part_id", "part_score", "steps"]:
                if key not in part:
                    raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}] is missing field: {key}")
            part_score = float(part["part_score"])
            if not math.isfinite(part_score):
                raise ValueError("Part score must be finite")
            if enforce_score_policy and not part_score.is_integer():
                raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}].part_score must be an integer")
            part_total += part_score
            steps = part.get("steps")
            if not isinstance(steps, list) or not steps:
                raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}].steps must be a non-empty list")
            step_total = 0.0
            for sidx, step in enumerate(steps, start=1):
                if not isinstance(step, dict):
                    raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}].steps[{sidx - 1}] must be an object")
                for key in ["step_id", "step_score", "core_goal", "required_elements", "allow_alternative_methods"]:
                    if key not in step:
                        raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}].steps[{sidx - 1}] is missing field: {key}")
                step_score = float(step["step_score"])
                if not math.isfinite(step_score):
                    raise ValueError("Step score must be finite")
                if enforce_score_policy and not step_score.is_integer():
                    raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}].steps[{sidx - 1}].step_score must be an integer")
                step_total += step_score
            if abs(step_total - part_score) > 1e-6:
                raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}] step total does not equal part_score")
            if not isinstance(part.get("presentation_rules", []), list):
                raise ValueError(f"rubric.questions[{idx - 1}].parts[{pidx - 1}].presentation_rules must be a list")
        if abs(part_total - max_score) > 1e-6:
            raise ValueError(f"rubric.questions[{idx - 1}] part total does not equal max_score")

    answer_q_map: dict[str, dict[str, Any]] = {}
    for idx, item in enumerate(answer_questions, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"answer_key.questions[{idx - 1}] must be an object")
        for key in ["question_id", "canonical_answer", "accepted_forms", "method_variants", "parts"]:
            if key not in item:
                raise ValueError(f"answer_key.questions[{idx - 1}] is missing field: {key}")
        qid = str(item["question_id"])
        answer_q_map[qid] = item
        if not isinstance(item["accepted_forms"], list):
            raise ValueError(f"answer_key.questions[{idx - 1}].accepted_forms must be a list")
        if not isinstance(item["method_variants"], list):
            raise ValueError(f"answer_key.questions[{idx - 1}].method_variants must be a list")
        parts = item.get("parts")
        if not isinstance(parts, list) or not parts:
            raise ValueError(f"answer_key.questions[{idx - 1}].parts must be a non-empty list")
        for pidx, part in enumerate(parts, start=1):
            if not isinstance(part, dict):
                raise ValueError(f"answer_key.questions[{idx - 1}].parts[{pidx - 1}] must be an object")
            for key in ["part_id", "answer", "analysis", "step_milestones"]:
                if key not in part:
                    raise ValueError(f"answer_key.questions[{idx - 1}].parts[{pidx - 1}] is missing field: {key}")
            if not isinstance(part["step_milestones"], list):
                raise ValueError(f"answer_key.questions[{idx - 1}].parts[{pidx - 1}].step_milestones must be a list")

    if set(rubric_q_map) != set(answer_q_map):
        raise ValueError("rubric.questions and answer_key.questions must have identical question_id sets")
    total_score = sum(float(item.get("max_score") or 0) for item in rubric_q_map.values())
    if enforce_score_policy and abs(total_score - 100.0) > 0.02:
        raise ValueError(f"rubric question total must be 100, got {total_score:.2f}")
    rubric["total_score"] = 100.0 if enforce_score_policy else total_score


    if "warnings" not in meta or not isinstance(meta["warnings"], list):
        raise ValueError("meta.warnings must exist and be a list")

SESSION_MANAGER_COMPAT_EXPORTS = (
    "_align_answer_parts_to_rubric_parts",
    "_align_solution_parts_with_answer_parts",
    "_align_step_required_elements_with_answer_values",
    "_allocate_scores",
    "_augment_answer_equivalences",
    "_best_step_alias",
    "_canonical_question_id",
    "_coerce_answer_item_aliases",
    "_coerce_answer_part_aliases",
    "_default_core_goal",
    "_default_required_elements",
    "_enforce_objective_question_rules",
    "_ensure_solution_hard_rules",
    "_extract_direct_answer_values",
    "_first_list_value",
    "_fix_question_sum",
    "_force_part_total",
    "_force_step_total",
    "_infer_part_response_mode",
    "_looks_like_serialized_answer_list",
    "_looks_like_serialized_knowledge_sequence",
    "_method_variants",
    "_normalize_answer_item",
    "_normalize_question_knowledge_fields",
    "_normalize_question_type",
    "_normalize_rubric_question",
    "_normalize_serialized_answer_list",
    "_promote_nested_question_knowledge",
    "_record_complete_answer_set_rule",
    "_redundant_knowledge_fields_match_pairs",
    "_remove_rule_by_id",
    "_safe_float",
    "_safe_knowledge_sequence",
    "_sanitize_choice_answer_forms",
    "_scale_deduction_policy",
    "_scale_question_scores",
    "_scale_question_to_score",
    "_should_treat_as_direct_answer_question",
    "_specific_step_goal",
    "_string_list",
    "_upsert_policy",
    "force_payload_total_score",
    "normalize_new_generated_config_payload",
    "normalize_generated_config_knowledge_fields",
    "normalize_generated_config_schema",
    "strip_generated_config_knowledge_fields",
    "validate_generated_config",
)

__all__ = [
    "force_payload_total_score",
    "normalize_new_generated_config_payload",
    "normalize_generated_config_knowledge_fields",
    "normalize_generated_config_schema",
    "strip_generated_config_knowledge_fields",
    "validate_generated_config",
]
