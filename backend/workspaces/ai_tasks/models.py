from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, Sequence


TaskStatus = Literal[
    "prepared",
    "queued",
    "running",
    "needs_input",
    "proposal_ready",
    "failed_before_dispatch",
    "failed",
    "result_unknown",
    "invalid_result",
    "cancelled_before_dispatch",
    "discarded",
]
DispatchEvidence = Literal[
    "not_started",
    "may_have_started",
    "response_persisted",
]
AdoptionState = Literal[
    "pending",
    "opened",
    "adoption_started",
    "adopted",
    "discarded",
    "stale",
]


@dataclass(frozen=True, slots=True)
class OpaqueRef:
    kind: str
    id: str
    revision: str


@dataclass(frozen=True, slots=True)
class PrepareRequest:
    module: str
    task_kind: str
    source_ref: OpaqueRef
    context_refs: tuple[OpaqueRef, ...]
    prompt_contract_version: str
    model_destination_fingerprint: str
    return_target: str


@dataclass(frozen=True, slots=True)
class HandoffDraft:
    work_item_id: str
    intent: str
    handling_mode: str
    destination_key: str
    subject_refs: tuple[OpaqueRef, ...]
    draft_ref: OpaqueRef
    prefill_keys: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    source_turn_id: str | None = None
    return_destination_key: str = ""
    return_focus_ref: str | None = None
    expires_on_source_change: bool = True


@dataclass(frozen=True, slots=True)
class AdapterResult:
    proposal_ref_id: str
    proposal_revision: str
    handoffs: tuple[HandoffDraft, ...]
    needs_input: bool = False


@dataclass(frozen=True, slots=True)
class AdoptionResult:
    adoption_id: str
    object_ref: str
    receipt_revision: str
    target_revision: str


@dataclass(frozen=True, slots=True)
class HandoffSnapshot:
    contract_version: str
    handoff_id: str
    work_item_id: str
    module: str
    intent: str
    handling_mode: str
    destination_key: str
    subject_refs: tuple[OpaqueRef, ...]
    draft_ref: OpaqueRef
    adoption_state: AdoptionState
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


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    contract_version: str
    task_id: str
    operation_id: str
    module: str
    task_kind: str
    source_ref: OpaqueRef
    return_target: str
    status: TaskStatus
    phase: str
    progress: float
    send_attempt_count: int
    dispatch_evidence: DispatchEvidence
    cancel_requested: bool
    job_id: int | None
    proposal_ref_id: str | None
    proposal_revision: str | None
    error_code: str | None
    revision: int
    safe_title: str
    safe_source: str
    teacher_message: str
    next_action: str
    handoffs: tuple[HandoffSnapshot, ...] = field(default_factory=tuple)
    handoff_total: int = 0
    adopted_count: int = 0
    discarded_count: int = 0
    stale_count: int = 0
    pending_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    finished_at: str | None = None


@dataclass(frozen=True, slots=True)
class StoredTask:
    task_id: str
    operation_id: str
    module: str
    task_kind: str
    source_ref: OpaqueRef
    context_refs: tuple[OpaqueRef, ...]
    prompt_contract_version: str
    request_fingerprint: str
    model_destination_fingerprint: str
    return_target: str
    status: TaskStatus
    phase: str
    progress: float
    send_attempt_count: int
    dispatch_evidence: DispatchEvidence
    cancel_requested: bool
    proposal_ref_id: str | None
    proposal_revision: str | None
    job_id: int | None
    error_code: str | None
    revision: int
    created_at: str
    updated_at: str
    finished_at: str | None


class WorkspaceAITaskError(RuntimeError):
    code = "workspace_ai_task_error"


class OperationConflictError(WorkspaceAITaskError):
    code = "operation_conflict"


class TaskNotFoundError(WorkspaceAITaskError):
    code = "task_not_found"


class HandoffNotFoundError(WorkspaceAITaskError):
    code = "handoff_not_found"


class InvalidAdapterResultError(WorkspaceAITaskError):
    code = "invalid_result"


class KnownAdapterFailure(WorkspaceAITaskError):
    code = "adapter_failed"


class RevisionConflictError(WorkspaceAITaskError):
    code = "revision_conflict"


def refs_from_mappings(values: Sequence[Mapping[str, object]]) -> tuple[OpaqueRef, ...]:
    return tuple(
        OpaqueRef(
            kind=str(value.get("kind") or ""),
            id=str(value.get("id") or ""),
            revision=str(value.get("revision") or ""),
        )
        for value in values
    )
