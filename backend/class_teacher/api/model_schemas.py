from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ModelPreviewRequest(BaseModel):
    purpose: str = Field(min_length=2, max_length=64)
    source_text: str = Field(min_length=1, max_length=4000)
    subject_id: str | None = Field(default=None, min_length=8, max_length=128)


class ModelPreviewResponse(BaseModel):
    preview_id: str
    purpose: str
    classification: Literal["restricted"]
    exact_payload: dict[str, object]
    removed_categories: list[str]
    fingerprint: str
    expires_at: str
    model_name: str
    model_enabled: bool
    max_physical_requests: Literal[1]
    estimated_cost: float | None


class ModelConfirmRequest(BaseModel):
    fingerprint: str = Field(min_length=64, max_length=64)
    operation_id: str = Field(min_length=8, max_length=128)


class ModelOperationResponse(BaseModel):
    preview_id: str
    operation_id: str
    state: Literal[
        "previewed",
        "confirmed",
        "claimed",
        "succeeded",
        "failed_before_send",
        "result_unknown",
        "cancelled_before_send",
    ]
    physical_request_count: int
    error_category: str | None
    draft_text: str | None
    response_kind: Literal["follow_up", "proposal"] | None = None
    follow_up_questions: list[str] = Field(default_factory=list)
    proposal: dict[str, object] | None = None
    teacher_confirmation_required: bool


__all__ = [
    "ModelConfirmRequest",
    "ModelOperationResponse",
    "ModelPreviewRequest",
    "ModelPreviewResponse",
]
