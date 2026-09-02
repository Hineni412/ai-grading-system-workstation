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
  slide_animation_model_available: boolean
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
  is_active?: boolean
  revision: number
  created_at: string
  updated_at: string
  members: ReferencePptCollectionMember[]
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

/** 题库自动选题的最终结果：随资源包冻结，供改编模型标注候选题。 */
export interface QuestionSelectionItemInput {
  question_id: number
  method: string | null
  difficulty: number | null
  frequency_score: number | null
}

export interface QuestionSelectionInput {
  volume_id: string
  section_ids: string[]
  difficulty_max: number
  stem_max_chars: number
  limit: number
  max_per_method: number
  items: QuestionSelectionItemInput[]
}

export interface FreezeResourcePackInput {
  request_token: string
  class_name: string | null
  lesson_type: 'new_lesson' | 'review'
  teacher_context: string | null
  reference_ppt_intents: Record<string, 'keep' | 'candidate_delete'>
  question_ids: number[]
  assessment_ids: number[]
  knowledge_scope: string[]
  preparation_preferences: TeachingPreferencesPayload
  selected_material_link_ids?: string[] | null
  selected_exercise_candidate_ids?: string[] | null
  question_selection?: QuestionSelectionInput | null
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

export interface SlidePlanReviewFinding {
  slide_refs: string[]
  finding: string
  category: string
  suggested_action: string
  citations: string[]
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
    review_findings?: SlidePlanReviewFinding[]
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
    || typeof value.slide_animation_model_available !== 'boolean'
    || typeof value.real_wps_enabled !== 'boolean'
    || typeof value.wps_execution_available !== 'boolean'
  ) throw new Error('Invalid teaching prep status response')
  return value as unknown as TeachingPrepModuleStatus
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
    options?: { includeInactive?: boolean },
    signal?: AbortSignal,
  ): Promise<ReferencePptCollection[]> {
    const suffix = options?.includeInactive ? '?include_inactive=true' : ''
    return apiClient.request(
      `/api/teaching-prep/semesters/${encodeURIComponent(semesterId)}/reference-ppt-collections${suffix}`,
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

  updateReferencePptCollection(
    collectionId: string,
    input: { is_active: boolean },
  ): Promise<ReferencePptCollection> {
    return apiClient.request(
      `/api/teaching-prep/reference-ppt-collections/${encodeURIComponent(collectionId)}`,
      {
        method: 'PATCH',
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

  getMaterialUnit(
    unitId: string,
    signal?: AbortSignal,
  ): Promise<MaterialUnit> {
    return apiClient.request(
      `/api/teaching-prep/material-units/${encodeURIComponent(unitId)}`,
      {
        signal,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return materialUnit(payload)
        },
      },
    )
  },

  requestPptPreviewRender(
    unitId: string,
    signal?: AbortSignal,
  ): Promise<MaterialUnit> {
    return apiClient.request(
      `/api/teaching-prep/material-units/${encodeURIComponent(unitId)}/preview-render`,
      {
        method: 'POST',
        signal,
        decode: (payload) => {
          assertNoPathLikeKeys(payload)
          return materialUnit(payload)
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
