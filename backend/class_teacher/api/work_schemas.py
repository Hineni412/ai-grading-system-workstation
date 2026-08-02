from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


WorkKind = Literal[
    "goal",
    "task",
    "waiting",
    "decision",
    "collection",
    "communication",
    "sop",
    "restricted_projection",
]
WorkStatus = Literal[
    "draft",
    "pending",
    "in_progress",
    "waiting",
    "completed",
    "cancelled",
]


class WorkNodeResponse(BaseModel):
    node_id: str
    kind: WorkKind
    classification: Literal["ordinary", "restricted_projection"]
    title: str
    details: str | None
    status: WorkStatus
    due_date: str | None
    revision: int
    created_at: str
    updated_at: str
    projection_type: Literal[
        "sensitive_affair", "attention_followup", "student_support"
    ] | None = None


class WorkEdgeResponse(BaseModel):
    source_node_id: str
    target_node_id: str
    relation: Literal["contains", "depends_on", "next", "review_of"]


class WorkSnapshotResponse(BaseModel):
    as_of: str
    start_date: str | None
    end_date: str | None
    nodes: list[WorkNodeResponse]
    edges: list[WorkEdgeResponse]
    today: list[WorkNodeResponse]
    overdue: list[WorkNodeResponse]
    waiting: list[WorkNodeResponse]
    review_due: list[WorkNodeResponse]
    summary: dict[str, int] = Field(default_factory=dict)
    view: Literal["today", "week", "timeline", "all"] = "all"
    cursor: str | None = None
    source_version: str = "empty"


class WorkCommandRequest(BaseModel):
    command: Literal[
        "update_status",
        "reschedule",
        "record_progress",
        "update_collection_summary",
        "open_restricted_projection",
    ]
    expected_revision: int = Field(ge=1)
    operation_id: str = Field(min_length=8, max_length=128)
    status: WorkStatus | None = None
    due_date: str | None = None
    progress: str | None = Field(default=None, max_length=240)
    expected_count: int | None = Field(default=None, ge=0)
    received_count: int | None = Field(default=None, ge=0)
    needs_review_count: int | None = Field(default=None, ge=0)


class WorkNodeDetailResponse(BaseModel):
    node: WorkNodeResponse
    upstream: list[WorkNodeResponse]
    downstream: list[WorkNodeResponse]
    progress_events: list[dict[str, object]]
    collection_summary: dict[str, int] | None = None
    pending_ai_branches: list[dict[str, object]] = Field(default_factory=list)
    allowed_commands: list[str]
    projection_id: str | None = None


class PlanNodeResponse(BaseModel):
    draft_key: str
    kind: WorkKind
    title: str
    details: str | None
    status: Literal["pending", "waiting"]
    due_date: str | None


class WorkPlanPreviewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=240)
    due_date: str | None = None


class WorkPlanPreviewResponse(BaseModel):
    preview_id: str
    source_text: str
    final_due_date: str | None
    date_semantics: Literal["date-only"]
    exact_payload: dict[str, object]
    fingerprint: str
    expires_at: str
    model_provider: str | None
    model_endpoint: str | None
    model_name: str
    destination_fingerprint: str
    model_enabled: bool
    max_physical_requests: Literal[1]
    physical_request_count: int


class WorkPlanInvokeRequest(BaseModel):
    fingerprint: str = Field(min_length=64, max_length=64)
    operation_id: str = Field(min_length=8, max_length=128)


class WorkPlanEdgeResponse(BaseModel):
    source_draft_key: str
    target_draft_key: str
    relation: Literal["contains", "depends_on", "next"]


class WorkPlanResponse(BaseModel):
    nodes: list[PlanNodeResponse]
    edges: list[WorkPlanEdgeResponse]
    assumptions: list[str]


class WorkPlanResultResponse(BaseModel):
    preview_id: str
    operation_id: str
    state: Literal[
        "succeeded",
        "needs_information",
        "unavailable",
        "invalid_result",
        "result_unknown",
        "in_progress",
        "destination_changed",
    ]
    physical_request_count: int
    error_category: str | None
    questions: list[str]
    assumptions: list[str]
    plan: WorkPlanResponse | None
    plan_fingerprint: str | None
    teacher_confirmation_required: bool
    local_context: dict[str, object]


class WorkPlanConfirmRequest(BaseModel):
    model_operation_id: str = Field(min_length=8, max_length=128)
    plan_fingerprint: str = Field(min_length=64, max_length=64)
    operation_id: str = Field(min_length=8, max_length=128)


class WorkPlanConfirmResponse(BaseModel):
    created: bool
    goal_id: str | None
    parent_node_id: str | None
    model_operation_id: str
    nodes: list[WorkNodeResponse]
    edges: list[WorkEdgeResponse]
    physical_request_count: int


class WorkNodeUpdateRequest(BaseModel):
    revision: int = Field(ge=1)
    status: Literal["pending", "in_progress", "waiting", "completed", "cancelled"]
    due_date: str | None = None
    operation_id: str = Field(min_length=8, max_length=128)


class WorkProgressPlanPreviewRequest(BaseModel):
    revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=240)
    due_date: str | None = None


__all__ = [
    "WorkNodeResponse",
    "WorkNodeDetailResponse",
    "WorkCommandRequest",
    "WorkNodeUpdateRequest",
    "WorkPlanConfirmRequest",
    "WorkPlanConfirmResponse",
    "WorkPlanInvokeRequest",
    "WorkPlanPreviewRequest",
    "WorkPlanPreviewResponse",
    "WorkPlanResultResponse",
    "WorkProgressPlanPreviewRequest",
    "WorkSnapshotResponse",
]
