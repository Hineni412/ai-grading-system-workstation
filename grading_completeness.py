from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from question_id_contract import (
    QuestionIdCatalog,
    canonicalize_question_document,
)

REVIEW_CONFIDENCE_THRESHOLD = 80.0
OBJECTIVE_REVIEW_CONFIDENCE_THRESHOLD = 70.0
_OBJECTIVE_METADATA_SOURCES = {
    "objective_paper_recognition",
    "objective_batch_recognition",
}


def review_confidence_threshold(objective: bool) -> float:
    return OBJECTIVE_REVIEW_CONFIDENCE_THRESHOLD if objective else REVIEW_CONFIDENCE_THRESHOLD


def is_objective_detail(detail: Any, metadata_item: Mapping | None = None) -> bool:
    """Objective questions auto-score via the objective recognition paths and
    always carry knowledge_ids == ["OBJECTIVE"]; subjective details do not."""
    if isinstance(metadata_item, Mapping) and str(
        metadata_item.get("source") or ""
    ) in _OBJECTIVE_METADATA_SOURCES:
        return True
    knowledge_ids = _detail_value(detail, "knowledge_ids")
    if isinstance(knowledge_ids, str):
        try:
            knowledge_ids = json.loads(knowledge_ids)
        except json.JSONDecodeError:
            knowledge_ids = None
    return isinstance(knowledge_ids, list) and knowledge_ids == ["OBJECTIVE"]


@dataclass(frozen=True)
class _ExpectedQuestion:
    question_id: str
    major_question_id: str
    max_score: float | None


def merge_detail_metadata(previous: Any, incoming: Any, replaced_question_ids: list[str]) -> dict:
    """Replace evidence only for retried questions, retaining all other evidence."""
    old = _safe_json_loads(previous)
    new = _safe_json_loads(incoming)
    result = dict(new) if isinstance(new, dict) else {}
    old_map = old.get("detail_metadata") if isinstance(old, dict) else None
    new_map = result.get("detail_metadata")
    if isinstance(old_map, dict) or isinstance(new_map, dict):
        replaced = set(replaced_question_ids)
        merged = {key: value for key, value in (old_map if isinstance(old_map, dict) else {}).items() if key not in replaced}
        # Incoming full-result metadata may contain retained items too.
        merged.update(new_map if isinstance(new_map, dict) else {})
        result["detail_metadata"] = merged
    return result


def details_require_review(details: list[Any], raw_json: dict) -> bool:
    metadata = raw_json.get("detail_metadata") or {}
    completeness = raw_json.get("grading_completeness") or {}
    if completeness.get("status") in {"incomplete", "invalid"} or raw_json.get("hybrid_batch_fallback"):
        return True
    for detail in details:
        category = str(_detail_value(detail, "error_category") or "")
        if category in {"教师已确认", "已复核", "人工复核"}:
            continue
        confidence = _detail_value(detail, "confidence_score")
        reason = str(_detail_value(detail, "deduction_reason") or "")
        item = metadata.get(str(_detail_value(detail, "question_id"))) or {}
        threshold = review_confidence_threshold(is_objective_detail(detail, item))
        if (confidence is not None and float(confidence) < threshold) or "需复核" in category or "需复核" in reason:
            return True
        if any(item.get(key) is True for key in ("need_review", "needs_human_review")) or any(
            item.get(key) for key in ("step_assessments_error", "score_contract_error")
        ):
            return True
    return False


def audit_grading_details(rubric: dict, details: list[Any]) -> dict:
    normalized_rubric = canonicalize_question_document(rubric)
    catalog = QuestionIdCatalog.from_document(normalized_rubric)
    expected = _expected_questions(normalized_rubric)
    expected_by_question_id = {
        item.question_id: item
        for item in expected
    }
    seen_counts: dict[str, int] = {}
    duplicate_question_ids: list[str] = []
    duplicate_seen: set[str] = set()
    unexpected_question_ids: list[str] = []
    unexpected_seen: set[str] = set()
    score_out_of_range: list[dict[str, Any]] = []

    for detail in details if isinstance(details, list) else []:
        raw_question_id = str(_detail_value(detail, "question_id") or "").strip()
        canonical = catalog.resolve_detail(raw_question_id)
        expected_item = expected_by_question_id.get(canonical or "")
        reported_question_id = expected_item.question_id if expected_item else raw_question_id

        seen_key = canonical or raw_question_id
        seen_counts[seen_key] = seen_counts.get(seen_key, 0) + 1
        if seen_counts[seen_key] > 1 and reported_question_id not in duplicate_seen:
            duplicate_seen.add(reported_question_id)
            duplicate_question_ids.append(reported_question_id)

        if expected_item is None:
            if reported_question_id not in unexpected_seen:
                unexpected_seen.add(reported_question_id)
                unexpected_question_ids.append(reported_question_id)
            max_score = _rubric_score_for_question_id(
                normalized_rubric,
                raw_question_id,
            )
        else:
            max_score = expected_item.max_score

        score_awarded = _detail_value(detail, "score_awarded")
        score_issue = _score_issue(reported_question_id, score_awarded, max_score)
        if score_issue is not None:
            score_out_of_range.append(score_issue)

    missing_question_ids = [
        item.question_id
        for item in expected
        if seen_counts.get(item.question_id, 0) == 0
    ]

    invalid = bool(duplicate_question_ids or unexpected_question_ids or score_out_of_range)
    if invalid:
        status = "invalid"
    elif missing_question_ids:
        status = "incomplete"
    else:
        status = "complete"

    issue_question_ids = [
        *missing_question_ids,
        *duplicate_question_ids,
        *unexpected_question_ids,
        *(str(item.get("question_id") or "") for item in score_out_of_range),
    ]
    affected = {
        major_id
        for question_id in issue_question_ids
        if (major_id := catalog.parent_for(question_id)) is not None
    }
    affected_major_question_ids = [
        major_id
        for major_id in catalog.parent_ids
        if major_id in affected
    ]

    return {
        "status": status,
        "missing_question_ids": missing_question_ids,
        "duplicate_question_ids": duplicate_question_ids,
        "unexpected_question_ids": unexpected_question_ids,
        "score_out_of_range": score_out_of_range,
        "affected_major_question_ids": affected_major_question_ids,
    }


def major_question_id(rubric: dict, question_id: str) -> str | None:
    catalog = QuestionIdCatalog.from_document(rubric)
    return catalog.parent_for(question_id)


def major_question_ids_for_issues(audit: dict) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    values = audit.get("affected_major_question_ids", []) if isinstance(audit, dict) else []
    for value in values if isinstance(values, list) else []:
        question_id = str(value or "").strip()
        if question_id and question_id not in seen:
            seen.add(question_id)
            result.append(question_id)
    return result


def resolve_grading_completeness(
    raw_json: Any,
    rubric: dict[str, Any] | None = None,
    details: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    completeness = _normalized_completeness_dict(raw_json)
    if completeness is not None:
        return completeness

    parsed = _safe_json_loads(raw_json)
    if isinstance(parsed, dict):
        completeness = _normalized_completeness_dict(
            parsed.get("grading_completeness")
        )
        if completeness is not None:
            return completeness

    if isinstance(rubric, dict):
        normalized = _normalized_completeness_dict(
            audit_grading_details(rubric, details or [])
        )
        if normalized is not None:
            return normalized

    if isinstance(parsed, dict):
        return _legacy_fallback_completeness(
            parsed.get("hybrid_batch_fallback"),
            rubric or {},
        )
    return None


def rubric_exact_question_id(rubric: dict, question_id: str) -> str | None:
    catalog = QuestionIdCatalog.from_document(rubric)
    return catalog.resolve_detail(question_id)


def _expected_questions(rubric: dict) -> list[_ExpectedQuestion]:
    result: list[_ExpectedQuestion] = []
    for question in _rubric_questions(rubric):
        major_id = str(question.get("question_id") or "").strip()
        if not major_id:
            continue
        parts = _question_parts(question)
        if parts:
            for part in parts:
                part_id = str(part.get("part_id") or "").strip()
                if part_id:
                    result.append(
                        _ExpectedQuestion(
                            question_id=part_id,
                            major_question_id=major_id,
                            max_score=_score_value(part, ("part_score", "max_score", "score")),
                        )
                    )
        else:
            result.append(
                _ExpectedQuestion(
                    question_id=major_id,
                    major_question_id=major_id,
                    max_score=_score_value(question, ("max_score", "score")),
                )
            )
    return result


def _safe_json_loads(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _normalized_completeness_dict(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    status = str(value.get("status") or "").strip()
    if status not in {"complete", "incomplete", "invalid"}:
        return None
    raw_score_issues = value.get("score_out_of_range")
    score_out_of_range = (
        [dict(item) for item in raw_score_issues if isinstance(item, dict)]
        if isinstance(raw_score_issues, list)
        else []
    )
    return {
        "status": status,
        "missing_question_ids": _unique_text_list(
            value.get("missing_question_ids")
        ),
        "duplicate_question_ids": _unique_text_list(
            value.get("duplicate_question_ids")
        ),
        "unexpected_question_ids": _unique_text_list(
            value.get("unexpected_question_ids")
        ),
        "score_out_of_range": score_out_of_range,
        "affected_major_question_ids": _unique_text_list(
            value.get("affected_major_question_ids")
        ),
    }


def _legacy_fallback_completeness(
    value: Any,
    rubric: dict[str, Any],
) -> dict[str, Any] | None:
    if isinstance(value, dict):
        items = value.get("items") if isinstance(value.get("items"), list) else []
    elif isinstance(value, list):
        items = value
    elif value:
        items = []
    else:
        return None

    affected_major_question_ids: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        question_id = str(item.get("question_id") or "").strip()
        if not question_id:
            continue
        major_id = major_question_id(rubric, question_id) or question_id
        if major_id not in affected_major_question_ids:
            affected_major_question_ids.append(major_id)
    return {
        "status": "incomplete",
        "missing_question_ids": [],
        "duplicate_question_ids": [],
        "unexpected_question_ids": [],
        "score_out_of_range": [],
        "affected_major_question_ids": affected_major_question_ids,
    }


def _unique_text_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _rubric_score_for_question_id(rubric: dict, question_id: str) -> float | None:
    catalog = QuestionIdCatalog.from_document(rubric)
    resolved = catalog.resolve_detail(question_id)
    if resolved is None:
        return None
    for item in _expected_questions(rubric):
        if item.question_id == resolved:
            return item.max_score
    return None


def _score_issue(
    question_id: str,
    raw_score: Any,
    max_score: float | None,
) -> dict[str, Any] | None:
    if isinstance(raw_score, bool):
        numeric_score = None
    else:
        try:
            numeric_score = float(raw_score)
        except (TypeError, ValueError):
            numeric_score = None
    if numeric_score is None or not math.isfinite(numeric_score):
        return {
            "question_id": question_id,
            "score_awarded": raw_score,
            "max_score": max_score,
            "reason": "non_numeric",
        }
    if numeric_score < 0:
        return {
            "question_id": question_id,
            "score_awarded": numeric_score,
            "max_score": max_score,
            "reason": "negative",
        }
    if max_score is not None and numeric_score > max_score:
        return {
            "question_id": question_id,
            "score_awarded": numeric_score,
            "max_score": max_score,
            "reason": "above_max",
        }
    return None


def _detail_value(detail: Any, field_name: str) -> Any:
    if isinstance(detail, dict):
        return detail.get(field_name)
    return getattr(detail, field_name, None)


def _rubric_questions(rubric: dict) -> list[dict[str, Any]]:
    questions = rubric.get("questions", []) if isinstance(rubric, dict) else []
    return [question for question in questions if isinstance(question, dict)] if isinstance(questions, list) else []


def _question_parts(question: dict[str, Any]) -> list[dict[str, Any]]:
    parts = question.get("parts", [])
    return [part for part in parts if isinstance(part, dict)] if isinstance(parts, list) else []


def _major_question_ids(rubric: dict) -> list[str]:
    return [
        question_id
        for question in _rubric_questions(rubric)
        if (question_id := str(question.get("question_id") or "").strip())
    ]


def _score_value(item: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key not in item or item.get(key) is None:
            continue
        value = item.get(key)
        if isinstance(value, bool):
            return None
        try:
            score = float(value)
        except (TypeError, ValueError):
            return None
        return score if math.isfinite(score) else None
    return None
