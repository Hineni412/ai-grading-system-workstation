from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.teaching_prep.application.preferences import (
    DEFAULT_TEACHING_PREFERENCES,
)
from backend.teaching_prep.domain.models import (
    ClassVariant,
    CurriculumEdition,
    ExerciseCandidate,
    ExerciseSuggestion,
    ExerciseSuggestionRun,
    LessonDraftVersion,
    LessonGenerationPerformance,
    LessonMaterialLink,
    LessonNode,
    LessonPreparation,
    MaterialUnit,
    MaterialVersion,
    ResourcePackVersion,
    ReferenceSelectionDraft,
    ReferenceSelectionSnapshot,
    ReferencePptCollection,
    SemesterLessonProgress,
    SemesterMappingProposal,
    SemesterMaterialRecord,
    SlidePlanVersion,
    PptxExecutionRun,
    PptxVersion,
    PostLessonReview,
    UpClassPackage,
    TeachingPreferences,
    TeachingSemester,
    TeachingPrepAIAdoption,
    SlideAnimationRun,
)
from backend.teaching_prep.domain.states import LessonPreparationState


class MaterialParseJobResponse(BaseModel):
    id: int
    job_type: str
    payload: dict[str, Any]
    result: dict[str, Any]
    status: str
    progress: float
    stage: str
    detail: str
    error: str | None = None
    cancel_requested: bool
    created_at: str
    started_at: str | None = None
    updated_at: str
    finished_at: str | None = None


class MaterialParseJobListResponse(BaseModel):
    items: list[MaterialParseJobResponse]


class TeachingPrepStatusResponse(BaseModel):
    module: str
    enabled: bool
    schema_version: str
    real_model_enabled: bool = False
    semester_mapping_model_available: bool = False
    exercise_suggestion_model_available: bool = False
    slide_animation_model_available: bool = False
    real_wps_enabled: bool = False
    wps_execution_available: bool = False
    real_model_enabled: bool
    semester_mapping_model_available: bool
    real_wps_enabled: bool
    wps_execution_available: bool


class TeachingPreferencesPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    label_textbook_pages: bool
    page_label_font_size: Literal[28] = 28
    trim_excess_practice: bool
    practice_trim_level: Literal["light", "moderate", "strong"]
    preserve_teaching_examples: bool
    prefer_short_practice: bool
    supplement_from_references: bool
    supplement_question_limit: int = Field(ge=0, le=3)
    supplement_as_source_image: bool
    prioritize_homework_workbook: bool
    avoid_direct_homework_copy: bool
    avoid_ppt_duplicates: bool


class TeachingPreferencesResponse(BaseModel):
    revision: int
    payload: TeachingPreferencesPayload
    updated_at: str

    @classmethod
    def from_domain(
        cls,
        item: TeachingPreferences,
    ) -> "TeachingPreferencesResponse":
        return cls.model_validate(asdict(item))


class UpdateTeachingPreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(gt=0)
    payload: TeachingPreferencesPayload


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


class SemesterWorkspaceCurriculumRequest(BaseModel):
    """The curriculum identity supplied while starting one semester."""

    model_config = ConfigDict(extra="forbid")

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


class CreateSemesterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=8, max_length=96)
    curriculum_id: str = Field(min_length=32, max_length=32)
    school_year: str = Field(min_length=4, max_length=20)
    term: Literal["first", "second"]
    planned_new_lesson_count: int = Field(ge=0, le=500)


class CreateSemesterWorkspaceRequest(BaseModel):
    """Atomically establish a curriculum together with its semester state."""

    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=8, max_length=96)
    curriculum: SemesterWorkspaceCurriculumRequest
    school_year: str = Field(min_length=4, max_length=20)
    term: Literal["first", "second"]
    planned_new_lesson_count: int = Field(ge=0, le=500)


class UpdateSemesterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(gt=0)
    planned_new_lesson_count: int = Field(ge=0, le=500)
    status: Literal["planning", "active", "completed", "archived"]


class SemesterResponse(BaseModel):
    id: str
    curriculum_id: str
    curriculum_title: str
    school_year: str
    term: str
    planned_new_lesson_count: int
    status: str
    active_lesson_count: int
    not_started_lesson_count: int
    preparing_lesson_count: int
    ready_lesson_count: int
    taught_lesson_count: int
    skipped_lesson_count: int
    material_count: int
    parsed_material_count: int
    mapped_material_count: int
    revision: int
    created_at: str
    updated_at: str

    @classmethod
    def from_domain(cls, item: TeachingSemester) -> "SemesterResponse":
        return cls.model_validate(asdict(item))


class SemesterWorkspaceResponse(BaseModel):
    curriculum: CurriculumResponse
    semester: SemesterResponse


class SemesterListResponse(BaseModel):
    items: list[SemesterResponse]


class SetSemesterLessonProgressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["not_started", "preparing", "ready", "taught", "skipped"]
    expected_revision: int | None = Field(default=None, gt=0)


class SemesterLessonProgressResponse(BaseModel):
    id: str
    semester_id: str
    lesson_node_id: str
    lesson_title: str
    status: str
    revision: int
    created_at: str
    updated_at: str

    @classmethod
    def from_domain(
        cls,
        item: SemesterLessonProgress,
    ) -> "SemesterLessonProgressResponse":
        return cls.model_validate(asdict(item))


class SemesterLessonProgressListResponse(BaseModel):
    items: list[SemesterLessonProgressResponse]


class AttachSemesterMaterialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=8, max_length=96)
    material_version_id: str = Field(min_length=32, max_length=32)
    material_role: Literal[
        "textbook",
        "reference_ppt",
        "exercise_workbook",
        "homework_workbook",
        "answer_book",
        "supplement",
    ]
    workbook_series: str | None = Field(default=None, max_length=120)
    workbook_volume: Literal["A", "B"] | None = None


class UpdateSemesterMaterialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(gt=0)
    material_role: Literal[
        "textbook",
        "reference_ppt",
        "exercise_workbook",
        "homework_workbook",
        "answer_book",
        "supplement",
    ]
    mapping_status: Literal[
        "unmapped",
        "proposed",
        "partial",
        "confirmed",
        "needs_review",
        "conflict",
    ]
    is_active: bool
    workbook_series: str | None = Field(default=None, max_length=120)
    workbook_volume: Literal["A", "B"] | None = None


class SemesterMaterialResponse(BaseModel):
    id: str
    semester_id: str
    material_source_id: str
    display_name: str
    material_role: str
    is_daily_workbook: bool
    workbook_series: str | None
    workbook_volume: str | None
    parse_status: str
    mapping_status: str
    current_material_version_id: str
    safe_filename: str
    current_inspection_status: str
    current_unit_count: int | None
    last_parsed_version_id: str | None
    has_unparsed_update: bool
    parsed_at: str | None
    is_active: bool
    revision: int
    created_at: str
    updated_at: str

    @classmethod
    def from_domain(
        cls,
        item: SemesterMaterialRecord,
    ) -> "SemesterMaterialResponse":
        payload = asdict(item)
        payload["safe_filename"] = payload.pop("current_file_name")
        return cls.model_validate(payload)


class SemesterMaterialListResponse(BaseModel):
    items: list[SemesterMaterialResponse]


class ReferencePptCollectionMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_record_id: str = Field(min_length=32, max_length=32)
    relative_path: str = Field(min_length=1, max_length=600)


class CreateReferencePptCollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=8, max_length=96)
    display_name: str = Field(min_length=1, max_length=160)
    ignored_file_count: int = Field(default=0, ge=0, le=10_000)
    members: list[ReferencePptCollectionMemberRequest] = Field(
        min_length=1,
        max_length=500,
    )


class UpdateReferencePptCollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool


class ReferencePptCollectionMemberResponse(BaseModel):
    id: str
    collection_id: str
    material_record_id: str
    relative_path: str
    kind: str
    confidence: str
    chapter_number: int | None
    section_number: int | None
    subsection_number: int | None
    lesson_number: int | None
    normalized_title: str
    evidence: list[str]
    issues: list[str]
    created_at: str


class ReferencePptCollectionResponse(BaseModel):
    id: str
    semester_id: str
    display_name: str
    mapping_proposal_id: str
    ignored_file_count: int
    is_active: bool
    revision: int
    created_at: str
    updated_at: str
    members: list[ReferencePptCollectionMemberResponse]

    @classmethod
    def from_domain(
        cls,
        item: ReferencePptCollection,
    ) -> "ReferencePptCollectionResponse":
        return cls.model_validate(asdict(item))


class ReferencePptCollectionListResponse(BaseModel):
    items: list[ReferencePptCollectionResponse]


class SemesterMappingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_record_ids: list[str] = Field(min_length=1, max_length=1)


class GenerateSemesterMappingRequest(SemesterMappingRequest):
    operation_id: str = Field(min_length=8, max_length=96)


class StartSemesterMappingJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=8, max_length=96)
    material_record_id: str = Field(min_length=32, max_length=32)
    expected_source_state_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class SemesterMappingJobResponse(MaterialParseJobResponse):
    pass


class SemesterMappingJobListResponse(BaseModel):
    items: list[SemesterMappingJobResponse]


class SemesterMappingPreflightResponse(BaseModel):
    semester_id: str
    source_state_sha256: str
    will_call_model: bool
    model_available: bool
    model_label: str | None
    model_destination_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    material_count: int
    unit_count: int
    existing_lesson_count: int
    creates_initial_tree: bool
    automatic_retry: bool
    evidence_strategy: str
    evidence_confidence: str
    scanned_unit_count: int
    directory_page_image_count: int
    directory_page_images_sent: bool
    toc_entry_count: int
    anchor_count: int
    estimated_input_characters: int
    full_page_text_sent: bool
    evidence_issues: list[str]


class SemesterMappingProposalResponse(BaseModel):
    id: str
    semester_id: str
    operation_id: str
    source_state_sha256: str
    status: str
    payload: dict[str, object]
    revision: int
    created_at: str
    updated_at: str
    applied_at: str | None

    @classmethod
    def from_domain(
        cls,
        item: SemesterMappingProposal,
    ) -> "SemesterMappingProposalResponse":
        return cls.model_validate(asdict(item))


class SemesterMappingProposalListResponse(BaseModel):
    items: list[SemesterMappingProposalResponse]


class ApplySemesterMappingProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(gt=0)
    chapter_key: str | None = Field(default=None, min_length=1, max_length=160)


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
    modified_ns: str | None
    unit_count: int | None
    parse_expected_unit_count: int | None
    preview_completed_count: int
    ocr_completed_count: int
    ocr_total_count: int
    inspection_status: str
    availability: str
    source_revision: int
    source_archived_at: str | None
    created_at: str

    @classmethod
    def from_domain(cls, item: MaterialVersion) -> "MaterialVersionResponse":
        payload = asdict(item)
        payload["safe_filename"] = payload.pop("file_name")
        payload["modified_ns"] = (
            str(item.modified_ns) if item.modified_ns is not None else None
        )
        return cls.model_validate(payload)


class MaterialVersionListResponse(BaseModel):
    items: list[MaterialVersionResponse]


class UpdateMaterialSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(gt=0)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None


class DeleteMaterialSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(gt=0)
    operation_id: str = Field(min_length=8, max_length=96)
    preview_version: str = Field(min_length=64, max_length=64)
    confirmation_phrase: str = Field(min_length=1, max_length=80)


class MaterialDeletionImpactCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_sources: int = Field(ge=0)
    material_versions: int = Field(ge=0)
    material_units: int = Field(ge=0)
    lesson_material_links: int = Field(ge=0)
    semester_material_records: int = Field(ge=0)
    semester_mapping_proposals: int = Field(ge=0)
    reference_ppt_collections: int = Field(ge=0)
    exercise_regions: int = Field(ge=0)
    exercise_candidates: int = Field(ge=0)


class MaterialDeletionAffectedSemester(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semester_id: str
    title: str
    school_year: str
    term: Literal["first", "second"]


class MaterialDeletionImpactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    display_name: str
    source_revision: int = Field(gt=0)
    impact_counts: MaterialDeletionImpactCounts
    affected_semesters: list[MaterialDeletionAffectedSemester]
    generation_history_count: int = Field(ge=0)
    preserved_snapshot_count: int = Field(ge=0)
    blocking_generation_count: int = Field(ge=0)
    can_delete: bool
    blocker_code: str | None
    preserved_history_note: str | None
    confirmation_phrase: str
    preview_version: str = Field(min_length=64, max_length=64)
    owned_file_count: int = Field(ge=0)


class DeleteMaterialSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str
    status: Literal["running", "succeeded", "failed", "interrupted"]
    preview_version: str = Field(min_length=64, max_length=64)
    deleted_source_id: str | None
    deleted_file_count: int = Field(ge=0)
    counts: MaterialDeletionImpactCounts
    error_code: str | None = None
    impact: MaterialDeletionImpactResponse | None = None


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
    preparation_preferences: TeachingPreferencesPayload = Field(
        default_factory=lambda: TeachingPreferencesPayload.model_validate(
            DEFAULT_TEACHING_PREFERENCES
        )
    )
    selected_material_link_ids: list[str] | None = Field(
        default=None, max_length=200
    )
    selected_exercise_candidate_ids: list[str] | None = Field(
        default=None, max_length=200
    )


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


class ResourcePackSelectionPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_ppt_intents: dict[
        str, Literal["keep", "candidate_delete"]
    ] = Field(default_factory=dict)
    selected_material_link_ids: list[str] | None = Field(
        default=None, max_length=200
    )
    selected_exercise_candidate_ids: list[str] | None = Field(
        default=None, max_length=200
    )


class LessonDraftPreflightResponse(BaseModel):
    resource_pack_id: str
    resource_pack_version: int
    resource_pack_sha256: str
    mode: Literal["local_template", "model"]
    will_call_model: bool
    model_available: bool
    model_label: str | None
    model_destination_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_scope: dict[str, Any]
    references: list[dict[str, str]]
    missing_and_uncertain_count: int
    preparation_preferences: TeachingPreferencesPayload


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


class SlidePositionOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class SlideOperationReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=32, max_length=32)
    decision: Literal["proposed", "approved", "rejected"]
    reason: str = Field(min_length=1, max_length=1_000)
    planned_minutes: int = Field(ge=0, le=120)
    teacher_note: str | None = Field(default=None, max_length=1_000)
    target_slide_number: int | None = Field(default=None, ge=1, le=2_000)
    position: SlidePositionOverride | None = None
    text: str | None = Field(default=None, min_length=1, max_length=64)


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
    preview_only: bool = False


class ConfirmPptxPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    phase: Literal["copying", "executing", "verifying", "publishing", "done"]
    cancel_requested: bool
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


class LessonGenerationPerformanceResponse(BaseModel):
    execution_run_id: str
    slide_plan_id: str
    status: str
    budget_ms: int
    total_machine_elapsed_ms: int
    draft_elapsed_ms: int
    wps_elapsed_ms: int
    model_call_count: int
    wps_execution_count: int
    technical_retry_count: int
    budget_status: Literal["running", "within", "exceeded"]
    within_budget: bool | None
    human_review_wait_excluded: bool

    @classmethod
    def from_domain(
        cls,
        item: LessonGenerationPerformance,
    ) -> "LessonGenerationPerformanceResponse":
        return cls.model_validate(asdict(item))


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


class LessonPreparationStatusResponse(BaseModel):
    lesson_node_id: str
    title: str
    sort_order: int
    duration_minutes: int
    manual_progress: Literal[
        "not_started", "preparing", "ready", "taught", "skipped"
    ]
    manual_progress_revision: int | None
    preparation_stage: Literal["select", "materials", "plan", "slides", "package"]
    next_action: str
    blockers: list[str]
    cells: dict[str, dict[str, Any]]
    ai_tasks: list[dict[str, Any]]
    summary_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    latest: dict[str, Any]


class LessonPreparationStatusListResponse(BaseModel):
    semester_id: str
    items: list[LessonPreparationStatusResponse]


class ApplySemesterMappingAdoptionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["apply_semester_mapping"]
    proposal_revision: int = Field(ge=1)


class ConfirmLessonDraftAdoptionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["confirm_lesson_draft"]
    payload: dict[str, Any]


class FinalizeExerciseSuggestionsAdoptionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["finalize_exercise_suggestions"]


class ReviewSlidePlanAdoptionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["review_slide_plan"]
    operation_reviews: list[dict[str, Any]]
    approve_low_risk_deletions: bool = False
    review_note: str | None = Field(default=None, max_length=1_000)


TeachingPrepAdoptionCommand = (
    ApplySemesterMappingAdoptionCommand
    | ConfirmLessonDraftAdoptionCommand
    | FinalizeExerciseSuggestionsAdoptionCommand
    | ReviewSlidePlanAdoptionCommand
)


class AdoptWorkspaceAIHandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_revision: str = Field(min_length=1, max_length=160)
    target_revision: str = Field(min_length=1, max_length=160)
    command: TeachingPrepAdoptionCommand | None = Field(
        default=None,
        discriminator="kind",
    )


class TeachingPrepAIAdoptionResponse(BaseModel):
    adoption_id: str
    handoff_id: str
    task_kind: str
    proposal_ref_id: str
    object_kind: str
    object_id: str
    object_ref: str
    object_status: str
    draft_revision: str
    target_revision: str
    receipt_revision: str
    adopted_at: str

    @classmethod
    def from_domain(
        cls,
        item: TeachingPrepAIAdoption,
    ) -> "TeachingPrepAIAdoptionResponse":
        return cls.model_validate(asdict(item))


class ReviewSemesterMappingRowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    decision: Literal["accepted", "modified", "rejected"]
    lesson_ref: str | None = Field(default=None, max_length=128)
    start_unit: int | None = Field(default=None, ge=1)
    end_unit: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=500)


class RejectSemesterMappingProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


class SaveReferenceSelectionDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = Field(default=None, ge=1)
    source_state_sha256: str = Field(min_length=64, max_length=64)
    selection: dict[str, Any]


class ReferenceSelectionDraftResponse(BaseModel):
    lesson_node_id: str
    payload: dict[str, Any]
    source_state_sha256: str
    revision: int
    created_at: str
    updated_at: str

    @classmethod
    def from_domain(
        cls, item: ReferenceSelectionDraft
    ) -> "ReferenceSelectionDraftResponse":
        return cls.model_validate(asdict(item))


class ReferenceSelectionPreflightResponse(BaseModel):
    lesson_node_id: str
    source_state_sha256: str
    catalog: dict[str, Any]
    draft: dict[str, Any] | None
    model_available: bool
    model_label: str | None
    model_destination_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    will_call_model: bool


class FreezeReferenceSelectionSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: str = Field(min_length=8, max_length=96)
    expected_draft_revision: int = Field(ge=1)


class ReferenceSelectionSnapshotResponse(BaseModel):
    id: str
    lesson_node_id: str
    source_state_sha256: str
    payload: dict[str, Any]
    created_at: str

    @classmethod
    def from_domain(
        cls, item: ReferenceSelectionSnapshot
    ) -> "ReferenceSelectionSnapshotResponse":
        return cls.model_validate(asdict(item))


class ExerciseSuggestionPreflightResponse(BaseModel):
    snapshot_id: str
    lesson_node_id: str
    source_state_sha256: str
    model_available: bool
    model_label: str | None
    will_call_model: bool
    automatic_retry_count: int
    material_range_count: int


class StartExerciseSuggestionRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=8, max_length=96)
    confirmed: bool


class ExerciseSuggestionResponse(BaseModel):
    id: str
    run_id: str
    lesson_node_id: str
    source_state_sha256: str
    decision: Literal["pending", "accepted", "modified", "rejected"]
    original_payload: dict[str, Any]
    teacher_payload: dict[str, Any] | None
    rejection_reason: str | None
    exercise_candidate_id: str | None
    revision: int
    created_at: str
    updated_at: str

    @classmethod
    def from_domain(
        cls, item: ExerciseSuggestion
    ) -> "ExerciseSuggestionResponse":
        return cls.model_validate(asdict(item))


class ExerciseSuggestionRunResponse(BaseModel):
    id: str
    snapshot_id: str
    operation_id: str
    status: Literal[
        "running", "succeeded", "failed", "cancelled", "result_unknown"
    ]
    error_code: str | None
    model_call_count: int
    created_at: str
    updated_at: str
    finished_at: str | None
    suggestions: list[ExerciseSuggestionResponse] = Field(default_factory=list)

    @classmethod
    def from_domain(
        cls,
        run: ExerciseSuggestionRun,
        suggestions: list[ExerciseSuggestion] | tuple[ExerciseSuggestion, ...] = (),
    ) -> "ExerciseSuggestionRunResponse":
        payload = asdict(run)
        payload["suggestions"] = [asdict(item) for item in suggestions]
        return cls.model_validate(payload)


class StartSlideAnimationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=8, max_length=96)
    confirmed: bool
    material_link_id: str = Field(min_length=32, max_length=32)
    page_indexes: list[int] = Field(min_length=1, max_length=4)


class DecideSlideAnimationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


class SlideAnimationSceneResponse(BaseModel):
    title: str
    narration: str
    duration_ms: int
    source_page: int
    highlight: str | None = None


class SlideAnimationStoryboardResponse(BaseModel):
    title: str
    scenes: list[SlideAnimationSceneResponse]


class SlideAnimationRunResponse(BaseModel):
    id: str
    lesson_node_id: str
    material_version_id: str
    material_link_id: str
    operation_id: str
    page_indexes: list[int]
    storyboard: SlideAnimationStoryboardResponse | None = None
    status: Literal[
        "running",
        "succeeded",
        "accepted",
        "discarded",
        "failed",
        "cancelled",
        "result_unknown",
    ]
    teacher_decision: Literal["pending", "accepted", "discarded"]
    error_code: str | None
    model_call_count: int
    revision: int
    can_preview: bool
    can_download: bool
    created_at: str
    updated_at: str
    finished_at: str | None

    @classmethod
    def from_domain(cls, run: SlideAnimationRun) -> "SlideAnimationRunResponse":
        storyboard = None
        if isinstance(run.storyboard, dict):
            storyboard = SlideAnimationStoryboardResponse.model_validate(
                run.storyboard
            )
        return cls(
            id=run.id,
            lesson_node_id=run.lesson_node_id,
            material_version_id=run.material_version_id,
            material_link_id=run.material_link_id,
            operation_id=run.operation_id,
            page_indexes=list(run.page_indexes),
            storyboard=storyboard,
            status=run.status,  # type: ignore[arg-type]
            teacher_decision=run.teacher_decision,  # type: ignore[arg-type]
            error_code=run.error_code,
            model_call_count=run.model_call_count,
            revision=run.revision,
            can_preview=run.status in {"succeeded", "accepted"},
            can_download=run.status == "accepted",
            created_at=run.created_at,
            updated_at=run.updated_at,
            finished_at=run.finished_at,
        )


class SlideAnimationRunListResponse(BaseModel):
    lesson_node_id: str
    items: list[SlideAnimationRunResponse]
    billed_count: int
    billed_limit: int
    page_limit: int


class ReviewExerciseSuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    decision: Literal["accepted", "modified", "rejected"]
    teacher_payload: dict[str, Any] | None = None
    rejection_reason: str | None = Field(default=None, max_length=500)


class LessonDraftCapacityPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]


class PptxVersionListItemResponse(PptxVersionResponse):
    is_current: bool
    current_revision: int | None
    preview_url: str
    file_verified: bool


class PptxVersionListResponse(BaseModel):
    lesson_node_id: str
    items: list[PptxVersionListItemResponse]


class ActivatePptxVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = Field(default=None, ge=1)


class ActivatePptxVersionResponse(BaseModel):
    version: PptxVersionResponse
    current_revision: int
    changed: bool


class AdaptationTraceToolResponse(BaseModel):
    name: str
    purpose: str | None = None
    page: int | None = None
    source_ref: str = ""


class AdaptationTraceResultResponse(BaseModel):
    ok: bool
    label: str
    preview_url: str | None = None


class AdaptationTraceEventResponse(BaseModel):
    round: int
    phase: str
    summary: str
    thinking_excerpt: str | None = None
    tool: AdaptationTraceToolResponse | None = None
    result: AdaptationTraceResultResponse | None = None
    model_calls_used: int
    model_calls_max: int


class AdaptationTraceResponse(BaseModel):
    operation_id: str
    lesson_node_id: str
    status: str
    model_calls_used: int
    model_calls_max: int
    events: list[AdaptationTraceEventResponse]

