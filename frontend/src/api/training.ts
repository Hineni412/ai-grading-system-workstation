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
}
