from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from backend.repositories.review import ReviewAdjustmentOwnershipError
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


class ReviewRevisionConflictError(RuntimeError):
    """Raised when another browser has already changed a teacher score."""


@dataclass(frozen=True, slots=True)
class ReviewItem:
    review_item_id: str
    revision: int
    session_id: int
    student_id: int
    result_id: int | None
    detail_id: int | None
    question_id: str
    student_code: str | None
    student_name: str
    class_name: str | None
    score_awarded: float | None
    max_score: float
    deduction_reason: str | None
    error_category: str | None
    error_summary: str | None
    confidence_score: float | None
    needs_review: bool
    score_status: str
    score_source: str
    teacher_locked: bool
    candidate_scores: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReviewQuestion:
    question_id: str
    total_count: int
    needs_review_count: int
    ungraded_count: int
    teacher_confirmed_count: int
    max_score: float


@dataclass(frozen=True, slots=True)
class ReviewConfirmationInput:
    result_id: int | None
    detail_id: int | None
    score_awarded: float
    review_item_id: str | None = None
    expected_revision: int = 0
    student_id: int | None = None
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
    def __init__(
        self,
        db: GradingRepositoryAccess,
        manual_review_service: Any | None = None,
    ) -> None:
        self.db = as_grading_repositories(db)
        self.manual_review_service = manual_review_service

    def list_items(
        self,
        session_id: int,
        session: dict[str, Any],
        *,
        requested_question_id: str | None = None,
        needs_review_only: bool = False,
        scope: str | None = None,
        manual_context: dict[str, Any] | None = None,
    ) -> list[ReviewItem]:
        score_map = _load_scoring_item_map(session, self.db)
        raw_rows = self.db.get_session_review_rows(int(session_id))
        if manual_context is not None:
            items = self._list_unified_items(
                int(session_id),
                score_map,
                raw_rows,
                manual_context,
            )
        else:
            items = [
                item
                for row in raw_rows
                if (
                    item := self._review_item_from_ai_row(
                        int(session_id),
                        score_map,
                        row,
                    )
                )
                is not None
            ]

        normalized_scope = str(scope or "").strip()
        if normalized_scope not in {
            "",
            "teacher_pending",
            "ungraded",
            "ai_review",
            "teacher_final",
            "all",
        }:
            raise ReviewValidationError("Unsupported review scope.")
        filtered_items = [
            item
            for item in items
            if (
                requested_question_id is None
                or item.question_id == requested_question_id
            )
            and _item_matches_scope(
                item,
                normalized_scope,
                needs_review_only,
            )
        ]
        return sorted(
            filtered_items,
            key=lambda item: (
                _question_sort_key(item.question_id),
                item.class_name or "",
                item.student_code or "",
                item.student_name,
                item.review_item_id,
            ),
        )

    def _review_item_from_ai_row(
        self,
        session_id: int,
        score_map: dict[str, float],
        row: dict[str, Any],
    ) -> ReviewItem | None:
        question_id = str(row.get("question_id") or "").strip()
        if not question_id:
            return None
        raw_json = (
            row.get("raw_json")
            if isinstance(row.get("raw_json"), dict)
            else {}
        )
        metadata = _detail_metadata_for_qid(raw_json, question_id)
        candidate_scores = (
            metadata.get("candidate_scores")
            if isinstance(metadata, dict)
            else []
        )
        confidence = _clean_confidence(row.get("confidence_score"))
        needs_review = _is_substantive_review_reason(
            str(row.get("deduction_reason") or ""),
            str(row.get("error_category") or ""),
            confidence,
        )
        detail_id = int(row.get("detail_id") or 0)
        student_id = int(row.get("student_id") or 0)
        if detail_id <= 0 or student_id <= 0:
            return None
        return ReviewItem(
            review_item_id=f"legacy:{detail_id}",
            revision=0,
            session_id=session_id,
            student_id=student_id,
            result_id=int(row.get("result_id") or 0),
            detail_id=detail_id,
            question_id=question_id,
            student_code=_clean_optional_text(row.get("student_code")),
            student_name=str(
                row.get("student_name") or row.get("ocr_name") or ""
            ),
            class_name=_clean_optional_text(row.get("class_name")),
            score_awarded=float(row.get("score_awarded") or 0),
            max_score=float(score_map.get(question_id) or 0),
            deduction_reason=_clean_optional_text(row.get("deduction_reason")),
            error_category=_clean_optional_text(row.get("error_category")),
            error_summary=_clean_optional_text(row.get("error_summary")),
            confidence_score=confidence,
            needs_review=needs_review,
            score_status="ai_review" if needs_review else "ai_ready",
            score_source="ai",
            teacher_locked=False,
            candidate_scores=(
                candidate_scores
                if isinstance(candidate_scores, list)
                else []
            ),
            metadata=metadata,
        )

    def _list_unified_items(
        self,
        session_id: int,
        score_map: dict[str, float],
        raw_rows: list[dict[str, Any]],
        manual_context: dict[str, Any],
    ) -> list[ReviewItem]:
        scan_batch_id = str(manual_context.get("scan_batch_id") or "").strip()
        papers = [
            item
            for item in manual_context.get("papers", [])
            if isinstance(item, dict) and item.get("student_id") is not None
        ]
        locks = self.db.reviews.list_teacher_score_locks(
            session_id,
            scan_batch_id,
        )
        lock_by_key = {
            (int(item["student_id"]), str(item["question_id"])): item
            for item in locks
        }
        ai_by_key = {
            (
                int(row.get("student_id") or 0),
                str(row.get("question_id") or "").strip(),
            ): row
            for row in raw_rows
            if int(row.get("student_id") or 0) > 0
            and str(row.get("question_id") or "").strip()
        }
        students = {
            int(item["id"]): item
            for item in self.db.list_students()
            if item.get("id") is not None
        }
        regions = [
            dict(item)
            for item in self.db.list_answer_regions(session_id)
        ]

        items: list[ReviewItem] = []
        for paper in papers:
            student_id = int(paper["student_id"])
            student = students.get(student_id, {})
            for question_id, max_score in score_map.items():
                row = ai_by_key.get((student_id, question_id))
                lock = lock_by_key.get((student_id, question_id))
                ai_item = (
                    self._review_item_from_ai_row(
                        session_id,
                        score_map,
                        row,
                    )
                    if row is not None
                    else None
                )
                metadata = dict(ai_item.metadata) if ai_item else {}
                metadata.update(
                    {
                        "scan_batch_id": scan_batch_id,
                        "preflight_target_type": str(
                            paper.get("target_type") or ""
                        ),
                        "preflight_target_id": str(
                            paper.get("target_id") or ""
                        ),
                        "preflight_front_media_url": str(
                            paper.get("front_media_url") or ""
                        ),
                        "preflight_back_media_url": (
                            str(paper.get("back_media_url"))
                            if paper.get("back_media_url")
                            else None
                        ),
                        "source_region_id": _source_region_id(
                            regions,
                            question_id,
                        ),
                    }
                )
                teacher_locked = lock is not None
                lock_scale_changed = bool(
                    lock
                    and (
                        abs(
                            float(lock.get("max_score") or 0)
                            - float(max_score)
                        )
                        > 1e-6
                        or float(lock.get("score_awarded") or 0)
                        > float(max_score) + 1e-6
                    )
                )
                if teacher_locked:
                    score_status = (
                        "failed"
                        if lock_scale_changed
                        else "teacher_final"
                    )
                    score_source = "teacher"
                    score_awarded: float | None = float(
                        lock["score_awarded"]
                    )
                    needs_review = lock_scale_changed
                elif ai_item is not None:
                    score_status = ai_item.score_status
                    score_source = "ai"
                    score_awarded = ai_item.score_awarded
                    needs_review = ai_item.needs_review
                else:
                    score_status = "ungraded"
                    score_source = "none"
                    score_awarded = None
                    needs_review = True
                items.append(
                    ReviewItem(
                        review_item_id=(
                            f"{scan_batch_id}:{student_id}:{question_id}"
                        ),
                        revision=int(lock.get("revision") or 0)
                        if lock
                        else 0,
                        session_id=session_id,
                        student_id=student_id,
                        result_id=ai_item.result_id if ai_item else None,
                        detail_id=ai_item.detail_id if ai_item else None,
                        question_id=question_id,
                        student_code=_clean_optional_text(
                            student.get("student_code")
                        ),
                        student_name=str(
                            student.get("name")
                            or paper.get("student_name")
                            or ""
                        ),
                        class_name=_clean_optional_text(
                            student.get("class_name")
                        ),
                        score_awarded=score_awarded,
                        max_score=float(max_score),
                        deduction_reason=(
                            _clean_optional_text(lock.get("deduction_reason"))
                            if lock
                            else (
                                ai_item.deduction_reason
                                if ai_item
                                else None
                            )
                        ),
                        error_category=(
                            (
                                "评分依据已变化"
                                if lock_scale_changed
                                else "教师已确认"
                            )
                            if teacher_locked
                            else (
                                ai_item.error_category
                                if ai_item
                                else None
                            )
                        ),
                        error_summary=(
                            (
                                "teacher_score_scale_changed"
                                if lock_scale_changed
                                else "teacher_score_locked"
                            )
                            if teacher_locked
                            else (
                                ai_item.error_summary
                                if ai_item
                                else None
                            )
                        ),
                        confidence_score=(
                            ai_item.confidence_score if ai_item else None
                        ),
                        needs_review=needs_review,
                        score_status=score_status,
                        score_source=score_source,
                        teacher_locked=teacher_locked,
                        candidate_scores=(
                            list(ai_item.candidate_scores)
                            if ai_item
                            else []
                        ),
                        metadata=metadata,
                    )
                )
        return items

    def list_questions(
        self,
        session_id: int,
        session: dict[str, Any],
        *,
        scope: str | None = None,
        manual_context: dict[str, Any] | None = None,
    ) -> list[ReviewQuestion]:
        by_question: dict[str, ReviewQuestion] = {}
        for item in self.list_items(
            session_id,
            session,
            scope=scope,
            manual_context=manual_context,
        ):
            current = by_question.get(item.question_id)
            if current is None:
                current = ReviewQuestion(
                    question_id=item.question_id,
                    total_count=0,
                    needs_review_count=0,
                    ungraded_count=0,
                    teacher_confirmed_count=0,
                    max_score=item.max_score,
                )
            by_question[item.question_id] = ReviewQuestion(
                question_id=item.question_id,
                total_count=current.total_count + 1,
                needs_review_count=current.needs_review_count + int(item.needs_review),
                ungraded_count=(
                    current.ungraded_count
                    + int(item.score_status == "ungraded")
                ),
                teacher_confirmed_count=(
                    current.teacher_confirmed_count
                    + int(item.teacher_locked)
                ),
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
        *,
        manual_context: dict[str, Any] | None = None,
    ) -> ReviewConfirmationResult:
        if manual_context is not None:
            return self._confirm_unified_items(
                int(session_id),
                session,
                str(question_id),
                items,
                manual_context,
            )
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

    def _confirm_unified_items(
        self,
        session_id: int,
        session: dict[str, Any],
        question_id: str,
        inputs: list[ReviewConfirmationInput],
        manual_context: dict[str, Any],
    ) -> ReviewConfirmationResult:
        requested_question_id = str(question_id or "").strip()
        if not requested_question_id or not inputs:
            raise ReviewValidationError(
                "Teacher confirmation must contain at least one item."
            )
        scan_batch_id = str(
            manual_context.get("scan_batch_id") or ""
        ).strip()
        if not scan_batch_id:
            raise ReviewValidationError(
                "The current frozen scan batch is unavailable."
            )
        available = self.list_items(
            session_id,
            session,
            requested_question_id=requested_question_id,
            scope="all",
            manual_context=manual_context,
        )
        by_review_item_id = {
            item.review_item_id: item for item in available
        }
        by_legacy_identity = {
            (item.result_id, item.detail_id): item
            for item in available
            if item.result_id is not None and item.detail_id is not None
        }
        confirmations: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        result_ids: set[int] = set()
        for raw in inputs:
            item = (
                by_review_item_id.get(str(raw.review_item_id))
                if raw.review_item_id
                else by_legacy_identity.get((raw.result_id, raw.detail_id))
            )
            if item is None:
                raise ReviewDetailNotFoundError(
                    session_id=session_id,
                    question_id=requested_question_id,
                    result_id=int(raw.result_id or 0),
                    detail_id=int(raw.detail_id or 0),
                )
            if item.review_item_id in seen_ids:
                raise ReviewValidationError(
                    "Teacher confirmation contains a duplicate item."
                )
            seen_ids.add(item.review_item_id)
            if (
                raw.student_id is not None
                and int(raw.student_id) != item.student_id
            ):
                raise ReviewValidationError(
                    "Teacher confirmation student does not match the item."
                )
            try:
                score = float(raw.score_awarded)
            except (TypeError, ValueError) as exc:
                raise ReviewValidationError(
                    "Teacher score must be a finite nonnegative number."
                ) from exc
            if (
                not math.isfinite(score)
                or score < 0
                or score > item.max_score
            ):
                raise ReviewValidationError(
                    f"{requested_question_id} score must be between "
                    f"0 and {item.max_score:g}."
                )
            expected_revision = int(raw.expected_revision)
            if expected_revision != item.revision:
                raise ReviewRevisionConflictError(
                    "Teacher score changed in another window."
                )
            confirmations.append(
                {
                    "student_id": item.student_id,
                    "question_id": item.question_id,
                    "score_awarded": score,
                    "max_score": item.max_score,
                    "deduction_reason": (
                        raw.deduction_reason or REVIEW_CONFIRMED_REASON
                    ),
                    "source_target_type": str(
                        "answer_region"
                    ),
                    "source_target_id": int(
                        item.metadata.get("source_region_id") or 0
                    ),
                    "expected_revision": expected_revision,
                    "result_id": item.result_id,
                    "detail_id": item.detail_id,
                }
            )
            if item.result_id is not None:
                result_ids.add(item.result_id)

        try:
            result = self.db.reviews.confirm_teacher_scores(
                session_id,
                scan_batch_id,
                confirmations,
            )
        except ValueError as exc:
            message = str(exc).lower()
            if "revision" in message or "changed" in message:
                raise ReviewRevisionConflictError(
                    "Teacher score changed in another window."
                ) from exc
            raise ReviewValidationError(
                "Teacher score could not be saved safely."
            ) from exc

        annotation_outcomes: list[ReviewAnnotationOutcome] = []
        if self.manual_review_service is not None:
            for result_id in sorted(result_ids):
                try:
                    rendered = self.manual_review_service.render_result_annotation(
                        result_id,
                        highlight_qids=[requested_question_id],
                    )
                except Exception:
                    rendered = None
                annotation_outcomes.append(
                    ReviewAnnotationOutcome(
                        result_id=result_id,
                        status=(
                            "succeeded"
                            if rendered is not None
                            else "retry_required"
                        ),
                        message=(
                            None
                            if rendered is not None
                            else "Annotation rendering failed; retry required."
                        ),
                    )
                )
        return ReviewConfirmationResult(
            updated_details=int(
                result.get("updated_details")
                or result.get("synced_details")
                or 0
            ),
            updated_results=int(result.get("updated_results") or 0),
            annotation_outcomes=annotation_outcomes,
        )


def _data_root(db: GradingRepositoryAccess) -> Path | None:
    return db.db_path.parent.parent if db.db_path.parent.name == "databases" else None


def _question_sort_key(question_id: str) -> list[Any]:
    parts = re.split(r"(\d+)", str(question_id or ""))
    return [int(part) if part.isdigit() else part for part in parts]


def _source_region_id(
    regions: list[dict[str, Any]],
    question_id: str,
) -> int:
    requested = str(question_id or "").strip()
    exact = {requested}
    if requested.startswith("Q"):
        exact.add(requested[1:])
    else:
        exact.add(f"Q{requested}")
    for region in regions:
        mapped = str(
            region.get("mapped_question_id")
            or region.get("detected_question_id")
            or ""
        ).strip()
        if mapped in exact:
            return int(region.get("id") or 0)

    match = re.match(r"^(?:Q)?(\d+)", requested)
    if match:
        base = match.group(1)
        for region in regions:
            mapped = str(
                region.get("mapped_question_id")
                or region.get("detected_question_id")
                or ""
            ).strip()
            if mapped in {base, f"Q{base}"}:
                return int(region.get("id") or 0)
    return 0


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


def _load_score_map(
    session: dict[str, Any],
    db: GradingRepositoryAccess,
) -> dict[str, float]:
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


def _load_scoring_item_map(
    session: dict[str, Any],
    db: GradingRepositoryAccess,
) -> dict[str, float]:
    """Return rubric leaves only, so an ungraded item is never double-counted."""

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
        parts = [
            part
            for part in question.get("parts", [])
            if isinstance(part, dict)
            and str(part.get("part_id") or "").strip()
        ]
        if parts:
            for part in parts:
                part_id = str(part["part_id"]).strip()
                try:
                    scores[part_id] = float(part.get("part_score", 0))
                except (TypeError, ValueError):
                    continue
            continue
        question_id = str(question.get("question_id") or "").strip()
        if not question_id:
            continue
        try:
            scores[question_id] = float(question.get("max_score", 0))
        except (TypeError, ValueError):
            continue
    return scores


def _item_matches_scope(
    item: ReviewItem,
    scope: str,
    needs_review_only: bool,
) -> bool:
    if scope == "teacher_pending":
        return item.score_status in {"ungraded", "ai_review", "failed"}
    if scope == "ungraded":
        return item.score_status == "ungraded"
    if scope == "ai_review":
        return item.score_status == "ai_review"
    if scope == "teacher_final":
        return item.score_status == "teacher_final"
    if scope == "all":
        return True
    return not needs_review_only or item.needs_review


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
