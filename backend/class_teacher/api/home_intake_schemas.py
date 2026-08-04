from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HomeIntakePreviewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    due_date: str | None = None
    reference_date: str | None = None


class HomeIntakeDispatchRequest(BaseModel):
    fingerprint: str = Field(min_length=64, max_length=64)
    operation_id: str = Field(min_length=8, max_length=128)


class HomeIntakeFollowUpRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)
    reference_date: str | None = None
    selected_step_keys: list[str] = Field(default_factory=list, max_length=50)
    selected_calendar_keys: list[str] = Field(default_factory=list, max_length=50)


class HomeIntakeManualFallbackRequest(BaseModel):
    source_operation_id: str = Field(min_length=8, max_length=128)
    operation_id: str = Field(min_length=8, max_length=128)
    title: str | None = Field(default=None, max_length=240)
    due_date: str | None = None


class HomeIntakeAdoptRequest(BaseModel):
    source_operation_id: str = Field(min_length=8, max_length=128)
    operation_id: str = Field(min_length=8, max_length=128)
    result_fingerprint: str = Field(min_length=64, max_length=64)
    subject_ids: list[str] = Field(min_length=1, max_length=50)
    draft_id: str | None = Field(default=None, min_length=8, max_length=128)
    draft_version: int | None = Field(default=None, ge=1)


class HomeIntakeDraftDiscardRequest(BaseModel):
    expected_version: int = Field(ge=1)


class HomeIntakeDraftAdoptRequest(BaseModel):
    expected_version: int = Field(ge=1)
    source_operation_id: str = Field(min_length=8, max_length=128)
    result_fingerprint: str = Field(min_length=64, max_length=64)


class HomeIntakePreviewResponse(BaseModel):
    preview_id: str
    route: Literal["ordinary", "sensitive", "emergency"]
    recommended_route: Literal["ordinary_plan", "affair", "student_support"]
    date_interpretation: dict[str, object]
    emergency_guidance: dict[str, object] | None
    round_number: int
    prior_operations: list[str]
    round_physical_request_count: int
    cumulative_physical_request_count: int
    physical_request_count: int
    dispatch_ready: bool
    local_only: bool
    blocked_categories: list[str]
    removed_categories: list[str]
    student_aliases: list[str]
    exact_payload: dict[str, object] | None
    fingerprint: str | None
    expires_at: str | None = None
    model_provider: str | None = None
    model_endpoint: str | None = None
    model_name: str | None = None
    destination_fingerprint: str | None = None
    model_enabled: bool = False
    max_physical_requests: int | None = None
    estimated_cost: float | None = None
    source_text: str | None = None
    final_due_date: str | None = None
    date_semantics: str | None = None


class HomeIntakeOperationResponse(BaseModel):
    operation_id: str
    route: Literal["ordinary", "sensitive", "emergency"]
    state: Literal[
        "succeeded",
        "needs_information",
        "unavailable",
        "invalid_result",
        "result_unknown",
        "in_progress",
        "destination_changed",
        "unsafe_output_suppressed",
        "failed_before_send",
    ]
    result_kind: Literal[
        "ordinary_plan",
        "affair_recommendation",
        "student_support_recommendation",
        "follow_up",
        "plain_text",
    ] | None
    result: dict[str, object] | None
    follow_up_questions: list[str]
    can_follow_up: bool
    assistant_message: str | None
    validation_issue: str | None
    error_category: str | None
    round_number: int
    round_physical_request_count: int
    cumulative_physical_request_count: int
    physical_request_count: int
    teacher_confirmation_required: bool
    result_fingerprint: str | None
    local_context: dict[str, object]
    draft_id: str | None = None
    draft_version: int | None = None
    draft_saved_at: str | None = None
    previous_result_preserved: bool = False
    preserved_result_kind: Literal[
        "ordinary_plan",
        "affair_recommendation",
        "student_support_recommendation",
    ] | None = None
    preserved_result: dict[str, object] | None = None


class HomeIntakeManualFallbackResponse(BaseModel):
    created: bool
    manual_fallback: bool
    source_operation_id: str
    node: dict[str, object]
    physical_request_count: Literal[0]


__all__ = [
    "HomeIntakeDispatchRequest",
    "HomeIntakeAdoptRequest",
    "HomeIntakeDraftAdoptRequest",
    "HomeIntakeDraftDiscardRequest",
    "HomeIntakeFollowUpRequest",
    "HomeIntakeManualFallbackRequest",
    "HomeIntakeManualFallbackResponse",
    "HomeIntakeOperationResponse",
    "HomeIntakePreviewRequest",
    "HomeIntakePreviewResponse",
]
