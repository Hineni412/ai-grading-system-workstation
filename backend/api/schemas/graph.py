from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.api.schemas.training import (
    TrainingCoverage,
    TrainingExamScopeRequest,
    TrainingNormalizedExamScope,
    TrainingNormalizedScope,
    TrainingScopeRequest,
)


class _GraphModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GraphQueryRequest(_GraphModel):
    scope: TrainingScopeRequest
    exam_scope: TrainingExamScopeRequest


class CurrentGraphQueryRequest(GraphQueryRequest):
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
            if not key.startswith(("kp_", "ki_", "sk_")):
                raise ValueError("knowledge_keys must use stable identities")
            if key not in normalized:
                normalized.append(key)
        return normalized


class CurrentGraphEvidenceRequest(GraphQueryRequest):
    stable_key: str = Field(
        pattern=r"^(?:kp_[a-z0-9_]+|sk_[a-z0-9_]+|ki_[0-9a-f]{32})$"
    )
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class CurrentGraphMastery(_GraphModel):
    status: Literal["available", "missing", "unavailable"]
    value: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_count: int = Field(ge=0)
    parameter_version: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    reason: str | None = None
    contributing_student_count: int = Field(default=0, ge=0)
    effective_weight: float = Field(default=0.0, ge=0.0)
    exam_evidence_count: int = Field(default=0, ge=0)
    training_evidence_count: int = Field(default=0, ge=0)


class CurrentGraphNode(_GraphModel):
    stable_key: str
    display_name: str
    definition: str
    include_scope: str
    exclude_scope: str
    curriculum_anchors: list[str]
    observable_evidence: str
    rationale: str
    evidence_source_ids: list[str]
    mastery: CurrentGraphMastery
    evidence: dict[str, Any]
    missing_reasons: list[str]


class CurrentGraphEdge(_GraphModel):
    relation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_key: str
    target_key: str
    relation_type: Literal["prerequisite", "parent", "related"]
    rationale: str
    basis_kind: Literal[
        "mathematical_logic",
        "curriculum_structure",
        "multi_textbook_sequence",
        "teacher_judgment",
        "empirical_evidence",
    ]
    strength: Literal["required", "recommended", "contextual"]
    evidence_source_ids: list[str]
    source_locator: str


class CurrentGraphStandard(_GraphModel):
    release_id: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    taxonomy_revision: int = Field(ge=1)


class CurrentGraphResponse(_GraphModel):
    response_schema_version: Literal["knowledge-graph-current"]
    response_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: TrainingNormalizedScope
    exam_scope: TrainingNormalizedExamScope
    coverage: TrainingCoverage
    current_standard: CurrentGraphStandard
    nodes: list[CurrentGraphNode]
    edges: list[CurrentGraphEdge]
    missing: list[dict[str, Any]]
    warnings: list[str]
    counts: dict[str, int]


class CurrentGraphEvidenceResponse(_GraphModel):
    response_schema_version: Literal["knowledge-graph-evidence-current"]
    response_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: TrainingNormalizedScope
    exam_scope: TrainingNormalizedExamScope
    coverage: TrainingCoverage
    current_standard: CurrentGraphStandard
    stable_key: str
    display_name: str
    items: list[dict[str, Any]]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_pages: int = Field(ge=1)


__all__ = [
    "GraphQueryRequest",
    "CurrentGraphEdge",
    "CurrentGraphEvidenceRequest",
    "CurrentGraphEvidenceResponse",
    "CurrentGraphMastery",
    "CurrentGraphNode",
    "CurrentGraphQueryRequest",
    "CurrentGraphResponse",
    "CurrentGraphStandard",
]
