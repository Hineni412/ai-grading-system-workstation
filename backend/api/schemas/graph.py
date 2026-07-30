from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.api.schemas.training import (
    TrainingCoverage,
    TrainingExamScopeRequest,
    TrainingNormalizedExamScope,
    TrainingNormalizedScope,
    TrainingScopeRequest,
    TrainingStudentProfile,
)


class _GraphModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GraphQueryRequest(_GraphModel):
    scope: TrainingScopeRequest
    exam_scope: TrainingExamScopeRequest


class GraphEvidenceRequest(GraphQueryRequest):
    knowledge_key: str = Field(min_length=17, max_length=300)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @field_validator("knowledge_key")
    @classmethod
    def normalize_exact_tag_key(cls, value: str) -> str:
        text = str(value or "").strip()
        prefix = "knowledge_point:"
        if not text.startswith(prefix):
            raise ValueError("knowledge_key must use question-tag identity")
        label = text[len(prefix) :].strip()
        if not label:
            raise ValueError("knowledge_key must include a knowledge point")
        return f"{prefix}{label}"


class GraphSourceQuestionReference(_GraphModel):
    session_id: int
    session_name: str
    question_id: str
    bank_question_id: int
    score_awarded: float
    full_score: float
    score_rate: float | None = None


class GraphRow(_GraphModel):
    student_id: int
    student_code: str
    student_name: str
    knowledge_key: str
    knowledge_label: str
    weighted_score_rate: float
    deduction_count: int
    item_count: int
    sample_reasons: str
    source_question_refs: list[GraphSourceQuestionReference]
    tag_context: dict[str, list[str]]
    error_counts: dict[str, dict[str, int]]


class GraphNode(_GraphModel):
    knowledge_key: str
    knowledge_label: str
    student_count: int
    item_count: int
    deduction_count: int
    average_mastery: float = Field(ge=0.0, le=1.0)
    tag_context: dict[str, list[str]]
    error_counts: dict[str, dict[str, int]]


class GraphEdge(_GraphModel):
    source_key: str
    target_key: str
    relation_type: Literal["prerequisite", "parent", "related"]
    weight: float = Field(ge=0.0, le=1.0)


class GraphEvidenceItem(_GraphModel):
    student_id: int
    student_code: str
    student_name: str
    class_id: str
    knowledge_key: str
    knowledge_label: str
    session_id: int
    session_name: str
    question_id: str
    bank_question_id: int
    score_awarded: float
    full_score: float
    score_rate: float | None = None
    tag_context: dict[str, list[str]]
    actionable_reasons: list[str]
    error_counts: dict[str, dict[str, int]]


class GraphProfilesResponse(_GraphModel):
    scope: TrainingNormalizedScope
    exam_scope: TrainingNormalizedExamScope
    students: list[TrainingStudentProfile]
    coverage: TrainingCoverage
    warnings: list[str]
    diagnosis_identity: Literal["question_tag"]


class GraphRowsResponse(_GraphModel):
    scope: TrainingNormalizedScope
    exam_scope: TrainingNormalizedExamScope
    rows: list[GraphRow]
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    coverage: TrainingCoverage
    warnings: list[str]
    diagnosis_identity: Literal["question_tag"]


class GraphEvidenceResponse(_GraphModel):
    scope: TrainingNormalizedScope
    exam_scope: TrainingNormalizedExamScope
    knowledge_key: str
    knowledge_label: str
    items: list[GraphEvidenceItem]
    total: int
    page: int
    page_size: int
    total_pages: int
    coverage: TrainingCoverage
    warnings: list[str]
    diagnosis_identity: Literal["question_tag"]


class RelationShape(_GraphModel):
    source_key: str = Field(pattern=r"^(?:kp_[a-z0-9_]+|ki_[0-9a-f]{32})$")
    target_key: str = Field(pattern=r"^(?:kp_[a-z0-9_]+|ki_[0-9a-f]{32})$")
    relation_type: Literal["prerequisite", "parent", "related"]


class RelationImpactRequest(_GraphModel):
    action: Literal["confirm", "reject", "retire", "restore", "amend"]
    amended_relation: RelationShape | None = None

    @field_validator("amended_relation")
    @classmethod
    def validate_amendment(cls, value: RelationShape | None, info):
        action = str(info.data.get("action") or "")
        if action == "amend" and value is None:
            raise ValueError("amended_relation is required for amend")
        if action != "amend" and value is not None:
            raise ValueError("amended_relation is only allowed for amend")
        return value


class RelationReviewRequest(RelationImpactRequest):
    expected_revision: int = Field(ge=1)
    teacher_ref: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=500)


class RelationBatchCommand(_GraphModel):
    relation_id: str = Field(min_length=1, max_length=80)
    expected_revision: int = Field(ge=1)
    action: Literal["confirm", "reject", "retire", "restore"]
    reason: str = Field(min_length=1, max_length=500)


class RelationBatchReviewRequest(_GraphModel):
    teacher_ref: str = Field(min_length=1, max_length=120)
    commands: list[RelationBatchCommand] = Field(min_length=1, max_length=20)


class RelationReviewQueueResponse(_GraphModel):
    status: Literal["suggested", "confirmed", "rejected", "retired"]
    items: list[dict[str, Any]]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_pages: int = Field(ge=1)


class RelationImpactResponse(_GraphModel):
    relation_id: str
    current_status: Literal["suggested", "confirmed", "rejected", "retired"]
    action: Literal["confirm", "reject", "retire", "restore", "amend"]
    target_status: (
        Literal["suggested", "confirmed", "rejected", "retired"] | None
    )
    can_apply: bool
    conflict_codes: list[str]
    activity_effect: str
    recommendation_effect: str
    revision: int = Field(ge=1)


class RelationReviewResponse(_GraphModel):
    relation: dict[str, Any]
    timeline: list[dict[str, Any]]


class RelationBatchReviewResponse(_GraphModel):
    status: Literal["applied", "partial", "failed"]
    applied_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    results: list[dict[str, Any]]


class RelationTimelineResponse(_GraphModel):
    relation_id: str
    timeline: list[dict[str, Any]]


class GraphV2QueryRequest(GraphQueryRequest):
    knowledge_keys: list[str] = Field(
        default_factory=list,
        max_length=1000,
    )
    prerequisite_depth: int = Field(default=1, ge=0, le=5)

    @field_validator("knowledge_keys")
    @classmethod
    def validate_stable_keys(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            key = str(value or "").strip().casefold()
            if not key.startswith(("kp_", "ki_")):
                raise ValueError("knowledge_keys must use stable identities")
            if key not in normalized:
                normalized.append(key)
        return normalized


class GraphV2EvidenceRequest(GraphQueryRequest):
    stable_key: str = Field(
        pattern=r"^(?:kp_[a-z0-9_]+|ki_[0-9a-f]{32})$"
    )
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class GraphV2Mastery(_GraphModel):
    status: Literal["available", "missing", "unavailable"]
    value: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_count: int = Field(ge=0)
    reason: str | None = None


class GraphV2Node(_GraphModel):
    stable_key: str
    display_name: str
    identity_revision: int = Field(ge=1)
    mastery_v1: GraphV2Mastery
    mastery_v2: GraphV2Mastery
    evidence: dict[str, Any]
    missing_reasons: list[str]


class GraphV2Edge(_GraphModel):
    relation_id: str
    source_key: str
    target_key: str
    relation_type: Literal["prerequisite", "parent", "related"]
    rationale: str
    revision: int = Field(ge=1)


class GraphV2Response(_GraphModel):
    response_schema_version: Literal["knowledge-graph-v2"]
    response_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: TrainingNormalizedScope
    exam_scope: TrainingNormalizedExamScope
    coverage: TrainingCoverage
    mastery_mode: Literal["v1", "v2"]
    mastery_parameter_version: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    nodes: list[GraphV2Node]
    edges: list[GraphV2Edge]
    missing: list[dict[str, Any]]
    warnings: list[str]
    counts: dict[str, int]


class GraphV2EvidenceResponse(_GraphModel):
    response_schema_version: Literal["knowledge-graph-evidence-v2"]
    response_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: TrainingNormalizedScope
    exam_scope: TrainingNormalizedExamScope
    coverage: TrainingCoverage
    stable_key: str
    display_name: str
    items: list[dict[str, Any]]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_pages: int = Field(ge=1)


class MasteryComparisonRequest(GraphQueryRequest):
    as_of: datetime
    parameter_version: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator("as_of")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        return value


class MasteryEvaluationGateResponse(_GraphModel):
    evaluation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    parameter_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(ge=1)
    required_review_count: int = Field(ge=1)
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    passed: bool


class MasteryV2ResultResponse(_GraphModel):
    schema_version: Literal["mastery-v2-result-v1"]
    stable_key: str = Field(min_length=1)
    status: Literal["available", "missing"]
    value: float | None = Field(default=None, ge=0.0, le=1.0)
    as_of: str
    parameter_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    direct_evidence_count: int = Field(ge=0)
    effective_sample_weight: float = Field(ge=0.0)
    prior_mean: float = Field(ge=0.0, le=1.0)
    prior_strength: float = Field(ge=0.0)
    contributions: list[dict[str, Any]]
    layers: list[dict[str, Any]]
    prerequisites: list[dict[str, Any]]
    explanations: list[str]


class MasteryComparisonItemResponse(_GraphModel):
    item_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    student_id: str = Field(min_length=1)
    student_code: str
    student_name: str
    class_id: str
    stable_key: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    mastery_v1: float | None = Field(default=None, ge=0.0, le=1.0)
    mastery_v2: MasteryV2ResultResponse
    signed_delta: float | None = Field(default=None, ge=-1.0, le=1.0)
    absolute_delta: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    reason_codes: list[str]
    reasons: list[str]
    requires_review: bool


class MasteryComparisonPerformanceResponse(_GraphModel):
    duration_ms: float = Field(ge=0.0)
    items_per_second: float = Field(gt=0.0)


class MasteryComparisonResponse(_GraphModel):
    schema_version: Literal["mastery-v1-v2-comparison-v1"]
    evaluation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    as_of: str
    parameter_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_delta: float = Field(gt=0.0, le=1.0)
    items: list[MasteryComparisonItemResponse]
    required_review_count: int = Field(ge=1)
    maximum_absolute_delta: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    performance: MasteryComparisonPerformanceResponse
    gate: MasteryEvaluationGateResponse


class MasterySpotCheckRequest(_GraphModel):
    evaluation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    item_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["accepted", "rejected"]
    teacher_ref: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)
    expected_revision: int = Field(ge=1)


class MasteryRolloutUpdateRequest(_GraphModel):
    enabled: bool
    expected_revision: int = Field(ge=1)
    teacher_ref: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)
    evaluation_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class MasteryRolloutStateResponse(_GraphModel):
    enabled: bool
    active_mode: Literal["v1", "v2"]
    active_parameter_version: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    approved_evaluation_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    revision: int = Field(ge=1)
    updated_by: str | None = None
    reason: str | None = None
    updated_at: str


class MasteryParameterCreateRequest(_GraphModel):
    prior_mean: float = Field(default=0.65, ge=0.0, le=1.0)
    prior_strength: float = Field(default=2.0, ge=0.0, le=1000.0)
    exam_source_weight: float = Field(default=1.0, gt=0.0, le=10000.0)
    training_source_weight: float = Field(
        default=0.7,
        gt=0.0,
        le=10000.0,
    )
    exam_half_life_days: float = Field(
        default=180.0,
        gt=0.0,
        le=10000.0,
    )
    training_half_life_days: float = Field(
        default=90.0,
        gt=0.0,
        le=10000.0,
    )
    future_tolerance_seconds: int = Field(default=300, ge=0, le=86400)
    output_precision: int = Field(default=6, ge=4, le=12)


class MasteryParameterVersionResponse(_GraphModel):
    parameter_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    parameters: dict[str, Any]
    created_at: str


class MasteryParameterHistoryResponse(_GraphModel):
    items: list[MasteryParameterVersionResponse]


__all__ = [
    "GraphEdge",
    "GraphEvidenceItem",
    "GraphEvidenceRequest",
    "GraphEvidenceResponse",
    "GraphNode",
    "GraphProfilesResponse",
    "GraphQueryRequest",
    "GraphRow",
    "GraphRowsResponse",
    "GraphSourceQuestionReference",
    "GraphV2Edge",
    "GraphV2EvidenceRequest",
    "GraphV2EvidenceResponse",
    "GraphV2Mastery",
    "GraphV2Node",
    "GraphV2QueryRequest",
    "GraphV2Response",
    "MasteryComparisonRequest",
    "MasteryComparisonItemResponse",
    "MasteryComparisonPerformanceResponse",
    "MasteryComparisonResponse",
    "MasteryEvaluationGateResponse",
    "MasteryParameterCreateRequest",
    "MasteryParameterHistoryResponse",
    "MasteryParameterVersionResponse",
    "MasteryRolloutStateResponse",
    "MasteryRolloutUpdateRequest",
    "MasterySpotCheckRequest",
    "MasteryV2ResultResponse",
    "RelationBatchCommand",
    "RelationBatchReviewRequest",
    "RelationBatchReviewResponse",
    "RelationImpactRequest",
    "RelationImpactResponse",
    "RelationReviewQueueResponse",
    "RelationReviewRequest",
    "RelationReviewResponse",
    "RelationShape",
    "RelationTimelineResponse",
]
