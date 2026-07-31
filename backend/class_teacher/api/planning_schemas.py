from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PlanningAction(BaseModel):
    draft_action_id: str
    title: str
    details: str | None
    due_at: str | None
    depends_on_draft_action_ids: list[str]


class CommunicationDraft(BaseModel):
    audience: str
    content: str
    basis: str
    unknowns: list[str]
    status: Literal["unsent"]
    sent_at: None = None


class SendPreview(BaseModel):
    model_enabled: bool
    ready_to_send: bool
    sent: bool
    physical_request_count: int
    summary: str
    excluded_field_kinds: list[str]


class PlanningDraftResponse(BaseModel):
    draft_id: str
    revision: int
    status: Literal["draft", "cancelled", "confirmed"]
    raw_input: str
    reference_at: str
    template_kind: str
    plan_title: str
    deadline_date: str | None
    final_deadline: str | None
    deadline_source: str
    actions: list[PlanningAction]
    communication_drafts: list[CommunicationDraft]
    sensitive_findings: list[str]
    unknowns: list[str]
    is_late: bool
    send_preview: SendPreview
    confirmed_plan_id: str | None
    confirmed_action_ids: list[str]
    created_at: str
    updated_at: str


class PlanningDraftListResponse(BaseModel):
    items: list[PlanningDraftResponse]


class PlanningDraftCreateRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)
    raw_input: str = Field(min_length=1, max_length=4000)
    reference_at: str | None = None
    final_deadline: str | None = None


class PlanningDraftCancelRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)
    revision: int = Field(ge=1)


class PlanningDraftConfirmRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)
    revision: int = Field(ge=1)
    plan_title: str = Field(min_length=1, max_length=240)
    actions: list[PlanningAction] = Field(min_length=1, max_length=20)


class PlanningConfirmResponse(BaseModel):
    draft_id: str
    plan_id: str
    action_ids: list[str]
    communication_draft_ids: list[str]
    physical_request_count: int
    model_enabled: bool


__all__ = [
    "PlanningConfirmResponse",
    "PlanningDraftCancelRequest",
    "PlanningDraftConfirmRequest",
    "PlanningDraftCreateRequest",
    "PlanningDraftListResponse",
    "PlanningDraftResponse",
]
