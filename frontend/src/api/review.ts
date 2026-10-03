import { apiClient } from './client'
import { isNullableString, isRecord } from './validation'

export type ReviewScope =
  | 'teacher_pending'
  | 'ungraded'
  | 'ai_review'
  | 'teacher_final'
  | 'all'
export type ReviewScoreStatus =
  | 'ungraded'
  | 'ai_ready'
  | 'ai_review'
  | 'teacher_final'
  | 'failed'
export type ReviewScoreSource = 'none' | 'ai' | 'teacher'

export interface ReviewQuestionSummary {
  question_id: string
  question_type: string | null
  total_count: number
  needs_review_count: number
  ungraded_count?: number
  failed_count?: number
  ai_ready_count?: number
  teacher_confirmed_count?: number
  max_score: number
}

export interface ReviewMediaLinks {
  originals_available?: boolean
  crop_url: string | null
  original_front_url: string | null
  original_back_url: string | null
  annotated_front_url: string | null
  annotated_back_url: string | null
}

/**
 * Legacy-compatible public shape used by existing fixtures and local callers.
 * API payloads are normalized to ResolvedReviewItem before entering the queue.
 */
export interface ReviewItem {
  review_item_id?: string
  revision?: number
  session_id: number
  student_id?: number
  result_id: number
  detail_id: number
  question_id: string
  student_code: string | null
  student_name: string
  class_name: string | null
  score_awarded: number
  max_score: number
  deduction_reason: string | null
  error_category: string | null
  error_summary: string | null
  confidence_score: number | null
  needs_review: boolean
  score_status?: ReviewScoreStatus
  score_source?: ReviewScoreSource
  teacher_locked?: boolean
  candidate_scores: Record<string, unknown>[]
  metadata: Record<string, unknown>
  media: ReviewMediaLinks
}

export interface ResolvedReviewItem extends Omit<
  ReviewItem,
  | 'review_item_id'
  | 'revision'
  | 'student_id'
  | 'result_id'
  | 'detail_id'
  | 'score_awarded'
  | 'score_status'
  | 'score_source'
  | 'teacher_locked'
> {
  review_item_id: string
  revision: number
  student_id: number
  result_id: number | null
  detail_id: number | null
  score_awarded: number | null
  score_status: ReviewScoreStatus
  score_source: ReviewScoreSource
  teacher_locked: boolean
}

export type ReviewItemLike = ReviewItem | ResolvedReviewItem

export interface ReviewQuestionListResponse { items: ReviewQuestionSummary[]; total: number }
export interface ReviewItemListResponse { items: ReviewItemLike[]; total: number }

export interface FetchReviewQuestionsOptions {
  scope?: ReviewScope
  signal?: AbortSignal
}

export interface FetchReviewItemsOptions {
  scope?: ReviewScope
  needsReviewOnly?: boolean
  signal?: AbortSignal
}

export interface ReviewRubricPoint {
  part_id: string
  part_label: string
  step_id: string
  core_goal: string
  score: number
  standard_answer: string
  accepted_answers: string[]
  match_rule: string
  required_elements: string[]
  deduction_rules: string[]
  answer_only_max_score: number | null
  require_final_answer: boolean | null
  final_answer_rule: string
}

export interface ReviewRubricSection {
  question_id: string
  parent_question_id: string
  question_type: string | null
  max_score: number
  knowledge_labels: string[]
  points: ReviewRubricPoint[]
}

export interface ReviewConfirmInput {
  review_item_id?: string
  expected_revision?: number
  student_id?: number
  result_id: number | null
  detail_id: number | null
  score_awarded: number
  deduction_reason?: string
  step_scores?: { part_id: string; step_id: string; score_awarded: number; teacher_note?: string | null; carried_error_from?: string | null }[]
}

export interface ReviewAnnotationOutcome {
  result_id: number
  status: 'succeeded' | 'retry_required' | 'on_demand'
  message?: string
}

export interface ReviewConfirmResponse {
  updated_details: number
  updated_results: number
  annotation_outcomes: ReviewAnnotationOutcome[]
}

const isNonnegativeInteger = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0
const isFiniteNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const isApiUrl = (value: unknown): value is string => typeof value === 'string' && /^\/api\//.test(value)
const isNullableApiUrl = (value: unknown): value is string | null => value === null || isApiUrl(value)
const isNullablePositiveInteger = (value: unknown): value is number | null =>
  value === null || (isNonnegativeInteger(value) && value > 0)
const isReviewScoreStatus = (value: unknown): value is ReviewScoreStatus =>
  value === 'ungraded' || value === 'ai_ready' || value === 'ai_review'
  || value === 'teacher_final' || value === 'failed'
const isReviewScoreSource = (value: unknown): value is ReviewScoreSource =>
  value === 'none' || value === 'ai' || value === 'teacher'

function isReviewQuestion(value: unknown): value is ReviewQuestionSummary {
  return isRecord(value)
    && typeof value.question_id === 'string' && value.question_id.trim().length > 0
    && isNullableString(value.question_type)
    && isNonnegativeInteger(value.total_count)
    && isNonnegativeInteger(value.needs_review_count)
    && Number(value.needs_review_count) <= Number(value.total_count)
    && (value.ungraded_count === undefined
      || (isNonnegativeInteger(value.ungraded_count)
        && Number(value.ungraded_count) <= Number(value.total_count)))
    && (value.failed_count === undefined
      || (isNonnegativeInteger(value.failed_count)
        && Number(value.failed_count) <= Number(value.total_count)))
    && (value.ai_ready_count === undefined
      || (isNonnegativeInteger(value.ai_ready_count)
        && Number(value.ai_ready_count) <= Number(value.total_count)))
    && (value.teacher_confirmed_count === undefined
      || (isNonnegativeInteger(value.teacher_confirmed_count)
        && Number(value.teacher_confirmed_count) <= Number(value.total_count)))
    && isFiniteNumber(value.max_score) && value.max_score >= 0
}

function isReviewMedia(value: unknown): value is ReviewMediaLinks {
  return isRecord(value)
    && (value.originals_available === undefined || typeof value.originals_available === 'boolean')
    && (value.originals_available === false ? value.crop_url === null && value.original_front_url === null : isApiUrl(value.crop_url) && isApiUrl(value.original_front_url))
    && isNullableApiUrl(value.original_back_url)
    && isNullableApiUrl(value.annotated_front_url)
    && isNullableApiUrl(value.annotated_back_url)
}

function isReviewItem(value: unknown): value is ReviewItemLike {
  return isRecord(value)
    && (value.review_item_id === undefined
      || (typeof value.review_item_id === 'string'
        && value.review_item_id.trim().length > 0))
    && (value.revision === undefined || isNonnegativeInteger(value.revision))
    && isNonnegativeInteger(value.session_id) && value.session_id > 0
    && (value.student_id === undefined
      || (isNonnegativeInteger(value.student_id) && value.student_id > 0))
    && isNullablePositiveInteger(value.result_id)
    && isNullablePositiveInteger(value.detail_id)
    && typeof value.question_id === 'string' && value.question_id.trim().length > 0
    && typeof value.student_name === 'string'
    && isNullableString(value.student_code)
    && isNullableString(value.class_name)
    && (value.score_awarded === null || isFiniteNumber(value.score_awarded))
    && isFiniteNumber(value.max_score) && value.max_score >= 0
    && isNullableString(value.deduction_reason)
    && isNullableString(value.error_category)
    && isNullableString(value.error_summary)
    && (value.confidence_score === null || isFiniteNumber(value.confidence_score))
    && typeof value.needs_review === 'boolean'
    && (value.score_status === undefined || isReviewScoreStatus(value.score_status))
    && (value.score_source === undefined || isReviewScoreSource(value.score_source))
    && (value.teacher_locked === undefined || typeof value.teacher_locked === 'boolean')
    && Array.isArray(value.candidate_scores) && value.candidate_scores.every(isRecord)
    && isRecord(value.metadata)
    && isReviewMedia(value.media)
}

export function isReviewQuestionListResponse(value: unknown): value is ReviewQuestionListResponse {
  return isRecord(value) && Array.isArray(value.items) && value.items.every(isReviewQuestion) && isNonnegativeInteger(value.total) && value.total === value.items.length
}

export function isReviewItemListResponse(value: unknown): value is ReviewItemListResponse {
  return isRecord(value) && Array.isArray(value.items) && value.items.every(isReviewItem) && isNonnegativeInteger(value.total) && value.total === value.items.length
}

function legacyReviewItemId(item: ReviewItemLike): string {
  return `${item.session_id}:${item.question_id}:${String(item.detail_id ?? item.result_id ?? 'manual')}`
}

export function resolveReviewItem(item: ReviewItemLike): ResolvedReviewItem {
  const resultId = item.result_id ?? null
  const detailId = item.detail_id ?? null
  const scoreStatus = item.score_status
    ?? (item.needs_review ? 'ai_review' : 'teacher_final')
  const scoreSource = item.score_source
    ?? (scoreStatus === 'teacher_final' ? 'teacher' : 'ai')
  const fallbackStudentId = item.student_id ?? resultId ?? detailId
  if (fallbackStudentId === null || fallbackStudentId <= 0) {
    throw new Error('review item student identity is required')
  }

  return {
    ...item,
    review_item_id: item.review_item_id?.trim() || legacyReviewItemId(item),
    revision: item.revision ?? 0,
    student_id: fallbackStudentId,
    result_id: resultId,
    detail_id: detailId,
    score_awarded: item.score_awarded ?? null,
    score_status: scoreStatus,
    score_source: scoreSource,
    teacher_locked: item.teacher_locked ?? scoreStatus === 'teacher_final',
  }
}

const RUBRIC_SECTION_KEYS = [
  'question_id',
  'parent_question_id',
  'question_type',
  'max_score',
  'knowledge_labels',
  'points',
] as const

const RUBRIC_POINT_KEYS = [
  'part_id',
  'part_label',
  'step_id',
  'core_goal',
  'score',
  'standard_answer',
  'accepted_answers',
  'match_rule',
  'required_elements',
  'deduction_rules',
  'answer_only_max_score',
  'require_final_answer',
  'final_answer_rule',
] as const

function hasExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value)
  return actual.length === expected.length
    && actual.every((key) => expected.includes(key))
}

function isNonblankString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((entry) => typeof entry === 'string')
}

function isNullableNonnegativeNumber(value: unknown): value is number | null {
  return value === null || (isFiniteNumber(value) && value >= 0)
}

function isReviewRubricPoint(value: unknown): value is ReviewRubricPoint {
  return isRecord(value)
    && hasExactKeys(value, RUBRIC_POINT_KEYS)
    && isNonblankString(value.part_id)
    && isNonblankString(value.part_label)
    && isNonblankString(value.step_id)
    && typeof value.core_goal === 'string'
    && isFiniteNumber(value.score) && value.score >= 0
    && typeof value.standard_answer === 'string'
    && isStringArray(value.accepted_answers)
    && typeof value.match_rule === 'string'
    && isStringArray(value.required_elements)
    && isStringArray(value.deduction_rules)
    && isNullableNonnegativeNumber(value.answer_only_max_score)
    && (
      value.require_final_answer === null
      || typeof value.require_final_answer === 'boolean'
    )
    && typeof value.final_answer_rule === 'string'
}

export function isReviewRubricSection(
  value: unknown,
): value is ReviewRubricSection {
  return isRecord(value)
    && hasExactKeys(value, RUBRIC_SECTION_KEYS)
    && isNonblankString(value.question_id)
    && isNonblankString(value.parent_question_id)
    && (
      value.question_type === null
      || isNonblankString(value.question_type)
    )
    && isFiniteNumber(value.max_score) && value.max_score >= 0
    && isStringArray(value.knowledge_labels)
    && Array.isArray(value.points)
    && value.points.every(isReviewRubricPoint)
}

export function isReviewConfirmResponse(value: unknown): value is ReviewConfirmResponse {
  if (
    !isRecord(value) ||
    !isNonnegativeInteger(value.updated_details) ||
    !isNonnegativeInteger(value.updated_results) ||
    !Array.isArray(value.annotation_outcomes)
  ) return false
  return value.annotation_outcomes.every((outcome) =>
    isRecord(outcome) &&
    isNonnegativeInteger(outcome.result_id) &&
    outcome.result_id > 0 &&
    (outcome.status === 'succeeded' || outcome.status === 'retry_required' || outcome.status === 'on_demand') &&
    (outcome.message === undefined || typeof outcome.message === 'string'),
  )
}

function isAbortSignal(value: unknown): value is AbortSignal {
  return typeof value === 'object'
    && value !== null
    && 'aborted' in value
    && typeof value.aborted === 'boolean'
    && 'addEventListener' in value
}

export function fetchReviewQuestions(
  sessionId: number,
  signal?: AbortSignal,
): Promise<ReviewQuestionSummary[]>
export function fetchReviewQuestions(
  sessionId: number,
  options?: FetchReviewQuestionsOptions,
): Promise<ReviewQuestionSummary[]>
export function fetchReviewQuestions(
  sessionId: number,
  signal: AbortSignal,
  options: FetchReviewQuestionsOptions,
): Promise<ReviewQuestionSummary[]>
export async function fetchReviewQuestions(
  sessionId: number,
  signalOrOptions?: AbortSignal | FetchReviewQuestionsOptions,
  scopedOptions?: FetchReviewQuestionsOptions,
): Promise<ReviewQuestionSummary[]> {
  const options = scopedOptions
    ?? (isAbortSignal(signalOrOptions) ? {} : signalOrOptions)
    ?? {}
  const signal = options.signal
    ?? (isAbortSignal(signalOrOptions) ? signalOrOptions : undefined)
  const path = options.scope === undefined
    ? `/api/sessions/${sessionId}/review/questions`
    : `/api/sessions/${sessionId}/review/questions?scope=${encodeURIComponent(options.scope)}`
  const payload = await apiClient.request(
    path,
    {
      signal,
      decode: (value) => {
        if (!isReviewQuestionListResponse(value)) throw new Error('invalid review questions')
        return value
      },
    },
  )
  return payload.items
}

export async function fetchReviewItems(
  sessionId: number,
  questionId: string,
  options: FetchReviewItemsOptions = {},
): Promise<ReviewItemLike[]> {
  const encodedQuestion = encodeURIComponent(questionId)
  const query = options.scope === undefined
    ? `needs_review_only=${String(options.needsReviewOnly ?? true)}`
    : `scope=${encodeURIComponent(options.scope)}`
  const payload = await apiClient.request(
    `/api/sessions/${sessionId}/review/questions/${encodedQuestion}/items?${query}`,
    {
      signal: options.signal,
      decode: (value) => {
        if (!isReviewItemListResponse(value)) throw new Error('invalid review items')
        return value
      },
    },
  )
  return payload.items.map(resolveReviewItem)
}

export async function fetchReviewRubric(
  sessionId: number,
  questionId: string,
  signal?: AbortSignal,
): Promise<ReviewRubricSection | null> {
  const requested = questionId.trim()
  if (!requested) throw new Error('question id is required')
  const encodedQuestion = encodeURIComponent(requested)
  return apiClient.request(
    `/api/sessions/${sessionId}/review/questions/${encodedQuestion}/rubric`,
    {
      signal,
      decode: (value) => {
        if (value === null) return null
        if (!isReviewRubricSection(value)) throw new Error('invalid review rubric')
        return value
      },
    },
  )
}

export async function confirmReviewItems(
  sessionId: number,
  questionId: string,
  inputs: readonly ReviewConfirmInput[],
  signal?: AbortSignal,
): Promise<ReviewConfirmResponse> {
  const requested = questionId.trim()
  if (!requested) throw new Error('question id is required')
  if (inputs.length === 0) throw new Error('review confirmation items are required')
  const encodedQuestion = encodeURIComponent(requested)
  return apiClient.request(
    `/api/sessions/${sessionId}/review/questions/${encodedQuestion}/confirm`,
    {
      method: 'POST',
      body: { items: [...inputs], annotation_mode: 'on_demand' },
      signal,
      decode: (value) => {
        if (!isReviewConfirmResponse(value)) throw new Error('invalid review confirmation')
        return value
      },
    },
  )
}

export async function confirmReviewItem(
  sessionId: number,
  questionId: string,
  input: ReviewConfirmInput,
  signal?: AbortSignal,
): Promise<ReviewConfirmResponse> {
  return confirmReviewItems(sessionId, questionId, [input], signal)
}
