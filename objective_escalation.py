from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


OBJECTIVE_QUESTION_TYPES = {
    "choice",
    "fill_blank",
    "judgement",
    "true_false",
    "direct_answer",
}

ABNORMAL_REVIEW_REASONS = {
    "unclear",
    "multiple",
    "low_confidence",
    "crop_contamination_suspected",
    "edge_touch_detected",
    "suspected_contamination",
    "standard_answer_missing",
    "max_score_missing_or_invalid",
    "objective_api_disabled",
    "objective_model_not_configured",
    "objective_api_key_or_url_missing",
}

MISSING_OR_ABNORMAL_ANSWERS = {"", "blank", "unclear", "multiple", "none", "null"}
AUTO_HANDLED_ZERO_REASONS = {
    "blank",
    "prompt_injection_or_score_bait",
    "prompt_injection",
    "discarded_answer_only",
    "discarded_answer_blank",
    "作废答案",
}


@dataclass(frozen=True)
class ObjectiveEscalationDecision:
    question_id: str
    escalate: bool
    reason: str


def objective_item_escalation_decision(
    item: dict[str, Any],
    *,
    confidence_threshold: float = 0.85,
    objective_question_types: Iterable[str] = OBJECTIVE_QUESTION_TYPES,
) -> ObjectiveEscalationDecision:
    question_id = str(item.get("question_id") or item.get("qid") or "").strip()
    if not question_id:
        return ObjectiveEscalationDecision("", False, "question_id_missing")

    question_type = str(item.get("question_type") or "").strip()
    allowed_types = {str(value).strip() for value in objective_question_types}
    if question_type not in allowed_types:
        return ObjectiveEscalationDecision(question_id, False, "question_type_not_objective")

    if _truthy(item.get("objective_need_review", item.get("need_review", False))):
        return ObjectiveEscalationDecision(question_id, True, "objective_need_review")

    if item.get("error"):
        return ObjectiveEscalationDecision(question_id, True, "objective_error")

    review_reason = str(item.get("objective_review_reason") or item.get("review_reason") or "").strip()
    observed_answer = _observed_answer(item)
    auto_scored = _truthy(item.get("objective_auto_scored", item.get("auto_scored", False)))
    score = item.get("objective_score", item.get("score"))
    if auto_scored and _is_zero_score(score) and _is_auto_handled_zero_reason(review_reason, observed_answer):
        return ObjectiveEscalationDecision(question_id, False, "objective_auto_handled_zero")

    if review_reason in ABNORMAL_REVIEW_REASONS or review_reason.startswith("Exception:"):
        return ObjectiveEscalationDecision(question_id, True, review_reason or "objective_abnormal")

    if observed_answer.lower() in MISSING_OR_ABNORMAL_ANSWERS:
        return ObjectiveEscalationDecision(question_id, True, "objective_answer_missing")

    if not auto_scored:
        return ObjectiveEscalationDecision(question_id, True, "objective_not_auto_scored")

    if score is None:
        return ObjectiveEscalationDecision(question_id, True, "objective_score_missing")

    confidence = _normalized_confidence(
        item.get("confidence", item.get("objective_confidence", item.get("confidence_score")))
    )
    if confidence is None:
        return ObjectiveEscalationDecision(question_id, True, "objective_confidence_invalid")
    if confidence < confidence_threshold:
        return ObjectiveEscalationDecision(question_id, True, "objective_low_confidence")

    return ObjectiveEscalationDecision(question_id, False, "objective_high_confidence_local")


def objective_question_ids_for_escalation(
    items: Iterable[dict[str, Any]],
    *,
    confidence_threshold: float = 0.85,
    objective_question_types: Iterable[str] = OBJECTIVE_QUESTION_TYPES,
) -> list[str]:
    escalated_ids: list[str] = []
    seen: set[str] = set()
    for item in items:
        decision = objective_item_escalation_decision(
            item,
            confidence_threshold=confidence_threshold,
            objective_question_types=objective_question_types,
        )
        if not decision.escalate or not decision.question_id or decision.question_id in seen:
            continue
        escalated_ids.append(decision.question_id)
        seen.add(decision.question_id)
    return escalated_ids


def _observed_answer(item: dict[str, Any]) -> str:
    for key in ("objective_answer", "observed_answer", "selected", "raw_answer"):
        if key in item:
            return str(item.get(key) or "").strip()
    return ""


def _is_auto_handled_zero_reason(review_reason: str, observed_answer: str) -> bool:
    reason = str(review_reason or "").strip()
    observed = str(observed_answer or "").strip().lower()
    return reason in AUTO_HANDLED_ZERO_REASONS or observed == "blank" or (observed == "" and reason in AUTO_HANDLED_ZERO_REASONS)


def _is_zero_score(value: Any) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _normalized_confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if confidence < 0:
        return None
    if confidence > 1:
        confidence = confidence / 100.0
    if confidence > 1:
        return None
    return confidence


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)
