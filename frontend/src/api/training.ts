import { apiClient } from './client'
import {
  decodeTrainingTaskDetail,
  type TrainingTaskDetail,
} from './exports'
import { assertNoPathLikeKeys, isRecord } from './validation'

export type TrainingStudentScopeMode = 'student' | 'selected' | 'class'
export type TrainingExamScopeMode = 'current' | 'manual' | 'cross_exam'
export type TrainingVariantMode = 'individual' | 'auto_group'
export type TrainingStage = 'direct' | 'prerequisite' | 'transfer'

export interface TrainingStudentScopeRequest {
  mode: TrainingStudentScopeMode
  student_ids: string[]
  class_id?: string
}

export interface TrainingExamScopeRequest {
  mode: TrainingExamScopeMode
  session_ids: number[]
}

export interface TrainingDiagnosisRequest {
  scope: TrainingStudentScopeRequest
  exam_scope: TrainingExamScopeRequest
}

export interface TrainingStageRatios {
  direct: number
  prerequisite: number
  transfer: number
}

export interface TrainingPlanRequest extends TrainingDiagnosisRequest {
  variant_mode: TrainingVariantMode
  teacher_groups?: Record<string, string[]>
  question_count: number
  stage_ratios: TrainingStageRatios
  exclude_current_exam_originals: boolean
}

export interface TrainingTaskConfirmRequest extends TrainingPlanRequest {
  confirmation_id: string
  expected_plan_revision: string
}

export interface PersonalizedRecommendationCreateRequest
  extends TrainingDiagnosisRequest {
  request_token: string
  question_count: number
  expected_minutes: number
  difficulty_min: number
  difficulty_max: number
  stage_ratios: TrainingStageRatios
  target_names: string[]
  exclude_current_exam_originals: boolean
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
  item_id: string
  item_order: number
  slot: number
  question_id: number
  question_number: string
  stage: TrainingStage
  target: Record<string, unknown>
  matched_key: string
  matched_name: string
  relation?: PersonalizedRecommendationRelation | null
  criterion_version_id: string
  criterion_point_count: number
  difficulty: number
  estimated_minutes: number
  source_paper: string
  reason: string
  locked: boolean
  replacement_history: Array<Record<string, unknown>>
}

export interface PersonalizedRecommendationStudent {
  student_id: string
  student_code: string
  student_name: string
  class_id: string
  selection_mode: 'mastery_targeted' | 'maintenance_fallback'
  targets: Array<Record<string, unknown>>
  items: PersonalizedRecommendationItem[]
  shortages: Array<Record<string, unknown>>
  warnings: string[]
  estimated_minutes: number
}

export interface PersonalizedRecommendationDraft {
  draft_id: string
  status: 'draft' | 'reviewed'
  revision: number
  result_version: string
  engine_version: string
  source_version: string
  config: Record<string, unknown>
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
  downloads: {
    review_docx?: string | null
    reviewed_docx?: string | null
    frozen_pdf?: string | null
  }
  error_code?: string | null
  created_at: string
  frozen_at?: string | null
}

export interface TrainingEvidenceReference {
  session_id: number
  session_name: string
  question_id: string
  bank_question_id: number
  score_awarded: number
  full_score: number
  score_rate?: number | null
}

export interface TrainingWeakPoint {
  knowledge_key: string
  knowledge_point: string
  mastery: number
  score_sum: number
  full_score_sum: number
  deduction_count: number
  evidence_count: number
  exam_count: number
  source_question_refs: TrainingEvidenceReference[]
  actionable_reasons: string[]
  tag_context: Record<string, string[]>
  error_counts: Record<string, Record<string, number>>
}

export interface TrainingStudentProfile {
  student_id: string
  student_code: string
  student_name: string
  class_id: string
  score_rate?: number | null
  weak_points: TrainingWeakPoint[]
}

export interface TrainingDiagnosis {
  scope: {
    mode: TrainingStudentScopeMode
    student_ids: string[]
    class_id?: string | null
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
}

export interface TrainingPlanItem {
  question_id: number
  item_order: number
  stage: TrainingStage
  knowledge_key: string
  knowledge_point: string
  match_kind: 'exact'
  reason: string
  recommend_score: number
  score_components: Record<string, number>
  tag_matches: Record<string, string[]>
  tags: Record<string, string[]>
  warnings: string[]
  question_fingerprint: string
  question_text: string
  question_number: string
  difficulty: number | string | null
  source_paper: string
  frequency: Record<string, unknown>
}

export interface TrainingPlanShortage {
  stage: TrainingStage
  requested_count: number
  selected_count: number
  missing_count: number
  decision_required?: boolean
  knowledge_points?: string[]
}

export interface TrainingPlanVariant {
  variant_key: string
  variant_type: 'individual' | 'group'
  student_ids: string[]
  grouping_reason: Record<string, unknown>
  diagnosis_snapshot: Record<string, unknown>
  items: TrainingPlanItem[]
  stage_counts: Record<TrainingStage, number>
  shortages: TrainingPlanShortage[]
  warnings: string[]
  dedupe_summary: {
    removed_count: number
    reason_counts: Record<string, number>
  }
  generation_config: Record<string, unknown>
}

export interface TrainingPlan {
  scope_snapshot: Record<string, unknown>
  exam_scope: Record<string, unknown>
  diagnosis_snapshot: Record<string, unknown>
  generation_config: Record<string, unknown>
  variant_mode: TrainingVariantMode
  variants: TrainingPlanVariant[]
  warnings: string[]
  ungrouped_students: string[]
  teacher_override: {
    allowed: boolean
    applied: boolean
    assignments: Record<string, string[]>
  }
}

export interface TrainingPlanResponse {
  plan_revision: string
  plan: TrainingPlan
}

function isInteger(value: unknown, minimum = 0): value is number {
  return Number.isSafeInteger(value) && Number(value) >= minimum
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isRate(value: unknown): boolean {
  return isFiniteNumber(value) && value >= 0 && value <= 1
}

function isPercentage(value: unknown): boolean {
  return isFiniteNumber(value) && value >= 0 && value <= 100
}

function isNullablePercentage(value: unknown): boolean {
  return value === null || value === undefined || isPercentage(value)
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && Boolean(value.trim())
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
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
  return value === 'student' || value === 'selected' || value === 'class'
}

function isExamScopeMode(value: unknown): value is TrainingExamScopeMode {
  return value === 'current' || value === 'manual' || value === 'cross_exam'
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
  )
}

function isWeakPoint(value: unknown): value is TrainingWeakPoint {
  return (
    isRecord(value)
    && isNonEmptyString(value.knowledge_key)
    && isNonEmptyString(value.knowledge_point)
    && isRate(value.mastery)
    && isFiniteNumber(value.score_sum)
    && isFiniteNumber(value.full_score_sum)
    && value.full_score_sum >= 0
    && isInteger(value.deduction_count)
    && isInteger(value.evidence_count)
    && isInteger(value.exam_count)
    && Array.isArray(value.source_question_refs)
    && value.source_question_refs.every(isEvidenceReference)
    && isStringArray(value.actionable_reasons)
    && isStringListRecord(value.tag_context)
    && isNestedCountRecord(value.error_counts)
  )
}

function isStudentProfile(value: unknown): value is TrainingStudentProfile {
  return (
    isRecord(value)
    && isNonEmptyString(value.student_id)
    && typeof value.student_code === 'string'
    && typeof value.student_name === 'string'
    && typeof value.class_id === 'string'
    && isNullablePercentage(value.score_rate)
    && Array.isArray(value.weak_points)
    && value.weak_points.every(isWeakPoint)
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
  if (
    !isRecord(value)
    || value.diagnosis_identity !== 'question_tag'
    || !isNormalizedScope(value.scope)
    || !isNormalizedExamScope(value.exam_scope)
    || !Array.isArray(value.students)
    || !value.students.every(isStudentProfile)
    || !isCoverage(value.coverage)
    || !isPositiveIntegerArray(value.confirmed_concept_ids)
    || !isStringArray(value.suggested_terms)
    || !isStringArray(value.unmapped_terms)
    || !isStringArray(value.warnings)
  ) {
    throw new Error('Invalid training diagnosis')
  }
  return value as unknown as TrainingDiagnosis
}

function isPlanItem(value: unknown): value is TrainingPlanItem {
  return (
    isRecord(value)
    && isInteger(value.question_id, 1)
    && isInteger(value.item_order, 1)
    && isStage(value.stage)
    && isNonEmptyString(value.knowledge_key)
    && isNonEmptyString(value.knowledge_point)
    && value.match_kind === 'exact'
    && isNonEmptyString(value.reason)
    && isFiniteNumber(value.recommend_score)
    && isRecord(value.score_components)
    && Object.values(value.score_components).every(isFiniteNumber)
    && isStringListRecord(value.tag_matches)
    && isStringListRecord(value.tags)
    && isStringArray(value.warnings)
    && typeof value.question_fingerprint === 'string'
    && typeof value.question_text === 'string'
    && typeof value.question_number === 'string'
    && (
      value.difficulty === null
      || value.difficulty === undefined
      || isFiniteNumber(value.difficulty)
      || typeof value.difficulty === 'string'
    )
    && typeof value.source_paper === 'string'
    && isRecord(value.frequency)
  )
}

function isShortage(value: unknown): value is TrainingPlanShortage {
  return (
    isRecord(value)
    && isStage(value.stage)
    && isInteger(value.requested_count)
    && isInteger(value.selected_count)
    && isInteger(value.missing_count)
    && value.selected_count + value.missing_count === value.requested_count
    && (
      value.decision_required === undefined
      || typeof value.decision_required === 'boolean'
    )
    && (
      value.knowledge_points === undefined
      || isStringArray(value.knowledge_points)
    )
  )
}

function isStageCounts(value: unknown): value is Record<TrainingStage, number> {
  return (
    isRecord(value)
    && isInteger(value.direct)
    && isInteger(value.prerequisite)
    && isInteger(value.transfer)
  )
}

function isPlanVariant(value: unknown): value is TrainingPlanVariant {
  return (
    isRecord(value)
    && isNonEmptyString(value.variant_key)
    && (value.variant_type === 'individual' || value.variant_type === 'group')
    && isStringArray(value.student_ids)
    && isRecord(value.grouping_reason)
    && isRecord(value.diagnosis_snapshot)
    && Array.isArray(value.items)
    && value.items.every(isPlanItem)
    && isStageCounts(value.stage_counts)
    && Array.isArray(value.shortages)
    && value.shortages.every(isShortage)
    && isStringArray(value.warnings)
    && isRecord(value.dedupe_summary)
    && isInteger(value.dedupe_summary.removed_count)
    && isCountRecord(value.dedupe_summary.reason_counts)
    && isRecord(value.generation_config)
  )
}

function isTeacherOverride(
  value: unknown,
): value is TrainingPlan['teacher_override'] {
  return (
    isRecord(value)
    && typeof value.allowed === 'boolean'
    && typeof value.applied === 'boolean'
    && isStringListRecord(value.assignments)
  )
}

export function decodeTrainingPlanResponse(value: unknown): TrainingPlanResponse {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || typeof value.plan_revision !== 'string'
    || !/^[0-9a-f]{64}$/.test(value.plan_revision)
    || !isRecord(value.plan)
    || !isRecord(value.plan.scope_snapshot)
    || !isRecord(value.plan.exam_scope)
    || !isRecord(value.plan.diagnosis_snapshot)
    || value.plan.diagnosis_snapshot.diagnosis_identity !== 'question_tag'
    || !isRecord(value.plan.generation_config)
    || (
      value.plan.variant_mode !== 'individual'
      && value.plan.variant_mode !== 'auto_group'
    )
    || !Array.isArray(value.plan.variants)
    || !value.plan.variants.every(isPlanVariant)
    || !isStringArray(value.plan.warnings)
    || !isStringArray(value.plan.ungrouped_students)
    || !isTeacherOverride(value.plan.teacher_override)
  ) {
    throw new Error('Invalid training plan')
  }
  return value as unknown as TrainingPlanResponse
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
    && isStage(value.stage)
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
    && isInteger(value.difficulty, 1)
    && value.difficulty <= 10
    && isInteger(value.estimated_minutes, 1)
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
    )
    && Array.isArray(value.targets)
    && value.targets.every(isRecord)
    && Array.isArray(value.items)
    && value.items.every(isRecommendationItem)
    && Array.isArray(value.shortages)
    && value.shortages.every(isRecord)
    && isStringArray(value.warnings)
    && isInteger(value.estimated_minutes)
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

async function fileSha256(file: File): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest(
    'SHA-256',
    await file.arrayBuffer(),
  )
  return [...new Uint8Array(digest)]
    .map((item) => item.toString(16).padStart(2, '0'))
    .join('')
}

export const trainingApi = {
  diagnose(
    body: TrainingDiagnosisRequest,
    signal?: AbortSignal,
  ): Promise<TrainingDiagnosis> {
    return apiClient.request('/api/training/diagnosis', {
      method: 'POST',
      body,
      decode: decodeTrainingDiagnosis,
      signal,
      timeoutMs: 30_000,
    })
  },

  preview(
    body: TrainingPlanRequest,
    signal?: AbortSignal,
  ): Promise<TrainingPlanResponse> {
    return apiClient.request('/api/training/plans/preview', {
      method: 'POST',
      body,
      decode: decodeTrainingPlanResponse,
      signal,
      timeoutMs: 30_000,
    })
  },

  confirm(
    body: TrainingTaskConfirmRequest,
    signal?: AbortSignal,
  ): Promise<TrainingTaskDetail> {
    return apiClient.request('/api/training/tasks', {
      method: 'POST',
      body,
      decode: decodeTrainingTaskDetail,
      signal,
      timeoutMs: 30_000,
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
}
