from __future__ import annotations

import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _TrainingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrainingScopeRequest(_TrainingModel):
    mode: Literal["all", "student", "selected", "class"] = "all"
    student_ids: list[str] = Field(default_factory=list, max_length=500)
    class_id: str | None = Field(default=None, max_length=100)
    class_ids: list[str] = Field(default_factory=list, max_length=100)
    score_rate_min: float | None = Field(default=None, ge=0.0, le=1.0)
    score_rate_max: float | None = Field(default=None, ge=0.0, le=1.0)
    include_student_ids: list[str] = Field(default_factory=list, max_length=500)
    exclude_student_ids: list[str] = Field(default_factory=list, max_length=500)
    use_historical_fallback: bool = True

    @field_validator("student_ids", "include_student_ids", "exclude_student_ids", "class_ids")
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
    def validate_scope(self) -> "TrainingScopeRequest":
        if self.score_rate_min is not None and self.score_rate_max is not None:
            if self.score_rate_min > self.score_rate_max:
                raise ValueError("score rate range is invalid")
        if self.mode in {"student", "selected"} and not self.student_ids:
            raise ValueError("selected scope requires student IDs")
        if self.mode == "class" and not (self.class_ids or self.class_id):
            raise ValueError("class scope requires at least one class")
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


class PersonalizedRecommendationCreateRequest(TrainingDiagnosisRequest):
    request_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    question_count: int = Field(default=10, ge=8, le=12)
    expected_minutes: int = Field(default=45, ge=10, le=180)
    difficulty_min: int = Field(default=1, ge=1, le=10)
    difficulty_max: int = Field(default=10, ge=1, le=10)
    stage_ratios: TrainingStageRatios = Field(
        default_factory=TrainingStageRatios
    )
    paper_mode: Literal["individual", "shared"] = "individual"
    target_keys: list[str] = Field(default_factory=list, max_length=50)
    scope_keys: list[str] = Field(default_factory=list, max_length=50)
    target_names: list[str] = Field(default_factory=list, max_length=50)
    exclude_current_exam_originals: bool = True
    curriculum_volume_id: str | None = None

    @field_validator("curriculum_volume_id")
    @classmethod
    def normalize_curriculum_volume_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = str(value).strip()
        return clean or None

    @field_validator("target_keys", "scope_keys")
    @classmethod
    def normalize_identity_keys(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for raw_value in values:
            value = str(raw_value or "").strip().casefold()
            if not value.startswith(("kp_", "ki_")):
                raise ValueError(
                    "keys must use governed stable identities"
                )
            if value not in result:
                result.append(value)
        return result

    @field_validator("target_names")
    @classmethod
    def normalize_target_names(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for raw_value in values:
            value = str(raw_value or "").strip()
            if not value:
                raise ValueError("target_names must not contain blanks")
            if value not in result:
                result.append(value)
        return result

    @model_validator(mode="after")
    def difficulty_range_is_ordered(
        self,
    ) -> "PersonalizedRecommendationCreateRequest":
        if self.difficulty_min > self.difficulty_max:
            raise ValueError("difficulty range is invalid")
        if self.target_keys and self.target_names:
            raise ValueError(
                "target_keys and target_names cannot both be provided"
            )
        if self.paper_mode == "shared" and not (
            self.target_keys or self.target_names or self.scope_keys
        ):
            raise ValueError(
                "shared paper mode requires teacher-selected targets"
            )
        return self


class PersonalizedRecommendationEditRequest(_TrainingModel):
    request_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    expected_revision: int = Field(ge=1)
    action: Literal["lock", "unlock", "exclude", "replace"]
    student_id: str = Field(min_length=1, max_length=100)
    item_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)
    replacement_question_id: int | None = Field(default=None, ge=1)


class PersonalizedRecommendationDraftResponse(_TrainingModel):
    draft_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["draft", "reviewed"]
    revision: int = Field(ge=1)
    result_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine_version: str
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    config: dict[str, Any]
    students: list[dict[str, Any]]
    warnings: list[str]
    history: list[dict[str, Any]]


class PersonalizedPaperCreateRequest(_TrainingModel):
    operation_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    expected_draft_revision: int = Field(ge=1)
    student_id: str = Field(min_length=1, max_length=100)
    context_window_tokens: Literal[32768, 65536, 128000] = 32768
    direct_freeze: bool = False


class PersonalizedPaperBatchCreateRequest(_TrainingModel):
    operation_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    expected_draft_revision: int = Field(ge=1)
    student_ids: list[str] = Field(default_factory=list, max_length=500)
    context_window_tokens: Literal[32768, 65536, 128000] = 32768
    direct_freeze: bool = False

    @field_validator("student_ids")
    @classmethod
    def normalize_students(cls, values: list[str]) -> list[str]:
        return TrainingScopeRequest.normalize_student_ids(values)


class PersonalizedPaperBatchCancelRequest(_TrainingModel):
    operation_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")


class PersonalizedPaperBatchRetryRequest(_TrainingModel):
    student_ids: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("student_ids")
    @classmethod
    def normalize_students(cls, values: list[str]) -> list[str]:
        return TrainingScopeRequest.normalize_student_ids(values)


class PersonalizedPaperInstanceResponse(_TrainingModel):
    paper_instance_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    paper_batch_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    draft_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    draft_revision: int = Field(ge=1)
    student_id: str
    student_code: str | None = None
    student_name: str | None = None
    class_id: str | None = None
    series_version: int = Field(ge=1)
    status: Literal["creating", "review_pending", "frozen", "failed"]
    revision: int = Field(ge=1)
    layout_version: str
    budget: dict[str, Any]
    question_count: int = Field(ge=0)
    criterion_point_count: int = Field(ge=0)
    items: list[dict[str, Any]]
    pages: list[dict[str, Any]]
    review_docx_sha256: str | None = None
    reviewed_docx_sha256: str | None = None
    frozen_pdf_sha256: str | None = None
    formula_fallbacks: list[dict[str, Any]] = Field(default_factory=list)
    downloads: dict[str, str | None]
    error_code: str | None = None
    created_at: str
    frozen_at: str | None = None


class PersonalizedPaperInstanceListResponse(_TrainingModel):
    items: list[PersonalizedPaperInstanceResponse]


class PersonalizedPaperBatchResponse(_TrainingModel):
    batch_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    paper_batch_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["creating", "complete", "partial", "failed", "cancelled"]
    requested_count: int = Field(ge=0)
    succeeded_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    items: list[PersonalizedPaperInstanceResponse]
    failures: list[dict[str, str]]
    downloads: dict[str, str | None]


class PersonalizedPaperBatchListResponse(_TrainingModel):
    items: list[PersonalizedPaperBatchResponse]


class TrainingScanBatchCreateRequest(_TrainingModel):
    operation_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    paper_instance_ids: list[str] = Field(min_length=1, max_length=200)

    @field_validator("paper_instance_ids")
    @classmethod
    def validate_paper_instance_ids(cls, value: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[0-9a-fA-F]{64}", item) for item in value):
            raise ValueError("paper instance id is invalid")
        if len(set(item.casefold() for item in value)) != len(value):
            raise ValueError("paper instance ids must be unique")
        return value


class TrainingScanPageResolveRequest(_TrainingModel):
    operation_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    expected_revision: int = Field(ge=1)
    action: Literal["match", "replace", "dismiss"]
    paper_instance_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    page_number: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_target(self) -> "TrainingScanPageResolveRequest":
        if self.action in {"match", "replace"} and (
            self.paper_instance_id is None or self.page_number is None
        ):
            raise ValueError("matching requires a paper and page number")
        return self


class TrainingSubmissionCancelRequest(_TrainingModel):
    operation_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class TrainingScanBatchResponse(_TrainingModel):
    batch_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    paper_batch_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["manual_review", "ready", "cancelled"]
    revision: int = Field(ge=1)
    duplicate_upload: bool
    submissions: list[dict[str, Any]]
    pages: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    history: list[dict[str, Any]]
    created_at: str
    updated_at: str


class TrainingAssessmentStartRequest(_TrainingModel):
    expected_revision: int = Field(ge=1)


class TrainingAssessmentReviewRequest(_TrainingModel):
    operation_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    submission_revision: int = Field(ge=1)
    expected_review_revision: int = Field(ge=1)
    task_item_code: str = Field(min_length=1, max_length=100)
    point_id: str = Field(min_length=1, max_length=100)
    final_state: Literal["met", "not_met", "uncertain", "unreadable"]
    teacher_evidence: str = Field(min_length=1, max_length=500)
    teacher_reason: str = Field(min_length=1, max_length=500)


class TrainingAssessmentActionRequest(_TrainingModel):
    operation_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    submission_revision: int = Field(ge=1)
    expected_review_revision: int = Field(ge=1)
    action: Literal["pause", "resume", "cancel", "recover", "retry"]
    reason: str = Field(min_length=1, max_length=500)


class TrainingEvidenceSyncRequest(_TrainingModel):
    operation_token: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    submission_revision: int = Field(ge=1)
    expected_review_revision: int = Field(ge=1)
    action: Literal["publish", "withdraw"]
    reason: str = Field(min_length=1, max_length=500)


class TrainingEvidenceReplayRequest(_TrainingModel):
    max_items: int | None = Field(default=None, ge=1, le=500)


class TrainingAssessmentOutcomeResponse(_TrainingModel):
    run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    submission_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    submission_revision: int = Field(ge=1)
    status: str
    request_count: int = Field(ge=0)
    expected_question_count: int = Field(ge=0)
    expected_point_count: int = Field(ge=0)
    model_name: str | None = None
    usage: dict[str, Any]
    latency_ms: int = Field(ge=0)
    issue_codes: list[str]
    error_code: str | None = None
    questions: list[dict[str, Any]]
    review_revision: int = Field(ge=1)
    control_state: str
    workflow_status: str
    action_message: str
    attempts: list[dict[str, Any]]


class TrainingFeedbackResponse(_TrainingModel):
    schema_version: Literal["training-feedback-v1"]
    feedback_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    submission_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    submission_revision: int = Field(ge=1)
    source_review_revision: int = Field(ge=1)
    status: Literal[
        "publication_pending",
        "partial",
        "complete",
        "withdrawn",
    ]
    student: dict[str, Any]
    summary: dict[str, Any]
    questions: list[dict[str, Any]]
    mastery_changes: list[dict[str, Any]]
    next_round: dict[str, Any]
    timeline: list[dict[str, Any]]
    safety: dict[str, bool]
    evidence_version: str = Field(pattern=r"^[0-9a-f]{64}$")


class TrainingEvidenceReplayResponse(_TrainingModel):
    examined_count: int = Field(ge=0)
    delivered_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    feedbacks: list[dict[str, Any]]


class TrainingExportSubmitRequest(_TrainingModel):
    variant_id: int | None = Field(default=None, ge=1)
    format: Literal["docx", "markdown"] = "docx"
    audience: Literal["student", "teacher"] | None = None

    @model_validator(mode="after")
    def validate_export_mode(self) -> "TrainingExportSubmitRequest":
        if self.variant_id is None and self.audience is not None:
            raise ValueError("task bundle export does not accept audience")
        if self.variant_id is not None and self.audience is None:
            raise ValueError("variant export requires audience")
        return self


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
    source_kind: Literal["current_exam", "historical_exam"] = "current_exam"


class TrainingWeakPoint(_TrainingModel):
    knowledge_key: str
    knowledge_point: str
    mastery: float
    score_sum: float
    full_score_sum: float
    deduction_count: int
    evidence_count: int
    effective_weight: float = Field(default=0.0, ge=0.0)
    exam_count: int
    source_question_refs: list[TrainingEvidenceReference]
    actionable_reasons: list[str]
    tag_context: dict[str, list[str]]
    error_counts: dict[str, dict[str, int]]
    hierarchy_kind: Literal["root", "child", "parent_summary"] = "root"
    parent_knowledge_key: str | None = None
    parent_knowledge_point: str | None = None
    child_knowledge_keys: list[str] = Field(default_factory=list)
    direct_evidence_count: int = Field(default=0, ge=0)
    child_evidence_count: int = Field(default=0, ge=0)


class TrainingStudentProfile(_TrainingModel):
    student_id: str
    student_code: str
    student_name: str
    class_id: str
    score_rate: float | None = None
    score_rate_source: Literal["current_exam", "historical_fallback", "none"] = "none"
    historical_exam_count: int = Field(default=0, ge=0)
    historical_latest_exam_at: str | None = None
    weak_points: list[TrainingWeakPoint]


class TrainingNormalizedScope(_TrainingModel):
    mode: Literal["all", "student", "selected", "class"]
    student_ids: list[str]
    class_id: str | None = None
    class_ids: list[str] = Field(default_factory=list)
    score_rate_min: float | None = Field(default=None, ge=0.0, le=1.0)
    score_rate_max: float | None = Field(default=None, ge=0.0, le=1.0)
    include_student_ids: list[str] = Field(default_factory=list)
    exclude_student_ids: list[str] = Field(default_factory=list)
    use_historical_fallback: bool = True
    matched_student_count: int = Field(default=0, ge=0)
    scope_revision: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    student_score_profiles: dict[str, dict[str, Any]] = Field(default_factory=dict)


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
    group_weak_points: list[TrainingWeakPoint] = Field(default_factory=list)
    knowledge_catalog: list[dict[str, Any]] = Field(default_factory=list)
    coverage: TrainingCoverage
    confirmed_concept_ids: list[int]
    suggested_terms: list[str]
    unmapped_terms: list[str]
    warnings: list[str]
    diagnosis_identity: Literal["question_tag"]


__all__ = [
    "PersonalizedPaperBatchCreateRequest",
    "PersonalizedPaperBatchResponse",
    "PersonalizedPaperCreateRequest",
    "PersonalizedPaperInstanceListResponse",
    "PersonalizedPaperInstanceResponse",
    "PersonalizedRecommendationCreateRequest",
    "PersonalizedRecommendationDraftResponse",
    "PersonalizedRecommendationEditRequest",
    "TrainingDiagnosisRequest",
    "TrainingDiagnosisResponse",
    "TrainingExamScopeRequest",
    "TrainingExportSubmitRequest",
    "TrainingPlanRequest",
    "TrainingPlanResponse",
    "TrainingScopeRequest",
    "TrainingStageRatios",
    "TrainingTaskConfirmRequest",
    "TrainingTaskDetail",
    "TrainingTaskListResponse",
    "TrainingTaskSummary",
]
