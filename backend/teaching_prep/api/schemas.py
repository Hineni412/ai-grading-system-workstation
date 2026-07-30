from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.teaching_prep.domain.models import (
    ClassVariant,
    CurriculumEdition,
    ExerciseCandidate,
    LessonDraftVersion,
    LessonMaterialLink,
    LessonNode,
    LessonPreparation,
    MaterialUnit,
    MaterialVersion,
    ResourcePackVersion,
    SlidePlanVersion,
    PptxExecutionRun,
    PptxVersion,
    PostLessonReview,
    UpClassPackage,
)
from backend.teaching_prep.domain.states import LessonPreparationState


class TeachingPrepStatusResponse(BaseModel):
    module: str
    enabled: bool
    schema_version: str
    real_model_enabled: bool
    real_wps_enabled: bool
    wps_execution_available: bool


class CreatePreparationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=8, max_length=96)
    title: str = Field(min_length=1, max_length=160)
    class_name: str | None = Field(default=None, max_length=120)


class UpdatePreparationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    class_name: str | None = Field(default=None, max_length=120)
    target_state: LessonPreparationState | None = None


class PreparationResponse(BaseModel):
    id: str
    title: str
    class_name: str | None
    state: LessonPreparationState
    revision: int
    created_at: str
    updated_at: str

    @classmethod
    def from_domain(
        cls,
        preparation: LessonPreparation,
    ) -> "PreparationResponse":
        return cls(
            id=preparation.id,
            title=preparation.title,
            class_name=preparation.class_name,
            state=preparation.state,
            revision=preparation.revision,
            created_at=preparation.created_at,
            updated_at=preparation.updated_at,
        )


class PreparationListResponse(BaseModel):
    items: list[PreparationResponse]


class CreateCurriculumRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=8, max_length=96)
    title: str = Field(min_length=1, max_length=160)
    grade_level: int = Field(ge=7, le=9)
    volume: Literal["first", "second", "whole_year"]
    publisher: str | None = Field(default=None, max_length=120)
    edition_label: str | None = Field(default=None, max_length=120)


class CurriculumResponse(BaseModel):
    id: str
    title: str
    grade_level: int
    volume: str
    publisher: str | None
    edition_label: str | None
    revision: int
    is_active: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_domain(cls, item: CurriculumEdition) -> "CurriculumResponse":
        return cls.model_validate(asdict(item))


class CurriculumListResponse(BaseModel):
    items: list[CurriculumResponse]


class CreateLessonNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=8, max_length=96)
    parent_id: str | None = Field(
        default=None,
        min_length=32,
        max_length=32,
    )
    node_type: Literal["chapter", "section", "lesson"]
    title: str = Field(min_length=1, max_length=160)
    duration_minutes: int | None = Field(default=None, ge=1, le=300)
    source_kind: Literal["teacher", "catalog"] = "teacher"


class UpdateLessonNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=160)
    duration_minutes: int | None = Field(default=None, ge=1, le=300)
    is_active: bool


class ReorderLessonNodesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_id: str | None = Field(
        default=None,
        min_length=32,
        max_length=32,
    )
    ordered_ids: list[str] = Field(min_length=1)
    expected_revisions: dict[str, int]


class LessonNodeResponse(BaseModel):
    id: str
    curriculum_id: str
    parent_id: str | None
    node_type: str
    title: str
    sort_order: int
    duration_minutes: int | None
    source_kind: str
    is_active: bool
    revision: int
    created_at: str
    updated_at: str

    @classmethod
    def from_domain(cls, item: LessonNode) -> "LessonNodeResponse":
        return cls.model_validate(asdict(item))


class LessonTreeResponse(BaseModel):
    curriculum_id: str
    items: list[LessonNodeResponse]


class MaterialVersionResponse(BaseModel):
    id: str
    source_id: str
    display_name: str
    material_type: str
    content_sha256: str
    safe_filename: str
    size_bytes: int
    modified_ns: int | None
    unit_count: int | None
    inspection_status: str
    availability: str
    created_at: str

    @classmethod
    def from_domain(cls, item: MaterialVersion) -> "MaterialVersionResponse":
        payload = asdict(item)
        payload["safe_filename"] = payload.pop("file_name")
        return cls.model_validate(payload)


class MaterialVersionListResponse(BaseModel):
    items: list[MaterialVersionResponse]


class MaterialUnitResponse(BaseModel):
    id: str
    material_version_id: str
    unit_kind: str
    unit_index: int
    title: str | None
    text_excerpt: str
    text_status: str
    formula_review_required: bool
    object_summary: dict[str, object]
    preview_url: str
    revision: int
    created_at: str
    updated_at: str

    @classmethod
    def from_domain(cls, item: MaterialUnit) -> "MaterialUnitResponse":
        return cls.model_validate(asdict(item))


class MaterialUnitListResponse(BaseModel):
    material_version_id: str
    items: list[MaterialUnitResponse]


class UpdateMaterialUnitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(gt=0)
    title: str | None = Field(default=None, max_length=160)
    manual_text: str = Field(default="", max_length=4_000)
    formula_review_required: bool = False


class NormalizedCrop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x0: float = Field(ge=0, le=1)
    y0: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)


class CreateMaterialLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=8, max_length=96)
    material_version_id: str = Field(min_length=32, max_length=32)
    start_unit: int = Field(gt=0)
    end_unit: int = Field(gt=0)
    crop: NormalizedCrop | None = None
    purpose: Literal[
        "textbook",
        "reference_ppt",
        "exercise",
        "answer",
        "supplement",
    ]
    teacher_note: str | None = Field(default=None, max_length=500)
    confirmation_status: Literal["proposed", "confirmed"] = "confirmed"


class UpdateMaterialLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(gt=0)
    start_unit: int = Field(gt=0)
    end_unit: int = Field(gt=0)
    crop: NormalizedCrop | None = None
    purpose: Literal[
        "textbook",
        "reference_ppt",
        "exercise",
        "answer",
        "supplement",
    ]
    teacher_note: str | None = Field(default=None, max_length=500)
    confirmation_status: Literal["proposed", "confirmed"]
    is_active: bool


class MaterialLinkResponse(BaseModel):
    id: str
    lesson_node_id: str
    material_version_id: str
    material_name: str
    material_type: str
    start_unit: int
    end_unit: int
    crop: dict[str, float] | None
    purpose: str
    teacher_note: str | None
    confirmation_status: str
    source_version_sha256: str
    sort_order: int
    is_active: bool
    revision: int
    created_at: str
    updated_at: str

    @classmethod
    def from_domain(
        cls,
        item: LessonMaterialLink,
    ) -> "MaterialLinkResponse":
        return cls.model_validate(asdict(item))


class MaterialLinkListResponse(BaseModel):
    lesson_node_id: str
    items: list[MaterialLinkResponse]


class ExerciseRegionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_unit_id: str = Field(min_length=32, max_length=32)
    crop: NormalizedCrop


class ExerciseCandidateFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_number: str | None = Field(default=None, max_length=80)
    content_label: str | None = Field(default=None, max_length=500)
    difficulty: Literal["unrated", "easy", "medium", "hard"] = "unrated"
    classroom_use: Literal[
        "introduction",
        "example",
        "guided_practice",
        "independent_practice",
        "diagnostic",
        "challenge",
        "summary",
    ] = "guided_practice"
    estimated_minutes: int | None = Field(default=None, ge=1, le=60)
    teaching_focus: str | None = Field(default=None, max_length=500)
    teacher_note: str | None = Field(default=None, max_length=1_000)
    selection_status: Literal[
        "classroom_candidate",
        "backup",
        "excluded",
    ] = "classroom_candidate"
    answer_status: Literal[
        "candidate",
        "teacher_verified",
        "rejected",
        "missing",
    ] = "missing"
    question_regions: list[ExerciseRegionInput] = Field(
        min_length=1,
        max_length=24,
    )
    answer_regions: list[ExerciseRegionInput] = Field(
        default_factory=list,
        max_length=24,
    )


class CreateExerciseCandidateRequest(ExerciseCandidateFields):
    request_token: str = Field(min_length=8, max_length=96)


class UpdateExerciseCandidateRequest(ExerciseCandidateFields):
    expected_revision: int = Field(gt=0)
    is_active: bool


class ExerciseRegionResponse(BaseModel):
    id: str
    region_role: str
    material_unit_id: str
    material_version_id: str
    material_name: str
    unit_index: int
    crop: dict[str, float]
    source_version_sha256: str
    sequence: int
    preview_url: str


class DuplicateExerciseSuggestionResponse(BaseModel):
    candidate_id: str
    question_number: str | None
    content_label: str | None
    basis: list[str]


class ExerciseCandidateResponse(BaseModel):
    id: str
    lesson_node_id: str
    question_number: str | None
    content_label: str | None
    difficulty: str
    classroom_use: str
    estimated_minutes: int | None
    teaching_focus: str | None
    teacher_note: str | None
    selection_status: str
    answer_status: str
    question_regions: list[ExerciseRegionResponse]
    answer_regions: list[ExerciseRegionResponse]
    duplicate_suggestions: list[DuplicateExerciseSuggestionResponse]
    is_active: bool
    revision: int
    created_at: str
    updated_at: str

    @classmethod
    def from_domain(
        cls,
        item: ExerciseCandidate,
    ) -> "ExerciseCandidateResponse":
        return cls.model_validate(asdict(item))


class ExerciseCandidateListResponse(BaseModel):
    lesson_node_id: str
    items: list[ExerciseCandidateResponse]


class AssessmentClassChoiceResponse(BaseModel):
    class_name: str
    student_count: int


class AssessmentChoiceResponse(BaseModel):
    assessment_id: int
    title: str
    status: str
    created_at: str
    updated_at: str
    classes: list[AssessmentClassChoiceResponse]


class AssessmentChoiceListResponse(BaseModel):
    items: list[AssessmentChoiceResponse]


class QuestionEvidenceChoiceResponse(BaseModel):
    question_id: int
    question_number: str
    question_type: str | None
    text_excerpt: str
    difficulty: str | None
    knowledge_points: list[str]
    updated_at: str


class QuestionEvidenceChoiceListResponse(BaseModel):
    items: list[QuestionEvidenceChoiceResponse]


class FreezeResourcePackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=8, max_length=96)
    class_name: str | None = Field(default=None, max_length=120)
    lesson_type: Literal["new_lesson"] = "new_lesson"
    teacher_context: str | None = Field(default=None, max_length=2_000)
    reference_ppt_intents: dict[
        str,
        Literal["keep", "candidate_delete"],
    ] = Field(default_factory=dict)
    question_ids: list[int] = Field(default_factory=list, max_length=500)
    assessment_ids: list[int] = Field(default_factory=list, max_length=50)
    knowledge_scope: list[str] = Field(default_factory=list, max_length=100)


class ResourcePackResponse(BaseModel):
    id: str
    lesson_node_id: str
    version_number: int
    source_state_sha256: str
    pack_sha256: str
    payload: dict[str, Any]
    created_at: str

    @classmethod
    def from_domain(
        cls,
        item: ResourcePackVersion,
    ) -> "ResourcePackResponse":
        return cls.model_validate(asdict(item))


class ResourcePackListResponse(BaseModel):
    lesson_node_id: str
    items: list[ResourcePackResponse]


class ResourcePackStatusResponse(BaseModel):
    lesson_node_id: str
    has_pack: bool
    latest_version_number: int | None
    latest_pack_id: str | None
    local_sources_changed: bool


class LessonDraftPreflightResponse(BaseModel):
    resource_pack_id: str
    resource_pack_version: int
    resource_pack_sha256: str
    mode: Literal["local_template", "model"]
    will_call_model: bool
    model_available: bool
    model_label: str | None
    data_scope: dict[str, Any]
    references: list[dict[str, str]]
    missing_and_uncertain_count: int


class GenerateLessonDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=8, max_length=96)
    mode: Literal["local_template", "model"] = "local_template"
    confirmed: bool


class LessonDraftGenerationCancellationResponse(BaseModel):
    operation_id: str
    status: Literal["cancelled"]
    newly_cancelled: bool


class ReviseLessonDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=8, max_length=96)
    payload: dict[str, Any]
    confirmed: bool = False


class LessonDraftResponse(BaseModel):
    id: str
    resource_pack_id: str
    version_number: int
    based_on_draft_id: str | None
    operation_id: str | None
    source_kind: Literal["local_template", "model", "teacher"]
    model_label: str | None
    status: Literal["draft", "confirmed"]
    payload: dict[str, Any]
    capacity: dict[str, Any]
    created_at: str

    @classmethod
    def from_domain(
        cls,
        item: LessonDraftVersion,
    ) -> "LessonDraftResponse":
        return cls.model_validate(asdict(item))


class LessonDraftListResponse(BaseModel):
    resource_pack_id: str
    items: list[LessonDraftResponse]


class CreateSlidePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=8, max_length=96)


class SlideOperationReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=32, max_length=32)
    decision: Literal["proposed", "approved", "rejected"]
    reason: str = Field(min_length=1, max_length=1_000)
    planned_minutes: int = Field(ge=0, le=120)
    teacher_note: str | None = Field(default=None, max_length=1_000)


class ReviseSlidePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=8, max_length=96)
    operation_reviews: list[SlideOperationReviewRequest] = Field(
        default_factory=list,
        max_length=1_000,
    )
    approve_low_risk_deletions: bool = False
    review_note: str | None = Field(default=None, max_length=1_000)


class SlidePlanResponse(BaseModel):
    id: str
    lesson_draft_id: str
    resource_pack_id: str
    version_number: int
    based_on_plan_id: str | None
    source_ppt_state_sha256: str
    status: Literal["in_review", "approved", "invalidated"]
    payload: dict[str, Any]
    created_at: str

    @classmethod
    def from_domain(
        cls,
        item: SlidePlanVersion,
    ) -> "SlidePlanResponse":
        return cls.model_validate(asdict(item))


class SlidePlanListResponse(BaseModel):
    lesson_draft_id: str
    items: list[SlidePlanResponse]


class SlidePlanPreviewResponse(BaseModel):
    valid_for_execution: bool
    source_changed: bool
    includes_proposed_operations: bool
    before_slide_count: int
    after_slide_count: int
    before: list[dict[str, Any]]
    after: list[dict[str, Any]]
    changes: list[dict[str, Any]]
    manual_only: list[dict[str, Any]]


class ExecuteSlidePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=8, max_length=96)
    confirmed: bool


class DiscardPptxStagingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool


class PptxVersionResponse(BaseModel):
    id: str
    slide_plan_id: str
    lesson_node_id: str
    execution_run_id: str
    version_number: int
    status: Literal["published"]
    output_filename: str
    output_sha256: str
    slide_count: int
    verification_report: dict[str, Any]
    download_url: str
    created_at: str
    published_at: str

    @classmethod
    def from_domain(cls, item: PptxVersion) -> "PptxVersionResponse":
        payload = asdict(item)
        payload["download_url"] = (
            f"/api/teaching-prep/pptx-versions/{item.id}/download"
        )
        return cls.model_validate(payload)


class PptxExecutionResponse(BaseModel):
    id: str
    operation_id: str
    slide_plan_id: str
    source_material_version_id: str
    source_sha256: str
    expected_slide_count: int
    status: Literal[
        "running",
        "verifying",
        "publishing",
        "published",
        "failed",
        "cancelled",
        "interrupted",
    ]
    execution_report: dict[str, Any] | None
    verification_report: dict[str, Any] | None
    error_code: str | None
    published_version_id: str | None
    staging_retained: bool
    recovery_actions: list[str]
    created_at: str
    updated_at: str
    finished_at: str | None

    @classmethod
    def from_domain(
        cls,
        item: PptxExecutionRun,
    ) -> "PptxExecutionResponse":
        return cls.model_validate(asdict(item))


class PptxExecutionResultResponse(BaseModel):
    execution: PptxExecutionResponse
    version: PptxVersionResponse | None


class PptxExecutionListResponse(BaseModel):
    slide_plan_id: str
    items: list[PptxExecutionResponse]


class DeriveClassVariantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=8, max_length=96)
    class_name: str = Field(min_length=1, max_length=120)
    teacher_context: str | None = Field(default=None, max_length=2_000)
    assessment_ids: list[int] = Field(default_factory=list, max_length=50)
    knowledge_scope: list[str] = Field(default_factory=list, max_length=100)
    prior_review_ids: list[str] = Field(default_factory=list, max_length=20)


class ClassVariantResponse(BaseModel):
    id: str
    base_resource_pack_id: str
    resource_pack_id: str
    lesson_node_id: str
    class_name: str
    prior_review_ids: list[str]
    created_at: str

    @classmethod
    def from_domain(cls, item: ClassVariant) -> "ClassVariantResponse":
        payload = asdict(item)
        payload["prior_review_ids"] = list(item.prior_review_ids)
        return cls.model_validate(payload)


class ClassVariantResultResponse(BaseModel):
    variant: ClassVariantResponse
    resource_pack: ResourcePackResponse


class ClassVariantListResponse(BaseModel):
    items: list[ClassVariantResponse]


class CreateUpClassPackageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=8, max_length=96)
    confirmed: bool


class ActivateUpClassPackageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=8, max_length=96)
    confirmed: bool


class UpClassPackageResponse(BaseModel):
    id: str
    pptx_version_id: str
    slide_plan_id: str
    lesson_draft_id: str
    resource_pack_id: str
    lesson_node_id: str
    class_name: str | None
    version_number: int
    status: str
    output_filename: str | None
    package_sha256: str | None
    manifest: dict[str, object] | None
    error_code: str | None
    is_current: bool
    staging_retained: bool
    recovery_actions: list[str]
    created_at: str
    updated_at: str
    completed_at: str | None
    download_url: str | None

    @classmethod
    def from_domain(cls, item: UpClassPackage) -> "UpClassPackageResponse":
        payload = asdict(item)
        payload["recovery_actions"] = list(item.recovery_actions)
        payload["download_url"] = (
            f"/api/teaching-prep/up-class-packages/{item.id}/download"
            if item.status == "complete"
            else None
        )
        return cls.model_validate(payload)


class UpClassPackageListResponse(BaseModel):
    items: list[UpClassPackageResponse]


class CreatePostLessonReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=8, max_length=96)
    timing: Literal["on_time", "over", "early"]
    question_outcome: Literal[
        "appropriate",
        "too_hard",
        "too_easy",
        "ineffective",
        "not_observed",
    ]
    reteach_points: list[str] = Field(default_factory=list, max_length=10)
    next_action: Literal["keep", "delete", "adjust"]
    note: str | None = Field(default=None, max_length=1_000)
    use_in_next_version: bool = True


class PostLessonReviewResponse(BaseModel):
    id: str
    package_id: str
    pptx_version_id: str
    lesson_node_id: str
    class_name: str | None
    payload: dict[str, object]
    use_in_next_version: bool
    created_at: str

    @classmethod
    def from_domain(
        cls,
        item: PostLessonReview,
    ) -> "PostLessonReviewResponse":
        return cls.model_validate(asdict(item))


class PostLessonReviewListResponse(BaseModel):
    items: list[PostLessonReviewResponse]
