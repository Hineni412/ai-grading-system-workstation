from __future__ import annotations

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
