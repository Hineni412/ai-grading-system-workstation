from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from backend.review.service import ReviewApplicationService
from path_manager import resolve_stored_file_path
from backend.question_id_contract import (
    QuestionIdContractError,
    canonicalize_question_document,
    question_id_coordinates,
    resolve_known_question_id,
)


@dataclass(frozen=True, slots=True)
class QuestionAnalysisRow:
    class_name: str
    question_id: str
    max_score: float | None
    score_rate: float | None
    average_score: float | None
    deduction_count: int
    attempt_count: int
    metric_status: Literal["ready", "missing_max_score", "no_attempts"]


@dataclass(frozen=True, slots=True)
class StudentAnalysisRow:
    result_id: int
    detail_id: int
    student_id: int
    student_code: str | None
    student_name: str
    class_name: str
    question_id: str
    score_awarded: float
    max_score: float | None
    deduction_amount: float | None
    deduction_reason: str | None
    needs_review: bool


@dataclass(slots=True)
class _QuestionTotals:
    class_name: str
    question_id: str
    score_sum: float = 0.0
    full_sum: float = 0.0
    attempt_count: int = 0
    deduction_count: int = 0

    @property
    def score_rate(self) -> float:
        return round(self.score_sum / self.full_sum * 100, 2) if self.full_sum > 0 else 0.0

    def public_row(self, rubric_max_score: float | None) -> QuestionAnalysisRow:
        metric_status: Literal["ready", "missing_max_score", "no_attempts"]
        if self.attempt_count <= 0:
            metric_status = "no_attempts"
        elif rubric_max_score is None or rubric_max_score <= 0 or self.full_sum <= 0:
            metric_status = "missing_max_score"
        else:
            metric_status = "ready"
        return QuestionAnalysisRow(
            class_name=self.class_name,
            question_id=self.question_id,
            max_score=(
                float(rubric_max_score)
                if rubric_max_score is not None and rubric_max_score > 0
                else None
            ),
            score_rate=self.score_rate if metric_status == "ready" else None,
            average_score=(
                round(self.score_sum / self.attempt_count, 2)
                if self.attempt_count > 0
                else None
            ),
            deduction_count=self.deduction_count,
            attempt_count=self.attempt_count,
            metric_status=metric_status,
        )


class SessionAnalysisService:
    def __init__(
        self,
        db: GradingRepositoryAccess,
        review_service: ReviewApplicationService | None = None,
    ) -> None:
        self.db = as_grading_repositories(db)
        self.review_service = review_service or ReviewApplicationService(db)

    def list_questions(
        self,
        session_id: int,
        class_name: str | None = None,
    ) -> list[QuestionAnalysisRow]:
        """Return per-question totals for one class or all classes."""
        session = self.db.sessions.get_grading_session(int(session_id))
        score_map, _type_map = load_session_score_type_maps(
            session,
            data_root=_data_root(self.db),
        )
        results, details_by_result = self.db.results.get_session_analysis_source(
            int(session_id), class_name
        )
        buckets: dict[tuple[str, str], _QuestionTotals] = {}
        for result in results:
            result_class_name = str(result.get("class_name") or "未分班")
            details = details_by_result.get(int(result["result_id"]), [])
            for detail in normalize_question_analysis_details(details, score_map):
                question_id = str(detail.get("question_id") or "").strip()
                if not question_id:
                    continue
                max_score = float(score_map.get(question_id) or 0)
                score_awarded = float(detail.get("score_awarded") or 0)
                if max_score <= 0:
                    max_score = max(score_awarded, 0.0)
                elif score_awarded > max_score:
                    score_awarded = max_score
                key = (result_class_name, question_id)
                if key not in buckets:
                    buckets[key] = _QuestionTotals(result_class_name, question_id)
                bucket = buckets[key]
                bucket.score_sum += score_awarded
                bucket.full_sum += max_score
                bucket.attempt_count += 1
                bucket.deduction_count += int(max_score - score_awarded > 0.01)
        totals = sorted(
            buckets.values(),
            key=lambda item: (item.class_name, question_sort_key(item.question_id)),
        )
        if class_name is None:
            merged: dict[str, _QuestionTotals] = {}
            for total in totals:
                if total.question_id not in merged:
                    merged[total.question_id] = _QuestionTotals("全部班级", total.question_id)
                bucket = merged[total.question_id]
                bucket.score_sum += total.score_sum
                bucket.full_sum += total.full_sum
                bucket.attempt_count += total.attempt_count
                bucket.deduction_count += total.deduction_count
            totals = sorted(
                merged.values(),
                key=lambda item: (item.score_rate, question_sort_key(item.question_id)),
            )
        return [total.public_row(score_map.get(total.question_id)) for total in totals]

    def list_students(
        self,
        session_id: int,
        question_id: str,
        class_name: str | None = None,
    ) -> list[StudentAnalysisRow]:
        """Return direct details plus legacy inferred child full-score rows."""
        raw_requested_question_id = str(question_id or "").strip()
        if not raw_requested_question_id:
            return []

        session = self.db.sessions.get_grading_session(int(session_id))
        score_map, _type_map = load_session_score_type_maps(
            session,
            data_root=_data_root(self.db),
        )
        requested_question_id = (
            resolve_known_question_id(raw_requested_question_id, score_map)
            or raw_requested_question_id
        )
        review_by_detail_id = {
            item.detail_id: item.needs_review
            for item in self.review_service.list_items(
                int(session_id),
                session or {},
            )
        }
        selected_max_score = score_map.get(requested_question_id)
        selected_parent_id = question_parent_id(requested_question_id)
        rows: list[StudentAnalysisRow] = []

        results, details_by_result = self.db.results.get_session_analysis_source(
            int(session_id), class_name
        )
        for result in results:
            result_class_name = str(result.get("class_name") or "未分班")
            result_id = int(result.get("result_id") or 0)
            details = details_by_result.get(result_id, [])
            normalized_details = normalize_question_analysis_details(
                details,
                score_map,
            )
            direct_details: list[dict[str, Any]] = []
            for detail in normalized_details:
                canonical_question_id = str(detail.get("question_id") or "").strip()
                if canonical_question_id != requested_question_id:
                    continue
                direct_details.append(detail)
                max_score = score_map.get(canonical_question_id)
                source_detail_ids = {
                    int(detail_id)
                    for detail_id in detail.get("_source_detail_ids", [])
                    if int(detail_id) > 0
                }
                rows.append(
                    _student_row(
                        result=result,
                        detail=detail,
                        class_name=result_class_name,
                        question_id=canonical_question_id,
                        max_score=max_score,
                        needs_review=any(
                            review_by_detail_id.get(detail_id, False)
                            for detail_id in source_detail_ids
                        ),
                    )
                )

            if direct_details or selected_parent_id is None or selected_max_score is None:
                continue
            parent_detail = next(
                (
                    detail
                    for detail in normalized_details
                    if str(detail.get("question_id") or "").strip() == selected_parent_id
                ),
                None,
            )
            parent_max_score = score_map.get(selected_parent_id)
            if parent_detail is None or parent_max_score is None:
                continue
            parent_score = float(parent_detail.get("score_awarded") or 0)
            if parent_score < float(parent_max_score) - 1e-6:
                continue
            rows.append(
                _student_row(
                    result=result,
                    detail=parent_detail,
                    class_name=result_class_name,
                    question_id=requested_question_id,
                    max_score=float(selected_max_score),
                    score_awarded=float(selected_max_score),
                    needs_review=review_by_detail_id.get(
                        int(parent_detail.get("detail_id") or 0),
                        False,
                    ),
                )
            )

        return sorted(
            rows,
            key=lambda item: (
                item.class_name,
                item.student_code or "",
                item.student_name,
                question_sort_key(item.question_id),
                item.detail_id,
            ),
        )


def load_session_score_type_maps(
    session: dict[str, Any] | None,
    *,
    data_root: Path | None = None,
) -> tuple[dict[str, float], dict[str, str]]:
    if not session:
        return {}, {}
    rubric_path = resolve_stored_file_path(
        session.get("rubric_path"),
        data_root=data_root,
    )
    try:
        rubric = json.loads(rubric_path.read_text(encoding="utf-8")) if rubric_path.exists() else {}
        if not isinstance(rubric, dict):
            return {}, {}
        rubric = canonicalize_question_document(rubric)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, QuestionIdContractError):
        rubric = {}
    score_map: dict[str, float] = {}
    type_map: dict[str, str] = {}
    questions = rubric.get("questions") if isinstance(rubric, dict) else []
    if not isinstance(questions, list):
        return score_map, type_map
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = str(question.get("question_id") or "").strip()
        question_type = str(question.get("question_type") or "").strip()
        if question_id:
            score_map[question_id] = _to_float(question.get("max_score"), 0.0)
            type_map[question_id] = question_type
        parts = question.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_id = str(part.get("part_id") or "").strip()
            if part_id:
                # A one-part question has the parent as its sole formal detail;
                # do not let a missing part_score replace the parent maximum.
                if part_id != question_id:
                    score_map[part_id] = _to_float(part.get("part_score"), 0.0)
                    type_map[part_id] = question_type
    return score_map, type_map


def normalize_question_analysis_details(
    details: list[dict[str, Any]],
    score_map: dict[str, float],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for detail in details:
        raw_question_id = str(detail.get("question_id") or "").strip()
        if not raw_question_id:
            continue
        question_id = canonical_question_id_for_score(raw_question_id, score_map)
        if question_id not in merged:
            item = dict(detail)
            item["question_id"] = question_id
            item["score_awarded"] = 0.0
            item["_source_question_ids"] = []
            item["_source_detail_ids"] = []
            item["_deduction_reasons"] = []
            merged[question_id] = item
            order.append(question_id)
        item = merged[question_id]
        source_question_ids = item["_source_question_ids"]
        if source_question_ids and raw_question_id not in source_question_ids:
            raise QuestionIdContractError(
                f"多个历史题号同时对应当前题号 {question_id}: "
                f"{source_question_ids[0]!r}, {raw_question_id!r}"
            )
        item["score_awarded"] = float(item.get("score_awarded") or 0) + float(
            detail.get("score_awarded") or 0
        )
        source_question_ids.append(raw_question_id)
        item["_source_detail_ids"].append(int(detail.get("detail_id") or 0))
        reason = str(detail.get("deduction_reason") or "").strip()
        if reason:
            item["_deduction_reasons"].append(reason)

    normalized: list[dict[str, Any]] = []
    for question_id in order:
        item = merged[question_id]
        full_score = float(score_map.get(question_id) or 0)
        if full_score > 0 and float(item.get("score_awarded") or 0) > full_score:
            item["score_awarded"] = full_score
        reasons: list[str] = []
        seen: set[str] = set()
        for reason in item.pop("_deduction_reasons", []):
            if reason not in seen:
                seen.add(reason)
                reasons.append(reason)
        if reasons:
            item["deduction_reason"] = "；".join(reasons)
        normalized.append(item)
    return normalized


def canonical_question_id_for_score(question_id: str, score_map: dict[str, float]) -> str:
    normalized = question_id.strip()
    return resolve_known_question_id(normalized, score_map) or normalized


def question_parent_id(question_id: str) -> str | None:
    coordinates = question_id_coordinates(question_id)
    if coordinates is None or coordinates[1] is None:
        return None
    return f"Q{coordinates[0]}"


def question_sort_key(question_id: str) -> tuple[int, str]:
    match = re.search(r"\d+", question_id)
    return (int(match.group()) if match else 9999, question_id)


def _student_row(
    *,
    result: dict[str, Any],
    detail: dict[str, Any],
    class_name: str,
    question_id: str,
    max_score: float | None,
    needs_review: bool,
    score_awarded: float | None = None,
) -> StudentAnalysisRow:
    awarded = (
        float(detail.get("score_awarded") or 0)
        if score_awarded is None
        else score_awarded
    )
    deduction: float | None
    if max_score is None:
        deduction = None
    else:
        raw_deduction = float(max_score) - awarded
        deduction = round(raw_deduction, 2) if raw_deduction > 0.01 else 0.0
    reason = str(detail.get("deduction_reason") or "").strip() or None
    return StudentAnalysisRow(
        result_id=int(result.get("result_id") or 0),
        detail_id=int(detail.get("detail_id") or 0),
        student_id=int(result.get("student_id") or 0),
        student_code=_optional_text(result.get("student_code")),
        student_name=str(result.get("student_name") or result.get("ocr_name") or ""),
        class_name=class_name,
        question_id=question_id,
        score_awarded=awarded,
        max_score=float(max_score) if max_score is not None else None,
        deduction_amount=deduction,
        deduction_reason=reason,
        needs_review=bool(needs_review),
    )


def _data_root(db: GradingRepositoryAccess) -> Path | None:
    return db.db_path.parent.parent if db.db_path.parent.name == "databases" else None


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
