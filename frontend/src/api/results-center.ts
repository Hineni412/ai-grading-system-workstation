import { apiClient } from './client'
import {
  hasExactKeys,
  isNonnegativeInteger,
  isNullableString,
  isPositiveInteger,
  isRecord,
} from './validation'

export type ResultsScoreStatus =
  | 'ungraded'
  | 'failed'
  | 'ai_review'
  | 'ai_ready'
  | 'teacher_final'

export type ResultsScoreSource = 'none' | 'ai' | 'teacher'
export type ResultsStudentStatus = 'complete' | 'needs_review' | 'incomplete' | 'failed'

export interface ResultsCenterSummary {
  student_count: number
  complete_student_count: number
  average_sample_count: number
  average_score: number | null
  highest_score: number | null
  lowest_score: number | null
  max_score: number
  ungraded_item_count: number
  failed_item_count: number
  needs_review_item_count: number
  ai_ready_item_count: number
  teacher_final_item_count: number
}

export interface ResultsCenterQuestion {
  question_id: string
  max_score: number
  total_count: number
  ungraded_count: number
  failed_count: number
  needs_review_count: number
  ai_ready_count: number
  teacher_final_count: number
  average_score: number | null
}

export interface ResultsCenterItem {
  review_item_id: string
  question_id: string
  score_awarded: number | null
  max_score: number
  score_status: ResultsScoreStatus
  score_source: ResultsScoreSource
  confidence_score: number | null
  needs_review: boolean
  review_reason: string | null
  result_id: number | null
  detail_id: number | null
  alternative_solution_detected?: boolean
}

export interface ResultsCenterStudent {
  student_id: number
  student_code: string | null
  student_name: string
  class_name: string | null
  pinyin_initials: string
  pinyin_full: string
  current_score: number
  max_score: number
  ungraded_count: number
  failed_count: number
  needs_review_count: number
  status: ResultsStudentStatus
  items: ResultsCenterItem[]
}

export interface ResultsCenterResponse {
  session_id: number
  session_name: string
  summary: ResultsCenterSummary
  questions: ResultsCenterQuestion[]
  students: ResultsCenterStudent[]
}

const SUMMARY_KEYS = [
  'student_count',
  'complete_student_count',
  'average_sample_count',
  'average_score',
  'highest_score',
  'lowest_score',
  'max_score',
  'ungraded_item_count',
  'failed_item_count',
  'needs_review_item_count',
  'ai_ready_item_count',
  'teacher_final_item_count',
] as const

const QUESTION_KEYS = [
  'question_id',
  'max_score',
  'total_count',
  'ungraded_count',
  'failed_count',
  'needs_review_count',
  'ai_ready_count',
  'teacher_final_count',
  'average_score',
] as const

const ITEM_KEYS = [
  'review_item_id',
  'question_id',
  'score_awarded',
  'max_score',
  'score_status',
  'score_source',
  'confidence_score',
  'needs_review',
  'review_reason',
  'result_id',
  'detail_id',
] as const

const STUDENT_KEYS = [
  'student_id',
  'student_code',
  'student_name',
  'class_name',
  'pinyin_initials',
  'pinyin_full',
  'current_score',
  'max_score',
  'ungraded_count',
  'failed_count',
  'needs_review_count',
  'status',
  'items',
] as const

function isNonnegativeNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

function isNullableNonnegativeNumber(value: unknown): value is number | null {
  return value === null || isNonnegativeNumber(value)
}

function isNullablePositiveInteger(value: unknown): value is number | null {
  return value === null || isPositiveInteger(value)
}

function isScoreStatus(value: unknown): value is ResultsScoreStatus {
  return value === 'ungraded'
    || value === 'failed'
    || value === 'ai_review'
    || value === 'ai_ready'
    || value === 'teacher_final'
}

function isScoreSource(value: unknown): value is ResultsScoreSource {
  return value === 'none' || value === 'ai' || value === 'teacher'
}

function isStudentStatus(value: unknown): value is ResultsStudentStatus {
  return value === 'complete'
    || value === 'needs_review'
    || value === 'incomplete'
    || value === 'failed'
}

function isSummary(value: unknown): value is ResultsCenterSummary {
  if (!isRecord(value) || !hasExactKeys(value, SUMMARY_KEYS)) return false
  return (
    isNonnegativeInteger(value.student_count)
    && isNonnegativeInteger(value.complete_student_count)
    && value.complete_student_count <= value.student_count
    && isNonnegativeInteger(value.average_sample_count)
    && value.average_sample_count <= value.student_count
    && isNullableNonnegativeNumber(value.average_score)
    && isNullableNonnegativeNumber(value.highest_score)
    && isNullableNonnegativeNumber(value.lowest_score)
    && isNonnegativeNumber(value.max_score)
    && isNonnegativeInteger(value.ungraded_item_count)
    && isNonnegativeInteger(value.failed_item_count)
    && isNonnegativeInteger(value.needs_review_item_count)
    && isNonnegativeInteger(value.ai_ready_item_count)
    && isNonnegativeInteger(value.teacher_final_item_count)
  )
}

function isQuestion(value: unknown): value is ResultsCenterQuestion {
  if (!isRecord(value) || !hasExactKeys(value, QUESTION_KEYS)) return false
  return (
    typeof value.question_id === 'string'
    && value.question_id.trim().length > 0
    && isNonnegativeNumber(value.max_score)
    && isNonnegativeInteger(value.total_count)
    && isNonnegativeInteger(value.ungraded_count)
    && value.ungraded_count <= value.total_count
    && isNonnegativeInteger(value.failed_count)
    && value.failed_count <= value.total_count
    && isNonnegativeInteger(value.needs_review_count)
    && value.needs_review_count <= value.total_count
    && isNonnegativeInteger(value.ai_ready_count)
    && value.ai_ready_count <= value.total_count
    && isNonnegativeInteger(value.teacher_final_count)
    && value.teacher_final_count <= value.total_count
    && isNullableNonnegativeNumber(value.average_score)
  )
}

function isItem(value: unknown): value is ResultsCenterItem {
  if (!isRecord(value) || !hasExactKeys(value, value.alternative_solution_detected === undefined
    ? ITEM_KEYS : [...ITEM_KEYS, 'alternative_solution_detected'])) return false
  return (
    typeof value.review_item_id === 'string'
    && value.review_item_id.trim().length > 0
    && typeof value.question_id === 'string'
    && value.question_id.trim().length > 0
    && isNullableNonnegativeNumber(value.score_awarded)
    && isNonnegativeNumber(value.max_score)
    && isScoreStatus(value.score_status)
    && isScoreSource(value.score_source)
    && isNullableNonnegativeNumber(value.confidence_score)
    && typeof value.needs_review === 'boolean'
    && isNullableString(value.review_reason)
    && isNullablePositiveInteger(value.result_id)
    && isNullablePositiveInteger(value.detail_id)
    && (value.alternative_solution_detected === undefined
      || typeof value.alternative_solution_detected === 'boolean')
  )
}

function isStudent(value: unknown): value is ResultsCenterStudent {
  if (!isRecord(value) || !hasExactKeys(value, STUDENT_KEYS)) return false
  return (
    isPositiveInteger(value.student_id)
    && isNullableString(value.student_code)
    && typeof value.student_name === 'string'
    && value.student_name.trim().length > 0
    && isNullableString(value.class_name)
    && typeof value.pinyin_initials === 'string'
    && typeof value.pinyin_full === 'string'
    && isNonnegativeNumber(value.current_score)
    && isNonnegativeNumber(value.max_score)
    && isNonnegativeInteger(value.ungraded_count)
    && isNonnegativeInteger(value.failed_count)
    && isNonnegativeInteger(value.needs_review_count)
    && isStudentStatus(value.status)
    && Array.isArray(value.items)
    && value.items.every(isItem)
  )
}

export function decodeResultsCenterResponse(value: unknown): ResultsCenterResponse {
  if (
    !isRecord(value)
    || !hasExactKeys(value, [
      'session_id',
      'session_name',
      'summary',
      'questions',
      'students',
    ])
    || !isPositiveInteger(value.session_id)
    || typeof value.session_name !== 'string'
    || value.session_name.trim().length === 0
    || !isSummary(value.summary)
    || !Array.isArray(value.questions)
    || !value.questions.every(isQuestion)
    || !Array.isArray(value.students)
    || !value.students.every(isStudent)
  ) {
    throw new Error('Invalid results center response')
  }
  return value as unknown as ResultsCenterResponse
}

function requireSessionId(sessionId: number): number {
  if (!isPositiveInteger(sessionId)) throw new Error('Invalid session id')
  return sessionId
}

export function fetchResultsCenter(
  sessionId: number,
  signal?: AbortSignal,
): Promise<ResultsCenterResponse> {
  return apiClient.request(
    `/api/sessions/${requireSessionId(sessionId)}/results-center`,
    {
      decode: decodeResultsCenterResponse,
      signal,
    },
  )
}
