from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SopTemplateStep(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=240)
    details: str | None = Field(default=None, max_length=8000)
    required: bool = True
    waivable: bool = False
    safety_required: bool = False
    depends_on: list[str] = Field(default_factory=list, max_length=100)
    activation: dict[str, object] | None = None
    decision_key: str | None = None
    decision_prompt: str | None = None
    decision_options: list[dict[str, str]] = Field(default_factory=list)
    communication_templates: list[dict[str, object]] = Field(
        default_factory=list
    )


class SopTemplatePublishRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)
    template_key: str = Field(min_length=1, max_length=80)
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=240)
    steps: list[SopTemplateStep] = Field(min_length=1, max_length=100)
    workflow_scope: Literal["personal_checklist", "school_confirmed"] = (
        "personal_checklist"
    )
    risk_level: Literal["ordinary", "elevated", "emergency"] = "ordinary"
    emergency_prompt: str | None = Field(default=None, max_length=2000)
    school_config_gaps: list[str] = Field(default_factory=list, max_length=50)


class SopTemplateResponse(BaseModel):
    template_version_id: str
    revision: int
    template_key: str
    version: int
    title: str
    steps: list[SopTemplateStep]
    workflow_scope: Literal["personal_checklist", "school_confirmed"] = (
        "personal_checklist"
    )
    risk_level: Literal["ordinary", "elevated", "emergency"] = "ordinary"
    emergency_prompt: str | None = None
    school_config_gaps: list[str] = Field(default_factory=list)
    model_enabled: bool = False
    physical_request_count: int = 0
    frozen: bool
    created_at: str


class SopTemplateListResponse(BaseModel):
    items: list[SopTemplateResponse]


class SopBaselineResponse(SopTemplateListResponse):
    workflow_scope: Literal["personal_checklist"]
    model_enabled: bool
    physical_request_count: int


class AffairCreateRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)
    template_version_id: str
    title: str = Field(min_length=1, max_length=240)
    summary: str | None = Field(default=None, max_length=8000)
    participant_refs: list[str] = Field(min_length=1, max_length=50)


class AffairParticipant(BaseModel):
    participant_id: str
    reference: str


class AffairStep(BaseModel):
    step_instance_id: str
    revision: int
    action_id: str | None
    key: str
    title: str
    details: str | None
    required: bool
    waivable: bool
    safety_required: bool
    depends_on: list[str]
    activation: dict[str, object] | None = None
    decision_key: str | None = None
    decision_prompt: str | None = None
    decision_options: list[dict[str, str]] = Field(default_factory=list)
    communication_templates: list[dict[str, object]] = Field(
        default_factory=list
    )
    state: Literal[
        "blocked",
        "ready",
        "in_progress",
        "waiting",
        "completed",
        "waived",
        "superseded",
    ]
    result: str | None
    completed_at: str | None


class AffairDecision(BaseModel):
    decision_id: str
    revision: int
    decision_kind: Literal["teacher", "school", "ai_suggestion"]
    step_instance_id: str | None
    summary: str
    decision_key: str | None = None
    selected_option: str | None = None
    can_drive_high_impact_branch: bool
    created_at: str


class AffairResponse(BaseModel):
    affair_id: str
    revision: int
    template_version_id: str
    plan_id: str
    title: str
    summary: str | None
    state: Literal["active", "closed"]
    template_key: str
    template_version: int
    workflow_scope: Literal["personal_checklist", "school_confirmed"] = (
        "personal_checklist"
    )
    risk_level: Literal["ordinary", "elevated", "emergency"] = "ordinary"
    emergency_prompt: str | None = None
    school_config_gaps: list[str] = Field(default_factory=list)
    model_enabled: bool = False
    physical_request_count: int = 0
    current_occurrence_sequence: int
    closure_summary: str | None
    occurrence_id: str
    occurrence_sequence: int
    participants: list[AffairParticipant]
    current_steps: list[AffairStep]
    completed_steps: list[AffairStep]
    preview_steps: list[AffairStep]
    decisions: list[AffairDecision]
    created_at: str
    updated_at: str
    closed_at: str | None


class AffairListResponse(BaseModel):
    items: list[AffairResponse]


class AffairDraftResponse(BaseModel):
    draft_id: str
    step_instance_id: str
    draft_kind: Literal["fact", "communication"]
    revision: int
    text: str
    updated_at: str
    expires_at: str


class AffairSummaryResponse(BaseModel):
    affair_id: str
    title: str
    summary: str | None
    state: Literal["active", "closed"]
    revision: int
    occurrence_id: str
    occurrence_sequence: int
    current_step_count: int
    completed_step_count: int
    updated_at: str
    projection_state: Literal["pending", "applied"]


class AffairWorkspaceListResponse(BaseModel):
    items: list[AffairSummaryResponse]
    cursor: str | None = None


class AffairWorkspaceResponse(AffairResponse):
    drafts: list[AffairDraftResponse] = Field(default_factory=list)
    projection_state: Literal["pending", "applied"]


class StepCompleteRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)
    revision: int = Field(ge=1)
    outcome: Literal["completed", "waived"]
    result: str = Field(min_length=1, max_length=8000)


class DecisionRecordRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)
    decision_kind: Literal["teacher", "school", "ai_suggestion"]
    summary: str = Field(min_length=1, max_length=8000)
    step_instance_id: str | None = None
    decision_key: str | None = Field(default=None, max_length=80)
    selected_option: str | None = Field(default=None, max_length=80)


class AffairCloseRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)
    revision: int = Field(ge=1)
    closure_summary: str = Field(min_length=1, max_length=8000)


class AffairReopenRequest(BaseModel):
    operation_id: str = Field(min_length=8, max_length=128)
    revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=4000)


class AffairDraftRequest(BaseModel):
    draft_kind: Literal["fact", "communication"]
    text: str = Field(min_length=1, max_length=8000)
    expected_revision: int | None = Field(default=None, ge=1)
    operation_id: str = Field(min_length=8, max_length=128)


class AffairCommandRequest(BaseModel):
    command: Literal["complete_step", "teacher_decision", "close", "reopen"]
    operation_id: str = Field(min_length=8, max_length=128)
    expected_revision: int = Field(ge=1)
    step_instance_id: str | None = None
    outcome: Literal["completed", "waived"] | None = None
    result: str | None = None
    decision_kind: Literal["teacher", "school", "ai_suggestion"] | None = None
    summary: str | None = None
    decision_key: str | None = None
    selected_option: str | None = None
    reason: str | None = None


__all__ = [
    "AffairCloseRequest",
    "AffairCommandRequest",
    "AffairCreateRequest",
    "AffairDraftRequest",
    "AffairDraftResponse",
    "AffairListResponse",
    "AffairReopenRequest",
    "AffairResponse",
    "AffairWorkspaceListResponse",
    "AffairWorkspaceResponse",
    "DecisionRecordRequest",
    "SopTemplateListResponse",
    "SopBaselineResponse",
    "SopTemplatePublishRequest",
    "SopTemplateResponse",
    "StepCompleteRequest",
]
