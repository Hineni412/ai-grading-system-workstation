from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from backend.config_workspace.editor import project_config_editor
from backend.config_workspace.publish import load_editor_config
from backend.repositories.access import GradingRepositoryAccess
from question_id_contract import (
    QuestionIdCatalog,
    QuestionIdContractError,
    canonicalize_grading_config_payload,
)


class ReviewRubricQuestionConflictError(ValueError):
    """Raised when historical question identifiers cannot be resolved safely."""


class ReviewRubricConfigError(RuntimeError):
    """Raised when the stored scoring configuration cannot be projected."""


@dataclass(frozen=True, slots=True)
class ReviewRubricPoint:
    part_id: str
    part_label: str
    step_id: str
    core_goal: str
    score: float
    standard_answer: str
    accepted_answers: tuple[str, ...]
    match_rule: str
    required_elements: tuple[str, ...]
    deduction_rules: tuple[str, ...]
    answer_only_max_score: float | None
    require_final_answer: bool | None
    final_answer_rule: str


@dataclass(frozen=True, slots=True)
class ReviewRubricSection:
    question_id: str
    parent_question_id: str
    question_type: str | None
    max_score: float
    knowledge_labels: tuple[str, ...]
    points: tuple[ReviewRubricPoint, ...]


def load_review_rubric_section(
    db: GradingRepositoryAccess,
    session_id: int,
    requested_question_id: str,
) -> ReviewRubricSection | None:
    """Project one scoring item from rubric and answer-key configuration.

    All identifier normalization happens on an in-memory copy. Stored rubric and
    answer-key files remain untouched.
    """

    requested = str(requested_question_id or "").strip()
    if not requested:
        return None

    try:
        loaded = load_editor_config(db, int(session_id))
    except (OSError, ValueError, KeyError) as exc:
        raise ReviewRubricConfigError(
            "The stored scoring configuration is unavailable."
        ) from exc
    if not loaded.configured:
        return None

    raw_rubric = loaded.payload.get("rubric")
    if not isinstance(raw_rubric, dict):
        raise ReviewRubricConfigError("The stored rubric is invalid.")
    try:
        catalog = QuestionIdCatalog.from_document(raw_rubric)
    except QuestionIdContractError as exc:
        raise ReviewRubricQuestionConflictError(
            "The rubric contains ambiguous question identifiers."
        ) from exc
    raw_answer_key = loaded.payload.get("answer_key")
    if isinstance(raw_answer_key, dict):
        try:
            QuestionIdCatalog.from_document(raw_answer_key)
        except QuestionIdContractError as exc:
            raise ReviewRubricQuestionConflictError(
                "The answer key contains ambiguous question identifiers."
            ) from exc

    resolved = catalog.resolve(requested)
    if resolved is None:
        return None
    parent_question_id = _parent_for_resolved(catalog, resolved)
    if parent_question_id is None:
        return None

    normalized = canonicalize_grading_config_payload(loaded.payload)
    normalized_rubric = normalized.get("rubric")
    if not isinstance(normalized_rubric, dict):
        raise ReviewRubricConfigError("The normalized rubric is invalid.")
    question = _question_for_parent(
        normalized_rubric,
        catalog,
        parent_question_id,
    )
    if question is None:
        return None

    try:
        projected = project_config_editor(normalized)
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewRubricConfigError(
            "The stored scoring configuration cannot be projected."
        ) from exc

    question_id = str(question.get("question_id") or "").strip()
    parts = [
        part
        for part in question.get("parts", [])
        if isinstance(part, dict)
    ]
    if parts and not (
        resolved == parent_question_id and len(parts) > 1
    ):
        rows = [
            row
            for row in projected
            if row.question_id == question_id and row.part_id == resolved
        ]
        target_part = next(
            (
                part
                for part in parts
                if str(part.get("part_id") or "").strip() == resolved
            ),
            None,
        )
        max_score = _score_value(
            target_part.get("part_score") if target_part else None,
            fallback=question.get("max_score"),
        )
        response_question_id = resolved
    else:
        rows = [
            row
            for row in projected
            if row.question_id == question_id
        ]
        max_score = _score_value(question.get("max_score"))
        response_question_id = parent_question_id

    return ReviewRubricSection(
        question_id=response_question_id,
        parent_question_id=parent_question_id,
        question_type=_optional_text(question.get("question_type")),
        max_score=max_score,
        knowledge_labels=_knowledge_labels(question),
        points=tuple(
            ReviewRubricPoint(
                part_id=row.part_id,
                part_label=row.part_label,
                step_id=row.step_id,
                core_goal=row.core_goal,
                score=float(row.score),
                standard_answer=row.standard_answer,
                accepted_answers=tuple(row.accepted_answers),
                match_rule=row.match_rule,
                required_elements=tuple(row.required_elements),
                deduction_rules=tuple(row.deduction_rules),
                answer_only_max_score=row.answer_only_max_score,
                require_final_answer=row.require_final_answer,
                final_answer_rule=row.final_answer_rule,
            )
            for row in rows
        ),
    )


def _parent_for_resolved(
    catalog: QuestionIdCatalog,
    resolved: str,
) -> str | None:
    for parent_id, detail_ids in catalog.parts_by_parent.items():
        if resolved == parent_id or resolved in detail_ids:
            return parent_id
    return None


def _question_for_parent(
    rubric: dict[str, Any],
    catalog: QuestionIdCatalog,
    parent_question_id: str,
) -> dict[str, Any] | None:
    questions = [
        question
        for question in rubric.get("questions", [])
        if isinstance(question, dict)
        and str(question.get("question_id") or "").strip()
    ]
    for parent_id, question in zip(catalog.parent_ids, questions):
        if parent_id == parent_question_id:
            return question
    return None


def _score_value(value: Any, *, fallback: Any = None) -> float:
    for candidate in (value, fallback):
        try:
            score = float(candidate)
        except (TypeError, ValueError):
            continue
        if math.isfinite(score) and score >= 0:
            return score
    return 0.0


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _knowledge_labels(question: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    points = question.get("knowledge_points")
    if isinstance(points, list):
        for point in points:
            if not isinstance(point, dict):
                continue
            for key in ("knowledge_name", "name", "knowledge_id", "id"):
                text = _optional_text(point.get(key))
                if text is not None:
                    values.append(text)
                    break
    knowledge_ids = question.get("knowledge_ids")
    if isinstance(knowledge_ids, list):
        values.extend(
            text
            for value in knowledge_ids
            if (text := _optional_text(value)) is not None
        )
    if not values:
        for key in ("knowledge_name", "knowledge_id"):
            text = _optional_text(question.get(key))
            if text is not None:
                values.append(text)
    return tuple(dict.fromkeys(values))


__all__ = [
    "ReviewRubricConfigError",
    "ReviewRubricPoint",
    "ReviewRubricQuestionConflictError",
    "ReviewRubricSection",
    "load_review_rubric_section",
]
