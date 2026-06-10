from __future__ import annotations

import re
from typing import Any

from answer_normalizer import normalize_answer_text

SOLUTION_TYPES = {"proof", "calculation", "comprehensive"}

_STEM_ECHO_PATTERNS = (
    r"是不是定值",
    r"是否为定值",
    r"是否定值",
    r"是不是常量",
    r"是否为常量",
    r"^求证",
    r"^试证明",
    r"^证明.+成立",
    r"^说明.+理由",
    r"^判断.+是否",
)

_ACK_ONLY_RE = re.compile(r"^[是对勾√✓✔]+$")

_PROCESS_EVIDENCE_PATTERNS = (
    r"∵|∴|因为|所以|推出|证得|则得|可得|故有|则有|假设|设",
    r"∠|△|⊥|∥|≌|∽|°|sin|cos|tan",
    r"证明|全等|相似|平行|垂直|辅助线",
    r"[=<>≤≥].*\d|\d.*[=<>≤≥+-]",
    r"\d+\s*[°度]",
)


def extract_observed_text(detail: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("observed_answer", "student_answer", "answer_observed"):
        value = detail.get(key)
        if value:
            parts.append(str(value))
    evidence_steps = detail.get("evidence_steps")
    if isinstance(evidence_steps, list):
        parts.extend(str(step) for step in evidence_steps if step)
    summary = detail.get("alternative_solution_summary")
    if summary:
        parts.append(str(summary))
    return " ".join(parts).strip()


def _strip_answer_artifacts(text: str) -> str:
    normalized = normalize_answer_text(text)
    if not isinstance(normalized, str):
        return ""
    cleaned = re.sub(r"[√✓✔vV勾]", "", normalized)
    cleaned = re.sub(r"^[（(]?\d+[）)]", "", cleaned)
    cleaned = re.sub(r"^第?\d+问", "", cleaned)
    cleaned = re.sub(r"^[（(]?[一二三123][）)]?", "", cleaned)
    return cleaned.strip(".,，;；:：、。 ")


def classify_non_substantive_solution_answer(answer: str | None) -> str | None:
    text = str(answer or "").strip()
    if not text:
        return "未见有效作答"

    cleaned = _strip_answer_artifacts(text)
    if not cleaned:
        return "仅勾选或涂改痕迹，无有效作答"

    for pattern in _STEM_ECHO_PATTERNS:
        if re.search(pattern, cleaned, flags=re.IGNORECASE):
            remainder = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip(".,，;；:：、。 ")
            if not remainder or len(cleaned) <= 30:
                return "仅复述题干，无有效作答"

    if len(cleaned) <= 12 and re.fullmatch(r"(是不是|是否为|是否).+", cleaned):
        return "仅复述题干，无有效作答"

    if len(cleaned) <= 2 and _ACK_ONLY_RE.fullmatch(cleaned):
        return "仅表态或勾选，无证明过程"

    return None


def has_solution_process_evidence(answer: str | None) -> bool:
    text = str(answer or "").strip()
    if len(text) < 3:
        return False
    normalized = normalize_answer_text(text)
    if not isinstance(normalized, str) or not normalized:
        return False
    if any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _PROCESS_EVIDENCE_PATTERNS):
        return True
    if len(normalized) >= 20 and re.search(r"\d", normalized) and re.search(r"[=+\-×÷*/]", normalized):
        return True
    return False


def rubric_question_meta(rubric: dict[str, Any], question_id: str) -> tuple[str, float, int]:
    questions = rubric.get("questions") if isinstance(rubric, dict) else []
    if not isinstance(questions, list):
        return "", 0.0, 1
    for question in questions:
        if not isinstance(question, dict):
            continue
        qtype = str(question.get("question_type") or "")
        qid = str(question.get("question_id") or "")
        if qid == question_id:
            return _meta_from_question_node(question, qtype)
        parts = question.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            if str(part.get("part_id") or "") == question_id:
                try:
                    part_score = float(part.get("part_score") or question.get("max_score") or 0)
                except (TypeError, ValueError):
                    part_score = 0.0
                answer_only = _answer_only_max_from_node(question, part_score)
                return qtype, part_score, answer_only
    return "", 0.0, 1


def _meta_from_question_node(question: dict[str, Any], qtype: str) -> tuple[str, float, int]:
    try:
        max_score = float(question.get("max_score") or 0)
    except (TypeError, ValueError):
        max_score = 0.0
    return qtype, max_score, _answer_only_max_from_node(question, max_score)


def _answer_only_max_from_node(question: dict[str, Any], max_score: float) -> int:
    default_answer_only = max(1, int(round(max_score * 0.25))) if max_score > 0 else 1
    raw = question.get("answer_only_max_score")
    try:
        answer_only = int(round(float(raw)))
    except (TypeError, ValueError):
        answer_only = default_answer_only
    if max_score > 0:
        return max(0, min(answer_only, int(round(max_score))))
    return max(0, answer_only)


def apply_solution_substance_rules(
    *,
    observed_answer: str | None,
    question_type: str,
    full_score: float,
    answer_only_max_score: int | None,
    current_score: float,
) -> tuple[float, str | None, str | None, str | None]:
    if question_type not in SOLUTION_TYPES or full_score <= 0:
        return current_score, None, None, None

    zero_reason = classify_non_substantive_solution_answer(observed_answer)
    if zero_reason:
        if current_score > 1e-6:
            return 0.0, "未作答", zero_reason, f"{zero_reason}，按硬规则判 0 分。"
        return current_score, None, None, None

    answer_only = answer_only_max_score
    if answer_only is None:
        answer_only = max(1, int(round(full_score * 0.25)))
    answer_only = max(0, min(int(answer_only), int(round(full_score))))

    if not has_solution_process_evidence(observed_answer) and current_score > answer_only + 1e-6:
        if current_score >= full_score - 1e-6:
            summary = "缺少有效过程"
            reason = f"未见有效证明或推导过程，最多给 {answer_only} 分。"
        else:
            summary = "缺少有效过程"
            reason = f"仅有结论或无效作答痕迹，最多给 {answer_only} 分。"
        return float(answer_only), "逻辑断裂", summary, reason

    return current_score, None, None, None
