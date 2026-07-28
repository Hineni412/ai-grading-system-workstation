from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ScoreStatus = Literal[
    "ungraded",
    "ai_ready",
    "ai_review",
    "teacher_final",
    "failed",
]
ScoreSource = Literal["none", "ai", "teacher"]
StudentResultStatus = Literal[
    "complete",
    "needs_review",
    "incomplete",
    "failed",
]


class ResultsCenterItemResponse(BaseModel):
    review_item_id: str
    question_id: str
    score_awarded: float | None = None
    max_score: float
    score_status: ScoreStatus
    score_source: ScoreSource
    confidence_score: float | None = None
    needs_review: bool
    review_reason: str | None = None
    result_id: int | None = None
    detail_id: int | None = None


class ResultsCenterQuestionResponse(BaseModel):
    question_id: str
    max_score: float
    total_count: int
    ungraded_count: int
    failed_count: int
    needs_review_count: int
    ai_ready_count: int
    teacher_final_count: int
    average_score: float | None = None


class ResultsCenterStudentResponse(BaseModel):
    student_id: int
    student_code: str | None = None
    student_name: str
    class_name: str | None = None
    current_score: float
    max_score: float
    ungraded_count: int
    failed_count: int
    needs_review_count: int
    status: StudentResultStatus
    items: list[ResultsCenterItemResponse] = Field(default_factory=list)


class ResultsCenterSummaryResponse(BaseModel):
    student_count: int
    complete_student_count: int
    average_sample_count: int
    average_score: float | None = None
    highest_score: float | None = None
    lowest_score: float | None = None
    max_score: float
    ungraded_item_count: int
    failed_item_count: int
    needs_review_item_count: int
    ai_ready_item_count: int
    teacher_final_item_count: int


class ResultsCenterResponse(BaseModel):
    session_id: int
    session_name: str
    summary: ResultsCenterSummaryResponse
    questions: list[ResultsCenterQuestionResponse] = Field(default_factory=list)
    students: list[ResultsCenterStudentResponse] = Field(default_factory=list)
