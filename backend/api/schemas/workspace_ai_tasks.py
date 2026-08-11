from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OpaqueRefPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=160)
    id: str = Field(min_length=1, max_length=160)
    revision: str = Field(min_length=1, max_length=160)


class PrepareWorkspaceAITaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=8, max_length=160)
    module: Literal["teaching_prep", "class_teacher"]
    task_kind: str = Field(min_length=1, max_length=160)
    source_ref: OpaqueRefPayload
    context_refs: list[OpaqueRefPayload] = Field(default_factory=list, max_length=100)
    prompt_contract_version: str = Field(min_length=1, max_length=160)
    model_destination_fingerprint: str = Field(min_length=64, max_length=64)
    return_target: str = Field(min_length=1, max_length=160)


class DispatchWorkspaceAITaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prepared_task_id: str = Field(min_length=1, max_length=160)
    request_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)


class UpdateWorkspaceAIHandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["opened", "discarded", "stale"]


class OpaqueRefResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    id: str
    revision: str


class WorkspaceAIHandoffResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    contract_version: str
    handoff_id: str
    work_item_id: str
    module: str
    intent: str
    handling_mode: str
    destination_key: str
    subject_refs: tuple[OpaqueRefResponse, ...]
    draft_ref: OpaqueRefResponse
    adoption_state: str
    prefill_keys: tuple[str, ...]
    missing_fields: tuple[str, ...]
    source_task_id: str
    source_turn_id: str | None
    return_destination_key: str
    return_focus_ref: str | None
    expires_on_source_change: bool
    adoption_id: str | None
    target_revision: str | None
    revision: int


class WorkspaceAITaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    contract_version: str
    task_id: str
    operation_id: str
    module: str
    task_kind: str
    source_ref: OpaqueRefResponse
    context_refs: tuple[OpaqueRefResponse, ...]
    return_target: str
    status: str
    phase: str
    progress: float
    send_attempt_count: int
    dispatch_evidence: str
    cancel_requested: bool
    job_id: int | None
    proposal_ref_id: str | None
    proposal_revision: str | None
    error_code: str | None
    error_detail: str | None
    revision: int
    safe_title: str
    safe_source: str
    teacher_message: str
    next_action: str
    handoffs: tuple[WorkspaceAIHandoffResponse, ...]
    handoff_total: int
    adopted_count: int
    discarded_count: int
    stale_count: int
    pending_count: int
    created_at: str
    updated_at: str
    finished_at: str | None
