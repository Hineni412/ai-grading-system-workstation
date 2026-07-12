from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _TrainingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrainingScopeRequest(_TrainingModel):
    mode: Literal["student", "selected", "class"]
    student_ids: list[str] = Field(default_factory=list, max_length=500)
    class_id: str | None = Field(default=None, max_length=100)

    @field_validator("student_ids")
    @classmethod
    def normalize_student_ids(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            student_id = str(value).strip()
            if not student_id:
                raise ValueError("student IDs must not be blank")
            if student_id not in seen:
                seen.add(student_id)
                result.append(student_id)
        return result

    @field_validator("class_id")
    @classmethod
    def normalize_class_id(cls, value: str | None) -> str | None:
        text = str(value or "").strip()
        return text or None

    @model_validator(mode="after")
    def class_scope_has_class_id(self) -> "TrainingScopeRequest":
        if self.mode == "class" and self.class_id is None:
            raise ValueError("class scope requires class_id")
        return self


class TrainingExamScopeRequest(_TrainingModel):
    mode: Literal["current", "manual", "cross_exam"]
    session_ids: list[int] = Field(default_factory=list, max_length=100)

    @field_validator("session_ids")
    @classmethod
    def normalize_session_ids(cls, values: list[int]) -> list[int]:
        result: list[int] = []
        seen: set[int] = set()
        for raw_value in values:
            value = int(raw_value)
            if value <= 0:
                raise ValueError("session IDs must be positive")
            if value not in seen:
                seen.add(value)
                result.append(value)
        return result


class TrainingDiagnosisRequest(_TrainingModel):
    scope: TrainingScopeRequest
    exam_scope: TrainingExamScopeRequest


class TrainingStageRatios(_TrainingModel):
    direct: float = Field(default=0.6, ge=0.0, le=1.0)
    prerequisite: float = Field(default=0.3, ge=0.0, le=1.0)
    transfer: float = Field(default=0.1, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def ratios_sum_to_one(self) -> "TrainingStageRatios":
        if abs(self.direct + self.prerequisite + self.transfer - 1.0) > 1e-9:
            raise ValueError("stage ratios must sum to 1")
        return self


class TrainingPlanRequest(TrainingDiagnosisRequest):
    variant_mode: Literal["individual", "auto_group"] = "individual"
    teacher_groups: dict[str, list[str]] | None = Field(
        default=None,
        max_length=100,
    )
    question_count: int = Field(default=10, ge=8, le=12)
    stage_ratios: TrainingStageRatios = Field(default_factory=TrainingStageRatios)
    exclude_current_exam_originals: bool = True

    @field_validator("teacher_groups")
    @classmethod
    def normalize_teacher_groups(
        cls,
        groups: dict[str, list[str]] | None,
    ) -> dict[str, list[str]] | None:
        if groups is None:
            return None
        normalized: dict[str, list[str]] = {}
        for raw_name, raw_members in groups.items():
            name = str(raw_name).strip()
            if not name or len(name) > 100:
                raise ValueError("teacher group names must be 1-100 characters")
            if len(raw_members) > 500:
                raise ValueError("teacher groups contain too many students")
            members = TrainingScopeRequest.normalize_student_ids(raw_members)
            if not members:
                raise ValueError("teacher groups must not be empty")
            normalized[name] = members
        return normalized


class TrainingPlanResponse(_TrainingModel):
    plan_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan: dict[str, Any]


class TrainingTaskConfirmRequest(TrainingPlanRequest):
    confirmation_id: UUID
    expected_plan_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class TrainingTaskSummary(_TrainingModel):
    id: int
    task_code: str
    created_by: str | None = None
    scope_snapshot: dict[str, Any]
    exam_scope: dict[str, Any]
    generation_config: dict[str, Any]
    warnings: list[str]
    status: Literal[
        "draft",
        "ready",
        "exporting",
        "completed",
        "cancelled",
        "failed",
    ]
    created_at: str
    updated_at: str


class TrainingTaskDetail(TrainingTaskSummary):
    diagnosis_snapshot: dict[str, Any]
    variants: list[dict[str, Any]]
    exports: list[dict[str, Any]]


class TrainingTaskListResponse(_TrainingModel):
    items: list[TrainingTaskSummary]
    total: int
    page: int
    page_size: int
    total_pages: int


class TrainingEvidenceReference(_TrainingModel):
    session_id: int
    session_name: str
    question_id: str
    bank_question_id: int
    score_awarded: float
    full_score: float
    score_rate: float | None = None


class TrainingWeakPoint(_TrainingModel):
    knowledge_key: str
    knowledge_point: str
    mastery: float
    score_sum: float
    full_score_sum: float
    deduction_count: int
    evidence_count: int
    exam_count: int
    source_question_refs: list[TrainingEvidenceReference]
    actionable_reasons: list[str]
    tag_context: dict[str, list[str]]
    error_counts: dict[str, dict[str, int]]


class TrainingStudentProfile(_TrainingModel):
    student_id: str
    student_code: str
    student_name: str
    class_id: str
    score_rate: float | None = None
    weak_points: list[TrainingWeakPoint]


class TrainingNormalizedScope(_TrainingModel):
    mode: Literal["student", "selected", "class"]
    student_ids: list[str]
    class_id: str | None = None


class TrainingExamSession(_TrainingModel):
    session_id: int
    session_name: str


class TrainingNormalizedExamScope(_TrainingModel):
    mode: Literal["current", "manual", "cross_exam"]
    session_ids: list[int]
    sessions: list[TrainingExamSession]


class TrainingCoverage(_TrainingModel):
    covered_items: int
    total_items: int
    missing_items: dict[str, str]


class TrainingDiagnosisResponse(_TrainingModel):
    scope: TrainingNormalizedScope
    exam_scope: TrainingNormalizedExamScope
    students: list[TrainingStudentProfile]
    coverage: TrainingCoverage
    confirmed_concept_ids: list[int]
    suggested_terms: list[str]
    unmapped_terms: list[str]
    warnings: list[str]
    diagnosis_identity: Literal["question_tag"]


__all__ = [
    "TrainingDiagnosisRequest",
    "TrainingDiagnosisResponse",
    "TrainingExamScopeRequest",
    "TrainingPlanRequest",
    "TrainingPlanResponse",
    "TrainingScopeRequest",
    "TrainingStageRatios",
    "TrainingTaskConfirmRequest",
    "TrainingTaskDetail",
    "TrainingTaskListResponse",
    "TrainingTaskSummary",
]
