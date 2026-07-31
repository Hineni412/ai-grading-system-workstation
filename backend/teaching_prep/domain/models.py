from __future__ import annotations

from dataclasses import dataclass

from .states import LessonPreparationState


@dataclass(frozen=True, slots=True)
class LessonPreparation:
    id: str
    title: str
    class_name: str | None
    state: LessonPreparationState
    revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class TeachingPreferences:
    revision: int
    payload: dict[str, object]
    updated_at: str


@dataclass(frozen=True, slots=True)
class CurriculumEdition:
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


@dataclass(frozen=True, slots=True)
class LessonNode:
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


@dataclass(frozen=True, slots=True)
class MaterialVersion:
    id: str
    source_id: str
    display_name: str
    material_type: str
    content_sha256: str
    file_name: str
    size_bytes: int
    modified_ns: int | None
    unit_count: int | None
    inspection_status: str
    availability: str
    created_at: str


@dataclass(frozen=True, slots=True)
class TeachingSemester:
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


@dataclass(frozen=True, slots=True)
class SemesterLessonProgress:
    id: str
    semester_id: str
    lesson_node_id: str
    lesson_title: str
    status: str
    revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class SemesterMaterialRecord:
    id: str
    semester_id: str
    material_source_id: str
    display_name: str
    material_role: str
    parse_status: str
    mapping_status: str
    current_material_version_id: str
    current_file_name: str
    current_inspection_status: str
    current_unit_count: int | None
    last_parsed_version_id: str | None
    has_unparsed_update: bool
    parsed_at: str | None
    is_active: bool
    revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class SemesterMappingProposal:
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


@dataclass(frozen=True, slots=True)
class MaterialUnit:
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


@dataclass(frozen=True, slots=True)
class LessonMaterialLink:
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


@dataclass(frozen=True, slots=True)
class ExerciseRegion:
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


@dataclass(frozen=True, slots=True)
class DuplicateExerciseSuggestion:
    candidate_id: str
    question_number: str | None
    content_label: str | None
    basis: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExerciseCandidate:
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
    question_regions: tuple[ExerciseRegion, ...]
    answer_regions: tuple[ExerciseRegion, ...]
    duplicate_suggestions: tuple[DuplicateExerciseSuggestion, ...]
    is_active: bool
    revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ResourcePackVersion:
    id: str
    lesson_node_id: str
    version_number: int
    source_state_sha256: str
    pack_sha256: str
    payload: dict[str, object]
    created_at: str


@dataclass(frozen=True, slots=True)
class LessonDraftVersion:
    id: str
    resource_pack_id: str
    version_number: int
    based_on_draft_id: str | None
    operation_id: str | None
    source_kind: str
    model_label: str | None
    status: str
    payload: dict[str, object]
    capacity: dict[str, object]
    created_at: str


@dataclass(frozen=True, slots=True)
class SlidePlanVersion:
    id: str
    lesson_draft_id: str
    resource_pack_id: str
    version_number: int
    based_on_plan_id: str | None
    source_ppt_state_sha256: str
    status: str
    payload: dict[str, object]
    created_at: str


@dataclass(frozen=True, slots=True)
class PptxVersion:
    id: str
    slide_plan_id: str
    lesson_node_id: str
    execution_run_id: str
    version_number: int
    status: str
    output_filename: str
    output_sha256: str | None
    slide_count: int
    verification_report: dict[str, object]
    created_at: str
    published_at: str | None


@dataclass(frozen=True, slots=True)
class PptxExecutionRun:
    id: str
    operation_id: str
    slide_plan_id: str
    source_material_version_id: str
    source_sha256: str
    expected_slide_count: int
    status: str
    execution_report: dict[str, object] | None
    verification_report: dict[str, object] | None
    error_code: str | None
    published_version_id: str | None
    staging_retained: bool
    recovery_actions: tuple[str, ...]
    created_at: str
    updated_at: str
    finished_at: str | None


@dataclass(frozen=True, slots=True)
class LessonGenerationPerformance:
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
    budget_status: str
    within_budget: bool | None
    human_review_wait_excluded: bool


@dataclass(frozen=True, slots=True)
class ClassVariant:
    id: str
    base_resource_pack_id: str
    resource_pack_id: str
    lesson_node_id: str
    class_name: str
    prior_review_ids: tuple[str, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class UpClassPackage:
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
    recovery_actions: tuple[str, ...]
    created_at: str
    updated_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class PostLessonReview:
    id: str
    package_id: str
    pptx_version_id: str
    lesson_node_id: str
    class_name: str | None
    payload: dict[str, object]
    use_in_next_version: bool
    created_at: str
