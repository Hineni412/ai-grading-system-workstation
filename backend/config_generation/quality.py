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


def _positive_score(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _meaningful_proof_obligations(node: dict[str, Any]) -> list[Any]:
    obligations = node.get("proof_obligations")
    if not isinstance(obligations, list):
        return []
    return [
        item
        for item in obligations
        if (
            isinstance(item, dict)
            and str(
                item.get("description")
                or item.get("obligation")
                or item.get("core_goal")
                or ""
            ).strip()
        )
        or (not isinstance(item, dict) and str(item or "").strip())
    ]


def _has_specific_deduction_evidence(
    question: dict[str, Any],
    part: dict[str, Any],
    steps: list[dict[str, Any]],
) -> bool:
    generic_policy_ids = {
        "answer_only_process_missing",
        "core_process_missing",
    }
    for node in (part, question):
        policies = node.get("deduction_policy")
        if isinstance(policies, str) and policies.strip():
            return True
        if not isinstance(policies, list):
            continue
        for policy in policies:
            if isinstance(policy, dict):
                policy_id = str(policy.get("policy_id") or "").strip()
                detail = str(
                    policy.get("issue")
                    or policy.get("description")
                    or policy.get("rule")
                    or ""
                ).strip()
                if policy_id not in generic_policy_ids and detail:
                    return True
            elif str(policy or "").strip():
                return True
    return any(_string_list(step.get("deduction_rules")) for step in steps)


def _has_independently_scorable_steps(
    steps: list[dict[str, Any]],
    generic_goals: set[str],
) -> bool:
    if len(steps) < 2:
        return False
    for step in steps:
        goal = str(step.get("core_goal") or "").strip()
        required = [
            item
            for item in _string_list(step.get("required_elements"))
            if item not in generic_goals
        ]
        if not goal or goal in generic_goals or not required:
            return False
    return True


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
            generic_goals = {
                "完成必要的推理或计算步骤",
                "合理的推理过程",
                "正确的结论",
            }
            if qtype in {"choice", "fill_blank", "judgement", "true_false", "direct_answer"} and mode == "process_required":
                warnings.append(f"[质量检查-阻断] {qid} 客观题被错误设置为过程评分")
            if mode == "visual_construction":
                visual_requirements = _string_list(part.get("visual_requirements"))
                meaningful_goals = [goal for goal in goals if goal and goal not in generic_goals]
                if not visual_requirements and not meaningful_goals:
                    warnings.append(f"[质量检查-阻断] {qid} 作图题缺少具体作图要求")
            if mode not in {"exact_objective", "short_answer_points", "visual_construction"}:
                if qtype in {"proof", "calculation", "comprehensive"} and goals and all(goal in generic_goals for goal in goals):
                    warnings.append(f"[质量检查-阻断] {qid} 评分点全部为通用描述，无法执行可靠批改")
                part_score = _positive_score(
                    part.get("part_score") or question.get("max_score")
                )
                if (
                    qtype in {"proof", "calculation", "comprehensive"}
                    and mode == "process_required"
                    and part_score > 1
                ):
                    part_id = str(part.get("part_id") or f"第{index + 1}问")
                    if not _has_independently_scorable_steps(steps, generic_goals):
                        warnings.append(
                            f"[质量检查-阻断] {qid}/{part_id} 缺少可独立评分的逻辑步骤和具体得分证据"
                        )
                    if qtype == "proof" and not (
                        _meaningful_proof_obligations(part)
                        or _meaningful_proof_obligations(question)
                    ):
                        warnings.append(
                            f"[质量检查-阻断] {qid}/{part_id} 缺少具体证明义务"
                        )
                    if not _has_specific_deduction_evidence(
                        question,
                        part,
                        steps,
                    ):
                        warnings.append(
                            f"[质量检查-阻断] {qid}/{part_id} 缺少具体扣分证据"
                        )
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
