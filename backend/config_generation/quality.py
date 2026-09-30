from __future__ import annotations

import re
from typing import Any, Mapping

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


_PROCESS_TYPES = {"proof", "calculation", "comprehensive"}
_GENERIC_GOALS = {
    "完成必要的推理或计算步骤",
    "合理的推理过程",
    "正确的结论",
    "完成解答",
    "答案正确",
}
_GENERIC_SCORING_FRAGMENTS = tuple(
    sorted(
        {
            "完成", "本题", "本问", "本步", "写出", "给出", "说明", "进行",
            "证明", "推理", "计算", "解题", "解答", "作答", "思路", "题目",
            "呈现", "展示", "论证", "规范", "清晰", "必要", "正确", "合理",
            "完整", "相关", "相应", "关键", "主要", "基本", "过程", "步骤",
            "结论", "答案", "理由", "方法", "依据", "内容", "结果", "要求",
            "评分点", "评分", "标准", "得出", "得到", "根据", "利用", "体现",
            "包含", "缺少", "未写出", "未给出", "酌情", "视情况", "扣除",
            "扣分", "给分", "分数", "不得分", "未达成", "成立", "对应", "部分",
            "该步", "否则", "需要", "以及", "或者", "并且", "如果", "时",
            "的", "地", "得", "由", "与", "和", "或", "并", "则", "若",
        },
        key=len,
        reverse=True,
    )
)
_QUESTION_REFERENCE_ID = re.compile(
    r"\bq\d+(?:\s*\(?p\d+\)?)?(?:\s*[-_/]\s*s\d+)?\b",
    re.IGNORECASE,
)
_INTRINSIC_MATH_EVIDENCE = re.compile(
    r"(?:\\(?:frac|sqrt|angle|triangle|parallel|perp|equiv|cong)\b|"
    r"[=≠≤≥<>≈≡≌∽∥⊥∠△□○⊙√π°])",
    re.IGNORECASE,
)
_DEDUCTION_FAILURE_TRIGGER = re.compile(
    r"(?:缺少|遗漏|漏(?:写|答|算|证)?|错误|错(?:写|答|算|证)?|跳步|"
    r"未(?:写|答|算|证|给|列|说明|推出|得到|满足|完成)?|"
    r"不(?:成立|正确|完整|符合|等价|满足))"
)
_MILESTONE_SIGNAL = re.compile(
    r"[=≠≤≥≈≡≌∽∥⊥∠]|(?:因此|所以|从而|可得|推出|得到|证得|解得)"
)


def _collect_baseline_quality_warnings(payload: dict[str, Any]) -> list[str]:
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


def _evidence_anchor_tokens(value: Any) -> set[str]:
    text = _QUESTION_REFERENCE_ID.sub(" ", str(value or "").strip().lower())
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    for fragment in _GENERIC_SCORING_FRAGMENTS:
        text = text.replace(fragment, " ")
    anchors: set[str] = set()
    for token in re.findall(r"[a-z]+|[\u4e00-\u9fff]+", text):
        if token.isascii():
            anchors.add(token)
            continue
        for size in range(2, min(4, len(token)) + 1):
            anchors.update(
                token[index : index + size]
                for index in range(0, len(token) - size + 1)
            )
    return anchors


def _question_evidence_anchors(
    question: dict[str, Any],
    answer_item: dict[str, Any],
    answer_parts: list[Any],
) -> set[str]:
    reference_texts: list[Any] = [
        question.get("stem_summary"),
        question.get("stem"),
        question.get("question_text"),
        question.get("knowledge_name"),
        *_quality_answer_texts(answer_item),
    ]
    for answer_part in answer_parts:
        if isinstance(answer_part, dict):
            reference_texts.extend(_quality_answer_texts(answer_part))
            reference_texts.extend(
                answer_part.get(key)
                for key in ("answer", "analysis", "full_answer")
            )
    anchors: set[str] = set()
    for value in reference_texts:
        anchors.update(_evidence_anchor_tokens(value))
    return anchors


def _has_specific_scoring_content(value: Any, evidence: set[str]) -> bool:
    text = str(value or "").strip()
    return bool(
        text
        and (
            _INTRINSIC_MATH_EVIDENCE.search(text)
            or (_evidence_anchor_tokens(text) & evidence)
        )
    )


def _step_is_specific(step: dict[str, Any], evidence: set[str]) -> bool:
    goal = str(step.get("core_goal") or "").strip()
    required = _string_list(step.get("required_elements"))
    return bool(
        goal
        and goal not in _GENERIC_GOALS
        and _has_specific_scoring_content(goal, evidence)
        and required
        and any(_has_specific_scoring_content(item, evidence) for item in required)
    )


def _meaningful_proof_obligations(
    node: dict[str, Any],
    evidence: set[str],
) -> list[Any]:
    obligations = node.get("proof_obligations")
    if not isinstance(obligations, list):
        return []
    return [
        item
        for item in obligations
        if _has_specific_scoring_content(
            (
                item.get("description")
                or item.get("obligation")
                or item.get("core_goal")
                if isinstance(item, dict)
                else item
            ),
            evidence,
        )
    ]


def _has_specific_deduction_evidence(
    question: dict[str, Any],
    part: dict[str, Any],
    steps: list[dict[str, Any]],
    evidence: set[str],
) -> bool:
    values: list[Any] = []
    for node in (part, question):
        policy = node.get("deduction_policy")
        if isinstance(policy, list):
            values.extend(policy)
        elif policy:
            values.append(policy)
    for step in steps:
        values.extend(_string_list(step.get("deduction_rules")))
    for value in values:
        text = str(
            value.get("issue")
            or value.get("description")
            or value.get("rule")
            or ""
            if isinstance(value, dict)
            else value
        ).strip()
        if (
            text
            and _DEDUCTION_FAILURE_TRIGGER.search(text)
            and _has_specific_scoring_content(text, evidence)
        ):
            return True
    return False


def _answer_milestone_count(answer_part: Mapping[str, Any]) -> int:
    text = "；".join(
        str(answer_part.get(key) or "").strip()
        for key in ("answer", "analysis", "full_answer")
        if str(answer_part.get(key) or "").strip()
    )
    if not text:
        return 0
    normalized = re.sub(
        r"(?:首先|先|然后|再|接着|最后|因此|所以|从而|可得)",
        "；",
        text,
    )
    clauses = [
        clause.strip()
        for clause in re.split(r"[；;。\n，,]|(?:\([1-9]\)|（[1-9]）|[①②③④⑤])", normalized)
        if clause.strip()
    ]
    milestones = {
        re.sub(r"\s+", "", clause)
        for clause in clauses
        if _MILESTONE_SIGNAL.search(clause)
    }
    return len(milestones)


def _quality_issue(
    *,
    question_id: str,
    code: str,
    path: str,
    expected: str,
    actual: str,
    message: str,
) -> dict[str, str]:
    return {
        "question_id": question_id,
        "code": code,
        "path": path,
        "expected": expected,
        "actual": actual,
        "message": message,
    }


def _baseline_issue_code(message: str) -> str:
    mappings = (
        ("疑似乱码", "garbled_generated_text"),
        ("列表字符串", "serialized_answer_list"),
        ("缺少可评分", "missing_scorable_answer"),
        ("客观题被错误设置为过程评分", "objective_marked_as_process"),
        ("作图题缺少", "missing_visual_requirement"),
    )
    return next((code for token, code in mappings if token in message), "local_quality_failed")


def collect_generated_config_quality_issues(
    payload: dict[str, Any],
) -> list[dict[str, str]]:
    rubric = payload.get("rubric") if isinstance(payload, dict) else None
    questions = rubric.get("questions") if isinstance(rubric, dict) else []
    question_ids = [
        str(item.get("question_id") or "").strip()
        for item in questions or []
        if isinstance(item, dict) and str(item.get("question_id") or "").strip()
    ] if isinstance(questions, list) else []
    ordered_ids = sorted(dict.fromkeys(question_ids), key=lambda value: (-len(value), value))
    issues: list[dict[str, str]] = []
    for warning in _collect_baseline_quality_warnings(payload):
        message = str(warning)
        question_id = next(
            (
                value
                for value in ordered_ids
                if message == f"[质量检查-阻断] {value}"
                or message.startswith(f"[质量检查-阻断] {value} ")
                or message.startswith(f"[质量检查-阻断] {value}/")
            ),
            "",
        )
        issues.append(
            _quality_issue(
                question_id=question_id,
                code=_baseline_issue_code(message),
                path="rubric.questions",
                expected="满足本地评分质量契约",
                actual="未满足",
                message=message,
            )
        )

    for question in questions or []:
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "未知题号")
        qtype = str(question.get("question_type") or "").strip()
        parts = [item for item in question.get("parts") or [] if isinstance(item, dict)]
        if qtype == "fill_blank":
            step_count = sum(
                len(part.get("steps") or [])
                for part in parts
                if isinstance(part.get("steps"), list)
            )
            modes = {str(part.get("response_mode") or "") for part in parts}
            if len(parts) != 1 or step_count != 1 or modes != {"exact_objective"}:
                issues.append(
                    _quality_issue(
                        question_id=qid,
                        code="ordinary_fill_has_multiple_units",
                        path=f"rubric.questions[{qid}].parts",
                        expected="填空题只有一个最终答案评分单元",
                        actual=f"{len(parts)}个小问、{step_count}个评分点",
                        message=f"[质量检查-阻断] {qid} 填空题被拆成多个小问或踩分点",
                    )
                )

        for index, part in enumerate(parts):
            mode = (
                str(part.get("response_mode") or "").strip()
                or _infer_part_response_mode(question, part)
            )
            if mode != "process_required":
                continue
            part_id = str(part.get("part_id") or f"第{index + 1}问")
            path = f"rubric.questions[{qid}].parts[{part_id}]"
            steps = [
                item
                for item in part.get("steps") or []
                if isinstance(item, dict)
            ]
            step_signatures = {
                (
                    str(step.get("core_goal") or "").strip(),
                    frozenset(_string_list(step.get("required_elements"))),
                )
                for step in steps
                if str(step.get("core_goal") or "").strip()
                and _string_list(step.get("required_elements"))
            }
            if len(step_signatures) < 2:
                issues.append(
                    _quality_issue(
                        question_id=qid,
                        code="process_requires_two_steps",
                        path=f"{path}.steps",
                        expected="至少两个非空且互不重复的结构步骤",
                        actual=f"只有{len(step_signatures)}个可区分步骤",
                        message=(
                            f"[质量检查-阻断] {qid}/{part_id} "
                            "过程题需要至少两个可区分步骤"
                        ),
                    )
                )

    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        signature = (issue["question_id"], issue["code"], issue["path"])
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(issue)
    return deduped


def collect_generated_config_quality_warnings(payload: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            issue["message"]
            for issue in collect_generated_config_quality_issues(payload)
        )
    )

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
    meta["quality_issues"] = collect_generated_config_quality_issues(payload)
    return quality_warnings


def blocking_quality_question_ids(payload: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            issue["question_id"]
            for issue in collect_generated_config_quality_issues(payload)
            if issue["question_id"]
        )
    )


__all__ = [
    "blocking_quality_question_ids",
    "collect_generated_config_quality_issues",
    "collect_generated_config_quality_warnings",
    "refresh_generated_config_quality_warnings",
]
