from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class OperationRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)


class SubjectCreateRequest(OperationRequest):
    source_student_id: str = Field(min_length=1, max_length=240)
    display_name: str = Field(min_length=1, max_length=240)
    class_label: str | None = Field(default=None, max_length=240)


class SubjectUpdateRequest(OperationRequest):
    revision: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=240)
    class_label: str | None = Field(default=None, max_length=240)


class SubjectDeleteRequest(OperationRequest):
    confirmation_phrase: str
    preview_version: str | None = Field(default=None, min_length=64, max_length=64)


class RecordFields(BaseModel):
    record_kind: Literal[
        "fact",
        "student_statement",
        "reported_statement",
        "teacher_observation",
        "provisional_judgment",
        "professional_conclusion",
        "ai_draft",
    ]
    content: str = Field(min_length=1, max_length=12_000)
    scene: str = Field(min_length=1, max_length=1000)
    source: str = Field(min_length=1, max_length=1000)
    basis: str | None = None
    counterexample: str | None = None
    category: str | None = None
    observed_at: str
    review_at: str | None = None
    expires_at: str | None = None


class RecordCreateRequest(OperationRequest, RecordFields):
    pass


class RecordReviseRequest(OperationRequest):
    expected_revision: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=12_000)
    scene: str = Field(min_length=1, max_length=1000)
    source: str = Field(min_length=1, max_length=1000)
    basis: str | None = None
    counterexample: str | None = None
    category: str | None = None
    observed_at: str
    review_at: str | None = None
    expires_at: str | None = None
    revision_reason: str = Field(min_length=1, max_length=1000)


class RecordStateRequest(OperationRequest):
    expected_revision: int = Field(ge=1)
    state: Literal["active", "withdrawn", "archived"]
    reason: str = Field(min_length=1, max_length=1000)


class AiDraftConfirmRequest(OperationRequest):
    confirmed_kind: str


class EvidenceLinkRequest(OperationRequest):
    observation_record_id: str
    evidence_record_id: str
    relation_kind: Literal["supports", "counterexample", "context"]


class SupportPlanCreateRequest(OperationRequest):
    goal: str = Field(min_length=1, max_length=1000)
    support_actions: list[str] = Field(min_length=1, max_length=30)
    review_at: str
    action_id: str | None = None


class SupportPlanCompleteRequest(OperationRequest):
    expected_revision: int = Field(ge=1)
    result: str = Field(min_length=1, max_length=4000)


class AffairProjectionRequest(OperationRequest):
    affair_id: str


class FollowUpPostponeRequest(BaseModel):
    due_date: str = Field(min_length=10, max_length=10)


class QuickTextRequest(OperationRequest):
    text: str = Field(min_length=1, max_length=12_000)
    subject_id: str | None = None


class QuickFragment(BaseModel):
    fragment_id: str | None = None
    text: str = Field(min_length=1, max_length=4000)
    suggested_kind: str = "unclassified"


class QuickUpdateRequest(OperationRequest):
    revision: int = Field(ge=1)
    fragments: list[QuickFragment] = Field(min_length=1, max_length=50)


class QuickConfirmRequest(OperationRequest):
    fragment_id: str
    target_kind: Literal["support_record", "action", "sop"]
    target_options: dict[str, Any] = Field(default_factory=dict)


class EvidenceBatchRequest(OperationRequest):
    batch: dict[str, Any]


class SpreadsheetPreviewRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=500)
    content_base64: str = Field(min_length=1, max_length=14_000_000)
    sheet_name: str | None = Field(default=None, max_length=500)


class EvidenceSupersedeRequest(OperationRequest):
    reason: str = Field(min_length=1, max_length=1000)


class AttentionCreateRequest(OperationRequest):
    evidence_version_id: str
    observed_fact: str = Field(min_length=1, max_length=4000)
    comparability: Literal[
        "directly_comparable",
        "reference_only",
        "not_comparable",
        "insufficient_information",
    ]
    limitations: list[str] = Field(default_factory=list, max_length=20)
    verification_question: str = Field(min_length=1, max_length=2000)
    low_risk_next_step: str = Field(min_length=1, max_length=2000)
    evidence_sufficiency: str = Field(min_length=1, max_length=1000)
    review_suggestion: str = Field(min_length=1, max_length=2000)


class AttentionResolveRequest(OperationRequest):
    revision: int = Field(ge=1)
    decision: Literal["follow_up", "observe", "no_action"]
    reason: str | None = None
    plan_id: str | None = None
    review_at: str | None = None


class AttentionDecisionRequest(AttentionResolveRequest):
    source_version: str = Field(min_length=64, max_length=64)


__all__ = [
    "AffairProjectionRequest",
    "AiDraftConfirmRequest",
    "AttentionCreateRequest",
    "AttentionDecisionRequest",
    "AttentionResolveRequest",
    "EvidenceBatchRequest",
    "EvidenceLinkRequest",
    "EvidenceSupersedeRequest",
    "FollowUpPostponeRequest",
    "SpreadsheetPreviewRequest",
    "OperationRequest",
    "QuickConfirmRequest",
    "QuickTextRequest",
    "QuickUpdateRequest",
    "RecordCreateRequest",
    "RecordReviseRequest",
    "RecordStateRequest",
    "SubjectCreateRequest",
    "SubjectDeleteRequest",
    "SubjectUpdateRequest",
    "SupportPlanCompleteRequest",
    "SupportPlanCreateRequest",
]
