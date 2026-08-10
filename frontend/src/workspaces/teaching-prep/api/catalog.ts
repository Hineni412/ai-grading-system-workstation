import { apiClient } from '../../../api/client'
import { decodeJobResponse, type JobResponse } from '../../../api/jobs'
import {
  assertNoPathLikeKeys,
  isRecord,
} from '../../../api/validation'

export type CurriculumVolume = 'first' | 'second' | 'whole_year'
export type LessonNodeType = 'chapter' | 'section' | 'lesson'

export interface TeachingPrepModuleStatus {
  module: 'teaching-prep'
  enabled: boolean
  schema_version: string
  real_model_enabled: boolean
  semester_mapping_model_available: boolean
  exercise_suggestion_model_available: boolean
  real_wps_enabled: boolean
  wps_execution_available: boolean
}

export interface CurriculumEdition {
  id: string
  title: string
  grade_level: number
  volume: CurriculumVolume
  publisher: string | null
  edition_label: string | null
  revision: number
  is_active: boolean
  created_at: string
  updated_at: string
}

export type SemesterStatus = 'planning' | 'active' | 'completed' | 'archived'
export type SemesterLessonProgressStatus =
  | 'not_started'
  | 'preparing'
  | 'ready'
  | 'taught'
  | 'skipped'
export type SemesterMaterialRole =
  | 'textbook'
  | 'reference_ppt'
  | 'exercise_workbook'
  | 'homework_workbook'
  | 'answer_book'
  | 'supplement'
export type SemesterMaterialMappingStatus =
  | 'unmapped'
  | 'proposed'
  | 'partial'
  | 'confirmed'
  | 'needs_review'
  | 'conflict'

export interface TeachingSemester {
  id: string
  curriculum_id: string
  curriculum_title: string
  school_year: string
  term: 'first' | 'second'
  planned_new_lesson_count: number
  status: SemesterStatus
  active_lesson_count: number
  not_started_lesson_count: number
  preparing_lesson_count: number
  ready_lesson_count: number
  taught_lesson_count: number
  skipped_lesson_count: number
  material_count: number
  parsed_material_count: number
  mapped_material_count: number
  revision: number
  created_at: string
  updated_at: string
}

export interface SemesterLessonProgress {
  id: string
  semester_id: string
  lesson_node_id: string
  lesson_title: string
  status: SemesterLessonProgressStatus
  revision: number
  created_at: string
  updated_at: string
}

export interface SemesterMaterialRecord {
  id: string
  semester_id: string
  material_source_id: string
  display_name: string
  material_role: SemesterMaterialRole
  is_daily_workbook?: boolean
  workbook_series?: string | null
  workbook_volume?: 'A' | 'B' | null
  parse_status: 'not_started' | 'parsed' | 'needs_review' | 'failed'
  mapping_status: SemesterMaterialMappingStatus
  current_material_version_id: string
  safe_filename: string
  current_inspection_status: string
  current_unit_count: number | null
  last_parsed_version_id: string | null
  has_unparsed_update: boolean
  parsed_at: string | null
  is_active: boolean
  revision: number
  created_at: string
  updated_at: string
}

export interface ReferencePptCollectionMember {
  id: string
  collection_id: string
  material_record_id: string
  relative_path: string
  kind: 'lesson' | 'review' | 'strategy' | 'practice'
  confidence: 'high' | 'medium' | 'low'
  chapter_number: number | null
  section_number: number | null
  subsection_number: number | null
  lesson_number: number | null
  normalized_title: string
  evidence: string[]
  issues: string[]
  created_at: string
}

export interface ReferencePptCollection {
  id: string
  semester_id: string
  display_name: string
  mapping_proposal_id: string
  ignored_file_count: number
  revision: number
  created_at: string
  updated_at: string
  members: ReferencePptCollectionMember[]
}

export interface SemesterMappingPreflight {
  semester_id: string
  source_state_sha256: string
  will_call_model: boolean
  model_available: boolean
  model_label: string | null
  model_destination_fingerprint: string
  material_count: number
  unit_count: number
  existing_lesson_count: number
  creates_initial_tree: boolean
  automatic_retry: boolean
  evidence_strategy: 'toc_calibrated' | 'toc_unverified' | 'sparse_outline'
  evidence_confidence: 'high' | 'medium' | 'low'
  scanned_unit_count: number
  directory_page_image_count: number
  directory_page_images_sent: boolean
  toc_entry_count: number
  anchor_count: number
  estimated_input_characters: number
  full_page_text_sent: boolean
  evidence_issues: string[]
}

export interface SemesterMappingProposalLesson {
  key: string
  title: string
  duration_minutes: number
}

export interface SemesterMappingProposalSection {
  key: string
  title: string
  lessons: SemesterMappingProposalLesson[]
}

export interface SemesterMappingProposalChapter {
  key: string
  title: string
  sections: SemesterMappingProposalSection[]
}

export interface SemesterMappingProposalRange {
  mapping_id: string
  material_record_id: string
  lesson_ref: string
  start_unit: number
  end_unit: number
  purpose: 'textbook' | 'reference_ppt' | 'exercise' | 'answer' | 'supplement'
  decision: 'pending' | 'accepted' | 'modified' | 'rejected'
  teacher_revision: {
    lesson_ref: string
    start_unit: number
    end_unit: number
  } | null
  decision_reason: string | null
  basis?: string
  evidence_refs?: string[]
  confidence?: 'high' | 'medium' | 'low'
  evidence?: string[]
}

export interface SemesterMappingProposal {
  id: string
  semester_id: string
  operation_id: string
  source_state_sha256: string
  status: 'proposed' | 'applied' | 'rejected'
  payload: {
    tree: SemesterMappingProposalChapter[]
    mappings: SemesterMappingProposalRange[]
    uncertainties: string[]
    source_material_record_ids: string[]
    generation_source?: 'local_reference_ppt_names'
    summary?: {
      ppt_count: number
      lesson_candidate_count: number
      special_count: number
      high_confidence_count: number
      needs_review_count: number
      chapter_count: number
    }
    directory_evidence?: {
      strategy: 'toc_calibrated' | 'toc_unverified' | 'sparse_outline'
      total_unit_count?: number
      directory_page_unit_indices?: number[]
      scanned_unit_indices?: number[]
      toc_entries: Array<Record<string, unknown>>
      resolved_ranges: Array<Record<string, unknown>>
      anchors: Array<Record<string, unknown>>
      printed_to_pdf_offset?: number | null
      confidence: 'high' | 'medium' | 'low'
      issues: string[]
      full_page_text_sent: boolean
    }
  }
  revision: number
  created_at: string
  updated_at: string
  applied_at: string | null
}

export interface LessonNode {
  id: string
  curriculum_id: string
  parent_id: string | null
  node_type: LessonNodeType
  title: string
  sort_order: number
  duration_minutes: number | null
  source_kind: 'teacher' | 'catalog' | 'assistant_draft'
  is_active: boolean
  revision: number
  created_at: string
  updated_at: string
}

export interface MaterialVersion {
  id: string
  source_id: string
  display_name: string
  material_type: 'pdf' | 'pptx' | 'image'
  content_sha256: string
  safe_filename: string
  size_bytes: number
  modified_ns: string | null
  unit_count: number | null
  parse_expected_unit_count?: number | null
  preview_completed_count?: number
  ocr_completed_count?: number
  ocr_total_count?: number
  inspection_status: string
  availability: 'available' | 'missing' | 'needs_relocation'
  source_revision?: number
  source_archived_at?: string | null
  created_at: string
}

export interface MaterialDeletionAffectedSemester {
  semester_id: string
  title: string
  school_year: string
  term: 'first' | 'second'
}

export interface MaterialDeletionImpactCounts {
  material_sources: number
  material_versions: number
  material_units: number
  lesson_material_links: number
  semester_material_records: number
  semester_mapping_proposals: number
  reference_ppt_collections: number
  exercise_regions: number
  exercise_candidates: number
}

export interface MaterialDeletionPreview {
  source_id: string
  display_name: string
  source_revision: number
  impact_counts: MaterialDeletionImpactCounts
  affected_semesters: MaterialDeletionAffectedSemester[]
  generation_history_count: number
  preserved_snapshot_count: number
  blocking_generation_count: number
  can_delete: boolean
  blocker_code: string | null
  preserved_history_note: string | null
  confirmation_phrase: string
  preview_version: string
  owned_file_count: number
}

export interface DeleteMaterialSourceResult {
  operation_id: string
  status: string
  preview_version: string
  deleted_source_id: string | null
  deleted_file_count: number
  counts: MaterialDeletionImpactCounts
  error_code: string | null
  impact?: MaterialDeletionPreview | null
}

export interface MaterialUnit {
  id: string
  material_version_id: string
  unit_kind: 'pdf_page' | 'ppt_slide' | 'image'
  unit_index: number
  title: string | null
  text_excerpt: string
  text_status: 'embedded' | 'empty' | 'manual' | 'not_applicable'
  formula_review_required: boolean
  object_summary: Record<string, unknown>
  preview_url: string
  revision: number
  created_at: string
  updated_at: string
}

export type MaterialLinkPurpose =
  | 'textbook'
  | 'reference_ppt'
  | 'exercise'
  | 'answer'
  | 'supplement'

export interface MaterialLink {
  id: string
  lesson_node_id: string
  material_version_id: string
  material_name: string
  material_type: 'pdf' | 'pptx' | 'image'
  start_unit: number
  end_unit: number
  crop: Record<string, number> | null
  purpose: MaterialLinkPurpose
  teacher_note: string | null
  confirmation_status: 'proposed' | 'confirmed'
  source_version_sha256: string
  sort_order: number
  is_active: boolean
  revision: number
  created_at: string
  updated_at: string
}

export interface NormalizedCrop {
  x0: number
  y0: number
  x1: number
  y1: number
}

export interface ExerciseRegionInput {
  material_unit_id: string
  crop: NormalizedCrop
}

export interface ExerciseRegion extends ExerciseRegionInput {
  id: string
  region_role: 'question' | 'answer'
  material_version_id: string
  material_name: string
  unit_index: number
  source_version_sha256: string
  sequence: number
  preview_url: string
}

export interface DuplicateExerciseSuggestion {
  candidate_id: string
  question_number: string | null
  content_label: string | null
  basis: Array<'same_teacher_clue' | 'same_question_number_and_source'>
}

export type ExerciseSelectionStatus =
  | 'classroom_candidate'
  | 'backup'
  | 'excluded'

export type ExerciseAnswerStatus =
  | 'candidate'
  | 'teacher_verified'
  | 'rejected'
  | 'missing'

export interface ExerciseCandidate {
  id: string
  lesson_node_id: string
  question_number: string | null
  content_label: string | null
  difficulty: 'unrated' | 'easy' | 'medium' | 'hard'
  classroom_use:
    | 'introduction'
    | 'example'
    | 'guided_practice'
    | 'independent_practice'
    | 'diagnostic'
    | 'challenge'
    | 'summary'
  estimated_minutes: number | null
  teaching_focus: string | null
  teacher_note: string | null
  selection_status: ExerciseSelectionStatus
  answer_status: ExerciseAnswerStatus
  question_regions: ExerciseRegion[]
  answer_regions: ExerciseRegion[]
  duplicate_suggestions: DuplicateExerciseSuggestion[]
  is_active: boolean
  revision: number
  created_at: string
  updated_at: string
}

export interface ExerciseCandidateInput {
  question_number: string | null
  content_label: string | null
  difficulty: ExerciseCandidate['difficulty']
  classroom_use: ExerciseCandidate['classroom_use']
  estimated_minutes: number | null
  teaching_focus: string | null
  teacher_note: string | null
  selection_status: ExerciseSelectionStatus
  answer_status: ExerciseAnswerStatus
  question_regions: ExerciseRegionInput[]
  answer_regions: ExerciseRegionInput[]
}

export interface AssessmentChoice {
  assessment_id: number
  title: string
  status: string
  created_at: string
  updated_at: string
  classes: Array<{
    class_name: string
    student_count: number
  }>
}

export interface QuestionEvidenceChoice {
  question_id: number
  question_number: string
  question_type: string | null
  text_excerpt: string
  difficulty: string | null
  knowledge_points: string[]
  updated_at: string
}

export type PracticeTrimLevel = 'light' | 'moderate' | 'strong'

export interface TeachingPreferencesPayload {
  schema_version: 1
  label_textbook_pages: boolean
  page_label_font_size: 28
  trim_excess_practice: boolean
  practice_trim_level: PracticeTrimLevel
  preserve_teaching_examples: boolean
  prefer_short_practice: boolean
  supplement_from_references: boolean
  supplement_question_limit: number
  supplement_as_source_image: boolean
  prioritize_homework_workbook: boolean
  avoid_direct_homework_copy: boolean
  avoid_ppt_duplicates: boolean
}

export interface TeachingPreferences {
  revision: number
  payload: TeachingPreferencesPayload
  updated_at: string
}

export interface ResourcePack {
  id: string
  lesson_node_id: string
  version_number: number
  source_state_sha256: string
  pack_sha256: string
  payload: Record<string, unknown>
  created_at: string
}

export interface ResourcePackStatus {
  lesson_node_id: string
  has_pack: boolean
  latest_version_number: number | null
  latest_pack_id: string | null
  local_sources_changed: boolean
}

export interface FreezeResourcePackInput {
  request_token: string
  class_name: string | null
  lesson_type: 'new_lesson'
  teacher_context: string | null
  reference_ppt_intents: Record<string, 'keep' | 'candidate_delete'>
  question_ids: number[]
  assessment_ids: number[]
  knowledge_scope: string[]
  preparation_preferences: TeachingPreferencesPayload
  selected_material_link_ids?: string[] | null
  selected_exercise_candidate_ids?: string[] | null
}

export interface DraftClaim {
  text: string
  citations: string[]
}

export interface DraftFocusPoint {
  kind: 'key' | 'difficulty'
  title: string
  rationale: string
  citations: string[]
}

export interface DraftFlowItem {
  phase: 'introduction' | 'exploration' | 'example' | 'practice' | 'summary'
  title: string
  purpose: string
  suggested_minutes: number
  citations: string[]
}

export interface DraftExerciseRecommendation {
  source_ref: string
  action: 'include' | 'backup' | 'move_after_class' | 'exclude' | 'replace_shorter'
  title: string
  reason: string
  estimated_minutes: number
  citations: string[]
}

export interface LessonDraftPayload {
  knowledge_objectives: DraftClaim[]
  focus_points: DraftFocusPoint[]
  anticipated_difficulties: DraftClaim[]
  lesson_flow: DraftFlowItem[]
  exercise_recommendations: DraftExerciseRecommendation[]
  slide_adaptations: Array<{
    slide_ref: string
    role: 'introduction' | 'explanation' | 'example' | 'practice' | 'summary' | 'other'
    action: 'keep' | 'delete'
    textbook_refs: string[]
    reason: string
    citations: string[]
  }>
  uncertainties: string[]
}

export interface LessonCapacity {
  lesson_minutes: number
  duration_source: string
  flow_breakdown: Array<Record<string, unknown>>
  flow_minutes: number
  exercise_minutes: number
  buffer_minutes: number
  planned_minutes: number
  overrun_minutes: number
  within_capacity: boolean
  reduction_options: Array<Record<string, unknown>>
}

export interface LessonDraft {
  id: string
  resource_pack_id: string
  version_number: number
  based_on_draft_id: string | null
  operation_id: string | null
  source_kind: 'local_template' | 'model' | 'teacher'
  model_label: string | null
  status: 'draft' | 'confirmed'
  payload: LessonDraftPayload
  capacity: LessonCapacity
  created_at: string
}

export interface LessonDraftPreflight {
  resource_pack_id: string
  resource_pack_version: number
  resource_pack_sha256: string
  mode: 'local_template' | 'model'
  will_call_model: boolean
  model_available: boolean
  model_label: string | null
  model_destination_fingerprint?: string
  data_scope: Record<string, unknown>
  references: Array<{ id: string; label: string }>
  missing_and_uncertain_count: number
  preparation_preferences: TeachingPreferencesPayload
}

export type SlideOperationDecision = 'proposed' | 'approved' | 'rejected'

export interface SlideOperation {
  operation_id: string
  kind: string
  decision: SlideOperationDecision
  target: Record<string, unknown>
  reason: string
  citations: string[]
  planned_minutes: number
  risk: 'low' | 'medium' | 'high' | 'blocked'
  execution_mode: 'automatic' | 'noop' | 'manual_only'
  support_note: string | null
  details: Record<string, unknown>
  teacher_note: string | null
}

export interface SlidePlan {
  id: string
  lesson_draft_id: string
  resource_pack_id: string
  version_number: number
  based_on_plan_id: string | null
  source_ppt_state_sha256: string
  status: 'in_review' | 'approved' | 'invalidated'
  payload: {
    schema_version: number
    source_presentations: Array<Record<string, unknown>>
    slides: Array<Record<string, unknown>>
    operations: SlideOperation[]
    unsupported_objects: Array<Record<string, unknown>>
    approval_history: Array<Record<string, unknown>>
  }
  created_at: string
}

export interface SlidePlanPreview {
  valid_for_execution: boolean
  source_changed: boolean
  includes_proposed_operations: boolean
  before_slide_count: number
  after_slide_count: number
  before: Array<Record<string, unknown>>
  after: Array<Record<string, unknown>>
  changes: Array<Record<string, unknown>>
  manual_only: Array<Record<string, unknown>>
}

export interface SlideOperationReviewInput {
  operation_id: string
  decision: SlideOperationDecision
  reason: string
  planned_minutes: number
  teacher_note: string | null
  target_slide_number?: number
  position?: { x: number; y: number; width: number; height: number }
  text?: string
}

export type PptxExecutionStatus =
  | 'running'
  | 'verifying'
  | 'publishing'
  | 'published'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

export interface PptxExecution {
  id: string
  operation_id: string
  slide_plan_id: string
  source_material_version_id: string
  source_sha256: string
  expected_slide_count: number
  status: PptxExecutionStatus
  execution_report: Record<string, unknown> | null
  verification_report: Record<string, unknown> | null
  error_code: string | null
  published_version_id: string | null
  phase: 'copying' | 'executing' | 'verifying' | 'publishing' | 'done'
  cancel_requested: boolean
  staging_retained: boolean
  recovery_actions: string[]
  created_at: string
  updated_at: string
  finished_at: string | null
}

export interface LessonGenerationPerformance {
  execution_run_id: string
  slide_plan_id: string
  status: PptxExecutionStatus
  budget_ms: number
  total_machine_elapsed_ms: number
  draft_elapsed_ms: number
  wps_elapsed_ms: number
  model_call_count: number
  wps_execution_count: number
  technical_retry_count: number
  budget_status: 'running' | 'within' | 'exceeded'
  within_budget: boolean | null
  human_review_wait_excluded: boolean
}

export interface PptxVersion {
  id: string
  slide_plan_id: string
  lesson_node_id: string
  execution_run_id: string
  version_number: number
  status: 'published'
  output_filename: string
  output_sha256: string
  slide_count: number
  verification_report: Record<string, unknown>
  download_url: string
  created_at: string
  published_at: string
}

export interface PptxExecutionResult {
  execution: PptxExecution
  version: PptxVersion | null
}

export interface ClassVariant {
  id: string
  base_resource_pack_id: string
  resource_pack_id: string
  lesson_node_id: string
  class_name: string
  prior_review_ids: string[]
  created_at: string
}

export interface UpClassPackage {
  id: string
  pptx_version_id: string
  slide_plan_id: string
  lesson_draft_id: string
  resource_pack_id: string
  lesson_node_id: string
  class_name: string | null
  version_number: number
  status: 'building' | 'publishing' | 'complete' | 'failed' | 'interrupted'
  output_filename: string | null
  package_sha256: string | null
  manifest: Record<string, unknown> | null
  error_code: string | null
  is_current: boolean
  staging_retained: boolean
  recovery_actions: string[]
  created_at: string
  updated_at: string
  completed_at: string | null
  download_url: string | null
}

export interface PostLessonReview {
  id: string
  package_id: string
  pptx_version_id: string
  lesson_node_id: string
  class_name: string | null
  payload: {
    timing: 'on_time' | 'over' | 'early'
    question_outcome:
      | 'appropriate'
      | 'too_hard'
      | 'too_easy'
      | 'ineffective'
      | 'not_observed'
    reteach_points: string[]
    next_action: 'keep' | 'delete' | 'adjust'
    note: string | null
  }
  use_in_next_version: boolean
  created_at: string
}

export interface CreateCurriculumInput {
  request_token: string
  title: string
  grade_level: number
  volume: CurriculumVolume
  publisher?: string | null
  edition_label?: string | null
}

export interface CreateSemesterWorkspaceInput {
  request_token: string
  curriculum: Omit<CreateCurriculumInput, 'request_token'>
  school_year: string
  term: 'first' | 'second'
  planned_new_lesson_count: number
}

export interface SemesterWorkspace {
  curriculum: CurriculumEdition
  semester: TeachingSemester
}

export interface CreateLessonNodeInput {
  request_token: string
  parent_id: string | null
  node_type: LessonNodeType
  title: string
  duration_minutes?: number | null
  source_kind?: 'teacher' | 'catalog'
}

export interface UpdateLessonNodeInput {
  expected_revision: number
  title: string
  duration_minutes: number | null
  is_active: boolean
}

export interface ReorderLessonNodesInput {
  parent_id: string | null
  ordered_ids: string[]
  expected_revisions: Record<string, number>
}

export interface CreateSemesterInput {
  request_token: string
  curriculum_id: string
  school_year: string
  term: 'first' | 'second'
  planned_new_lesson_count: number
}

function text(value: unknown): value is string {
  return typeof value === 'string' && Boolean(value.trim())
}

function nullableText(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

function integer(value: unknown, minimum = 0): value is number {
  return Number.isSafeInteger(value) && Number(value) >= minimum
}

function curriculum(value: unknown): CurriculumEdition {
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.title)
    || !integer(value.grade_level, 7)
    || !['first', 'second', 'whole_year'].includes(String(value.volume))
    || !nullableText(value.publisher)
    || !nullableText(value.edition_label)
    || !integer(value.revision, 1)
    || typeof value.is_active !== 'boolean'
    || !text(value.created_at)
    || !text(value.updated_at)
  ) throw new Error('Invalid curriculum response')
  return value as unknown as CurriculumEdition
}

function semester(value: unknown): TeachingSemester {
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.curriculum_id)
    || !text(value.curriculum_title)
    || !text(value.school_year)
    || !['first', 'second'].includes(String(value.term))
    || !integer(value.planned_new_lesson_count)
    || !['planning', 'active', 'completed', 'archived'].includes(
      String(value.status),
    )
    || !integer(value.active_lesson_count)
    || !integer(value.not_started_lesson_count)
    || !integer(value.preparing_lesson_count)
    || !integer(value.ready_lesson_count)
    || !integer(value.taught_lesson_count)
    || !integer(value.skipped_lesson_count)
    || !integer(value.material_count)
    || !integer(value.parsed_material_count)
    || !integer(value.mapped_material_count)
    || !integer(value.revision, 1)
    || !text(value.created_at)
    || !text(value.updated_at)
  ) throw new Error('Invalid semester response')
  return value as unknown as TeachingSemester
}

function semesterWorkspace(value: unknown): SemesterWorkspace {
  if (
    !isRecord(value)
    || !isRecord(value.curriculum)
    || !isRecord(value.semester)
  ) throw new Error('Invalid semester workspace response')
  return {
    curriculum: curriculum(value.curriculum),
    semester: semester(value.semester),
  }
}

function semesterLessonProgress(value: unknown): SemesterLessonProgress {
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.semester_id)
    || !text(value.lesson_node_id)
    || !text(value.lesson_title)
    || !['not_started', 'preparing', 'ready', 'taught', 'skipped'].includes(
      String(value.status),
    )
    || !integer(value.revision, 1)
    || !text(value.created_at)
    || !text(value.updated_at)
  ) throw new Error('Invalid semester lesson progress response')
  return value as unknown as SemesterLessonProgress
}

function semesterMaterial(value: unknown): SemesterMaterialRecord {
  if (isRecord(value)) {
    value = {
      is_daily_workbook: false,
      workbook_series: null,
      workbook_volume: null,
      ...value,
    }
  }
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.semester_id)
    || !text(value.material_source_id)
    || !text(value.display_name)
    || ![
      'textbook',
      'reference_ppt',
      'exercise_workbook',
      'homework_workbook',
      'answer_book',
      'supplement',
    ].includes(String(value.material_role))
    || typeof value.is_daily_workbook !== 'boolean'
    || !nullableText(value.workbook_series)
    || !(value.workbook_volume === null || ['A', 'B'].includes(String(value.workbook_volume)))
    || !['not_started', 'parsed', 'needs_review', 'failed'].includes(
      String(value.parse_status),
    )
    || ![
      'unmapped',
      'proposed',
      'partial',
      'confirmed',
      'needs_review',
      'conflict',
    ].includes(String(value.mapping_status))
    || !text(value.current_material_version_id)
    || !text(value.safe_filename)
    || !text(value.current_inspection_status)
    || !(value.current_unit_count === null || integer(value.current_unit_count))
    || !nullableText(value.last_parsed_version_id)
    || typeof value.has_unparsed_update !== 'boolean'
    || !nullableText(value.parsed_at)
    || typeof value.is_active !== 'boolean'
    || !integer(value.revision, 1)
    || !text(value.created_at)
    || !text(value.updated_at)
  ) throw new Error('Invalid semester material response')
  return value as unknown as SemesterMaterialRecord
}

const SEMESTER_MAPPING_PREFLIGHT_KEYS = new Set([
  'semester_id',
  'source_state_sha256',
  'will_call_model',
  'model_available',
  'model_label',
  'model_destination_fingerprint',
  'material_count',
  'unit_count',
  'existing_lesson_count',
  'creates_initial_tree',
  'automatic_retry',
  'evidence_strategy',
  'evidence_confidence',
  'scanned_unit_count',
  'directory_page_image_count',
  'directory_page_images_sent',
  'toc_entry_count',
  'anchor_count',
  'estimated_input_characters',
  'full_page_text_sent',
  'evidence_issues',
])

function semesterMappingPreflight(value: unknown): SemesterMappingPreflight {
  if (
    !isRecord(value)
    || Object.keys(value).length !== SEMESTER_MAPPING_PREFLIGHT_KEYS.size
    || Object.keys(value).some(key => !SEMESTER_MAPPING_PREFLIGHT_KEYS.has(key))
    || !text(value.semester_id)
    || !text(value.source_state_sha256)
    || typeof value.will_call_model !== 'boolean'
    || typeof value.model_available !== 'boolean'
    || !nullableText(value.model_label)
    || !text(value.model_destination_fingerprint)
    || !integer(value.material_count, 1)
    || !integer(value.unit_count, 1)
    || !integer(value.existing_lesson_count)
    || typeof value.creates_initial_tree !== 'boolean'
    || typeof value.automatic_retry !== 'boolean'
    || !['toc_calibrated', 'toc_unverified', 'sparse_outline'].includes(String(value.evidence_strategy))
    || !['high', 'medium', 'low'].includes(String(value.evidence_confidence))
    || !integer(value.scanned_unit_count)
    || !integer(value.directory_page_image_count)
    || typeof value.directory_page_images_sent !== 'boolean'
    || !integer(value.toc_entry_count)
    || !integer(value.anchor_count)
    || !integer(value.estimated_input_characters)
    || typeof value.full_page_text_sent !== 'boolean'
    || !Array.isArray(value.evidence_issues)
    || !value.evidence_issues.every(text)
  ) throw new Error('Invalid semester mapping preflight response')
  return value as unknown as SemesterMappingPreflight
}

const MATERIAL_DELETION_COUNT_KEYS = new Set([
  'material_sources',
  'material_versions',
  'material_units',
  'lesson_material_links',
  'semester_material_records',
  'semester_mapping_proposals',
  'reference_ppt_collections',
  'exercise_regions',
  'exercise_candidates',
])
const MATERIAL_DELETION_PREVIEW_KEYS = new Set([
  'source_id',
  'display_name',
  'source_revision',
  'impact_counts',
  'affected_semesters',
  'generation_history_count',
  'preserved_snapshot_count',
  'blocking_generation_count',
  'can_delete',
  'blocker_code',
  'preserved_history_note',
  'confirmation_phrase',
  'preview_version',
  'owned_file_count',
])
const MATERIAL_DELETION_OPERATION_KEYS = new Set([
  'operation_id',
  'status',
  'preview_version',
  'deleted_source_id',
  'deleted_file_count',
  'counts',
  'error_code',
  'impact',
])

function hasExactKeys(value: Record<string, unknown>, expected: Set<string>): boolean {
  const keys = Object.keys(value)
  return keys.length === expected.size && keys.every(key => expected.has(key))
}

function materialDeletionCounts(value: unknown): MaterialDeletionImpactCounts {
  if (
    !isRecord(value)
    || !hasExactKeys(value, MATERIAL_DELETION_COUNT_KEYS)
    || Object.values(value).some(item => !integer(item))
  ) throw new Error('Invalid material deletion impact counts')
  return value as unknown as MaterialDeletionImpactCounts
}

function materialDeletionPreview(value: unknown): MaterialDeletionPreview {
  if (
    !isRecord(value)
    || !hasExactKeys(value, MATERIAL_DELETION_PREVIEW_KEYS)
    || !text(value.source_id)
    || !text(value.display_name)
    || !integer(value.source_revision, 1)
    || !Array.isArray(value.affected_semesters)
    || value.affected_semesters.some(semester => (
      !isRecord(semester)
      || !hasExactKeys(semester, new Set(['semester_id', 'title', 'school_year', 'term']))
      || !text(semester.semester_id)
      || !text(semester.title)
      || !text(semester.school_year)
      || !['first', 'second'].includes(String(semester.term))
    ))
    || !integer(value.generation_history_count)
    || !integer(value.preserved_snapshot_count)
    || !integer(value.blocking_generation_count)
    || typeof value.can_delete !== 'boolean'
    || !nullableText(value.blocker_code)
    || !nullableText(value.preserved_history_note)
    || !text(value.confirmation_phrase)
    || !text(value.preview_version)
    || !integer(value.owned_file_count)
  ) throw new Error('Invalid material deletion preview')
  materialDeletionCounts(value.impact_counts)
  return value as unknown as MaterialDeletionPreview
}

function materialDeletionResult(value: unknown): DeleteMaterialSourceResult {
  if (!isRecord(value)) throw new Error('Invalid material deletion response')
  const keys = Object.keys(value)
  const requiredKeys = [...MATERIAL_DELETION_OPERATION_KEYS].filter(key => key !== 'impact')
  if (
    !requiredKeys.every(key => Object.hasOwn(value, key))
    || keys.some(key => !MATERIAL_DELETION_OPERATION_KEYS.has(key))
    || !text(value.operation_id)
    || !text(value.status)
    || !text(value.preview_version)
    || !nullableText(value.deleted_source_id)
    || !integer(value.deleted_file_count)
    || !nullableText(value.error_code)
    || (value.impact !== undefined && value.impact !== null && !isRecord(value.impact))
  ) throw new Error('Invalid material deletion response')
  materialDeletionCounts(value.counts)
  if (value.impact !== undefined && value.impact !== null) materialDeletionPreview(value.impact)
  return value as unknown as DeleteMaterialSourceResult
}

function semesterMappingJob(value: unknown): JobResponse {
  const job = decodeJobResponse(value)
  const payload = job.payload
  const digest = payload.source_state_sha256
  if (
    job.job_type !== 'teaching_prep.semester_mapping'
    || typeof payload.semester_id !== 'string'
    || payload.semester_id.length !== 32
    || typeof payload.material_record_id !== 'string'
    || payload.material_record_id.length !== 32
    || !text(payload.operation_id)
    || typeof digest !== 'string'
    || !/^[0-9a-f]{64}$/.test(digest)
    || job.progress < 0
    || job.progress > 1
  ) throw new Error('Invalid semester mapping Job response')
  if (
    job.status === 'succeeded'
    && (
      job.result.semester_id !== payload.semester_id
      || job.result.operation_id !== payload.operation_id
      || job.result.source_state_sha256 !== digest
      || typeof job.result.proposal_id !== 'string'
      || job.result.proposal_id.length !== 32
      || typeof job.result.recovered_existing !== 'boolean'
    )
  ) throw new Error('Invalid semester mapping Job result')
  return job
}

const SEMESTER_MAPPING_PROPOSAL_KEYS = new Set([
  'id',
  'semester_id',
  'operation_id',
  'source_state_sha256',
  'status',
  'payload',
  'revision',
  'created_at',
  'updated_at',
  'applied_at',
])
const SEMESTER_MAPPING_PROPOSAL_PAYLOAD_REQUIRED_KEYS = new Set([
  'tree',
  'mappings',
  'uncertainties',
  'source_material_record_ids',
])
const SEMESTER_MAPPING_PROPOSAL_PAYLOAD_KEYS = new Set([
  ...SEMESTER_MAPPING_PROPOSAL_PAYLOAD_REQUIRED_KEYS,
  'generation_source',
  'summary',
  'directory_evidence',
])
const SEMESTER_MAPPING_PROPOSAL_CHAPTER_KEYS = new Set([
  'key', 'title', 'sections',
])
const SEMESTER_MAPPING_PROPOSAL_SECTION_KEYS = new Set([
  'key', 'title', 'lessons',
])
const SEMESTER_MAPPING_PROPOSAL_LESSON_KEYS = new Set([
  'key', 'title', 'duration_minutes',
])
const SEMESTER_MAPPING_PROPOSAL_RANGE_REQUIRED_KEYS = new Set([
  'mapping_id',
  'material_record_id',
  'lesson_ref',
  'start_unit',
  'end_unit',
  'purpose',
  'decision',
  'teacher_revision',
  'decision_reason',
])
const SEMESTER_MAPPING_PROPOSAL_RANGE_KEYS = new Set([
  ...SEMESTER_MAPPING_PROPOSAL_RANGE_REQUIRED_KEYS,
  'basis',
  'evidence_refs',
  'confidence',
  'evidence',
])
const SEMESTER_MAPPING_TEACHER_REVISION_KEYS = new Set([
  'lesson_ref', 'start_unit', 'end_unit',
])
const SEMESTER_MAPPING_PROPOSAL_SUMMARY_KEYS = new Set([
  'ppt_count',
  'lesson_candidate_count',
  'special_count',
  'high_confidence_count',
  'needs_review_count',
  'chapter_count',
])
const SEMESTER_MAPPING_DIRECTORY_EVIDENCE_REQUIRED_KEYS = new Set([
  'strategy',
  'toc_entries',
  'resolved_ranges',
  'anchors',
  'confidence',
  'issues',
  'full_page_text_sent',
])
const SEMESTER_MAPPING_DIRECTORY_EVIDENCE_KEYS = new Set([
  ...SEMESTER_MAPPING_DIRECTORY_EVIDENCE_REQUIRED_KEYS,
  'total_unit_count',
  'directory_page_unit_indices',
  'scanned_unit_indices',
  'printed_to_pdf_offset',
])
const SEMESTER_MAPPING_DIRECTORY_TOC_REQUIRED_KEYS = new Set([
  'evidence_id', 'level', 'title', 'printed_page', 'source_unit',
])
const SEMESTER_MAPPING_DIRECTORY_TOC_KEYS = new Set([
  ...SEMESTER_MAPPING_DIRECTORY_TOC_REQUIRED_KEYS,
  'printed_page_track',
  'page_refs',
  'confidence',
  'source',
])
const SEMESTER_MAPPING_DIRECTORY_RESOLVED_RANGE_KEYS = new Set([
  'evidence_id',
  'toc_evidence_id',
  'title',
  'level',
  'printed_page',
  'start_unit',
  'end_unit',
])
const SEMESTER_MAPPING_DIRECTORY_ANCHOR_KEYS = new Set([
  'evidence_id', 'unit_index', 'title', 'text_excerpt',
])
const SEMESTER_MAPPING_DIRECTORY_PAGE_TRACKS = new Set([
  '听', '作', '活', '评', '页',
])

function hasRequiredAllowedKeys(
  value: Record<string, unknown>,
  required: Set<string>,
  allowed: Set<string>,
): boolean {
  return [...required].every(key => Object.hasOwn(value, key))
    && Object.keys(value).every(key => allowed.has(key))
}

function validSemesterProposalSummary(value: unknown): boolean {
  return isRecord(value)
    && hasExactKeys(value, SEMESTER_MAPPING_PROPOSAL_SUMMARY_KEYS)
    && [...SEMESTER_MAPPING_PROPOSAL_SUMMARY_KEYS].every(key => integer(value[key]))
}

function validDirectoryEvidenceTocEntry(value: unknown): boolean {
  if (
    !isRecord(value)
    || !hasRequiredAllowedKeys(
      value,
      SEMESTER_MAPPING_DIRECTORY_TOC_REQUIRED_KEYS,
      SEMESTER_MAPPING_DIRECTORY_TOC_KEYS,
    )
    || !text(value.evidence_id)
    || !['chapter', 'section', 'lesson'].includes(String(value.level))
    || !text(value.title)
    || !(value.printed_page === null || integer(value.printed_page, 1))
    || !integer(value.source_unit, 1)
  ) return false

  const layoutKeys = ['printed_page_track', 'page_refs', 'confidence', 'source']
  const layoutKeyCount = layoutKeys.filter(key => Object.hasOwn(value, key)).length
  if (layoutKeyCount === 0) return value.printed_page !== null
  if (layoutKeyCount !== layoutKeys.length || !isRecord(value.page_refs)) return false
  if (
    !(value.printed_page_track === null
      || SEMESTER_MAPPING_DIRECTORY_PAGE_TRACKS.has(String(value.printed_page_track)))
    || Object.keys(value.page_refs).some(
      key => !SEMESTER_MAPPING_DIRECTORY_PAGE_TRACKS.has(key),
    )
    || !Object.values(value.page_refs).every(page => integer(page, 1))
    || typeof value.confidence !== 'number'
    || !Number.isFinite(value.confidence)
    || value.confidence < 0
    || value.confidence > 1
    || value.source !== 'ocr_layout'
  ) return false
  return value.printed_page_track === null
    || Object.hasOwn(value.page_refs, String(value.printed_page_track))
}

function validDirectoryEvidenceResolvedRange(value: unknown): boolean {
  return isRecord(value)
    && hasExactKeys(value, SEMESTER_MAPPING_DIRECTORY_RESOLVED_RANGE_KEYS)
    && text(value.evidence_id)
    && text(value.toc_evidence_id)
    && text(value.title)
    && ['chapter', 'section', 'lesson'].includes(String(value.level))
    && integer(value.printed_page, 1)
    && integer(value.start_unit, 1)
    && integer(value.end_unit, 1)
    && value.end_unit >= value.start_unit
}

function validDirectoryEvidenceAnchor(value: unknown): boolean {
  return isRecord(value)
    && hasExactKeys(value, SEMESTER_MAPPING_DIRECTORY_ANCHOR_KEYS)
    && text(value.evidence_id)
    && integer(value.unit_index, 1)
    && nullableText(value.title)
    && typeof value.text_excerpt === 'string'
    && Boolean(value.title || value.text_excerpt.trim())
}

function validDirectoryEvidence(value: unknown): boolean {
  if (
    !isRecord(value)
    || !hasRequiredAllowedKeys(
      value,
      SEMESTER_MAPPING_DIRECTORY_EVIDENCE_REQUIRED_KEYS,
      SEMESTER_MAPPING_DIRECTORY_EVIDENCE_KEYS,
    )
    || !['toc_calibrated', 'toc_unverified', 'sparse_outline'].includes(
      String(value.strategy),
    )
    || !Array.isArray(value.toc_entries)
    || !value.toc_entries.every(validDirectoryEvidenceTocEntry)
    || !Array.isArray(value.resolved_ranges)
    || !value.resolved_ranges.every(validDirectoryEvidenceResolvedRange)
    || !Array.isArray(value.anchors)
    || !value.anchors.every(validDirectoryEvidenceAnchor)
    || !['high', 'medium', 'low'].includes(String(value.confidence))
    || !Array.isArray(value.issues)
    || !value.issues.every(text)
    || typeof value.full_page_text_sent !== 'boolean'
  ) return false
  if (value.total_unit_count !== undefined && !integer(value.total_unit_count)) return false
  if (
    value.directory_page_unit_indices !== undefined
    && (!Array.isArray(value.directory_page_unit_indices)
      || !value.directory_page_unit_indices.every(item => integer(item, 1)))
  ) return false
  if (
    value.scanned_unit_indices !== undefined
    && (!Array.isArray(value.scanned_unit_indices)
      || !value.scanned_unit_indices.every(item => integer(item, 1)))
  ) return false
  return value.printed_to_pdf_offset === undefined
    || value.printed_to_pdf_offset === null
    || Number.isSafeInteger(value.printed_to_pdf_offset)
}

export function decodeSemesterMappingProposal(value: unknown): SemesterMappingProposal {
  if (
    !isRecord(value)
    || !hasExactKeys(value, SEMESTER_MAPPING_PROPOSAL_KEYS)
    || !text(value.id)
    || !text(value.semester_id)
    || !text(value.operation_id)
    || !text(value.source_state_sha256)
    || !['proposed', 'applied', 'rejected'].includes(String(value.status))
    || !isRecord(value.payload)
    || !hasRequiredAllowedKeys(
      value.payload,
      SEMESTER_MAPPING_PROPOSAL_PAYLOAD_REQUIRED_KEYS,
      SEMESTER_MAPPING_PROPOSAL_PAYLOAD_KEYS,
    )
    || !Array.isArray(value.payload.tree)
    || !Array.isArray(value.payload.mappings)
    || !Array.isArray(value.payload.uncertainties)
    || !Array.isArray(value.payload.source_material_record_ids)
    || !value.payload.tree.every(validSemesterProposalChapter)
    || !value.payload.mappings.every(validSemesterProposalRange)
    || !value.payload.uncertainties.every(text)
    || !value.payload.source_material_record_ids.every(text)
    || (value.payload.generation_source !== undefined
      && value.payload.generation_source !== 'local_reference_ppt_names')
    || (value.payload.summary !== undefined
      && !validSemesterProposalSummary(value.payload.summary))
    || (value.payload.directory_evidence !== undefined
      && !validDirectoryEvidence(value.payload.directory_evidence))
    || !integer(value.revision, 1)
    || !text(value.created_at)
    || !text(value.updated_at)
    || !nullableText(value.applied_at)
  ) throw new Error('Invalid semester mapping proposal response')
  return value as unknown as SemesterMappingProposal
}

function referencePptCollection(value: unknown): ReferencePptCollection {
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.semester_id)
    || !text(value.display_name)
    || !text(value.mapping_proposal_id)
    || !integer(value.ignored_file_count, 0)
    || !integer(value.revision, 1)
    || !text(value.created_at)
    || !text(value.updated_at)
    || !Array.isArray(value.members)
    || !value.members.every(referencePptCollectionMember)
  ) throw new Error('Invalid reference PPT collection response')
  return value as unknown as ReferencePptCollection
}

function referencePptCollectionMember(value: unknown): boolean {
  const nullablePositiveInteger = (item: unknown): boolean => (
    item === null || integer(item, 1)
  )
  return (
    isRecord(value)
    && text(value.id)
    && text(value.collection_id)
    && text(value.material_record_id)
    && text(value.relative_path)
    && ['lesson', 'review', 'strategy', 'practice'].includes(String(value.kind))
    && ['high', 'medium', 'low'].includes(String(value.confidence))
    && nullablePositiveInteger(value.chapter_number)
    && nullablePositiveInteger(value.section_number)
    && nullablePositiveInteger(value.subsection_number)
    && nullablePositiveInteger(value.lesson_number)
    && text(value.normalized_title)
    && Array.isArray(value.evidence)
    && value.evidence.every(text)
    && Array.isArray(value.issues)
    && value.issues.every(text)
    && text(value.created_at)
  )
}

function validSemesterProposalChapter(value: unknown): boolean {
  return (
    isRecord(value)
    && hasExactKeys(value, SEMESTER_MAPPING_PROPOSAL_CHAPTER_KEYS)
    && text(value.key)
    && text(value.title)
    && Array.isArray(value.sections)
    && value.sections.every((section) => (
      isRecord(section)
      && hasExactKeys(section, SEMESTER_MAPPING_PROPOSAL_SECTION_KEYS)
      && text(section.key)
      && text(section.title)
      && Array.isArray(section.lessons)
      && section.lessons.every((lesson) => (
        isRecord(lesson)
        && hasExactKeys(lesson, SEMESTER_MAPPING_PROPOSAL_LESSON_KEYS)
        && text(lesson.key)
        && text(lesson.title)
        && integer(lesson.duration_minutes, 1)
      ))
    ))
  )
}

function validSemesterProposalRange(value: unknown): boolean {
  return (
    isRecord(value)
    && hasRequiredAllowedKeys(
      value,
      SEMESTER_MAPPING_PROPOSAL_RANGE_REQUIRED_KEYS,
      SEMESTER_MAPPING_PROPOSAL_RANGE_KEYS,
    )
    && text(value.mapping_id)
    && text(value.material_record_id)
    && text(value.lesson_ref)
    && integer(value.start_unit, 1)
    && integer(value.end_unit, 1)
    && value.end_unit >= value.start_unit
    && [
      'textbook',
      'reference_ppt',
      'exercise',
      'answer',
      'supplement',
    ].includes(String(value.purpose))
    && ['pending', 'accepted', 'modified', 'rejected'].includes(
      String(value.decision),
    )
    && (
      value.teacher_revision === null
      || (
        isRecord(value.teacher_revision)
        && hasExactKeys(value.teacher_revision, SEMESTER_MAPPING_TEACHER_REVISION_KEYS)
        && text(value.teacher_revision.lesson_ref)
        && integer(value.teacher_revision.start_unit, 1)
        && integer(value.teacher_revision.end_unit, 1)
        && value.teacher_revision.end_unit >= value.teacher_revision.start_unit
      )
    )
    && nullableText(value.decision_reason)
    && (value.basis === undefined || text(value.basis))
    && (value.evidence_refs === undefined || (
      Array.isArray(value.evidence_refs) && value.evidence_refs.every(text)
    ))
    && (value.confidence === undefined
      || ['high', 'medium', 'low'].includes(String(value.confidence)))
    && (value.evidence === undefined || (
      Array.isArray(value.evidence) && value.evidence.every(text)
    ))
  )
}

function lessonNode(value: unknown): LessonNode {
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.curriculum_id)
    || !nullableText(value.parent_id)
    || !['chapter', 'section', 'lesson'].includes(String(value.node_type))
    || !text(value.title)
    || !integer(value.sort_order, 1)
    || !(value.duration_minutes === null || integer(value.duration_minutes, 1))
    || !['teacher', 'catalog', 'assistant_draft'].includes(
      String(value.source_kind),
    )
    || typeof value.is_active !== 'boolean'
    || !integer(value.revision, 1)
    || !text(value.created_at)
    || !text(value.updated_at)
  ) throw new Error('Invalid lesson node response')
  return value as unknown as LessonNode
}

function material(value: unknown): MaterialVersion {
  if (isRecord(value)) {
    value = {
      parse_expected_unit_count: null,
      preview_completed_count: 0,
      ocr_completed_count: 0,
      ocr_total_count: 0,
      source_revision: 1,
      source_archived_at: null,
      ...value,
    }
  }
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.source_id)
    || !text(value.display_name)
    || !['pdf', 'pptx', 'image'].includes(String(value.material_type))
    || !text(value.content_sha256)
    || !text(value.safe_filename)
    || !integer(value.size_bytes)
    || !(
      value.modified_ns === null
      || (
        typeof value.modified_ns === 'string'
        && /^[0-9]+$/.test(value.modified_ns)
      )
    )
    || !(value.unit_count === null || integer(value.unit_count))
    || !(value.parse_expected_unit_count === null || integer(value.parse_expected_unit_count))
    || !integer(value.preview_completed_count)
    || !integer(value.ocr_completed_count)
    || !integer(value.ocr_total_count)
    || !text(value.inspection_status)
    || !['available', 'missing', 'needs_relocation'].includes(
      String(value.availability),
    )
    || !integer(value.source_revision, 1)
    || !nullableText(value.source_archived_at)
    || !text(value.created_at)
  ) throw new Error('Invalid material response')
  return value as unknown as MaterialVersion
}

function materialUnit(value: unknown): MaterialUnit {
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.material_version_id)
    || !['pdf_page', 'ppt_slide', 'image'].includes(String(value.unit_kind))
    || !integer(value.unit_index, 1)
    || !nullableText(value.title)
    || typeof value.text_excerpt !== 'string'
    || !['embedded', 'empty', 'manual', 'not_applicable'].includes(
      String(value.text_status),
    )
    || typeof value.formula_review_required !== 'boolean'
    || !isRecord(value.object_summary)
    || !text(value.preview_url)
    || !integer(value.revision, 1)
    || !text(value.created_at)
    || !text(value.updated_at)
  ) throw new Error('Invalid material unit response')
  return value as unknown as MaterialUnit
}

function materialLink(value: unknown): MaterialLink {
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.lesson_node_id)
    || !text(value.material_version_id)
    || !text(value.material_name)
    || !['pdf', 'pptx', 'image'].includes(String(value.material_type))
    || !integer(value.start_unit, 1)
    || !integer(value.end_unit, 1)
    || !(value.crop === null || isRecord(value.crop))
    || !['textbook', 'reference_ppt', 'exercise', 'answer', 'supplement'].includes(
      String(value.purpose),
    )
    || !nullableText(value.teacher_note)
    || !['proposed', 'confirmed'].includes(String(value.confirmation_status))
    || !text(value.source_version_sha256)
    || !integer(value.sort_order, 1)
    || typeof value.is_active !== 'boolean'
    || !integer(value.revision, 1)
    || !text(value.created_at)
    || !text(value.updated_at)
  ) throw new Error('Invalid material link response')
  return value as unknown as MaterialLink
}

function normalizedCrop(value: unknown): value is NormalizedCrop {
  if (!isRecord(value)) return false
  const coordinates = ['x0', 'y0', 'x1', 'y1'] as const
  if (!coordinates.every(
    (key) => typeof value[key] === 'number'
      && Number(value[key]) >= 0
      && Number(value[key]) <= 1,
  )) return false
  return Number(value.x1) > Number(value.x0)
    && Number(value.y1) > Number(value.y0)
}

function exerciseRegion(value: unknown): ExerciseRegion {
  if (
    !isRecord(value)
    || !text(value.id)
    || !['question', 'answer'].includes(String(value.region_role))
    || !text(value.material_unit_id)
    || !text(value.material_version_id)
    || !text(value.material_name)
    || !integer(value.unit_index, 1)
    || !normalizedCrop(value.crop)
    || !text(value.source_version_sha256)
    || !integer(value.sequence, 1)
    || !text(value.preview_url)
  ) throw new Error('Invalid exercise region response')
  return value as unknown as ExerciseRegion
}

function exerciseCandidate(value: unknown): ExerciseCandidate {
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.lesson_node_id)
    || !nullableText(value.question_number)
    || !nullableText(value.content_label)
    || !['unrated', 'easy', 'medium', 'hard'].includes(String(value.difficulty))
    || ![
      'introduction',
      'example',
      'guided_practice',
      'independent_practice',
      'diagnostic',
      'challenge',
      'summary',
    ].includes(String(value.classroom_use))
    || !(value.estimated_minutes === null || integer(value.estimated_minutes, 1))
    || !nullableText(value.teaching_focus)
    || !nullableText(value.teacher_note)
    || !['classroom_candidate', 'backup', 'excluded'].includes(
      String(value.selection_status),
    )
    || !['candidate', 'teacher_verified', 'rejected', 'missing'].includes(
      String(value.answer_status),
    )
    || !Array.isArray(value.question_regions)
    || !Array.isArray(value.answer_regions)
    || !Array.isArray(value.duplicate_suggestions)
    || typeof value.is_active !== 'boolean'
    || !integer(value.revision, 1)
    || !text(value.created_at)
    || !text(value.updated_at)
  ) throw new Error('Invalid exercise candidate response')
  value.question_regions.forEach(exerciseRegion)
  value.answer_regions.forEach(exerciseRegion)
  for (const suggestion of value.duplicate_suggestions) {
    if (
      !isRecord(suggestion)
      || !text(suggestion.candidate_id)
      || !nullableText(suggestion.question_number)
      || !nullableText(suggestion.content_label)
      || !Array.isArray(suggestion.basis)
      || !suggestion.basis.every(
        (basis) => [
          'same_teacher_clue',
          'same_question_number_and_source',
        ].includes(String(basis)),
      )
    ) throw new Error('Invalid duplicate exercise suggestion')
  }
  return value as unknown as ExerciseCandidate
}

function assessmentChoice(value: unknown): AssessmentChoice {
  if (
    !isRecord(value)
    || !integer(value.assessment_id, 1)
    || !text(value.title)
    || !text(value.status)
    || !text(value.created_at)
    || !text(value.updated_at)
    || !Array.isArray(value.classes)
  ) throw new Error('Invalid assessment choice response')
  for (const item of value.classes) {
    if (
      !isRecord(item)
      || !text(item.class_name)
      || !integer(item.student_count)
    ) throw new Error('Invalid assessment class response')
  }
  return value as unknown as AssessmentChoice
}

function questionEvidenceChoice(value: unknown): QuestionEvidenceChoice {
  if (
    !isRecord(value)
    || !integer(value.question_id, 1)
    || !text(value.question_number)
    || !nullableText(value.question_type)
    || typeof value.text_excerpt !== 'string'
    || !nullableText(value.difficulty)
    || !Array.isArray(value.knowledge_points)
    || !value.knowledge_points.every((item) => typeof item === 'string')
    || !text(value.updated_at)
  ) throw new Error('Invalid question evidence choice response')
  return value as unknown as QuestionEvidenceChoice
}

function teachingPreferencesPayload(value: unknown): TeachingPreferencesPayload {
  if (
    !isRecord(value)
    || value.schema_version !== 1
    || typeof value.label_textbook_pages !== 'boolean'
    || value.page_label_font_size !== 28
    || typeof value.trim_excess_practice !== 'boolean'
    || !['light', 'moderate', 'strong'].includes(String(value.practice_trim_level))
    || typeof value.preserve_teaching_examples !== 'boolean'
    || typeof value.prefer_short_practice !== 'boolean'
    || typeof value.supplement_from_references !== 'boolean'
    || !integer(value.supplement_question_limit, 0)
    || value.supplement_question_limit > 3
    || typeof value.supplement_as_source_image !== 'boolean'
    || typeof value.prioritize_homework_workbook !== 'boolean'
    || typeof value.avoid_direct_homework_copy !== 'boolean'
    || typeof value.avoid_ppt_duplicates !== 'boolean'
  ) throw new Error('Invalid teaching preferences payload')
  return value as unknown as TeachingPreferencesPayload
}

function teachingPreferences(value: unknown): TeachingPreferences {
  if (
    !isRecord(value)
    || !integer(value.revision, 1)
    || !text(value.updated_at)
  ) throw new Error('Invalid teaching preferences response')
  return {
    revision: value.revision,
    payload: teachingPreferencesPayload(value.payload),
    updated_at: value.updated_at,
  }
}

function resourcePack(value: unknown): ResourcePack {
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.lesson_node_id)
    || !integer(value.version_number, 1)
    || !text(value.source_state_sha256)
    || !text(value.pack_sha256)
    || !isRecord(value.payload)
    || !text(value.created_at)
  ) throw new Error('Invalid resource pack response')
  return value as unknown as ResourcePack
}

function resourcePackStatus(value: unknown): ResourcePackStatus {
  if (
    !isRecord(value)
    || !text(value.lesson_node_id)
    || typeof value.has_pack !== 'boolean'
    || !(value.latest_version_number === null
      || integer(value.latest_version_number, 1))
    || !nullableText(value.latest_pack_id)
    || typeof value.local_sources_changed !== 'boolean'
  ) throw new Error('Invalid resource pack status response')
  return value as unknown as ResourcePackStatus
}

function stringArray(value: unknown): value is string[] {
  return Array.isArray(value)
    && value.every((item) => typeof item === 'string')
}

function lessonDraft(value: unknown): LessonDraft {
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.resource_pack_id)
    || !integer(value.version_number, 1)
    || !nullableText(value.based_on_draft_id)
    || !nullableText(value.operation_id)
    || !['local_template', 'model', 'teacher'].includes(String(value.source_kind))
    || !nullableText(value.model_label)
    || !['draft', 'confirmed'].includes(String(value.status))
    || !isRecord(value.payload)
    || !Array.isArray(value.payload.knowledge_objectives)
    || !Array.isArray(value.payload.focus_points)
    || !Array.isArray(value.payload.anticipated_difficulties)
    || !Array.isArray(value.payload.lesson_flow)
    || !Array.isArray(value.payload.exercise_recommendations)
    || !(value.payload.slide_adaptations === undefined
      || Array.isArray(value.payload.slide_adaptations))
    || !stringArray(value.payload.uncertainties)
    || !isRecord(value.capacity)
    || !integer(value.capacity.lesson_minutes, 1)
    || !integer(value.capacity.planned_minutes, 0)
    || !integer(value.capacity.overrun_minutes, 0)
    || typeof value.capacity.within_capacity !== 'boolean'
    || !text(value.created_at)
  ) throw new Error('Invalid lesson draft response')
  if (value.payload.slide_adaptations === undefined) {
    value.payload.slide_adaptations = []
  }
  return value as unknown as LessonDraft
}

function lessonDraftPreflight(value: unknown): LessonDraftPreflight {
  const legacyLocalTemplate = isRecord(value)
    && value.mode === 'local_template'
    && value.will_call_model === false
    && value.model_destination_fingerprint === undefined
  if (
    !isRecord(value)
    || !text(value.resource_pack_id)
    || !integer(value.resource_pack_version, 1)
    || !text(value.resource_pack_sha256)
    || !['local_template', 'model'].includes(String(value.mode))
    || typeof value.will_call_model !== 'boolean'
    || typeof value.model_available !== 'boolean'
    || !nullableText(value.model_label)
    || (!text(value.model_destination_fingerprint) && !legacyLocalTemplate)
    || !isRecord(value.data_scope)
    || !Array.isArray(value.references)
    || !integer(value.missing_and_uncertain_count, 0)
    || !isRecord(value.preparation_preferences)
  ) throw new Error('Invalid lesson draft preflight response')
  value.preparation_preferences = teachingPreferencesPayload(
    value.preparation_preferences,
  )
  return value as unknown as LessonDraftPreflight
}

function slideOperation(value: unknown): SlideOperation {
  if (
    !isRecord(value)
    || !text(value.operation_id)
    || !text(value.kind)
    || !['proposed', 'approved', 'rejected'].includes(String(value.decision))
    || !isRecord(value.target)
    || !text(value.reason)
    || !stringArray(value.citations)
    || !integer(value.planned_minutes, 0)
    || !['low', 'medium', 'high', 'blocked'].includes(String(value.risk))
    || !['automatic', 'noop', 'manual_only'].includes(String(value.execution_mode))
    || !nullableText(value.support_note)
    || !isRecord(value.details)
    || !nullableText(value.teacher_note)
  ) throw new Error('Invalid slide operation response')
  return value as unknown as SlideOperation
}

function slidePlan(value: unknown): SlidePlan {
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.lesson_draft_id)
    || !text(value.resource_pack_id)
    || !integer(value.version_number, 1)
    || !nullableText(value.based_on_plan_id)
    || !text(value.source_ppt_state_sha256)
    || !['in_review', 'approved', 'invalidated'].includes(String(value.status))
    || !isRecord(value.payload)
    || value.payload.schema_version !== 1
    || !Array.isArray(value.payload.source_presentations)
    || !Array.isArray(value.payload.slides)
    || !Array.isArray(value.payload.operations)
    || !Array.isArray(value.payload.unsupported_objects)
    || !Array.isArray(value.payload.approval_history)
    || !text(value.created_at)
  ) throw new Error('Invalid slide plan response')
  value.payload.operations.forEach(slideOperation)
  return value as unknown as SlidePlan
}

function slidePlanPreview(value: unknown): SlidePlanPreview {
  if (
    !isRecord(value)
    || typeof value.valid_for_execution !== 'boolean'
    || typeof value.source_changed !== 'boolean'
    || typeof value.includes_proposed_operations !== 'boolean'
    || !integer(value.before_slide_count, 0)
    || !integer(value.after_slide_count, 0)
    || !Array.isArray(value.before)
    || !Array.isArray(value.after)
    || !Array.isArray(value.changes)
    || !Array.isArray(value.manual_only)
  ) throw new Error('Invalid slide plan preview response')
  return value as unknown as SlidePlanPreview
}

function moduleStatus(value: unknown): TeachingPrepModuleStatus {
  if (
    !isRecord(value)
    || value.module !== 'teaching-prep'
    || typeof value.enabled !== 'boolean'
    || !text(value.schema_version)
    || typeof value.real_model_enabled !== 'boolean'
    || typeof value.semester_mapping_model_available !== 'boolean'
    || typeof value.exercise_suggestion_model_available !== 'boolean'
    || typeof value.real_wps_enabled !== 'boolean'
    || typeof value.wps_execution_available !== 'boolean'
  ) throw new Error('Invalid teaching prep status response')
  return value as unknown as TeachingPrepModuleStatus
}

function pptxExecution(value: unknown): PptxExecution {
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.operation_id)
    || !text(value.slide_plan_id)
    || !text(value.source_material_version_id)
    || !text(value.source_sha256)
    || !integer(value.expected_slide_count, 1)
    || ![
      'running',
      'verifying',
      'publishing',
      'published',
      'failed',
      'cancelled',
      'interrupted',
    ].includes(String(value.status))
    || !(value.execution_report === null || isRecord(value.execution_report))
    || !(value.verification_report === null || isRecord(value.verification_report))
    || !nullableText(value.error_code)
    || !nullableText(value.published_version_id)
    || !['copying', 'executing', 'verifying', 'publishing', 'done'].includes(
      String(value.phase),
    )
    || typeof value.cancel_requested !== 'boolean'
    || typeof value.staging_retained !== 'boolean'
    || !stringArray(value.recovery_actions)
    || !text(value.created_at)
    || !text(value.updated_at)
    || !nullableText(value.finished_at)
  ) throw new Error('Invalid PPTX execution response')
  return value as unknown as PptxExecution
}

function lessonGenerationPerformance(
  value: unknown,
): LessonGenerationPerformance {
  if (
    !isRecord(value)
    || !text(value.execution_run_id)
    || !text(value.slide_plan_id)
    || ![
      'running',
      'verifying',
      'publishing',
      'published',
      'failed',
      'cancelled',
      'interrupted',
    ].includes(String(value.status))
    || !integer(value.budget_ms, 1)
    || !integer(value.total_machine_elapsed_ms)
    || !integer(value.draft_elapsed_ms)
    || !integer(value.wps_elapsed_ms)
    || !integer(value.model_call_count)
    || !integer(value.wps_execution_count)
    || !integer(value.technical_retry_count)
    || !['running', 'within', 'exceeded'].includes(
      String(value.budget_status),
    )
    || !(value.within_budget === null || typeof value.within_budget === 'boolean')
    || typeof value.human_review_wait_excluded !== 'boolean'
  ) throw new Error('Invalid lesson generation performance response')
  return value as unknown as LessonGenerationPerformance
}

function pptxVersion(value: unknown): PptxVersion {
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.slide_plan_id)
    || !text(value.lesson_node_id)
    || !text(value.execution_run_id)
    || !integer(value.version_number, 1)
    || value.status !== 'published'
    || !text(value.output_filename)
    || !text(value.output_sha256)
    || !integer(value.slide_count, 1)
    || !isRecord(value.verification_report)
    || !text(value.download_url)
    || !text(value.created_at)
    || !text(value.published_at)
  ) throw new Error('Invalid PPTX version response')
  return value as unknown as PptxVersion
}

function pptxExecutionResult(value: unknown): PptxExecutionResult {
  if (
    !isRecord(value)
    || !isRecord(value.execution)
    || !(value.version === null || isRecord(value.version))
  ) throw new Error('Invalid PPTX execution result response')
  return {
    execution: pptxExecution(value.execution),
    version: value.version === null ? null : pptxVersion(value.version),
  }
}

function classVariant(value: unknown): ClassVariant {
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.base_resource_pack_id)
    || !text(value.resource_pack_id)
    || !text(value.lesson_node_id)
    || !text(value.class_name)
    || !stringArray(value.prior_review_ids)
    || !text(value.created_at)
  ) throw new Error('Invalid class variant response')
  return value as unknown as ClassVariant
}

function upClassPackage(value: unknown): UpClassPackage {
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.pptx_version_id)
    || !text(value.slide_plan_id)
    || !text(value.lesson_draft_id)
    || !text(value.resource_pack_id)
    || !text(value.lesson_node_id)
    || !nullableText(value.class_name)
    || !integer(value.version_number, 1)
    || !['building', 'publishing', 'complete', 'failed', 'interrupted']
      .includes(String(value.status))
    || !nullableText(value.output_filename)
    || !nullableText(value.package_sha256)
    || !(value.manifest === null || isRecord(value.manifest))
    || !nullableText(value.error_code)
    || typeof value.is_current !== 'boolean'
    || typeof value.staging_retained !== 'boolean'
    || !stringArray(value.recovery_actions)
    || !text(value.created_at)
    || !text(value.updated_at)
    || !nullableText(value.completed_at)
    || !nullableText(value.download_url)
  ) throw new Error('Invalid up-class package response')
  return value as unknown as UpClassPackage
}

function postLessonReview(value: unknown): PostLessonReview {
  if (
    !isRecord(value)
    || !text(value.id)
    || !text(value.package_id)
    || !text(value.pptx_version_id)
    || !text(value.lesson_node_id)
    || !nullableText(value.class_name)
    || !isRecord(value.payload)
    || !stringArray(value.payload.reteach_points)
    || typeof value.use_in_next_version !== 'boolean'
    || !text(value.created_at)
  ) throw new Error('Invalid post-lesson review response')
  return value as unknown as PostLessonReview
}

function itemList<T>(
  payload: unknown,
  decodeItem: (value: unknown) => T,
): T[] {
  assertNoPathLikeKeys(payload)
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new Error('Invalid list response')
  }
  return payload.items.map(decodeItem)
}

export const teachingPrepCatalogApi = {
  status(signal?: AbortSignal): Promise<TeachingPrepModuleStatus> {
    return apiClient.request('/api/teaching-prep/status', {
      signal,
      decode: (payload) => {
        assertNoPathLikeKeys(payload)
        return moduleStatus(payload)
      },
    })
  },

  getTeachingPreferences(
    signal?: AbortSignal,
  ): Promise<TeachingPreferences> {
    return apiClient.request('/api/teaching-prep/preferences', {
      signal,
      decode: (payload) => {
        assertNoPathLikeKeys(payload)
        return teachingPreferences(payload)
      },
    })
  },

  updateTeachingPreferences(
    current: TeachingPreferences,
    payload: TeachingPreferencesPayload,
  ): Promise<TeachingPreferences> {
    return apiClient.request('/api/teaching-prep/preferences', {
      method: 'PATCH',
      body: {
        expected_revision: current.revision,
        payload,
      },
      decode: (value) => {
        assertNoPathLikeKeys(value)
        return teachingPreferences(value)
      },
    })
  },

  listCurricula(signal?: AbortSignal): Promise<CurriculumEdition[]> {
    return apiClient.request('/api/teaching-prep/curricula', {
      signal,
      decode: (payload) => itemList(payload, curriculum),
    })
  },

  createCurriculum(
    input: CreateCurriculumInput,
    signal?: AbortSignal,
  ): Promise<CurriculumEdition> {
    return apiClient.request('/api/teaching-prep/curricula', {
      method: 'POST',
      body: input,
      signal,
      decode: (payload) => {
        assertNoPathLikeKeys(payload)
        return curriculum(payload)
      },
    })
  },

  createSemesterWorkspace(
    input: CreateSemesterWorkspaceInput,
    signal?: AbortSignal,
  ): Promise<SemesterWorkspace> {
    return apiClient.request('/api/teaching-prep/semester-workspaces', {
      method: 'POST',
      body: input,
      signal,
      decode: (payload) => {
        assertNoPathLikeKeys(payload)
        return semesterWorkspace(payload)
      },
    })
  },

  listSemesters(signal?: AbortSignal): Promise<TeachingSemester[]> {
    return apiClient.request('/api/teaching-prep/semesters', {
      signal,
      decode: (payload) => itemList(payload, semester),
    })
  },

  createSemester(
    input: CreateSemesterInput,
    signal?: AbortSignal,
  ): Promise<TeachingSemester> {
    return apiClient.request('/api/teaching-prep/semesters', {
      method: 'POST',
      body: input,
      signal,
      decode: (payload) => {
        assertNoPathLikeKeys(payload)
        return semester(payload)
      },
    })
  },

  updateSemester(
    current: TeachingSemester,
    input: {
      planned_new_lesson_count: number
      status: SemesterStatus
    },
  ): Promise<TeachingSemester> {
    return apiClient.request(
      `/api/teaching-prep/semesters/${encodeURIComponent(current.id)}`,
      {
        method: 'PATCH',
        body: {
          expected_revision: current.revision,
          ...input,
        },
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return semester(payload)
        },
      },
    )
  },

  listSemesterLessonProgress(
    semesterId: string,
    signal?: AbortSignal,
  ): Promise<SemesterLessonProgress[]> {
    return apiClient.request(
      `/api/teaching-prep/semesters/${encodeURIComponent(semesterId)}/lesson-progress`,
      {
        signal,
        decode: (payload) => itemList(payload, semesterLessonProgress),
      },
    )
  },

  setSemesterLessonProgress(
    semesterId: string,
    lessonNodeId: string,
    input: {
      status: SemesterLessonProgressStatus
      expected_revision: number | null
    },
  ): Promise<SemesterLessonProgress> {
    return apiClient.request(
      (
        `/api/teaching-prep/semesters/${encodeURIComponent(semesterId)}`
        + `/lesson-progress/${encodeURIComponent(lessonNodeId)}`
      ),
      {
        method: 'PUT',
        body: input,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return semesterLessonProgress(payload)
        },
      },
    )
  },

  listSemesterMaterials(
    semesterId: string,
    signal?: AbortSignal,
  ): Promise<SemesterMaterialRecord[]> {
    return apiClient.request(
      `/api/teaching-prep/semesters/${encodeURIComponent(semesterId)}/materials`,
      {
        signal,
        decode: (payload) => itemList(payload, semesterMaterial),
      },
    )
  },

  listReferencePptCollections(
    semesterId: string,
    signal?: AbortSignal,
  ): Promise<ReferencePptCollection[]> {
    return apiClient.request(
      `/api/teaching-prep/semesters/${encodeURIComponent(semesterId)}/reference-ppt-collections`,
      {
        signal,
        decode: (payload) => {
          if (!isRecord(payload) || !Array.isArray(payload.items)) {
            throw new Error('Invalid reference PPT collection list')
          }
          return payload.items.map(referencePptCollection)
        },
      },
    )
  },

  createReferencePptCollection(
    semesterId: string,
    input: {
      request_token: string
      display_name: string
      ignored_file_count: number
      members: Array<{
        material_record_id: string
        relative_path: string
      }>
    },
  ): Promise<ReferencePptCollection> {
    return apiClient.request(
      `/api/teaching-prep/semesters/${encodeURIComponent(semesterId)}/reference-ppt-collections`,
      {
        method: 'POST',
        body: input,
        decode: referencePptCollection,
      },
    )
  },

  attachSemesterMaterial(
    semesterId: string,
    input: {
      request_token: string
      material_version_id: string
      material_role: SemesterMaterialRole
      workbook_series?: string | null
      workbook_volume?: 'A' | 'B' | null
    },
  ): Promise<SemesterMaterialRecord> {
    return apiClient.request(
      `/api/teaching-prep/semesters/${encodeURIComponent(semesterId)}/materials`,
      {
        method: 'POST',
        body: input,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return semesterMaterial(payload)
        },
      },
    )
  },

  updateSemesterMaterial(
    current: SemesterMaterialRecord,
    input: {
      material_role: SemesterMaterialRole
      mapping_status: SemesterMaterialMappingStatus
      is_active: boolean
      workbook_series?: string | null
      workbook_volume?: 'A' | 'B' | null
    },
  ): Promise<SemesterMaterialRecord> {
    return apiClient.request(
      `/api/teaching-prep/semester-materials/${encodeURIComponent(current.id)}`,
      {
        method: 'PATCH',
        body: {
          expected_revision: current.revision,
          ...input,
        },
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return semesterMaterial(payload)
        },
      },
    )
  },

  semesterMappingPreflight(
    semesterId: string,
    materialRecordIds: string[],
  ): Promise<SemesterMappingPreflight> {
    return apiClient.request(
      `/api/teaching-prep/semesters/${encodeURIComponent(semesterId)}/mapping-preflight`,
      {
        method: 'POST',
        body: { material_record_ids: materialRecordIds },
        decode: semesterMappingPreflight,
      },
    )
  },

  startSemesterMappingProposalJob(
    semesterId: string,
    input: {
      operation_id: string
      material_record_id: string
      expected_source_state_sha256: string
    },
  ): Promise<JobResponse> {
    return apiClient.request(
      `/api/teaching-prep/semesters/${encodeURIComponent(semesterId)}/mapping-proposal-jobs`,
      {
        method: 'POST',
        body: input,
        decode: semesterMappingJob,
      },
    )
  },

  listSemesterMappingProposalJobs(
    semesterId: string,
    signal?: AbortSignal,
  ): Promise<JobResponse[]> {
    return apiClient.request(
      `/api/teaching-prep/semesters/${encodeURIComponent(semesterId)}/mapping-proposal-jobs`,
      {
        signal,
        decode: payload => itemList(payload, semesterMappingJob),
      },
    )
  },

  listSemesterMappingProposals(
    semesterId: string,
    signal?: AbortSignal,
  ): Promise<SemesterMappingProposal[]> {
    return apiClient.request(
      `/api/teaching-prep/semesters/${encodeURIComponent(semesterId)}/mapping-proposals`,
      {
        signal,
        decode: (payload) => {
          if (
            !isRecord(payload)
            || !hasExactKeys(payload, new Set(['items']))
            || !Array.isArray(payload.items)
          ) throw new Error('Invalid semester mapping proposal list response')
          return payload.items.map(decodeSemesterMappingProposal)
        },
      },
    )
  },

  applySemesterMappingProposal(
    proposal: SemesterMappingProposal,
  ): Promise<SemesterMappingProposal> {
    return apiClient.request(
      `/api/teaching-prep/semester-mapping-proposals/${encodeURIComponent(proposal.id)}/apply`,
      {
        method: 'POST',
        body: { expected_revision: proposal.revision },
        decode: decodeSemesterMappingProposal,
      },
    )
  },

  acceptLocalReferencePptMappings(
    proposal: SemesterMappingProposal,
  ): Promise<SemesterMappingProposal> {
    return apiClient.request(
      `/api/teaching-prep/semester-mapping-proposals/${encodeURIComponent(proposal.id)}/accept-local-high-confidence`,
      {
        method: 'POST',
        body: { expected_revision: proposal.revision },
        decode: decodeSemesterMappingProposal,
      },
    )
  },

  listLessons(
    curriculumId: string,
    signal?: AbortSignal,
  ): Promise<LessonNode[]> {
    return apiClient.request(
      `/api/teaching-prep/curricula/${encodeURIComponent(curriculumId)}/lessons`,
      {
        signal,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          if (!isRecord(payload) || !Array.isArray(payload.items)) {
            throw new Error('Invalid lesson tree response')
          }
          return payload.items.map(lessonNode)
        },
      },
    )
  },

  createLesson(
    curriculumId: string,
    input: CreateLessonNodeInput,
    signal?: AbortSignal,
  ): Promise<LessonNode> {
    return apiClient.request(
      `/api/teaching-prep/curricula/${encodeURIComponent(curriculumId)}/lessons`,
      {
        method: 'POST',
        body: input,
        signal,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return lessonNode(payload)
        },
      },
    )
  },

  updateLesson(
    nodeId: string,
    input: UpdateLessonNodeInput,
    signal?: AbortSignal,
  ): Promise<LessonNode> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(nodeId)}`,
      {
        method: 'PATCH',
        body: input,
        signal,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return lessonNode(payload)
        },
      },
    )
  },

  reorderLessons(
    curriculumId: string,
    input: ReorderLessonNodesInput,
    signal?: AbortSignal,
  ): Promise<LessonNode[]> {
    return apiClient.request(
      `/api/teaching-prep/curricula/${encodeURIComponent(curriculumId)}/lessons/reorder`,
      {
        method: 'POST',
        body: input,
        signal,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          if (!isRecord(payload) || !Array.isArray(payload.items)) {
            throw new Error('Invalid lesson reorder response')
          }
          return payload.items.map(lessonNode)
        },
      },
    )
  },

  listMaterials(signal?: AbortSignal, includeArchived = false): Promise<MaterialVersion[]> {
    const suffix = includeArchived ? '?include_archived=true' : ''
    return apiClient.request(`/api/teaching-prep/materials${suffix}`, {
      signal,
      decode: (payload) => itemList(payload, material),
    })
  },

  updateMaterialSource(
    current: MaterialVersion,
    input: { display_name?: string; archived?: boolean },
  ): Promise<MaterialVersion> {
    return apiClient.request(
      `/api/teaching-prep/material-sources/${encodeURIComponent(current.source_id)}`,
      {
        method: 'PATCH',
        body: { expected_revision: current.source_revision ?? 1, ...input },
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return material(payload)
        },
      },
    )
  },

  getMaterialDeletionPreview(current: MaterialVersion): Promise<MaterialDeletionPreview> {
    const expectedRevision = current.source_revision ?? 1
    return apiClient.request(
      `/api/teaching-prep/material-sources/${encodeURIComponent(current.source_id)}/deletion-preview?expected_revision=${expectedRevision}`,
      {
        decode: materialDeletionPreview,
      },
    )
  },

  deleteMaterialSource(
    current: MaterialVersion,
    input: {
      operation_id: string
      preview_version: string
      confirmation_phrase: string
    },
  ): Promise<DeleteMaterialSourceResult> {
    return apiClient.request(
      `/api/teaching-prep/material-sources/${encodeURIComponent(current.source_id)}`,
      {
        method: 'DELETE',
        body: { expected_revision: current.source_revision ?? 1, ...input },
        decode: materialDeletionResult,
      },
    )
  },

  getMaterialDeletionStatus(operationId: string): Promise<DeleteMaterialSourceResult> {
    return apiClient.request(
      `/api/teaching-prep/material-deletions/${encodeURIComponent(operationId)}`,
      { decode: materialDeletionResult },
    )
  },

  importMaterialCopy(
    file: File,
    requestToken: string,
    signal?: AbortSignal,
  ): Promise<MaterialVersion> {
    return apiClient.request('/api/teaching-prep/materials/import-copy', {
      method: 'POST',
      rawBody: file,
      headers: materialUploadHeaders(file, requestToken),
      signal,
      timeoutMs: 120_000,
      decode: (payload) => {
        assertNoPathLikeKeys(payload)
        return material(payload)
      },
    })
  },

  relocateMaterialCopy(
    materialVersionId: string,
    file: File,
    requestToken: string,
    signal?: AbortSignal,
  ): Promise<MaterialVersion> {
    return apiClient.request(
      `/api/teaching-prep/materials/${encodeURIComponent(materialVersionId)}/relocate-copy`,
      {
        method: 'POST',
        rawBody: file,
        headers: materialUploadHeaders(file, requestToken),
        signal,
        timeoutMs: 120_000,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return material(payload)
        },
      },
    )
  },

  parseMaterial(
    materialVersionId: string,
    signal?: AbortSignal,
  ): Promise<MaterialUnit[]> {
    return apiClient.request(
      `/api/teaching-prep/materials/${encodeURIComponent(materialVersionId)}/parse`,
      {
        method: 'POST',
        signal,
        timeoutMs: 10 * 60_000,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          if (!isRecord(payload) || !Array.isArray(payload.items)) {
            throw new Error('Invalid material unit response')
          }
          return payload.items.map(materialUnit)
        },
      },
    )
  },

  startMaterialParse(
    materialVersionId: string,
    signal?: AbortSignal,
  ): Promise<JobResponse> {
    return apiClient.request(
      `/api/teaching-prep/materials/${encodeURIComponent(materialVersionId)}/parse-job`,
      {
        method: 'POST',
        signal,
        decode: decodeJobResponse,
      },
    )
  },

  listMaterialParseJobs(signal?: AbortSignal): Promise<JobResponse[]> {
    return apiClient.request('/api/teaching-prep/material-parse-jobs', {
      signal,
      decode: payload => itemList(payload, decodeJobResponse),
    })
  },

  listMaterialUnits(
    materialVersionId: string,
    signal?: AbortSignal,
  ): Promise<MaterialUnit[]> {
    return apiClient.request(
      `/api/teaching-prep/materials/${encodeURIComponent(materialVersionId)}/units`,
      {
        signal,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          if (!isRecord(payload) || !Array.isArray(payload.items)) {
            throw new Error('Invalid material unit response')
          }
          return payload.items.map(materialUnit)
        },
      },
    )
  },

  updateMaterialUnit(
    unitId: string,
    input: {
      expected_revision: number
      title: string | null
      manual_text: string
      formula_review_required: boolean
    },
  ): Promise<MaterialUnit> {
    return apiClient.request(
      `/api/teaching-prep/material-units/${encodeURIComponent(unitId)}`,
      {
        method: 'PATCH',
        body: input,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return materialUnit(payload)
        },
      },
    )
  },

  listMaterialLinks(
    lessonNodeId: string,
    signal?: AbortSignal,
  ): Promise<MaterialLink[]> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonNodeId)}/material-links`,
      {
        signal,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          if (!isRecord(payload) || !Array.isArray(payload.items)) {
            throw new Error('Invalid material link response')
          }
          return payload.items.map(materialLink)
        },
      },
    )
  },

  createMaterialLink(
    lessonNodeId: string,
    input: {
      request_token: string
      material_version_id: string
      start_unit: number
      end_unit: number
      crop: null
      purpose: MaterialLinkPurpose
      teacher_note: string | null
      confirmation_status: 'confirmed'
    },
  ): Promise<MaterialLink> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonNodeId)}/material-links`,
      {
        method: 'POST',
        body: input,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return materialLink(payload)
        },
      },
    )
  },

  updateMaterialLink(
    link: MaterialLink,
    changes: Partial<Pick<
      MaterialLink,
      | 'start_unit'
      | 'end_unit'
      | 'purpose'
      | 'teacher_note'
      | 'confirmation_status'
      | 'is_active'
    >>,
  ): Promise<MaterialLink> {
    return apiClient.request(
      `/api/teaching-prep/material-links/${encodeURIComponent(link.id)}`,
      {
        method: 'PATCH',
        body: {
          expected_revision: link.revision,
          start_unit: changes.start_unit ?? link.start_unit,
          end_unit: changes.end_unit ?? link.end_unit,
          crop: link.crop,
          purpose: changes.purpose ?? link.purpose,
          teacher_note: changes.teacher_note ?? link.teacher_note,
          confirmation_status: changes.confirmation_status
            ?? link.confirmation_status,
          is_active: changes.is_active ?? link.is_active,
        },
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return materialLink(payload)
        },
      },
    )
  },

  listExerciseCandidates(
    lessonNodeId: string,
    signal?: AbortSignal,
  ): Promise<ExerciseCandidate[]> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonNodeId)}/exercise-candidates`,
      {
        signal,
        decode: (payload) => itemList(payload, exerciseCandidate),
      },
    )
  },

  createExerciseCandidate(
    lessonNodeId: string,
    input: ExerciseCandidateInput & { request_token: string },
  ): Promise<ExerciseCandidate> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonNodeId)}/exercise-candidates`,
      {
        method: 'POST',
        body: input,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return exerciseCandidate(payload)
        },
      },
    )
  },

  updateExerciseCandidate(
    candidate: ExerciseCandidate,
    input: ExerciseCandidateInput & { is_active?: boolean },
  ): Promise<ExerciseCandidate> {
    return apiClient.request(
      `/api/teaching-prep/exercise-candidates/${encodeURIComponent(candidate.id)}`,
      {
        method: 'PATCH',
        body: {
          ...input,
          expected_revision: candidate.revision,
          is_active: input.is_active ?? candidate.is_active,
        },
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return exerciseCandidate(payload)
        },
      },
    )
  },

  listAvailableAssessments(
    signal?: AbortSignal,
  ): Promise<AssessmentChoice[]> {
    return apiClient.request('/api/teaching-prep/evidence/assessments', {
      signal,
      decode: (payload) => itemList(payload, assessmentChoice),
    })
  },

  listAvailableQuestions(
    signal?: AbortSignal,
  ): Promise<QuestionEvidenceChoice[]> {
    return apiClient.request('/api/teaching-prep/evidence/questions', {
      signal,
      decode: (payload) => itemList(payload, questionEvidenceChoice),
    })
  },

  listResourcePacks(
    lessonNodeId: string,
    signal?: AbortSignal,
  ): Promise<ResourcePack[]> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonNodeId)}/resource-packs`,
      {
        signal,
        decode: (payload) => itemList(payload, resourcePack),
      },
    )
  },

  getResourcePackStatus(
    lessonNodeId: string,
    signal?: AbortSignal,
  ): Promise<ResourcePackStatus> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonNodeId)}/resource-packs/status`,
      {
        signal,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return resourcePackStatus(payload)
        },
      },
    )
  },

  freezeResourcePack(
    lessonNodeId: string,
    input: FreezeResourcePackInput,
  ): Promise<ResourcePack> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonNodeId)}/resource-packs`,
      {
        method: 'POST',
        body: input,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return resourcePack(payload)
        },
      },
    )
  },

  getLessonDraftPreflight(
    resourcePackId: string,
    mode: 'local_template' | 'model',
    signal?: AbortSignal,
  ): Promise<LessonDraftPreflight> {
    const query = new URLSearchParams({ mode })
    return apiClient.request(
      `/api/teaching-prep/resource-packs/${encodeURIComponent(resourcePackId)}/draft-preflight?${query}`,
      {
        signal,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return lessonDraftPreflight(payload)
        },
      },
    )
  },

  listLessonDrafts(
    resourcePackId: string,
    signal?: AbortSignal,
  ): Promise<LessonDraft[]> {
    return apiClient.request(
      `/api/teaching-prep/resource-packs/${encodeURIComponent(resourcePackId)}/lesson-drafts`,
      {
        signal,
        decode: (payload) => itemList(payload, lessonDraft),
      },
    )
  },

  generateLessonDraft(
    resourcePackId: string,
    input: {
      operation_id: string
      mode: 'local_template' | 'model'
      confirmed: boolean
    },
  ): Promise<LessonDraft> {
    return apiClient.request(
      `/api/teaching-prep/resource-packs/${encodeURIComponent(resourcePackId)}/lesson-drafts`,
      {
        method: 'POST',
        body: input,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return lessonDraft(payload)
        },
      },
    )
  },

  cancelLessonDraftGeneration(
    operationId: string,
  ): Promise<{
      operation_id: string
      status: 'cancelled'
      newly_cancelled: boolean
    }> {
    return apiClient.request(
      `/api/teaching-prep/lesson-draft-generations/${encodeURIComponent(operationId)}/cancel`,
      {
        method: 'POST',
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          if (
            !isRecord(payload)
            || !text(payload.operation_id)
            || payload.status !== 'cancelled'
            || typeof payload.newly_cancelled !== 'boolean'
          ) throw new Error('Invalid draft cancellation response')
          return payload as {
            operation_id: string
            status: 'cancelled'
            newly_cancelled: boolean
          }
        },
      },
    )
  },

  reviseLessonDraft(
    draftId: string,
    input: {
      request_token: string
      payload: LessonDraftPayload
      confirmed: boolean
    },
  ): Promise<LessonDraft> {
    return apiClient.request(
      `/api/teaching-prep/lesson-drafts/${encodeURIComponent(draftId)}/revisions`,
      {
        method: 'POST',
        body: input,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return lessonDraft(payload)
        },
      },
    )
  },

  listSlidePlans(
    lessonDraftId: string,
    signal?: AbortSignal,
  ): Promise<SlidePlan[]> {
    return apiClient.request(
      `/api/teaching-prep/lesson-drafts/${encodeURIComponent(lessonDraftId)}/slide-plans`,
      {
        signal,
        decode: (payload) => itemList(payload, slidePlan),
      },
    )
  },

  createSlidePlan(
    lessonDraftId: string,
    requestToken: string,
  ): Promise<SlidePlan> {
    return apiClient.request(
      `/api/teaching-prep/lesson-drafts/${encodeURIComponent(lessonDraftId)}/slide-plans`,
      {
        method: 'POST',
        body: { request_token: requestToken },
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return slidePlan(payload)
        },
      },
    )
  },

  getSlidePlanPreview(
    planId: string,
    includeProposed = true,
    signal?: AbortSignal,
  ): Promise<SlidePlanPreview> {
    const query = new URLSearchParams({
      include_proposed: String(includeProposed),
    })
    return apiClient.request(
      `/api/teaching-prep/slide-plans/${encodeURIComponent(planId)}/preview?${query}`,
      {
        signal,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return slidePlanPreview(payload)
        },
      },
    )
  },

  reviseSlidePlan(
    planId: string,
    input: {
      request_token: string
      operation_reviews: SlideOperationReviewInput[]
      approve_low_risk_deletions: boolean
      review_note: string | null
    },
  ): Promise<SlidePlan> {
    return apiClient.request(
      `/api/teaching-prep/slide-plans/${encodeURIComponent(planId)}/revisions`,
      {
        method: 'POST',
        body: input,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return slidePlan(payload)
        },
      },
    )
  },

  listPptxExecutions(
    planId: string,
    signal?: AbortSignal,
  ): Promise<PptxExecution[]> {
    return apiClient.request(
      `/api/teaching-prep/slide-plans/${encodeURIComponent(planId)}/executions`,
      {
        signal,
        decode: (payload) => itemList(payload, pptxExecution),
      },
    )
  },

  getLessonGenerationPerformance(
    runId: string,
    signal?: AbortSignal,
  ): Promise<LessonGenerationPerformance> {
    return apiClient.request(
      `/api/teaching-prep/pptx-executions/${encodeURIComponent(runId)}/performance`,
      {
        signal,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return lessonGenerationPerformance(payload)
        },
      },
    )
  },

  executeSlidePlan(
    planId: string,
    operationId: string,
  ): Promise<PptxExecutionResult> {
    return apiClient.request(
      `/api/teaching-prep/slide-plans/${encodeURIComponent(planId)}/executions`,
      {
        method: 'POST',
        body: { operation_id: operationId, confirmed: true },
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return pptxExecutionResult(payload)
        },
      },
    )
  },

  cancelPptxExecution(runId: string): Promise<PptxExecution> {
    return apiClient.request(
      `/api/teaching-prep/pptx-executions/${encodeURIComponent(runId)}/cancel`,
      {
        method: 'POST',
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return pptxExecution(payload)
        },
      },
    )
  },

  recoverPptxExecution(runId: string): Promise<PptxExecutionResult> {
    return apiClient.request(
      `/api/teaching-prep/pptx-executions/${encodeURIComponent(runId)}/recover`,
      {
        method: 'POST',
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return pptxExecutionResult(payload)
        },
      },
    )
  },

  discardPptxStaging(runId: string): Promise<PptxExecution> {
    return apiClient.request(
      `/api/teaching-prep/pptx-executions/${encodeURIComponent(runId)}/discard-staging`,
      {
        method: 'POST',
        body: { confirmed: true },
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return pptxExecution(payload)
        },
      },
    )
  },

  listClassVariants(
    lessonNodeId: string,
    signal?: AbortSignal,
  ): Promise<ClassVariant[]> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonNodeId)}/class-variants`,
      {
        signal,
        decode: (payload) => itemList(payload, classVariant),
      },
    )
  },

  deriveClassVariant(
    basePackId: string,
    input: {
      request_token: string
      class_name: string
      teacher_context: string | null
      assessment_ids: number[]
      knowledge_scope: string[]
      prior_review_ids: string[]
    },
  ): Promise<{ variant: ClassVariant; resource_pack: ResourcePack }> {
    return apiClient.request(
      `/api/teaching-prep/resource-packs/${encodeURIComponent(basePackId)}/class-variants`,
      {
        method: 'POST',
        body: input,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          if (
            !isRecord(payload)
            || !isRecord(payload.variant)
            || !isRecord(payload.resource_pack)
          ) throw new Error('Invalid class variant result response')
          return {
            variant: classVariant(payload.variant),
            resource_pack: resourcePack(payload.resource_pack),
          }
        },
      },
    )
  },

  listUpClassPackages(
    lessonNodeId: string,
    signal?: AbortSignal,
  ): Promise<UpClassPackage[]> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonNodeId)}/up-class-packages`,
      {
        signal,
        decode: (payload) => itemList(payload, upClassPackage),
      },
    )
  },

  createUpClassPackage(
    pptxVersionId: string,
    requestToken: string,
  ): Promise<UpClassPackage> {
    return apiClient.request(
      `/api/teaching-prep/pptx-versions/${encodeURIComponent(pptxVersionId)}/up-class-package`,
      {
        method: 'POST',
        body: { request_token: requestToken, confirmed: true },
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return upClassPackage(payload)
        },
      },
    )
  },

  activateUpClassPackage(
    packageId: string,
    requestToken: string,
  ): Promise<UpClassPackage> {
    return apiClient.request(
      `/api/teaching-prep/up-class-packages/${encodeURIComponent(packageId)}/activate`,
      {
        method: 'POST',
        body: { request_token: requestToken, confirmed: true },
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return upClassPackage(payload)
        },
      },
    )
  },

  recoverUpClassPackage(packageId: string): Promise<UpClassPackage> {
    return apiClient.request(
      `/api/teaching-prep/up-class-packages/${encodeURIComponent(packageId)}/recover`,
      {
        method: 'POST',
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return upClassPackage(payload)
        },
      },
    )
  },

  discardUpClassPackageStaging(packageId: string): Promise<UpClassPackage> {
    return apiClient.request(
      `/api/teaching-prep/up-class-packages/${encodeURIComponent(packageId)}/discard-staging`,
      {
        method: 'POST',
        body: { confirmed: true },
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return upClassPackage(payload)
        },
      },
    )
  },

  listPostLessonReviews(
    lessonNodeId: string,
    signal?: AbortSignal,
  ): Promise<PostLessonReview[]> {
    return apiClient.request(
      `/api/teaching-prep/lessons/${encodeURIComponent(lessonNodeId)}/post-lesson-reviews`,
      {
        signal,
        decode: (payload) => itemList(payload, postLessonReview),
      },
    )
  },

  createPostLessonReview(
    packageId: string,
    input: {
      request_token: string
      timing: PostLessonReview['payload']['timing']
      question_outcome: PostLessonReview['payload']['question_outcome']
      reteach_points: string[]
      next_action: PostLessonReview['payload']['next_action']
      note: string | null
      use_in_next_version: boolean
    },
  ): Promise<PostLessonReview> {
    return apiClient.request(
      `/api/teaching-prep/up-class-packages/${encodeURIComponent(packageId)}/reviews`,
      {
        method: 'POST',
        body: input,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return postLessonReview(payload)
        },
      },
    )
  },
}

function materialUploadHeaders(
  file: File,
  requestToken: string,
): Readonly<Record<string, string>> {
  const displayName = file.name.replace(/\.[^.]+$/, '') || file.name
  return {
    'content-type': file.type || 'application/octet-stream',
    'x-upload-filename': encodeURIComponent(file.name),
    'x-display-name': encodeURIComponent(displayName),
    'x-request-token': requestToken,
    'x-file-modified-ms': String(Math.max(0, Math.trunc(file.lastModified))),
  }
}
