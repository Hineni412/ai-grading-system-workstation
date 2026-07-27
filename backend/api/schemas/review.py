from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from backend.api.schemas.media import ReviewMediaLinksResponse


class ReviewQuestionSummary(BaseModel):
    question_id: str
    total_count: int
    needs_review_count: int
    ungraded_count: int = 0
    teacher_confirmed_count: int = 0
    max_score: float


class ReviewQuestionListResponse(BaseModel):
    items: list[ReviewQuestionSummary]
    total: int


class ReviewItemResponse(BaseModel):
    review_item_id: str
    revision: int
    session_id: int
    student_id: int
    result_id: int | None = None
    detail_id: int | None = None
    question_id: str
    student_code: str | None = None
    student_name: str
    class_name: str | None = None
    score_awarded: float | None = None
    max_score: float
    deduction_reason: str | None = None
    error_category: str | None = None
    error_summary: str | None = None
    confidence_score: float | None = None
    needs_review: bool
    score_status: Literal[
        "ungraded",
        "ai_ready",
        "ai_review",
        "teacher_final",
        "failed",
    ]
    score_source: Literal["none", "ai", "teacher"]
    teacher_locked: bool
    candidate_scores: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    media: ReviewMediaLinksResponse


class ReviewItemListResponse(BaseModel):
    items: list[ReviewItemResponse]
    total: int


class ReviewConfirmItem(BaseModel):
    review_item_id: str | None = None
    expected_revision: int = Field(default=0, ge=0)
    student_id: int | None = Field(default=None, gt=0)
    result_id: int | None = Field(default=None, gt=0)
    detail_id: int | None = Field(default=None, gt=0)
    score_awarded: float
    deduction_reason: str | None = None
    error_category: str | None = None
    error_summary: str | None = None


class ReviewConfirmRequest(BaseModel):
    items: list[ReviewConfirmItem]

    @field_validator("items")
    @classmethod
    def _nonempty_items(cls, value: list[ReviewConfirmItem]) -> list[ReviewConfirmItem]:
        if not value:
            raise ValueError("must contain at least one item")
        return value


class ReviewAnnotationOutcomeResponse(BaseModel):
    result_id: int
    status: Literal["succeeded", "retry_required"]
    message: str | None = None


class ReviewConfirmResponse(BaseModel):
    updated_details: int
    updated_results: int
    annotation_outcomes: list[ReviewAnnotationOutcomeResponse] = Field(default_factory=list)
