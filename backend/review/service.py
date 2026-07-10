from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from db_manager import DBManager, ReviewAdjustmentOwnershipError
from backend.public_data import sanitize_public_mapping
from path_manager import resolve_stored_file_path


REVIEW_CONFIRMED_REASON = "人工复核已确认"
REVIEW_CONFIRMED_CATEGORY = "已复核"
REVIEW_CONFIRMED_SUMMARY = "manual_review_confirmed"


class ReviewDetailNotFoundError(LookupError):
    def __init__(
        self,
        *,
        session_id: int,
        question_id: str,
        result_id: int,
        detail_id: int,
    ) -> None:
        self.session_id = int(session_id)
        self.question_id = str(question_id)
        self.result_id = int(result_id)
        self.detail_id = int(detail_id)
        super().__init__(
            "Review detail not found for the requested session, result, and question."
        )


class ReviewValidationError(ValueError):
    """Raised when a review confirmation request is unsafe to apply."""


@dataclass(frozen=True, slots=True)
class ReviewItem:
    session_id: int
    result_id: int
    detail_id: int
    question_id: str
    student_code: str | None
    student_name: str
    class_name: str | None
    score_awarded: float
    max_score: float
    deduction_reason: str | None
    error_category: str | None
    error_summary: str | None
    confidence_score: float | None
    needs_review: bool
    candidate_scores: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReviewQuestion:
    question_id: str
    total_count: int
    needs_review_count: int
    max_score: float


@dataclass(frozen=True, slots=True)
class ReviewConfirmationInput:
    result_id: int
    detail_id: int
    score_awarded: float
    deduction_reason: str | None = None
    error_category: str | None = None
    error_summary: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewAnnotationOutcome:
    result_id: int
    status: str
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewConfirmationResult:
    updated_details: int
    updated_results: int
    annotation_outcomes: list[ReviewAnnotationOutcome] = field(default_factory=list)


class ReviewApplicationService:
    def __init__(self, db: DBManager, manual_review_service: Any | None = None) -> None:
        self.db = db
        self.manual_review_service = manual_review_service

    def list_items(
        self,
        session_id: int,
        session: dict[str, Any],
        *,
        requested_question_id: str | None = None,
        needs_review_only: bool = False,
    ) -> list[ReviewItem]:
        score_map = _load_score_map(session, self.db)
        items: list[ReviewItem] = []
        for row in self.db.get_session_review_rows(int(session_id)):
            question_id = str(row.get("question_id") or "").strip()
            if not question_id:
                continue
            raw_json = row.get("raw_json") if isinstance(row.get("raw_json"), dict) else {}
            metadata = _detail_metadata_for_qid(raw_json, question_id)
            candidate_scores = metadata.get("candidate_scores") if isinstance(metadata, dict) else []
            confidence = _clean_confidence(row.get("confidence_score"))
            items.append(
                ReviewItem(
                    session_id=int(session_id),
                    result_id=int(row.get("result_id") or 0),
                    detail_id=int(row.get("detail_id") or 0),
                    question_id=question_id,
                    student_code=_clean_optional_text(row.get("student_code")),
                    student_name=str(row.get("student_name") or row.get("ocr_name") or ""),
                    class_name=_clean_optional_text(row.get("class_name")),
                    score_awarded=float(row.get("score_awarded") or 0),
                    max_score=float(score_map.get(question_id) or 0),
                    deduction_reason=_clean_optional_text(row.get("deduction_reason")),
                    error_category=_clean_optional_text(row.get("error_category")),
                    error_summary=_clean_optional_text(row.get("error_summary")),
                    confidence_score=confidence,
                    needs_review=_is_substantive_review_reason(
                        str(row.get("deduction_reason") or ""),
                        str(row.get("error_category") or ""),
                        confidence,
                    ),
                    candidate_scores=candidate_scores if isinstance(candidate_scores, list) else [],
                    metadata=metadata,
                )
            )
        filtered_items = [
            item
            for item in items
            if (
                requested_question_id is None
                or item.question_id == requested_question_id
            )
            and (not needs_review_only or item.needs_review)
        ]
        return sorted(
            filtered_items,
            key=lambda item: (
                _question_sort_key(item.question_id),
                item.class_name or "",
                item.student_code or "",
                item.student_name,
                item.detail_id,
            ),
        )

    def list_questions(
        self,
        session_id: int,
        session: dict[str, Any],
    ) -> list[ReviewQuestion]:
        by_question: dict[str, ReviewQuestion] = {}
        for item in self.list_items(session_id, session):
            current = by_question.get(item.question_id)
            if current is None:
                current = ReviewQuestion(
                    question_id=item.question_id,
                    total_count=0,
                    needs_review_count=0,
                    max_score=item.max_score,
                )
            by_question[item.question_id] = ReviewQuestion(
                question_id=item.question_id,
                total_count=current.total_count + 1,
                needs_review_count=current.needs_review_count + int(item.needs_review),
                max_score=max(current.max_score, item.max_score),
            )
        return sorted(
            by_question.values(),
            key=lambda item: _question_sort_key(item.question_id),
        )

    def prepare_adjustments(
        self,
        session_id: int,
        session: dict[str, Any],
        question_id: str,
        items: list[ReviewConfirmationInput],
    ) -> list[dict[str, Any]]:
        requested_session_id = int(session_id)
        requested_question_id = str(question_id or "").strip()
        session_context_id = session.get("id")
        if session_context_id is not None and int(session_context_id) != requested_session_id:
            raise ReviewValidationError("Session context does not match the requested session.")
        if not items:
            raise ReviewValidationError("Review confirmation must contain at least one item.")

        score_map = _load_score_map(session, self.db)
        detail_lookup = {
            int(row.get("detail_id") or 0): row
            for row in self.db.get_session_review_rows(requested_session_id)
        }
        normalized: list[dict[str, Any]] = []
        seen_detail_ids: set[int] = set()
        for item in items:
            result_id = int(item.result_id)
            detail_id = int(item.detail_id)
            if detail_id in seen_detail_ids:
                raise ReviewValidationError(
                    f"Review confirmation contains duplicate detail_id {detail_id}."
                )
            seen_detail_ids.add(detail_id)

            row = detail_lookup.get(detail_id)
            row_result_id = int(row.get("result_id") or 0) if row is not None else 0
            row_question_id = (
                str(row.get("question_id") or "").strip() if row is not None else ""
            )
            if (
                row is None
                or row_result_id != result_id
                or row_question_id != requested_question_id
            ):
                raise ReviewDetailNotFoundError(
                    session_id=requested_session_id,
                    question_id=requested_question_id,
                    result_id=result_id,
                    detail_id=detail_id,
                )

            try:
                score = float(item.score_awarded)
            except (TypeError, ValueError) as exc:
                raise ReviewValidationError(
                    f"{requested_question_id} score must be a finite nonnegative number."
                ) from exc
            if not math.isfinite(score) or score < 0:
                raise ReviewValidationError(
                    f"{requested_question_id} score must be a finite nonnegative number."
                )

            max_score = score_map.get(requested_question_id)
            if max_score is None or not math.isfinite(max_score) or max_score < 0:
                raise ReviewValidationError(
                    f"No valid rubric maximum is available for {requested_question_id}."
                )
            if score > max_score:
                raise ReviewValidationError(
                    f"{requested_question_id} score {score:g} exceeds rubric maximum {max_score:g}."
                )

            normalized.append(
                {
                    "session_id": requested_session_id,
                    "result_id": result_id,
                    "detail_id": detail_id,
                    "question_id": requested_question_id,
                    "score_awarded": score,
                    "deduction_reason": item.deduction_reason or REVIEW_CONFIRMED_REASON,
                    "error_category": item.error_category or REVIEW_CONFIRMED_CATEGORY,
                    "error_summary": item.error_summary or REVIEW_CONFIRMED_SUMMARY,
                }
            )
        return normalized

    def confirm(
        self,
        session_id: int,
        session: dict[str, Any],
        question_id: str,
        items: list[ReviewConfirmationInput],
    ) -> ReviewConfirmationResult:
        if self.manual_review_service is None:
            raise RuntimeError("Manual review service is required for confirmation.")
        adjustments = self.prepare_adjustments(
            session_id,
            session,
            question_id,
            items,
        )
        try:
            result = self.manual_review_service.apply_review_adjustments(
                session_id,
                adjustments,
                highlight_qids=[question_id],
            )
        except ReviewAdjustmentOwnershipError as exc:
            raise ReviewDetailNotFoundError(
                session_id=exc.session_id,
                question_id=exc.question_id,
                result_id=exc.result_id,
                detail_id=exc.detail_id,
            ) from exc
        annotation_outcomes = [
            ReviewAnnotationOutcome(
                result_id=int(item["result_id"]),
                status=str(item["status"]),
                message=item.get("message"),
            )
            for item in result.get("annotation_outcomes", [])
        ]
        return ReviewConfirmationResult(
            updated_details=int(result.get("updated_details") or 0),
            updated_results=int(result.get("updated_results") or 0),
            annotation_outcomes=annotation_outcomes,
        )


def _data_root(db: DBManager) -> Path | None:
    return db.db_path.parent.parent if db.db_path.parent.name == "databases" else None


def _question_sort_key(question_id: str) -> list[Any]:
    parts = re.split(r"(\d+)", str(question_id or ""))
    return [int(part) if part.isdigit() else part for part in parts]


def _clean_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return None
    return text


def _clean_confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    return confidence if math.isfinite(confidence) else None


def _load_score_map(session: dict[str, Any], db: DBManager) -> dict[str, float]:
    rubric_path = resolve_stored_file_path(
        session.get("rubric_path"),
        data_root=_data_root(db),
    )
    if not rubric_path.exists():
        return {}
    try:
        rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    questions = rubric.get("questions") if isinstance(rubric, dict) else []
    if not isinstance(questions, list):
        return {}

    scores: dict[str, float] = {}
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = str(question.get("question_id") or "").strip()
        if question_id:
            try:
                scores[question_id] = float(question.get("max_score", 0))
            except (TypeError, ValueError):
                pass
        parts = question.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_id = str(part.get("part_id") or "").strip()
            if not part_id:
                continue
            try:
                scores[part_id] = float(part.get("part_score", 0))
            except (TypeError, ValueError):
                continue
    return scores


def _detail_metadata_for_qid(raw_json: dict[str, Any], question_id: str) -> dict[str, Any]:
    metadata_map = raw_json.get("detail_metadata") if isinstance(raw_json, dict) else None
    if not isinstance(metadata_map, dict):
        return {}
    direct = metadata_map.get(question_id)
    if not isinstance(direct, dict):
        return {}

    metadata = sanitize_public_mapping(
        {
            key: direct[key]
            for key in ("question_id", "evidence_steps", "missing_steps")
            if key in direct
        }
    )
    raw_candidates = direct.get("candidate_scores")
    if isinstance(raw_candidates, list):
        candidates = []
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            candidate = sanitize_public_mapping(
                {
                    key: item[key]
                    for key in ("score", "confidence", "reason")
                    if key in item
                }
            )
            if candidate:
                candidates.append(candidate)
        metadata["candidate_scores"] = candidates
    return metadata


def _is_substantive_review_reason(reason: str, cat: str = "", confidence: Any = None) -> bool:
    text = str(reason or "").strip()
    cat_text = str(cat or "").strip()
    if "已复核" in cat_text or "人工复核" in cat_text:
        return False

    review_markers = ["需复核", "踴", "锟借复锟"]
    if any(marker in cat_text for marker in review_markers):
        return True
    if any(marker in text for marker in review_markers):
        return True

    clean_confidence = _clean_confidence(confidence)
    return clean_confidence is not None and clean_confidence < 80.0
