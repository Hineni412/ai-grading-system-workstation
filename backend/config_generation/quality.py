from __future__ import annotations

from typing import Any

from .normalization import (
    _infer_part_response_mode,
    _looks_like_serialized_answer_list,
    _string_list,
)

def _quality_answer_texts(node: Any) -> list[str]:
    if not isinstance(node, dict):
        return []
    values: list[str] = []
    for key in ("answer", "canonical_answer", "standard_answer", "correct_answer"):
        value = str(node.get(key) or "").strip()
        if value:
            values.append(value)
    accepted = node.get("accepted_forms")
    if isinstance(accepted, list):
        values.extend(str(item).strip() for item in accepted if str(item).strip())
    return values

def _looks_like_garbled_generated_text(value: Any) -> bool:
    text = str(value or "")
    return "\ufffd" in text or "锟" in text or "��" in text


def collect_generated_config_quality_warnings(payload: dict[str, Any]) -> list[str]:
    rubric = payload.get("rubric") if isinstance(payload, dict) else None
    answer_key = payload.get("answer_key") if isinstance(payload, dict) else None
    rubric_questions = rubric.get("questions") if isinstance(rubric, dict) else []
    answer_questions = answer_key.get("questions") if isinstance(answer_key, dict) else []
    if not isinstance(rubric_questions, list):
        return ["[质量检查-阻断] rubric.questions 结构无效"]
    answer_map = {
        str(item.get("question_id") or ""): item
        for item in answer_questions
        if isinstance(item, dict)
    } if isinstance(answer_questions, list) else {}

    warnings: list[str] = []
    for question in rubric_questions:
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "未知题号")
        qtype = str(question.get("question_type") or "")
        stem = str(question.get("stem_summary") or "").strip()

        answer_item = answer_map.get(qid, {})
        answer_image_present = bool(isinstance(answer_item, dict) and answer_item.get("answer_image_base64"))
        answer_texts = _quality_answer_texts(answer_item)
        answer_parts = answer_item.get("parts") if isinstance(answer_item, dict) else []
        if not isinstance(answer_parts, list):
            answer_parts = []
        text_fields: list[Any] = [stem, *answer_texts]
        parts = question.get("parts")
        if not isinstance(parts, list):
            parts = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            for step in part.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                text_fields.append(step.get("core_goal"))
                required = step.get("required_elements")
                if isinstance(required, list):
                    text_fields.extend(required)
        for answer_part in answer_parts:
            text_fields.extend(_quality_answer_texts(answer_part))
        if any(_looks_like_garbled_generated_text(value) for value in text_fields):
            warnings.append(f"[质量检查-阻断] {qid} 的题干、公式、答案或踩分点中存在疑似乱码")
        if any(_looks_like_serialized_answer_list(value) for value in text_fields):
            warnings.append(
                f"[质量检查-阻断] {qid} 的答案或评分点中混入列表字符串"
            )

        if qtype in {"choice", "fill_blank", "judgement", "true_false", "direct_answer"}:
            if not answer_texts:
                warnings.append(
                    f"[质量检查-阻断] {qid} 缺少可评分的文本标准答案"
                )

        for index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            mode = str(part.get("response_mode") or "").strip() or _infer_part_response_mode(question, part)
            steps = [step for step in part.get("steps") or [] if isinstance(step, dict)]
            goals = [str(step.get("core_goal") or "").strip() for step in steps]
            if qtype in {"choice", "fill_blank", "judgement", "true_false", "direct_answer"} and mode == "process_required":
                warnings.append(f"[质量检查-阻断] {qid} 客观题被错误设置为过程评分")
            if mode == "visual_construction":
                visual_requirements = _string_list(part.get("visual_requirements"))
                meaningful_goals = [goal for goal in goals if goal]
                if not visual_requirements and not meaningful_goals:
                    warnings.append(f"[质量检查-阻断] {qid} 作图题缺少具体作图要求")
            if mode not in {"exact_objective", "short_answer_points", "visual_construction"}:
                continue
            answer_part = answer_parts[index] if index < len(answer_parts) and isinstance(answer_parts[index], dict) else {}
            if (
                not _quality_answer_texts(answer_part)
                and not answer_texts
                and (
                    mode == "exact_objective"
                    or not answer_image_present
                )
            ):
                part_id = str(part.get("part_id") or f"第{index + 1}问")
                suffix = (
                    "文本标准答案"
                    if mode == "exact_objective"
                    else "标准答案或答案图"
                )
                warnings.append(
                    f"[质量检查-阻断] {qid}/{part_id} 缺少可评分的{suffix}"
                )

    return list(dict.fromkeys(warnings))

def refresh_generated_config_quality_warnings(payload: dict[str, Any]) -> list[str]:
    meta = payload.setdefault("meta", {}) if isinstance(payload, dict) else {}
    if not isinstance(meta, dict):
        return []
    warnings = meta.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    warnings = [warning for warning in warnings if not str(warning).startswith("[质量检查-")]
    quality_warnings = collect_generated_config_quality_warnings(payload)
    meta["warnings"] = [*warnings, *quality_warnings]
    return quality_warnings


def blocking_quality_question_ids(payload: dict[str, Any]) -> list[str]:
    rubric = payload.get("rubric") if isinstance(payload, dict) else None
    questions = rubric.get("questions") if isinstance(rubric, dict) else None
    question_ids = [
        str(question.get("question_id") or "").strip()
        for question in questions or []
        if isinstance(question, dict)
        and str(question.get("question_id") or "").strip()
    ] if isinstance(questions, list) else []
    ordered_ids = sorted(
        dict.fromkeys(question_ids),
        key=lambda value: (-len(value), value),
    )
    result: list[str] = []
    for warning in collect_generated_config_quality_warnings(payload):
        text = str(warning)
        for question_id in ordered_ids:
            prefix = f"[质量检查-阻断] {question_id}"
            if text == prefix or text.startswith(prefix + " ") or text.startswith(prefix + "/"):
                result.append(question_id)
                break
    return list(dict.fromkeys(result))


SESSION_MANAGER_COMPAT_EXPORTS = (
    "_looks_like_garbled_generated_text",
    "_quality_answer_texts",
    "collect_generated_config_quality_warnings",
    "refresh_generated_config_quality_warnings",
)

__all__ = [
    "blocking_quality_question_ids",
    "collect_generated_config_quality_warnings",
    "refresh_generated_config_quality_warnings",
]
