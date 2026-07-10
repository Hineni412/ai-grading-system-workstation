from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from backend.api.schemas.media import ReviewMediaLinksResponse


class ReviewQuestionSummary(BaseModel):
    question_id: str
    total_count: int
    needs_review_count: int
    max_score: float


class ReviewQuestionListResponse(BaseModel):
    items: list[ReviewQuestionSummary]
    total: int


class ReviewItemResponse(BaseModel):
    session_id: int
    result_id: int
    detail_id: int
    question_id: str
    student_code: str | None = None
    student_name: str
    class_name: str | None = None
    score_awarded: float
    max_score: float
    deduction_reason: str | None = None
    error_category: str | None = None
    error_summary: str | None = None
    confidence_score: float | None = None
    needs_review: bool
    candidate_scores: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    media: ReviewMediaLinksResponse


class ReviewItemListResponse(BaseModel):
    items: list[ReviewItemResponse]
    total: int


class ReviewConfirmItem(BaseModel):
    result_id: int
    detail_id: int
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
