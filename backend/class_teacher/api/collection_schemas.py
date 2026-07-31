from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .planning_schemas import PlanningAction


class MeetingDraft(BaseModel):
    meeting_draft_id: str
    title: str
    objective: str
    deadline_date: str | None
    final_deadline: str | None
    deadline_source: str
    actions: list[PlanningAction]
    unknowns: list[str]
    sensitive_findings: list[str]
    is_late: bool
    template_kind: str


class MeetingImportRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)
    raw_text: str = Field(min_length=1, max_length=20_000)
    reference_at: str | None = None


class MeetingInboxUpdateRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)
    revision: int = Field(ge=1)
    drafts: list[MeetingDraft] = Field(max_length=30)
    delete_source_after_confirm: bool = True


class RevisionRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)
    revision: int = Field(ge=1)


class MeetingInboxResponse(BaseModel):
    inbox_id: str
    revision: int
    status: Literal["draft", "cancelled", "confirmed"]
    raw_text: str | None
    source_deleted: bool
    delete_source_after_confirm: bool
    model_enabled: bool
    physical_request_count: int
    drafts: list[MeetingDraft]
    confirmed_plan_ids: list[str]
    confirmed_action_ids: list[str]
    created_at: str
    updated_at: str


class MeetingInboxListResponse(BaseModel):
    items: list[MeetingInboxResponse]


class MeetingConfirmResponse(BaseModel):
    inbox_id: str
    plan_ids: list[str]
    action_ids: list[str]
    source_deleted: bool
    model_enabled: bool
    physical_request_count: int


CollectionStatus = Literal[
    "pending_notice",
    "pending_submission",
    "submitted",
    "needs_review",
    "completed",
]


class CollectionBoardCreateRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)
    action_id: str
    title: str = Field(min_length=1, max_length=240)
    participant_refs: list[str] = Field(min_length=1, max_length=100)


class CollectionItemUpdateRequest(RevisionRequest):
    status: CollectionStatus


class CollectionItem(BaseModel):
    collection_item_id: str
    participant_ref: str
    status: CollectionStatus
    updated_at: str


class CommunicationPreview(BaseModel):
    content: str
    basis: str
    unknowns: list[str]
    status: Literal["unsent"]
    sent_at: None = None


class GroupReminder(CommunicationPreview):
    audience: str


class IndividualReminder(CommunicationPreview):
    board_id: str
    collection_item_id: str
    audience_ref: str


class CollectionBoardResponse(BaseModel):
    board_id: str
    revision: int
    action_id: str
    title: str
    items: list[CollectionItem]
    created_at: str
    updated_at: str
    total_count: int
    counts: dict[str, int]
    group_reminder: GroupReminder


class CollectionBoardListResponse(BaseModel):
    items: list[CollectionBoardResponse]


__all__ = [
    "CollectionBoardCreateRequest",
    "CollectionBoardListResponse",
    "CollectionBoardResponse",
    "CollectionItemUpdateRequest",
    "IndividualReminder",
    "MeetingConfirmResponse",
    "MeetingImportRequest",
    "MeetingInboxListResponse",
    "MeetingInboxResponse",
    "MeetingInboxUpdateRequest",
    "RevisionRequest",
]
