from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from backend.public_data import contains_filesystem_reference
from backend.review.service import ReviewApplicationService, ReviewItem


@dataclass(frozen=True, slots=True)
class ResultsCenterItem:
    review_item_id: str
    question_id: str
    score_awarded: float | None
    max_score: float
    score_status: str
    score_source: str
    confidence_score: float | None
    needs_review: bool
    review_reason: str | None
    result_id: int | None
    detail_id: int | None


@dataclass(frozen=True, slots=True)
class ResultsCenterQuestion:
    question_id: str
    max_score: float
    total_count: int
    ungraded_count: int
    failed_count: int
    needs_review_count: int
    ai_ready_count: int
    teacher_final_count: int
    average_score: float | None


@dataclass(frozen=True, slots=True)
class ResultsCenterStudent:
    student_id: int
    student_code: str | None
    student_name: str
    class_name: str | None
    current_score: float
    max_score: float
    ungraded_count: int
    failed_count: int
    needs_review_count: int
    status: str
    items: list[ResultsCenterItem] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ResultsCenterSummary:
    student_count: int
    complete_student_count: int
    average_sample_count: int
    average_score: float | None
    highest_score: float | None
    lowest_score: float | None
    max_score: float
    ungraded_item_count: int
    failed_item_count: int
    needs_review_item_count: int
    ai_ready_item_count: int
    teacher_final_item_count: int


@dataclass(frozen=True, slots=True)
class ResultsCenterSnapshot:
    session_id: int
    session_name: str
    summary: ResultsCenterSummary
    questions: list[ResultsCenterQuestion] = field(default_factory=list)
    students: list[ResultsCenterStudent] = field(default_factory=list)


class ResultsCenterService:
    """Build a safe, read-only view over the unified review ledger."""

    def __init__(self, review_service: ReviewApplicationService) -> None:
        self.review_service = review_service

    def get_snapshot(
        self,
        session_id: int,
        session: dict[str, Any],
        *,
        manual_context: dict[str, Any] | None = None,
    ) -> ResultsCenterSnapshot:
        items = self.review_service.list_items(
            int(session_id),
            session,
            scope="all",
            manual_context=manual_context,
        )
        public_items = [_public_item(item) for item in items]

        items_by_question: dict[str, list[ResultsCenterItem]] = defaultdict(list)
        items_by_student: dict[int, list[ResultsCenterItem]] = defaultdict(list)
        student_identity: dict[int, ReviewItem] = {}
        for source, public in zip(items, public_items, strict=True):
            items_by_question[public.question_id].append(public)
            items_by_student[int(source.student_id)].append(public)
            student_identity.setdefault(int(source.student_id), source)

        questions = [
            _question_summary(question_id, question_items)
            for question_id, question_items in items_by_question.items()
        ]
        students = [
            _student_summary(
                student_id,
                student_identity[student_id],
                student_items,
            )
            for student_id, student_items in items_by_student.items()
        ]
        students.sort(
            key=lambda student: (
                student.class_name or "",
                student.student_code or "",
                student.student_name,
                student.student_id,
            )
        )

        complete_students = [
            student
            for student in students
            if student.ungraded_count == 0 and student.failed_count == 0
        ]
        complete_scores = [
            student.current_score
            for student in complete_students
        ]
        summary = ResultsCenterSummary(
            student_count=len(students),
            complete_student_count=len(complete_students),
            average_sample_count=len(complete_scores),
            average_score=_average(complete_scores),
            highest_score=max(complete_scores) if complete_scores else None,
            lowest_score=min(complete_scores) if complete_scores else None,
            max_score=sum(question.max_score for question in questions),
            ungraded_item_count=_count_status(public_items, "ungraded"),
            failed_item_count=_count_status(public_items, "failed"),
            needs_review_item_count=_count_status(public_items, "ai_review"),
            ai_ready_item_count=_count_status(public_items, "ai_ready"),
            teacher_final_item_count=_count_status(
                public_items,
                "teacher_final",
            ),
        )
        return ResultsCenterSnapshot(
            session_id=int(session_id),
            session_name=str(
                session.get("session_name")
                or session.get("name")
                or ""
            ),
            summary=summary,
            questions=questions,
            students=students,
        )


def _public_item(item: ReviewItem) -> ResultsCenterItem:
    return ResultsCenterItem(
        review_item_id=item.review_item_id,
        question_id=item.question_id,
        score_awarded=(
            float(item.score_awarded)
            if item.score_awarded is not None
            else None
        ),
        max_score=float(item.max_score),
        score_status=item.score_status,
        score_source=item.score_source,
        confidence_score=(
            float(item.confidence_score)
            if item.confidence_score is not None
            else None
        ),
        needs_review=bool(item.needs_review),
        review_reason=_review_reason(item),
        result_id=item.result_id,
        detail_id=item.detail_id,
    )


def _review_reason(item: ReviewItem) -> str | None:
    if not item.needs_review:
        return None
    for value in (
        item.error_category,
        item.error_summary,
        item.deduction_reason,
    ):
        reason = _clean_text(value)
        if (
            reason is not None
            and not contains_filesystem_reference(reason)
        ):
            return reason
    if item.score_status == "ungraded":
        return "尚未评分"
    if item.score_status == "failed":
        return "评分结果需要处理"
    if item.score_status == "ai_review":
        return "AI 置信度较低，建议复核"
    return "需要人工复核"


def _question_summary(
    question_id: str,
    items: list[ResultsCenterItem],
) -> ResultsCenterQuestion:
    scored = [
        float(item.score_awarded)
        for item in items
        if _is_scored(item)
    ]
    return ResultsCenterQuestion(
        question_id=question_id,
        max_score=max(
            (float(item.max_score) for item in items),
            default=0.0,
        ),
        total_count=len(items),
        ungraded_count=_count_status(items, "ungraded"),
        failed_count=_count_status(items, "failed"),
        needs_review_count=_count_status(items, "ai_review"),
        ai_ready_count=_count_status(items, "ai_ready"),
        teacher_final_count=_count_status(items, "teacher_final"),
        average_score=_average(scored),
    )


def _student_summary(
    student_id: int,
    identity: ReviewItem,
    items: list[ResultsCenterItem],
) -> ResultsCenterStudent:
    ungraded_count = _count_status(items, "ungraded")
    failed_count = _count_status(items, "failed")
    needs_review_count = _count_status(items, "ai_review")
    if failed_count:
        status = "failed"
    elif ungraded_count:
        status = "incomplete"
    elif needs_review_count:
        status = "needs_review"
    else:
        status = "complete"
    return ResultsCenterStudent(
        student_id=int(student_id),
        student_code=identity.student_code,
        student_name=identity.student_name,
        class_name=identity.class_name,
        current_score=sum(
            float(item.score_awarded)
            for item in items
            if _is_scored(item)
        ),
        max_score=sum(float(item.max_score) for item in items),
        ungraded_count=ungraded_count,
        failed_count=failed_count,
        needs_review_count=needs_review_count,
        status=status,
        items=list(items),
    )


def _is_scored(item: ResultsCenterItem) -> bool:
    return (
        item.score_awarded is not None
        and item.score_status not in {"ungraded", "failed"}
    )


def _count_status(
    items: list[ResultsCenterItem],
    score_status: str,
) -> int:
    return sum(item.score_status == score_status for item in items)


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _clean_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
