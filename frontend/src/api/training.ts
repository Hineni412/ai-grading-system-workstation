import { apiClient } from './client'
import { decodeJobResponse, type JobResponse } from './jobs'
import {
  assertNoPathLikeKeys,
  isFiniteNumber,
  isInteger,
  isRecord,
  isStringArray,
} from './validation'

export type TrainingStudentScopeMode = 'all' | 'student' | 'selected' | 'class'
export type TrainingExamScopeMode = 'current' | 'manual' | 'cross_exam' | 'semester'
export type TrainingStage = 'direct' | 'prerequisite' | 'transfer'

export interface TrainingStudentScopeRequest {
  mode: TrainingStudentScopeMode
  student_ids: string[]
  class_id?: string
  class_ids?: string[]
  score_rate_min?: number | null
  score_rate_max?: number | null
  include_student_ids?: string[]
  exclude_student_ids?: string[]
  use_historical_fallback?: boolean
}

export interface TrainingExamScopeRequest {
  mode: TrainingExamScopeMode
  session_ids: number[]
  curriculum_volume_id?: string | null
}

export interface TrainingDiagnosisRequest {
  scope: TrainingStudentScopeRequest
  exam_scope: TrainingExamScopeRequest
  grouping?: TrainingGroupingRequest
  include_student_detail?: boolean
}

export interface TrainingGroupingRequest {
  scope_keys: string[]
  member_ids?: string[]
  target_keys?: string[]
  purpose?: 'training' | 'handout'
  max_questions_per_skill?: number
  max_written_questions?: number
  recent_activity_count?: number
  question_count: number
  expected_minutes?: number
  difficulty_min?: number
  difficulty_max: number
  exclude_current_exam_originals: boolean
  curriculum_volume_id: string
  training_intent?: 'remediation' | 'challenge'
  teaching_progress_chapter_id?: string
}

export interface TrainingGroup {
  group_id: string
  source_version: string
  members: Array<{
    student_id: string; student_name: string; student_code: string; class_id: string; evidence_count: number
    targets: Array<{ knowledge_key: string; knowledge_point: string; mastery: number; evidence_count: number; source_question_refs: TrainingEvidenceReference[] }>
  }>
  targets: Array<{
    knowledge_key: string; knowledge_point: string; min_mastery: number; max_mastery: number
    median_mastery: number; evidence_count: number; sparse_member_count: number
    target_difficulty?: number | null; affected_student_count?: number; eligible_member_ids?: string[]; available_question_count: number; difficulty_unknown: boolean
  }>
  ready: boolean
  issues: string[]
  warnings: string[]
  reason: string
  compatibility: number
  available_question_count: number
  recent_excluded_count: number
}

export interface TrainingGrouping {
  version: string
  scope_keys: string[]
  source_scope_revision?: string
  mastery_parameter_version?: string
  groups: TrainingGroup[]
  selection?: TrainingGroup | null
  summary?: {
    student_count: number
    students_with_needs: number
    unlinked_loss_count?: number
    grouped_student_count: number
    group_count: number
  }
  unassigned: Array<{ student_id: string; student_name: string; class_id: string; reason: string; reason_kind?: 'no_direct_evidence' | 'no_group_fit' }>
  warnings: string[]
}

export interface PersonalizedRecommendationCreateRequest
  extends TrainingDiagnosisRequest {
  request_token: string
  remediation_only?: boolean
  max_unmeasured_questions?: number
  max_consolidation_questions?: number
  purpose?: 'training' | 'handout'
  max_questions_per_skill?: number
  max_written_questions?: number
  recent_activity_count?: number
  question_count: number
  expected_minutes?: number
  difficulty_min?: number
  difficulty_max: number
  paper_mode?: 'individual' | 'shared'
  target_keys?: string[]
  scope_keys?: string[]
  target_names: string[]
  exclude_current_exam_originals: boolean
  curriculum_volume_id?: string | null
  group_scope_keys?: string[]
  group_source_version?: string
  training_intent?: 'remediation' | 'challenge'
  teaching_progress_chapter_id?: string
}

export interface PersonalizedRecommendationEditRequest {
  request_token: string
  expected_revision: number
  action: 'lock' | 'unlock' | 'exclude' | 'replace'
  student_id: string
  item_id: string
  reason: string
  replacement_question_id?: number
}

export interface PersonalizedRecommendationRelation {
  relation_id: string
  relation_type: 'prerequisite' | 'related'
  source_key: string
  target_key: string
  rationale: string
  revision: number
}

export interface PersonalizedRecommendationItem {
  knowledge_section?: { id: string | null; title: string }
  primary_skill_name?: string
  practice_purpose?: 'remediation' | 'consolidation' | 'new'
  difficulty_basis?: string
  evidence_confidence?: 'repeated' | 'sparse' | 'auxiliary' | 'unknown'
  item_id: string
  item_order: number
  slot: number
  question_id: number
  question_number: string
  // 旧草稿没有题干；新建/替换的草稿项才带 question_text。
  question_text?: string
  stage: TrainingStage
  selection_kind?: 'direct' | 'task_matched' | 'supplement'
  match_level?: 1 | 2 | 3 | 4
  match_label?: string
  matched_topic_keys?: string[]
  matched_skill_keys?: string[]
  target: Record<string, unknown>
  matched_key: string
  matched_name: string
  relation?: PersonalizedRecommendationRelation | null
  criterion_version_id: string
  criterion_point_count: number
  difficulty: number
  difficulty_band?: 'starter' | 'consolidation' | 'stretch'
  practice_role?: 'step_practice' | 'full_response' | 'supplement'
  part_assessment?: {
    profile_revision: string | number
    evidence_version_id: string
    selection_basis: 'hardest_part'
    parts: Array<{ part_id: string; label?: string; difficulty: number | null; source: string; rationale: string; direct_keys: string[] }>
  } | null
  estimated_minutes?: number
  source_paper: string
  practice_tasks?: Array<{ code: string; label: string; source_refs: Array<Record<string, unknown>> }>
  beneficiary_student_ids?: string[]
  response_modes?: string[]
  reason: string
  locked: boolean
  replacement_history: Array<Record<string, unknown>>
}

export interface PersonalizedRecommendationStudent {
  student_id: string
  student_code: string
  student_name: string
  class_id: string
  selection_mode: 'mastery_targeted' | 'maintenance_fallback' | 'teacher_fixed_class'
  targets: Array<Record<string, unknown>>
  items: PersonalizedRecommendationItem[]
  shortages: Array<Record<string, unknown>>
  warnings: string[]
  estimated_minutes?: number
}

export interface PersonalizedRecommendationDraft {
  draft_id: string
  status: 'draft' | 'reviewed'
  revision: number
  result_version: string
  engine_version: string
  source_version: string
  config: Record<string, unknown>
  group_basis?: Record<string, unknown> | null
  students: PersonalizedRecommendationStudent[]
  warnings: string[]
  history: Array<Record<string, unknown>>
}

export type PersonalizedPaperStatus =
  | 'creating'
  | 'review_pending'
  | 'frozen'
  | 'failed'

export interface PersonalizedPaperBudget {
  version: string
  status: 'ready' | 'blocked'
  context_window_tokens: number
  question_count: number
  criterion_point_count: number
  image_count: number
  page_count: number
  page_count_is_estimate: boolean
  estimated_input_tokens: number
  estimated_output_tokens: number
  estimated_total_tokens: number
  limits: Record<string, number>
  blockers: string[]
}

export interface PersonalizedPaperInstance {
  paper_instance_id: string
  paper_batch_id: string
  draft_id: string
  draft_revision: number
  student_id: string
  student_code?: string | null
  student_name?: string | null
  class_id?: string | null
  series_version: number
  status: PersonalizedPaperStatus
  revision: number
  layout_version: string
  budget: PersonalizedPaperBudget
  question_count: number
  criterion_point_count: number
  items: Array<Record<string, unknown>>
  pages: Array<Record<string, unknown>>
  review_docx_sha256?: string | null
  reviewed_docx_sha256?: string | null
  frozen_pdf_sha256?: string | null
  formula_fallbacks?: Array<Record<string, unknown>>
  downloads: {
    review_docx?: string | null
    reviewed_docx?: string | null
    frozen_pdf?: string | null
  }
  error_code?: string | null
  created_at: string
  frozen_at?: string | null
}

export interface PersonalizedPaperBatch {
  batch_run_id: string
  paper_batch_id: string
  status: 'creating' | 'complete' | 'partial' | 'failed' | 'cancelled'
  requested_count: number
  succeeded_count: number
  failed_count: number
  items: PersonalizedPaperInstance[]
  failures: Array<{ student_id: string; error_code: string }>
  downloads: {
    bundle?: string | null
    manifest?: string | null
    frozen_bundle?: string | null
  }
}

export type TrainingScanIssue =
  | 'identity_unreadable'
  | 'invalid_identity'
  | 'unexpected_paper'
  | 'duplicate_page'
  | 'page_content_conflict'
  | 'image_blurry'
  | 'severe_crop'

export interface TrainingScanPage {
  scan_page_id: string
  upload_id: string
  upload_page_number: number
  submission_id?: string | null
  paper_instance_id?: string | null
  page_number?: number | null
  total_pages?: number | null
  issue_code?: TrainingScanIssue | null
  state: 'assigned' | 'unassigned' | 'duplicate' | 'conflict' | 'replaced' | 'dismissed'
  rotation_degrees: 0 | 90 | 180 | 270
  preview_url: string
}

export interface TrainingSubmission {
  submission_id: string
  paper_instance_id: string
  student_id: string
  student_code?: string | null
  student_name?: string | null
  class_id?: string | null
  series_version: number
  status: 'manual_review' | 'ready' | 'cancelled'
  revision: number
  expected_total_pages: number
  missing_pages: number[]
  issue_codes: TrainingScanIssue[]
  assessment_started: boolean
}

export interface TrainingScanCandidate {
  paper_instance_id: string
  student_id: string
  student_code?: string | null
  student_name?: string | null
  series_version: number
  total_pages: number
}

export interface TrainingScanBatchSummary {
  batch_id: string
  paper_batch_id: string
  status: 'manual_review' | 'ready' | 'cancelled'
  submission_count: number
  created_at: string
  updated_at: string
}

export interface TrainingScanBatch {
  batch_id: string
  paper_batch_id: string
  status: 'manual_review' | 'ready' | 'cancelled'
  revision: number
  duplicate_upload: boolean
  submissions: TrainingSubmission[]
  pages: TrainingScanPage[]
  candidates: TrainingScanCandidate[]
  history: Array<Record<string, unknown>>
  created_at: string
  updated_at: string
}

export type TrainingPointState =
  | 'met'
  | 'not_met'
  | 'uncertain'
  | 'unreadable'

export interface TrainingAssessmentPoint {
  candidate_state?: TrainingPointState | null
  point_id: string
  content: string
  state?: TrainingPointState | null
  evidence?: string | null
  teacher_locked: boolean
  teacher_reason?: string | null
  actor_ref?: string | null
  lock_revision: number
}

export interface TrainingAssessmentQuestion {
  task_item_code: string
  item_order: number
  status: string
  met_count: number
  not_met_count: number
  uncertain_count: number
  unreadable_count: number
  total_count: number
  review_status: string
  review_points: TrainingAssessmentPoint[]
}

export interface TrainingAssessmentOutcome {
  run_id: string
  submission_id: string
  submission_revision: number
  status: string
  request_count: number
  expected_question_count: number
  expected_point_count: number
  model_name?: string | null
  usage: {
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
  }
  latency_ms: number
  issue_codes: string[]
  error_code?: string | null
  questions: TrainingAssessmentQuestion[]
  review_revision: number
  control_state: string
  workflow_status: string
  action_message: string
  attempts: Array<Record<string, unknown>>
}

export interface TrainingFeedback {
  schema_version: 'training-feedback-v1'
  feedback_id: string
  submission_id: string
  submission_revision: number
  source_review_revision: number
  status: 'publication_pending' | 'partial' | 'complete' | 'withdrawn'
  student: Record<string, unknown>
  summary: {
    published_question_count: number
    ready_question_count: number
    total_question_count: number
    pending_outbox_count: number
    message: string
  }
  questions: Array<Record<string, unknown>>
  mastery_changes: Array<Record<string, unknown>>
  next_round: {
    status: string
    draft_id?: string | null
    message: string
    changes: Array<Record<string, unknown>>
    student?: Record<string, unknown>
  }
  timeline: Array<Record<string, unknown>>
  safety: {
    is_exam_score: boolean
    changes_exam_score: boolean
    auto_paper_created: boolean
    auto_printed: boolean
  }
  evidence_version: string
}

export interface TrainingEvidenceReference {
  assessment?: Record<string, unknown>
  deduction_reason?: string
  error_summary?: string
  secondary_errors?: Array<Record<string, unknown>>
  causes?: Array<Record<string, unknown>>
  session_id: number
  session_name: string
  question_id: string
  bank_question_id: number
  score_awarded: number
  full_score: number
  score_rate?: number | null
  source_kind?: 'current_exam' | 'historical_exam'
}

export interface TrainingWeakPoint {
  target_kind?: OverviewTargetKind
  interval_low?: number | null
  interval_high?: number | null
  tier?: 'stable' | 'unsteady' | 'weak' | 'insufficient'
  observation_count?: number
  full_correct_count?: number
  recent_trend?: string | null
  tier_counts?: Partial<Record<'stable' | 'unsteady' | 'weak' | 'insufficient', number>>

  knowledge_key: string
  knowledge_point: string
  mastery?: number | null
  score_sum: number
  full_score_sum: number
  deduction_count: number
  evidence_count: number
  effective_weight?: number
  exam_count: number
  source_question_refs: TrainingEvidenceReference[]
  actionable_reasons: string[]
  tag_context: Record<string, string[]>
  error_counts: Record<string, Record<string, number>>
  error_categories?: string[]
  error_patterns?: string[]
  hierarchy_kind?: 'root' | 'child' | 'parent_summary'
  parent_knowledge_key?: string | null
  parent_knowledge_point?: string | null
  child_knowledge_keys?: string[]
  direct_evidence_count?: number
  child_evidence_count?: number
  precise_training_evidence_count?: number
}

export type TrainingWeakPointSummary = Pick<TrainingWeakPoint,
  | 'knowledge_key' | 'knowledge_point' | 'mastery' | 'tier'
  | 'observation_count' | 'full_correct_count' | 'score_sum' | 'full_score_sum'
  | 'deduction_count' | 'evidence_count' | 'effective_weight' | 'exam_count'
  | 'source_question_refs' | 'parent_knowledge_key' | 'direct_evidence_count'
  | 'child_evidence_count' | 'precise_training_evidence_count'
>

export interface TrainingStudentProfile {
  student_id: string
  student_code: string
  student_name: string
  class_id: string
  score_rate?: number | null
  score_rate_source?: 'current_exam' | 'historical_fallback' | 'none'
  historical_exam_count?: number
  historical_latest_exam_at?: string | null
  weak_points: Array<TrainingWeakPoint | TrainingWeakPointSummary>
}

export interface TrainingDiagnosis {
  include_student_detail?: false
  grouping?: TrainingGrouping | null
  scope: {
    mode: TrainingStudentScopeMode
    student_ids: string[]
    class_id?: string | null
    class_ids?: string[]
    score_rate_min?: number | null
    score_rate_max?: number | null
    include_student_ids?: string[]
    exclude_student_ids?: string[]
    use_historical_fallback?: boolean
    matched_student_count?: number
    scope_revision?: string
    student_score_profiles?: Record<string, Record<string, unknown>>
  }
  exam_scope: {
    mode: TrainingExamScopeMode
    session_ids: number[]
    sessions: Array<{
      session_id: number
      session_name: string
    }>
  }
  students: TrainingStudentProfile[]
  group_weak_points?: Array<TrainingWeakPoint | TrainingWeakPointSummary>
  knowledge_catalog?: Array<{
    knowledge_key: string
    knowledge_point: string
    parent_knowledge_key?: string | null
    parent_knowledge_point?: string | null
    node_kind?: 'chapter' | 'section' | 'topic' | 'skill' | 'type'
    target_kind?: OverviewTargetKind
  }>
  knowledge_associations?: Array<{
    topic_key: string
    skill_key: string
    question_count: number
    same_part_question_count: number
    basis: 'same_part' | 'question_cooccurrence'
  }>
  coverage: {
    covered_items: number
    total_items: number
    missing_items: Record<string, string>
  }
  confirmed_concept_ids: number[]
  suggested_terms: string[]
  unmapped_terms: string[]
  warnings: string[]
  diagnosis_identity: 'question_tag'
  target_kind?: OverviewTargetKind
}

export type TrainingDisplayWeakPoint = Pick<TrainingWeakPoint,
  'knowledge_key' | 'knowledge_point' | 'mastery' | 'parent_knowledge_key'
> & {
  tier: 'stable' | 'unsteady' | 'weak' | 'insufficient'
  observation_count: number
  evidence_count: number
  source_reference_count: number
}

export type TrainingDisplayStudent = Omit<TrainingStudentProfile, 'weak_points'> & {
  weak_points: TrainingDisplayWeakPoint[]
}

export type TrainingDisplayDiagnosis = Omit<TrainingDiagnosis,
  'include_student_detail' | 'students' | 'group_weak_points' | 'knowledge_catalog' | 'knowledge_associations'
> & {
  response_mode: 'display'
  students: TrainingDisplayStudent[]
  group_weak_points: TrainingDisplayWeakPoint[]
  knowledge_catalog: Array<Pick<NonNullable<TrainingDiagnosis['knowledge_catalog']>[number],
    'knowledge_key' | 'knowledge_point' | 'parent_knowledge_key' | 'node_kind' | 'target_kind'>>
}

export type TrainingReadDiagnosis = TrainingDiagnosis | TrainingDisplayDiagnosis
export type TrainingReadStudent = TrainingStudentProfile | TrainingDisplayStudent

export function trainingSourceReferenceCount(
  point: TrainingWeakPointSummary | TrainingDisplayWeakPoint,
): number {
  return 'source_reference_count' in point ? point.source_reference_count : point.source_question_refs.length
}

export interface TrainingOverviewRequest {
  scope: TrainingStudentScopeRequest
  exam_scope: TrainingExamScopeRequest
  include_student_detail?: boolean
}

export type OverviewNodeKind = 'chapter' | 'section' | 'topic' | 'skill' | 'type'
export type OverviewTargetKind = 'skill' | 'type' | 'knowledge' | 'mixed'

export interface TrainingOverviewDistribution {
  weak: number
  unsteady: number
  stable: number
  insufficient: number
}

export interface TrainingOverviewExamQuestion {
  session_name: string
  question_label: string
  class_rate: number | null
}

export interface TrainingOverviewTypicalQuestion extends TrainingOverviewExamQuestion {
  bank_question_id: number
}

export interface TrainingOverviewNode {
  target_kind?: OverviewTargetKind
  definition?: string
  in_volume?: boolean
  group_interval_low?: number | null
  group_interval_high?: number | null
  knowledge_key: string
  display_name: string
  kind: OverviewNodeKind
  chapter_key: string
  section_key: string
  group_mastery: number | null
  tier?: string
  evidence_student_count: number
  distribution: TrainingOverviewDistribution
  students: Array<{ student_id: string; mastery: number; tier?: string; interval_low?: number | null; interval_high?: number | null; observation_count?: number; full_correct_count?: number; recent_trend?: string | null }>
  typical_question?: TrainingOverviewTypicalQuestion | null
  other_questions?: TrainingOverviewExamQuestion[]
}

export interface TrainingOverviewTierCounts {
  weak: number
  unsteady: number
  insufficient: number
  stable: number
  evidence: number
}

export interface TrainingOverviewStudent {
  student_id: string
  student_code: string
  student_name: string
  class_id: string
  score_rate: number | null
  score_rate_source: 'current_exam' | 'historical_fallback' | 'none'
  topics: TrainingOverviewTierCounts
  skills: TrainingOverviewTierCounts
  types?: TrainingOverviewTierCounts
}

export interface TrainingOverviewSummary {
  student_count: number
  evidence_student_count: number
  exam_student_count: number
  exam_score_rate: number | null
  topic_count: number
  skill_count: number
  weak_topic_count: number
  weak_skill_count: number
  type_count?: number
  weak_type_count?: number
  target_count?: number
  weak_target_count?: number
}

export interface TrainingOverviewAssociation {
  topic_key: string
  skill_key: string
  question_count: number
  same_part_question_count: number
  basis: 'same_part' | 'question_cooccurrence'
}

export interface TrainingOverview {
  chapter_target_kinds?: Record<string, OverviewTargetKind>
  associations?: TrainingOverviewAssociation[]
  target_kind?: OverviewTargetKind
  scope: TrainingDiagnosis['scope']
  exam_scope: TrainingDiagnosis['exam_scope'] & {
    curriculum_volume_id?: string | null
  }
  warnings: string[]
  nodes: TrainingOverviewNode[]
  students: TrainingOverviewStudent[]
  summary: TrainingOverviewSummary
}

function isRate(value: unknown): boolean {
  return isFiniteNumber(value) && value >= 0 && value <= 1
}

function isNullablePercentage(value: unknown): boolean {
  return value === null || value === undefined || isRate(value)
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && Boolean(value.trim())
}

function isPositiveIntegerArray(value: unknown): value is number[] {
  return Array.isArray(value) && value.every((item) => isInteger(item, 1))
}

function isStringListRecord(value: unknown): value is Record<string, string[]> {
  return isRecord(value) && Object.values(value).every(isStringArray)
}

function isCountRecord(value: unknown): value is Record<string, number> {
  return isRecord(value) && Object.values(value).every((count) => isInteger(count))
}

function isNestedCountRecord(
  value: unknown,
): value is Record<string, Record<string, number>> {
  return isRecord(value) && Object.values(value).every(isCountRecord)
}

function isStudentScopeMode(value: unknown): value is TrainingStudentScopeMode {
  return value === 'all' || value === 'student' || value === 'selected' || value === 'class'
}

function isExamScopeMode(value: unknown): value is TrainingExamScopeMode {
  return value === 'current' || value === 'manual' || value === 'cross_exam' || value === 'semester'
}

function isStage(value: unknown): value is TrainingStage {
  return value === 'direct' || value === 'prerequisite' || value === 'transfer'
}

function isEvidenceReference(value: unknown): value is TrainingEvidenceReference {
  return (
    isRecord(value)
    && isInteger(value.session_id, 1)
    && typeof value.session_name === 'string'
    && isNonEmptyString(value.question_id)
    && isInteger(value.bank_question_id, 1)
    && isFiniteNumber(value.score_awarded)
    && isFiniteNumber(value.full_score)
    && value.full_score >= 0
    && (
      value.score_rate === undefined
      || value.score_rate === null
      || isRate(value.score_rate)
    )
    && (value.source_kind === undefined
      || value.source_kind === 'current_exam'
      || value.source_kind === 'historical_exam')
  )
}

function isWeakPointSummary(value: unknown): value is TrainingWeakPointSummary {
  return (
    isRecord(value)
    && isNonEmptyString(value.knowledge_key)
    && isNonEmptyString(value.knowledge_point)
    && (value.mastery === undefined || value.mastery === null || isRate(value.mastery))
    && isFiniteNumber(value.score_sum)
    && isFiniteNumber(value.full_score_sum)
    && value.full_score_sum >= 0
    && isInteger(value.deduction_count)
    && isInteger(value.evidence_count)
    && (value.effective_weight === undefined || (isFiniteNumber(value.effective_weight) && value.effective_weight >= 0))
    && isInteger(value.exam_count)
    && Array.isArray(value.source_question_refs)
    && value.source_question_refs.every(isEvidenceReference)
    && (value.tier === undefined || (typeof value.tier === 'string'
      && ['stable', 'unsteady', 'weak', 'insufficient'].includes(value.tier)))
    && (value.observation_count === undefined || isInteger(value.observation_count))
    && (value.full_correct_count === undefined || isInteger(value.full_correct_count))
    && (value.parent_knowledge_key === undefined || value.parent_knowledge_key === null
      || typeof value.parent_knowledge_key === 'string')
    && (value.direct_evidence_count === undefined || isInteger(value.direct_evidence_count))
    && (value.child_evidence_count === undefined || isInteger(value.child_evidence_count))
    && (value.precise_training_evidence_count === undefined || isInteger(value.precise_training_evidence_count))
  )
}

function isWeakPoint(value: unknown): value is TrainingWeakPoint {
  return isRecord(value)
    && isStringArray(value.actionable_reasons)
    && isStringListRecord(value.tag_context)
    && isNestedCountRecord(value.error_counts)
    && (value.child_knowledge_keys === undefined || isStringArray(value.child_knowledge_keys))
    && isWeakPointSummary(value)
}

function isStudentProfile(value: unknown,
  pointDecoder: (point: unknown) => point is TrainingWeakPointSummary = isWeakPoint,
): value is TrainingStudentProfile {
  return (
    isRecord(value)
    && isNonEmptyString(value.student_id)
    && typeof value.student_code === 'string'
    && typeof value.student_name === 'string'
    && typeof value.class_id === 'string'
    && isNullablePercentage(value.score_rate)
    && (value.score_rate_source === undefined || (
      value.score_rate_source === 'current_exam'
      || value.score_rate_source === 'historical_fallback'
      || value.score_rate_source === 'none'
    ))
    && (value.historical_exam_count === undefined || isInteger(value.historical_exam_count))
    && (value.historical_latest_exam_at === null
      || value.historical_latest_exam_at === undefined
      || typeof value.historical_latest_exam_at === 'string')
    && Array.isArray(value.weak_points)
    && value.weak_points.every(pointDecoder)
  )
}

function isNormalizedScope(value: unknown): value is TrainingDiagnosis['scope'] {
  return (
    isRecord(value)
    && isStudentScopeMode(value.mode)
    && isStringArray(value.student_ids)
    && (
      value.class_id === undefined
      || value.class_id === null
      || typeof value.class_id === 'string'
    )
    && (value.class_ids === undefined || isStringArray(value.class_ids))
    && (value.score_rate_min === null || value.score_rate_min === undefined || isRate(value.score_rate_min))
    && (value.score_rate_max === null || value.score_rate_max === undefined || isRate(value.score_rate_max))
    && (value.include_student_ids === undefined || isStringArray(value.include_student_ids))
    && (value.exclude_student_ids === undefined || isStringArray(value.exclude_student_ids))
    && (value.use_historical_fallback === undefined || typeof value.use_historical_fallback === 'boolean')
    && (value.matched_student_count === undefined || isInteger(value.matched_student_count))
    && (value.scope_revision === undefined || typeof value.scope_revision === 'string')
    && (value.student_score_profiles === undefined || isRecord(value.student_score_profiles))
  )
}

function isNormalizedExamScope(
  value: unknown,
): value is TrainingDiagnosis['exam_scope'] {
  return (
    isRecord(value)
    && isExamScopeMode(value.mode)
    && isPositiveIntegerArray(value.session_ids)
    && Array.isArray(value.sessions)
    && value.sessions.every((session) => (
      isRecord(session)
      && isInteger(session.session_id, 1)
      && typeof session.session_name === 'string'
    ))
  )
}

function isCoverage(value: unknown): value is TrainingDiagnosis['coverage'] {
  return (
    isRecord(value)
    && isInteger(value.covered_items)
    && isInteger(value.total_items)
    && value.covered_items <= value.total_items
    && isRecord(value.missing_items)
    && Object.values(value.missing_items).every(
      (reason) => typeof reason === 'string',
    )
  )
}

export function decodeTrainingDiagnosis(value: unknown): TrainingDiagnosis {
  assertNoPathLikeKeys(value)
  const pointDecoder = isRecord(value) && value.include_student_detail === false
    ? isWeakPointSummary : isWeakPoint
  if (
    !isRecord(value)
    || value.response_mode !== undefined
    || value.diagnosis_identity !== 'question_tag'
    || !isNormalizedScope(value.scope)
    || !isNormalizedExamScope(value.exam_scope)
    || !Array.isArray(value.students)
    || (value.include_student_detail !== undefined && value.include_student_detail !== false)
    || !value.students.every(student => isStudentProfile(student, pointDecoder))
    || (value.group_weak_points !== undefined && (
      !Array.isArray(value.group_weak_points)
      || !value.group_weak_points.every(pointDecoder)
    ))
    || (value.knowledge_catalog !== undefined && !Array.isArray(value.knowledge_catalog))
    || !isCoverage(value.coverage)
    || !isPositiveIntegerArray(value.confirmed_concept_ids)
    || !isStringArray(value.suggested_terms)
    || !isStringArray(value.unmapped_terms)
    || !isStringArray(value.warnings)
    || (value.target_kind !== undefined && !['skill', 'type', 'knowledge', 'mixed'].includes(String(value.target_kind)))
  ) {
    throw new Error('Invalid training diagnosis')
  }
  return value as unknown as TrainingDiagnosis
}

function isDisplayPoint(value: unknown): value is TrainingDisplayWeakPoint {
  return isRecord(value)
    && Object.keys(value).every(key => ['knowledge_key', 'knowledge_point', 'mastery', 'tier',
      'observation_count', 'evidence_count', 'parent_knowledge_key', 'source_reference_count'].includes(key))
    && isNonEmptyString(value.knowledge_key) && isNonEmptyString(value.knowledge_point)
    && (value.mastery === undefined || value.mastery === null || isRate(value.mastery))
    && (value.tier === 'stable' || value.tier === 'unsteady' || value.tier === 'weak' || value.tier === 'insufficient')
    && isInteger(value.observation_count) && isInteger(value.evidence_count) && isInteger(value.source_reference_count)
    && (value.parent_knowledge_key === undefined || value.parent_knowledge_key === null
      || typeof value.parent_knowledge_key === 'string')
}

function isDisplayStudent(value: unknown): value is TrainingDisplayStudent {
  return isRecord(value) && isNonEmptyString(value.student_id)
    && typeof value.student_code === 'string' && typeof value.student_name === 'string'
    && typeof value.class_id === 'string' && isNullablePercentage(value.score_rate)
    && (value.score_rate_source === undefined || value.score_rate_source === 'current_exam'
      || value.score_rate_source === 'historical_fallback' || value.score_rate_source === 'none')
    && (value.historical_exam_count === undefined || isInteger(value.historical_exam_count))
    && (value.historical_latest_exam_at === undefined || value.historical_latest_exam_at === null
      || typeof value.historical_latest_exam_at === 'string')
    && Array.isArray(value.weak_points) && value.weak_points.every(isDisplayPoint)
}

function isDisplayCatalogNode(value: unknown): value is TrainingDisplayDiagnosis['knowledge_catalog'][number] {
  return isRecord(value) && isNonEmptyString(value.knowledge_key) && isNonEmptyString(value.knowledge_point)
    && Object.keys(value).every(key => ['knowledge_key', 'knowledge_point', 'parent_knowledge_key', 'node_kind', 'target_kind'].includes(key))
    && (value.parent_knowledge_key === undefined || value.parent_knowledge_key === null
      || typeof value.parent_knowledge_key === 'string')
    && (value.node_kind === undefined || value.node_kind === 'chapter' || value.node_kind === 'section'
      || value.node_kind === 'topic' || value.node_kind === 'skill' || value.node_kind === 'type')
}

function isTrainingGroup(value: unknown): value is TrainingGroup {
  return isRecord(value) && isNonEmptyString(value.group_id) && isNonEmptyString(value.source_version)
    && Array.isArray(value.members) && value.members.every(member => isRecord(member)
      && isNonEmptyString(member.student_id) && typeof member.student_name === 'string'
      && typeof member.student_code === 'string' && typeof member.class_id === 'string' && isInteger(member.evidence_count)
      && Array.isArray(member.targets) && member.targets.every(target => isRecord(target)
        && isNonEmptyString(target.knowledge_key) && isNonEmptyString(target.knowledge_point) && isRate(target.mastery)
        && isInteger(target.evidence_count) && Array.isArray(target.source_question_refs)
        && target.source_question_refs.every(isEvidenceReference)))
    && Array.isArray(value.targets) && value.targets.every(target => isRecord(target)
      && isNonEmptyString(target.knowledge_key) && isNonEmptyString(target.knowledge_point)
      && isRate(target.min_mastery) && isRate(target.max_mastery) && isRate(target.median_mastery)
      && isInteger(target.evidence_count) && isInteger(target.sparse_member_count)
      && (target.target_difficulty === undefined || target.target_difficulty === null || isFiniteNumber(target.target_difficulty))
      && (target.affected_student_count === undefined || isInteger(target.affected_student_count))
      && (target.eligible_member_ids === undefined || isStringArray(target.eligible_member_ids))
      && isInteger(target.available_question_count) && typeof target.difficulty_unknown === 'boolean')
    && typeof value.ready === 'boolean' && isStringArray(value.issues) && isStringArray(value.warnings)
    && typeof value.reason === 'string' && isFiniteNumber(value.compatibility)
    && isInteger(value.available_question_count) && isInteger(value.recent_excluded_count)
}

function isTrainingGrouping(value: unknown): value is TrainingGrouping {
  return isRecord(value) && isNonEmptyString(value.version) && isStringArray(value.scope_keys)
    && (value.source_scope_revision === undefined || typeof value.source_scope_revision === 'string')
    && (value.mastery_parameter_version === undefined || typeof value.mastery_parameter_version === 'string')
    && Array.isArray(value.groups) && value.groups.every(isTrainingGroup)
    && (value.selection === undefined || value.selection === null || isTrainingGroup(value.selection))
    && (value.summary === undefined || (isRecord(value.summary)
      && isInteger(value.summary.student_count) && isInteger(value.summary.students_with_needs)
      && isInteger(value.summary.grouped_student_count) && isInteger(value.summary.group_count)
      && (value.summary.unlinked_loss_count === undefined || isInteger(value.summary.unlinked_loss_count))))
    && Array.isArray(value.unassigned) && value.unassigned.every(item => isRecord(item)
      && isNonEmptyString(item.student_id) && typeof item.student_name === 'string'
      && typeof item.class_id === 'string' && typeof item.reason === 'string'
      && (item.reason_kind === undefined || item.reason_kind === 'no_direct_evidence' || item.reason_kind === 'no_group_fit'))
    && isStringArray(value.warnings)
}

function isDisplayDiagnosis(value: unknown): value is TrainingDisplayDiagnosis {
  return isRecord(value) && value.response_mode === 'display' && value.include_student_detail === undefined
    && value.diagnosis_identity === 'question_tag' && isNormalizedScope(value.scope)
    && isNormalizedExamScope(value.exam_scope) && Array.isArray(value.students) && value.students.every(isDisplayStudent)
    && Array.isArray(value.group_weak_points) && value.group_weak_points.every(isDisplayPoint)
    && Array.isArray(value.knowledge_catalog) && value.knowledge_catalog.every(isDisplayCatalogNode)
    && (value.grouping === undefined || value.grouping === null || isTrainingGrouping(value.grouping))
    && isCoverage(value.coverage) && isPositiveIntegerArray(value.confirmed_concept_ids)
    && isStringArray(value.suggested_terms) && isStringArray(value.unmapped_terms) && isStringArray(value.warnings)
    && (value.target_kind === undefined || ['skill', 'type', 'knowledge', 'mixed'].includes(String(value.target_kind)))
}

export function decodeTrainingDisplayDiagnosis(value: unknown): TrainingDisplayDiagnosis {
  assertNoPathLikeKeys(value)
  if (!isDisplayDiagnosis(value)) throw new Error('Invalid training display diagnosis')
  return value
}

function isOverviewDistribution(
  value: unknown,
): value is TrainingOverviewDistribution {
  return (
    isRecord(value)
    && isInteger(value.weak)
    && isInteger(value.unsteady)
    && isInteger(value.stable)
    && isInteger(value.insufficient)
  )
}

function isOverviewNodeKind(value: unknown): value is OverviewNodeKind {
  return value === 'chapter' || value === 'section' || value === 'topic' || value === 'skill' || value === 'type'
}

function isOverviewExamQuestion(value: unknown): value is TrainingOverviewExamQuestion {
  return (
    isRecord(value)
    && typeof value.session_name === 'string'
    && typeof value.question_label === 'string'
    && (value.class_rate === null || isFiniteNumber(value.class_rate))
  )
}

function isOverviewNode(value: unknown): value is TrainingOverviewNode {
  return (
    isRecord(value)
    && (value.definition === undefined || typeof value.definition === 'string')
    && (value.in_volume === undefined || typeof value.in_volume === 'boolean')
    && isNullablePercentage(value.group_interval_low)
    && isNullablePercentage(value.group_interval_high)
    && isNonEmptyString(value.knowledge_key)
    && typeof value.display_name === 'string'
    && isOverviewNodeKind(value.kind)
    && typeof value.chapter_key === 'string'
    && typeof value.section_key === 'string'
    && (value.group_mastery === null || isFiniteNumber(value.group_mastery))
    && isInteger(value.evidence_student_count)
    && isOverviewDistribution(value.distribution)
    && Array.isArray(value.students)
    && value.students.every((student) => (
      isRecord(student)
      && typeof student.student_id === 'string'
      && isFiniteNumber(student.mastery)
    ))
    && (value.typical_question === undefined || value.typical_question === null
      || (isOverviewExamQuestion(value.typical_question) && isInteger((value.typical_question as TrainingOverviewTypicalQuestion).bank_question_id)))
    && (value.other_questions === undefined || (Array.isArray(value.other_questions) && value.other_questions.every(isOverviewExamQuestion)))
  )
}

function isOverviewTierCounts(
  value: unknown,
): value is TrainingOverviewTierCounts {
  return (
    isRecord(value)
    && isInteger(value.weak)
    && isInteger(value.unsteady)
    && isInteger(value.stable)
    && isInteger(value.evidence)
  )
}

function isOverviewStudent(value: unknown): value is TrainingOverviewStudent {
  return (
    isRecord(value)
    && typeof value.student_id === 'string'
    && typeof value.student_code === 'string'
    && typeof value.student_name === 'string'
    && typeof value.class_id === 'string'
    && (value.score_rate === null || isFiniteNumber(value.score_rate))
    && (
      value.score_rate_source === 'current_exam'
      || value.score_rate_source === 'historical_fallback'
      || value.score_rate_source === 'none'
    )
    && isOverviewTierCounts(value.topics)
    && isOverviewTierCounts(value.skills)
    && (value.types === undefined || value.types === null || isOverviewTierCounts(value.types))
  )
}

function isOverviewSummary(value: unknown): value is TrainingOverviewSummary {
  return (
    isRecord(value)
    && isInteger(value.student_count)
    && isInteger(value.evidence_student_count)
    && isInteger(value.exam_student_count)
    && (value.exam_score_rate === null || isFiniteNumber(value.exam_score_rate))
    && isInteger(value.topic_count)
    && isInteger(value.skill_count)
    && isInteger(value.weak_topic_count)
    && isInteger(value.weak_skill_count)
    && (value.type_count === undefined || isInteger(value.type_count))
    && (value.weak_type_count === undefined || isInteger(value.weak_type_count))
  )
}

export function decodeTrainingOverview(value: unknown): TrainingOverview {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || !isNormalizedScope(value.scope)
    || !isNormalizedExamScope(value.exam_scope)
    || !isStringArray(value.warnings)
    || !Array.isArray(value.nodes)
    || !value.nodes.every(isOverviewNode)
    || !Array.isArray(value.students)
    || !value.students.every(isOverviewStudent)
    || !isOverviewSummary(value.summary)
    || (value.target_kind !== undefined && !['skill', 'type', 'knowledge', 'mixed'].includes(String(value.target_kind)))
    || (value.associations !== undefined && (!Array.isArray(value.associations) || !value.associations.every(a =>
      isRecord(a) && isNonEmptyString(a.topic_key) && isNonEmptyString(a.skill_key)
      && isInteger(a.question_count) && isInteger(a.same_part_question_count)
      && a.same_part_question_count <= a.question_count
      && (a.basis === 'same_part' ? a.same_part_question_count > 0
        : a.basis === 'question_cooccurrence' && a.same_part_question_count === 0))))
  ) {
    throw new Error('Invalid training overview')
  }
  return value as unknown as TrainingOverview
}

function isRecommendationRelation(
  value: unknown,
): value is PersonalizedRecommendationRelation {
  return (
    isRecord(value)
    && isNonEmptyString(value.relation_id)
    && (value.relation_type === 'prerequisite' || value.relation_type === 'related')
    && isNonEmptyString(value.source_key)
    && isNonEmptyString(value.target_key)
    && typeof value.rationale === 'string'
    && isInteger(value.revision, 1)
  )
}

function isRecommendationItem(
  value: unknown,
): value is PersonalizedRecommendationItem {
  return (
    isRecord(value)
    && isNonEmptyString(value.item_id)
    && isInteger(value.item_order, 1)
    && isInteger(value.slot, 1)
    && isInteger(value.question_id, 1)
    && typeof value.question_number === 'string'
    && (value.question_text === undefined || typeof value.question_text === 'string')
    && isStage(value.stage)
    && (value.selection_kind === undefined || value.selection_kind === 'direct' || value.selection_kind === 'task_matched' || value.selection_kind === 'supplement')
    && isRecord(value.target)
    && isNonEmptyString(value.matched_key)
    && typeof value.matched_name === 'string'
    && (
      value.relation === undefined
      || value.relation === null
      || isRecommendationRelation(value.relation)
    )
    && /^[0-9a-f]{64}$/.test(String(value.criterion_version_id || ''))
    && isInteger(value.criterion_point_count, 1)
    && isFiniteNumber(value.difficulty)
    && value.difficulty >= 1
    && value.difficulty <= 10
    && (
      value.part_assessment === undefined || value.part_assessment === null
      || (isRecord(value.part_assessment)
        && (isNonEmptyString(value.part_assessment.profile_revision)
          || isInteger(value.part_assessment.profile_revision, 1))
        && isNonEmptyString(value.part_assessment.evidence_version_id)
        && value.part_assessment.selection_basis === 'hardest_part'
        && Array.isArray(value.part_assessment.parts)
        && value.part_assessment.parts.length > 0
        && value.part_assessment.parts.every(part => isRecord(part)
          && isNonEmptyString(part.part_id)
          && (part.difficulty === null || (typeof part.difficulty === 'number'
            && part.difficulty >= 1 && part.difficulty <= 10))
          && (part.label === undefined || typeof part.label === 'string')
          && typeof part.source === 'string' && typeof part.rationale === 'string'
          && isStringArray(part.direct_keys)))
    )
    && (value.estimated_minutes === undefined || isInteger(value.estimated_minutes, 1))
    && typeof value.source_paper === 'string'
    && isNonEmptyString(value.reason)
    && typeof value.locked === 'boolean'
    && Array.isArray(value.replacement_history)
    && value.replacement_history.every(isRecord)
  )
}

function isRecommendationStudent(
  value: unknown,
): value is PersonalizedRecommendationStudent {
  return (
    isRecord(value)
    && isNonEmptyString(value.student_id)
    && typeof value.student_code === 'string'
    && typeof value.student_name === 'string'
    && typeof value.class_id === 'string'
    && (
      value.selection_mode === 'mastery_targeted'
      || value.selection_mode === 'maintenance_fallback'
      || value.selection_mode === 'teacher_fixed_class'
    )
    && Array.isArray(value.targets)
    && value.targets.every(isRecord)
    && Array.isArray(value.items)
    && value.items.every(isRecommendationItem)
    && Array.isArray(value.shortages)
    && value.shortages.every(isRecord)
    && isStringArray(value.warnings)
    && (value.estimated_minutes === undefined || isInteger(value.estimated_minutes))
  )
}

export function decodePersonalizedRecommendationDraft(
  value: unknown,
): PersonalizedRecommendationDraft {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || !/^[0-9a-f]{64}$/.test(String(value.draft_id || ''))
    || (value.status !== 'draft' && value.status !== 'reviewed')
    || !isInteger(value.revision, 1)
    || !/^[0-9a-f]{64}$/.test(String(value.result_version || ''))
    || !isNonEmptyString(value.engine_version)
    || !/^[0-9a-f]{64}$/.test(String(value.source_version || ''))
    || !isRecord(value.config)
    || !Array.isArray(value.students)
    || !value.students.every(isRecommendationStudent)
    || !isStringArray(value.warnings)
    || !Array.isArray(value.history)
    || !value.history.every(isRecord)
  ) {
    throw new Error('Invalid personalized recommendation draft')
  }
  return value as unknown as PersonalizedRecommendationDraft
}

function isNullableString(value: unknown): boolean {
  return value === undefined || value === null || typeof value === 'string'
}

function isPersonalizedPaperBudget(
  value: unknown,
): value is PersonalizedPaperBudget {
  return (
    isRecord(value)
    && isNonEmptyString(value.version)
    && (value.status === 'ready' || value.status === 'blocked')
    && isInteger(value.context_window_tokens, 1)
    && isInteger(value.question_count)
    && isInteger(value.criterion_point_count)
    && isInteger(value.image_count)
    && isInteger(value.page_count, 1)
    && typeof value.page_count_is_estimate === 'boolean'
    && isInteger(value.estimated_input_tokens)
    && isInteger(value.estimated_output_tokens)
    && isInteger(value.estimated_total_tokens)
    && isRecord(value.limits)
    && Object.values(value.limits).every((item) => isInteger(item))
    && isStringArray(value.blockers)
  )
}

export function decodePersonalizedPaperInstance(
  value: unknown,
): PersonalizedPaperInstance {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || !/^[0-9a-f]{64}$/.test(String(value.paper_instance_id || ''))
    || !/^[0-9a-f]{64}$/.test(String(value.paper_batch_id || ''))
    || !/^[0-9a-f]{64}$/.test(String(value.draft_id || ''))
    || !isInteger(value.draft_revision, 1)
    || !isNonEmptyString(value.student_id)
    || !isNullableString(value.student_code)
    || !isNullableString(value.student_name)
    || !isNullableString(value.class_id)
    || !isInteger(value.series_version, 1)
    || !['creating', 'review_pending', 'frozen', 'failed'].includes(
      String(value.status),
    )
    || !isInteger(value.revision, 1)
    || !isNonEmptyString(value.layout_version)
    || !isPersonalizedPaperBudget(value.budget)
    || !isInteger(value.question_count)
    || !isInteger(value.criterion_point_count)
    || !Array.isArray(value.items)
    || !value.items.every(isRecord)
    || !Array.isArray(value.pages)
    || !value.pages.every(isRecord)
    || !isNullableString(value.review_docx_sha256)
    || !isNullableString(value.reviewed_docx_sha256)
    || !isNullableString(value.frozen_pdf_sha256)
    || (value.formula_fallbacks !== undefined
      && (!Array.isArray(value.formula_fallbacks) || !value.formula_fallbacks.every(isRecord)))
    || !isRecord(value.downloads)
    || !isNullableString(value.downloads.review_docx)
    || !isNullableString(value.downloads.reviewed_docx)
    || !isNullableString(value.downloads.frozen_pdf)
    || !isNullableString(value.error_code)
    || !isNonEmptyString(value.created_at)
    || !isNullableString(value.frozen_at)
  ) {
    throw new Error('Invalid personalized paper instance')
  }
  return value as unknown as PersonalizedPaperInstance
}

function decodePersonalizedPaperList(
  value: unknown,
): { items: PersonalizedPaperInstance[] } {
  if (
    !isRecord(value)
    || !Array.isArray(value.items)
  ) {
    throw new Error('Invalid personalized paper list')
  }
  return { items: value.items.map(decodePersonalizedPaperInstance) }
}

function decodePersonalizedPaperBatch(value: unknown): PersonalizedPaperBatch {
  assertNoPathLikeKeys(value)
  if (!isRecord(value)
    || !/^[0-9a-f]{64}$/.test(String(value.batch_run_id || ''))
    || !/^[0-9a-f]{64}$/.test(String(value.paper_batch_id || ''))
    || !['creating', 'complete', 'partial', 'failed', 'cancelled'].includes(String(value.status))
    || !isInteger(value.requested_count)
    || !isInteger(value.succeeded_count)
    || !isInteger(value.failed_count)
    || !Array.isArray(value.items)
    || !value.items.every((item) => {
      try { decodePersonalizedPaperInstance(item); return true } catch { return false }
    })
    || !Array.isArray(value.failures)
    || !value.failures.every((item) => isRecord(item)
      && isNonEmptyString(item.student_id) && isNonEmptyString(item.error_code))
    || !isRecord(value.downloads)
    || !isNullableString(value.downloads.bundle)
    || !isNullableString(value.downloads.manifest)
    || !isNullableString(value.downloads.frozen_bundle)) {
    throw new Error('Invalid personalized paper batch')
  }
  return value as unknown as PersonalizedPaperBatch
}

function decodePersonalizedPaperBatchList(
  value: unknown,
): { items: PersonalizedPaperBatch[] } {
  if (!isRecord(value) || !Array.isArray(value.items)) {
    throw new Error('Invalid personalized paper batch list')
  }
  return { items: value.items.map(decodePersonalizedPaperBatch) }
}

function decodeTrainingScanBatchList(value: unknown): { items: TrainingScanBatchSummary[] } {
  if (!isRecord(value) || !Array.isArray(value.items)) {
    throw new Error('Invalid training scan batch list')
  }
  return { items: value.items.map((item: unknown) => {
    if (
      !isRecord(item)
      || !/^[0-9a-f]{64}$/.test(String(item.batch_id || ''))
      || !/^[0-9a-f]{64}$/.test(String(item.paper_batch_id || ''))
      || !['manual_review', 'ready', 'cancelled'].includes(String(item.status))
      || !isInteger(item.submission_count, 0)
      || !isNonEmptyString(item.created_at)
      || !isNonEmptyString(item.updated_at)
    ) throw new Error('Invalid training scan batch summary')
    return item as unknown as TrainingScanBatchSummary
  }) }
}

function decodeTrainingScanBatch(value: unknown): TrainingScanBatch {
  assertNoPathLikeKeys(value)
  const submissionIsValid = (item: unknown): boolean => (
    isRecord(item)
    && /^[0-9a-f]{64}$/.test(String(item.submission_id || ''))
    && /^[0-9a-f]{64}$/.test(String(item.paper_instance_id || ''))
    && isNonEmptyString(item.student_id)
    && isNullableString(item.student_code)
    && isNullableString(item.student_name)
    && isNullableString(item.class_id)
    && isInteger(item.series_version, 1)
    && ['manual_review', 'ready', 'cancelled'].includes(String(item.status))
    && isInteger(item.revision, 1)
    && isInteger(item.expected_total_pages, 1)
    && Array.isArray(item.missing_pages)
    && item.missing_pages.every((page) => isInteger(page, 1))
    && isStringArray(item.issue_codes)
    && typeof item.assessment_started === 'boolean'
  )
  const pageIsValid = (item: unknown): boolean => (
    isRecord(item)
    && /^[0-9a-f]{64}$/.test(String(item.scan_page_id || ''))
    && /^[0-9a-f]{64}$/.test(String(item.upload_id || ''))
    && isInteger(item.upload_page_number, 1)
    && isNullableString(item.submission_id)
    && isNullableString(item.paper_instance_id)
    && (item.page_number === null || isInteger(item.page_number, 1))
    && (item.total_pages === null || isInteger(item.total_pages, 1))
    && isNullableString(item.issue_code)
    && ['assigned', 'unassigned', 'duplicate', 'conflict', 'replaced', 'dismissed']
      .includes(String(item.state))
    && [0, 90, 180, 270].includes(Number(item.rotation_degrees))
    && isNonEmptyString(item.preview_url)
  )
  const candidateIsValid = (item: unknown): boolean => (
    isRecord(item)
    && /^[0-9a-f]{64}$/.test(String(item.paper_instance_id || ''))
    && isNonEmptyString(item.student_id)
    && isNullableString(item.student_code)
    && isNullableString(item.student_name)
    && isInteger(item.series_version, 1)
    && isInteger(item.total_pages, 1)
  )
  if (
    !isRecord(value)
    || !/^[0-9a-f]{64}$/.test(String(value.batch_id || ''))
    || !/^[0-9a-f]{64}$/.test(String(value.paper_batch_id || ''))
    || !['manual_review', 'ready', 'cancelled'].includes(String(value.status))
    || !isInteger(value.revision, 1)
    || typeof value.duplicate_upload !== 'boolean'
    || !Array.isArray(value.submissions)
    || !value.submissions.every(submissionIsValid)
    || !Array.isArray(value.pages)
    || !value.pages.every(pageIsValid)
    || !Array.isArray(value.candidates)
    || !value.candidates.every(candidateIsValid)
    || !Array.isArray(value.history)
    || !isNonEmptyString(value.created_at)
    || !isNonEmptyString(value.updated_at)
  ) {
    throw new Error('Invalid training scan batch')
  }
  return value as unknown as TrainingScanBatch
}

function decodeTrainingAssessment(
  value: unknown,
): TrainingAssessmentOutcome {
  assertNoPathLikeKeys(value)
  const pointStateIsValid = (state: unknown): boolean => (
    state === null
    || state === undefined
    || ['met', 'not_met', 'uncertain', 'unreadable'].includes(String(state))
  )
  const expectedPointIsValid = (expected: unknown): boolean => (
    isRecord(expected)
    && isNonEmptyString(expected.point_id)
    && isNonEmptyString(expected.target)
    && isNonEmptyString(expected.observable_evidence)
    && isStringArray(expected.equivalent_rules)
    && isStringArray(expected.counterexamples)
  )
  const pointContent = (point: Record<string, unknown>): string | null => {
    if (typeof point.content === 'string') return point.content
    if (
      expectedPointIsValid(point.expected_point)
      && isRecord(point.expected_point)
    ) {
      return String(point.expected_point.target)
    }
    return null
  }
  const pointIsValid = (point: unknown): boolean => (
    isRecord(point)
    && isNonEmptyString(point.point_id)
    && pointContent(point) !== null
    && (
      point.expected_point === undefined
      || (
        expectedPointIsValid(point.expected_point)
        && isRecord(point.expected_point)
        && point.expected_point.point_id === point.point_id
      )
    )
    && pointStateIsValid(point.candidate_state)
    && pointStateIsValid(point.state)
    && isNullableString(point.evidence)
    && typeof point.teacher_locked === 'boolean'
    && isNullableString(point.teacher_reason)
    && isNullableString(point.actor_ref)
    && (
      point.teacher_locked
        ? isInteger(point.lock_revision, 1)
        : (
          point.lock_revision === null
          || point.lock_revision === undefined
          || point.lock_revision === 0
        )
    )
  )
  const questionIsValid = (question: unknown): boolean => (
    isRecord(question)
    && isNonEmptyString(question.task_item_code)
    && isInteger(question.item_order, 1)
    && isNonEmptyString(question.status)
    && isInteger(question.met_count)
    && isInteger(question.not_met_count)
    && isInteger(question.uncertain_count)
    && isInteger(question.unreadable_count)
    && isInteger(question.total_count, 1)
    && isNonEmptyString(question.review_status)
    && Array.isArray(question.review_points)
    && question.review_points.every(pointIsValid)
  )
  if (
    !isRecord(value)
    || !/^[0-9a-f]{64}$/.test(String(value.run_id || ''))
    || !/^[0-9a-f]{64}$/.test(String(value.submission_id || ''))
    || !isInteger(value.submission_revision, 1)
    || !isNonEmptyString(value.status)
    || !isInteger(value.request_count)
    || !isInteger(value.expected_question_count)
    || !isInteger(value.expected_point_count)
    || !isRecord(value.usage)
    || !isInteger(value.usage.prompt_tokens)
    || !isInteger(value.usage.completion_tokens)
    || !isInteger(value.usage.total_tokens)
    || !isInteger(value.latency_ms)
    || !isStringArray(value.issue_codes)
    || !Array.isArray(value.questions)
    || !value.questions.every(questionIsValid)
    || !isInteger(value.review_revision, 1)
    || !isNonEmptyString(value.control_state)
    || !isNonEmptyString(value.workflow_status)
    || typeof value.action_message !== 'string'
    || !Array.isArray(value.attempts)
    || !value.attempts.every(isRecord)
  ) {
    throw new Error('Invalid training assessment')
  }
  return {
    ...value,
    questions: value.questions.map((question: Record<string, unknown>) => {
      const reviewPoints = (
        question.review_points as Record<string, unknown>[]
      )
      return {
        ...question,
        review_points: reviewPoints.map((point) => ({
          ...point,
          content: pointContent(point),
          lock_revision: point.lock_revision ?? 0,
        })),
      }
    }),
  } as unknown as TrainingAssessmentOutcome
}

function decodeTrainingFeedback(value: unknown): TrainingFeedback {
  assertNoPathLikeKeys(value)
  const changesExamScore = isRecord(value) && isRecord(value.safety)
    ? value.safety.changes_exam_score ?? value.safety.changes_v1
    : undefined
  if (
    !isRecord(value)
    || value.schema_version !== 'training-feedback-v1'
    || !/^[0-9a-f]{64}$/.test(String(value.feedback_id || ''))
    || !/^[0-9a-f]{64}$/.test(String(value.submission_id || ''))
    || !isInteger(value.submission_revision, 1)
    || !isInteger(value.source_review_revision, 1)
    || !['publication_pending', 'partial', 'complete', 'withdrawn']
      .includes(String(value.status))
    || !isRecord(value.student)
    || !isRecord(value.summary)
    || !isInteger(value.summary.published_question_count)
    || !isInteger(value.summary.ready_question_count)
    || !isInteger(value.summary.total_question_count)
    || !isInteger(value.summary.pending_outbox_count)
    || typeof value.summary.message !== 'string'
    || !Array.isArray(value.questions)
    || !value.questions.every(isRecord)
    || !Array.isArray(value.mastery_changes)
    || !value.mastery_changes.every(isRecord)
    || !isRecord(value.next_round)
    || !isNonEmptyString(value.next_round.status)
    || typeof value.next_round.message !== 'string'
    || !Array.isArray(value.next_round.changes)
    || !value.next_round.changes.every(isRecord)
    || !Array.isArray(value.timeline)
    || !value.timeline.every(isRecord)
    || !isRecord(value.safety)
    || typeof value.safety.is_exam_score !== 'boolean'
    || typeof changesExamScore !== 'boolean'
    || typeof value.safety.auto_paper_created !== 'boolean'
    || typeof value.safety.auto_printed !== 'boolean'
    || !/^[0-9a-f]{64}$/.test(String(value.evidence_version || ''))
  ) {
    throw new Error('Invalid training feedback')
  }
  return {
    ...value,
    safety: { ...value.safety, changes_exam_score: changesExamScore },
  } as unknown as TrainingFeedback
}

function decodeTrainingEvidenceReplay(value: unknown): {
  examined_count: number
  delivered_count: number
  failed_count: number
  feedbacks: TrainingFeedback[]
} {
  if (
    !isRecord(value)
    || !isInteger(value.examined_count)
    || !isInteger(value.delivered_count)
    || !isInteger(value.failed_count)
    || !Array.isArray(value.feedbacks)
  ) {
    throw new Error('Invalid training evidence replay')
  }
  return {
    examined_count: value.examined_count,
    delivered_count: value.delivered_count,
    failed_count: value.failed_count,
    feedbacks: value.feedbacks.map(decodeTrainingFeedback),
  }
}

async function fileSha256(file: File): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest(
    'SHA-256',
    await file.arrayBuffer(),
  )
  return [...new Uint8Array(digest)]
    .map((item) => item.toString(16).padStart(2, '0'))
    .join('')
}

export interface TrainingPendingItem {
  draft_id: string
  draft_name: string
  scan_page_count: number
  review_submission_count: number
  publish_submission_count: number
}
export interface TrainingPendingSummary { items: TrainingPendingItem[] }

export const trainingApi = {
  async getPendingSummary(signal?: AbortSignal): Promise<TrainingPendingSummary> {
    return apiClient.request('/api/training/pending-summary', { signal, decode(value) {
      if (!isRecord(value) || Object.keys(value).length !== 1 || !Array.isArray(value.items)
        || !value.items.every(item => isRecord(item) && Object.keys(item).length === 5
          && typeof item.draft_id === 'string' && /^[0-9a-f]{64}$/.test(item.draft_id)
          && typeof item.draft_name === 'string' && item.draft_name.length > 0
          && [item.scan_page_count, item.review_submission_count, item.publish_submission_count]
            .every(count => Number.isSafeInteger(count) && Number(count) >= 0))) {
        throw new Error('Invalid training pending summary')
      }
      return value as unknown as TrainingPendingSummary
    } })
  },
  exportHandout(draftId: string, body: { expected_revision: number; request_token: string }): Promise<JobResponse> {
    return apiClient.request(`/api/training/personalized-drafts/${draftId}/handout-exports`, {
      method: 'POST', body, decode: decodeJobResponse,
    })
  },
  getHandoutExportByRequest(draftId: string, token: string): Promise<JobResponse> {
    return apiClient.request(`/api/training/personalized-drafts/${draftId}/handout-exports/by-request/${token}`, {
      decode: decodeJobResponse,
    })
  },
  overview(
    body: TrainingOverviewRequest,
    signal?: AbortSignal,
  ): Promise<TrainingOverview> {
    return apiClient.request('/api/training/overview', {
      method: 'POST',
      body,
      decode: decodeTrainingOverview,
      signal,
      timeoutMs: 120_000,
    })
  },

  diagnose(
    body: TrainingDiagnosisRequest,
    signal?: AbortSignal,
  ): Promise<TrainingDiagnosis> {
    return apiClient.request('/api/training/diagnosis', {
      method: 'POST',
      body,
      decode: decodeTrainingDiagnosis,
      signal,
      // Group analysis also evaluates the common question pool for the roster.
      timeoutMs: body.grouping ? 120_000 : 30_000,
    })
  },

  diagnoseDisplay(body: TrainingDiagnosisRequest, signal?: AbortSignal): Promise<TrainingDisplayDiagnosis> {
    const request = { ...body }
    delete request.include_student_detail
    return apiClient.request('/api/training/diagnosis', {
      method: 'POST', body: { ...request, response_mode: 'display' },
      decode: decodeTrainingDisplayDiagnosis, signal, timeoutMs: body.grouping ? 120_000 : 30_000,
    })
  },

  createPersonalizedDraft(
    body: PersonalizedRecommendationCreateRequest,
    signal?: AbortSignal,
  ): Promise<PersonalizedRecommendationDraft> {
    return apiClient.request('/api/training/personalized-drafts', {
      method: 'POST',
      body,
      decode: decodePersonalizedRecommendationDraft,
      signal,
      timeoutMs: 120_000,
    })
  },

  getPersonalizedDraftByRequest(
    requestToken: string,
    signal?: AbortSignal,
  ): Promise<PersonalizedRecommendationDraft> {
    return apiClient.request(`/api/training/personalized-drafts/by-request/${requestToken}`, {
      decode: decodePersonalizedRecommendationDraft,
      signal,
      timeoutMs: 30_000,
    })
  },

  getPersonalizedDraft(
    draftId: string,
    signal?: AbortSignal,
  ): Promise<PersonalizedRecommendationDraft> {
    return apiClient.request(`/api/training/personalized-drafts/${draftId}`, {
      decode: decodePersonalizedRecommendationDraft,
      signal,
      timeoutMs: 30_000,
    })
  },

  editPersonalizedDraft(
    draftId: string,
    body: PersonalizedRecommendationEditRequest,
    signal?: AbortSignal,
  ): Promise<PersonalizedRecommendationDraft> {
    return apiClient.request(`/api/training/personalized-drafts/${draftId}/edits`, {
      method: 'POST',
      body,
      decode: decodePersonalizedRecommendationDraft,
      signal,
      timeoutMs: 30_000,
    })
  },

  createPaperInstance(
    draftId: string,
    body: {
      operation_token: string
      expected_draft_revision: number
      student_id: string
      context_window_tokens: 32768 | 65536 | 128000
    },
  ): Promise<PersonalizedPaperInstance> {
    return apiClient.request(
      `/api/training/personalized-drafts/${draftId}/paper-instances`,
      {
        method: 'POST',
        body,
        decode: decodePersonalizedPaperInstance,
        timeoutMs: 120_000,
      },
    )
  },

  createPaperBatch(
    draftId: string,
    body: {
      operation_token: string
      expected_draft_revision: number
      student_ids: string[]
      context_window_tokens: 32768 | 65536 | 128000
      direct_freeze?: boolean
    },
  ): Promise<PersonalizedPaperBatch> {
    return apiClient.request(
      `/api/training/personalized-drafts/${draftId}/paper-batches`,
      {
        method: 'POST',
        body,
        decode: decodePersonalizedPaperBatch,
        timeoutMs: 600_000,
      },
    )
  },

  async listPaperBatches(
    draftId: string,
  ): Promise<PersonalizedPaperBatch[]> {
    const result = await apiClient.request(
      `/api/training/personalized-drafts/${draftId}/paper-batches`,
      {
        decode: decodePersonalizedPaperBatchList,
        timeoutMs: 30_000,
      },
    )
    return result.items
  },

  cancelPaperBatch(
    batchRunId: string,
    operationToken: string,
  ): Promise<PersonalizedPaperBatch> {
    return apiClient.request(
      `/api/training/paper-batches/${batchRunId}/cancel`,
      {
        method: 'POST',
        body: { operation_token: operationToken },
        decode: decodePersonalizedPaperBatch,
        timeoutMs: 30_000,
      },
    )
  },

  retryPaperBatch(
    batchRunId: string,
    studentIds: string[] = [],
  ): Promise<PersonalizedPaperBatch> {
    return apiClient.request(
      `/api/training/paper-batches/${batchRunId}/retry`,
      {
        method: 'POST',
        body: { student_ids: studentIds },
        decode: decodePersonalizedPaperBatch,
        timeoutMs: 600_000,
      },
    )
  },

  async listPaperInstances(
    draftId: string,
  ): Promise<PersonalizedPaperInstance[]> {
    const result = await apiClient.request(
      `/api/training/personalized-drafts/${draftId}/paper-instances`,
      {
        decode: decodePersonalizedPaperList,
        timeoutMs: 30_000,
      },
    )
    return result.items
  },

  async freezePaperInstance(
    instance: PersonalizedPaperInstance,
    file: File,
    operationToken: string,
  ): Promise<PersonalizedPaperInstance> {
    const digest = await fileSha256(file)
    return apiClient.request(
      `/api/training/paper-instances/${instance.paper_instance_id}/freeze`
      + `?expected_revision=${instance.revision}`,
      {
        method: 'POST',
        rawBody: file,
        headers: {
          'content-type': (
            'application/vnd.openxmlformats-officedocument.'
            + 'wordprocessingml.document'
          ),
          'x-operation-token': operationToken,
          'x-content-sha256': digest,
          'x-upload-filename': encodeURIComponent(file.name),
        },
        decode: decodePersonalizedPaperInstance,
        timeoutMs: 180_000,
      },
    )
  },

  downloadPaperArtifact(path: string) {
    return apiClient.download(path, { timeoutMs: 60_000 })
  },

  async listTrainingScanBatches(paperBatchId: string): Promise<TrainingScanBatchSummary[]> {
    const result = await apiClient.request(
      `/api/training/scan-batches?paper_batch_id=${encodeURIComponent(paperBatchId)}`,
      { decode: decodeTrainingScanBatchList, timeoutMs: 30_000 },
    )
    return result.items
  },

  createTrainingScanBatch(
    paperInstanceIds: string[],
    operationToken: string,
  ): Promise<TrainingScanBatch> {
    return apiClient.request('/api/training/scan-batches', {
      method: 'POST',
      body: {
        operation_token: operationToken,
        paper_instance_ids: paperInstanceIds,
      },
      decode: decodeTrainingScanBatch,
      timeoutMs: 30_000,
    })
  },

  getTrainingScanBatch(batchId: string): Promise<TrainingScanBatch> {
    return apiClient.request(`/api/training/scan-batches/${batchId}`, {
      decode: decodeTrainingScanBatch,
      timeoutMs: 30_000,
    })
  },

  async uploadTrainingScan(
    batch: TrainingScanBatch,
    file: File,
    operationToken: string,
  ): Promise<TrainingScanBatch> {
    const digest = await fileSha256(file)
    const suffix = file.name.toLocaleLowerCase()
    const mediaType = file.type || (
      suffix.endsWith('.pdf')
        ? 'application/pdf'
        : suffix.endsWith('.png')
          ? 'image/png'
          : 'image/jpeg'
    )
    return apiClient.request(
      `/api/training/scan-batches/${batch.batch_id}/uploads`
      + `?expected_revision=${batch.revision}`,
      {
        method: 'POST',
        rawBody: file,
        headers: {
          'content-type': mediaType,
          'x-operation-token': operationToken,
          'x-content-sha256': digest,
          'x-upload-filename': encodeURIComponent(file.name),
        },
        decode: decodeTrainingScanBatch,
        timeoutMs: 180_000,
      },
    )
  },

  resolveTrainingScanPage(
    batch: TrainingScanBatch,
    page: TrainingScanPage,
    body: {
      operation_token: string
      action: 'match' | 'replace' | 'dismiss'
      paper_instance_id?: string
      page_number?: number
    },
  ): Promise<TrainingScanBatch> {
    return apiClient.request(
      `/api/training/scan-batches/${batch.batch_id}`
      + `/pages/${page.scan_page_id}/resolve`,
      {
        method: 'POST',
        body: {
          ...body,
          expected_revision: batch.revision,
        },
        decode: decodeTrainingScanBatch,
        timeoutMs: 30_000,
      },
    )
  },

  cancelTrainingSubmission(
    batch: TrainingScanBatch,
    submissionId: string,
    operationToken: string,
  ): Promise<TrainingScanBatch> {
    return apiClient.request(
      `/api/training/submissions/${submissionId}/cancel`,
      {
        method: 'POST',
        body: {
          operation_token: operationToken,
          expected_revision: batch.revision,
          reason: '教师确认本次不提交该训练卷',
        },
        decode: decodeTrainingScanBatch,
        timeoutMs: 30_000,
      },
    )
  },

  startTrainingAssessment(
    submissionId: string,
    expectedRevision: number,
  ): Promise<TrainingAssessmentOutcome> {
    return apiClient.request(
      `/api/training/submissions/${submissionId}/assessment`,
      {
        method: 'POST',
        body: { expected_revision: expectedRevision },
        decode: decodeTrainingAssessment,
        timeoutMs: 180_000,
      },
    )
  },

  getTrainingAssessment(
    submissionId: string,
    submissionRevision: number,
  ): Promise<TrainingAssessmentOutcome> {
    return apiClient.request(
      `/api/training/submissions/${submissionId}/assessment`
      + `?submission_revision=${submissionRevision}`,
      {
        decode: decodeTrainingAssessment,
        timeoutMs: 30_000,
      },
    )
  },

  reviewTrainingPoint(
    assessment: TrainingAssessmentOutcome,
    body: {
      operation_token: string
      task_item_code: string
      point_id: string
      final_state: TrainingPointState
      teacher_evidence: string
      teacher_reason: string
    },
  ): Promise<TrainingAssessmentOutcome> {
    return apiClient.request(
      `/api/training/submissions/${assessment.submission_id}`
      + '/assessment/reviews',
      {
        method: 'POST',
        body: {
          ...body,
          submission_revision: assessment.submission_revision,
          expected_review_revision: assessment.review_revision,
        },
        decode: decodeTrainingAssessment,
        timeoutMs: 30_000,
      },
    )
  },

  controlTrainingAssessment(
    assessment: TrainingAssessmentOutcome,
    body: {
      operation_token: string
      action: 'pause' | 'resume' | 'cancel' | 'recover' | 'retry'
      reason: string
    },
  ): Promise<TrainingAssessmentOutcome> {
    return apiClient.request(
      `/api/training/submissions/${assessment.submission_id}`
      + '/assessment/actions',
      {
        method: 'POST',
        body: {
          ...body,
          submission_revision: assessment.submission_revision,
          expected_review_revision: assessment.review_revision,
        },
        decode: decodeTrainingAssessment,
        timeoutMs: 180_000,
      },
    )
  },

  syncTrainingEvidence(
    assessment: TrainingAssessmentOutcome,
    action: 'publish' | 'withdraw',
    operationToken: string,
  ): Promise<TrainingFeedback> {
    return apiClient.request(
      `/api/training/submissions/${assessment.submission_id}/evidence`,
      {
        method: 'POST',
        body: {
          operation_token: operationToken,
          submission_revision: assessment.submission_revision,
          expected_review_revision: assessment.review_revision,
          action,
          reason: action === 'publish'
            ? '教师确认发布当前已完成题目的训练证据'
            : '教师确认撤回本次训练证据',
        },
        decode: decodeTrainingFeedback,
        timeoutMs: 60_000,
      },
    )
  },

  getTrainingFeedback(
    submissionId: string,
    submissionRevision: number,
  ): Promise<TrainingFeedback> {
    return apiClient.request(
      `/api/training/submissions/${submissionId}/feedback`
      + `?submission_revision=${submissionRevision}`,
      {
        decode: decodeTrainingFeedback,
        timeoutMs: 30_000,
      },
    )
  },

  replayTrainingEvidence(): Promise<{
    examined_count: number
    delivered_count: number
    failed_count: number
    feedbacks: TrainingFeedback[]
  }> {
    return apiClient.request('/api/training/evidence/replay', {
      method: 'POST',
      body: { max_items: 100 },
      decode: decodeTrainingEvidenceReplay,
      timeoutMs: 60_000,
    })
  },
}


export function trainingTargetNodeKinds(kind: OverviewTargetKind | undefined): string[] {
  switch (kind) {
    case 'type': return ['type']
    case 'knowledge': return ['topic']
    case 'mixed': return ['type', 'topic', 'skill']
    case 'skill':
    case undefined: return ['skill', 'topic']
    default: { const exhaustive: never = kind; return exhaustive }
  }
}

export function trainingTargetLabelForKey(diagnosis: Pick<TrainingReadDiagnosis, 'knowledge_catalog' | 'target_kind'> | null | undefined, key: string): string {
  const node = diagnosis?.knowledge_catalog?.find(item => item.knowledge_key === key)
  if (node?.node_kind === 'type' || /_t\d{2}$/.test(key)) return '题型'
  if (node?.node_kind === 'skill' || key.startsWith('sk_')) return '技能'
  if (node?.node_kind === 'topic' || key.startsWith('kp_') || diagnosis?.target_kind === 'knowledge') return '知识点'
  return diagnosis?.target_kind === 'type' ? '题型' : '训练目标'
}
