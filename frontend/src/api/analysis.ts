import { apiClient } from './client'
import { isNullableString, isRecord } from './validation'

export type MetricStatus = 'ready' | 'missing_max_score' | 'no_attempts'

export interface QuestionAnalysisItem {
  class_name: string
  question_id: string
  max_score: number | null
  score_rate: number | null
  average_score: number | null
  deduction_count: number
  attempt_count: number
  metric_status: MetricStatus
}

export interface StudentAnalysisItem {
  result_id: number
  detail_id: number
  student_id: number
  student_code: string | null
  student_name: string
  class_name: string
  question_id: string
  score_awarded: number
  max_score: number | null
  deduction_amount: number | null
  deduction_reason: string | null
  needs_review: boolean
  evidence_url: string | null
}

export interface AnalysisScope {
  session_id: number
  class_name: string | null
  question_id: string | null
}

export interface QuestionAnalysisResponse {
  scope: AnalysisScope
  classes: string[]
  items: QuestionAnalysisItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface StudentAnalysisResponse {
  scope: AnalysisScope
  items: StudentAnalysisItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

interface StudentAnalysisExpectation {
  sessionId: number
  questionId: string
  className: string | null
}

function isInteger(value: unknown, positive = false): value is number {
  return Number.isSafeInteger(value) && Number(value) >= (positive ? 1 : 0)
}

function isNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || isNumber(value)
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value)
  return actual.length === keys.length && keys.every(
    (key) => Object.prototype.hasOwnProperty.call(value, key),
  )
}

function isAnalysisScope(value: unknown): value is AnalysisScope {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['session_id', 'class_name', 'question_id']) &&
    isInteger(value.session_id, true) &&
    isNullableString(value.class_name) &&
    isNullableString(value.question_id)
  )
}

function isQuestionItem(value: unknown): value is QuestionAnalysisItem {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'class_name', 'question_id', 'max_score', 'score_rate', 'average_score',
      'deduction_count', 'attempt_count', 'metric_status',
    ]) &&
    typeof value.class_name === 'string' &&
    typeof value.question_id === 'string' &&
    value.question_id.length > 0 &&
    isNullableNumber(value.max_score) &&
    (value.max_score === null || value.max_score >= 0) &&
    isNullableNumber(value.score_rate) &&
    (value.score_rate === null || (value.score_rate >= 0 && value.score_rate <= 100)) &&
    isNullableNumber(value.average_score) &&
    isInteger(value.deduction_count) &&
    isInteger(value.attempt_count) &&
    (value.metric_status === 'ready' ||
      value.metric_status === 'missing_max_score' ||
      value.metric_status === 'no_attempts')
  )
}

function isStudentItem(value: unknown): value is StudentAnalysisItem {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'result_id', 'detail_id', 'student_id', 'student_code', 'student_name',
      'class_name', 'question_id', 'score_awarded', 'max_score', 'deduction_amount',
      'deduction_reason', 'needs_review', 'evidence_url',
    ]) &&
    isInteger(value.result_id, true) &&
    isInteger(value.detail_id, true) &&
    isInteger(value.student_id, true) &&
    isNullableString(value.student_code) &&
    typeof value.student_name === 'string' &&
    typeof value.class_name === 'string' &&
    typeof value.question_id === 'string' &&
    value.question_id.length > 0 &&
    isNumber(value.score_awarded) &&
    isNullableNumber(value.max_score) &&
    (value.max_score === null || value.max_score >= 0) &&
    isNullableNumber(value.deduction_amount) &&
    (value.deduction_amount === null || value.deduction_amount >= 0) &&
    isNullableString(value.deduction_reason) &&
    typeof value.needs_review === 'boolean' &&
    (value.evidence_url === null || (typeof value.evidence_url === 'string' && value.evidence_url.startsWith('/api/')))
  )
}

function isValidPage(itemsLength: number, value: Record<string, unknown>): boolean {
  if (!isInteger(value.total) || !isInteger(value.page, true) || !isInteger(value.page_size, true) || !isInteger(value.total_pages)) return false
  const expectedPages = Math.ceil(value.total / value.page_size)
  if (value.total_pages !== expectedPages || itemsLength > value.page_size) return false
  return itemsLength === Math.max(0, Math.min(value.page_size, value.total - (value.page - 1) * value.page_size))
}

export function decodeQuestionAnalysisResponse(value: unknown): QuestionAnalysisResponse {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['scope', 'classes', 'items', 'total', 'page', 'page_size', 'total_pages']) ||
    !isAnalysisScope(value.scope) ||
    !Array.isArray(value.classes) ||
    !value.classes.every((item) => typeof item === 'string') ||
    !Array.isArray(value.items) ||
    !value.items.every(isQuestionItem) ||
    !isValidPage(value.items.length, value)
  ) {
    throw new Error('Invalid question analysis')
  }
  return value as unknown as QuestionAnalysisResponse
}

function hasMatchingStudentScope(
  response: StudentAnalysisResponse,
  expected?: StudentAnalysisExpectation,
): boolean {
  const { scope } = response
  if (scope.question_id === null) return false
  if (expected && (
    scope.session_id !== expected.sessionId ||
    scope.question_id !== expected.questionId ||
    scope.class_name !== expected.className
  )) return false
  return response.items.every((item) => (
    item.question_id === scope.question_id &&
    (scope.class_name === null || item.class_name === scope.class_name) &&
    (item.evidence_url === null || item.evidence_url ===
      `/api/sessions/${scope.session_id}/results/${item.result_id}/details/${item.detail_id}/crop`)
  ))
}

export function decodeStudentAnalysisResponse(
  value: unknown,
  expected?: StudentAnalysisExpectation,
): StudentAnalysisResponse {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['scope', 'items', 'total', 'page', 'page_size', 'total_pages']) ||
    !isAnalysisScope(value.scope) ||
    !Array.isArray(value.items) ||
    !value.items.every(isStudentItem) ||
    !isValidPage(value.items.length, value)
  ) {
    throw new Error('Invalid student analysis')
  }
  const response = value as unknown as StudentAnalysisResponse
  if (!hasMatchingStudentScope(response, expected)) {
    throw new Error('Invalid student analysis')
  }
  return response
}

function requireSessionId(sessionId: number): number {
  if (!isInteger(sessionId, true)) throw new Error('Invalid session id')
  return sessionId
}

export function fetchQuestionAnalysis(
  sessionId: number,
  className: string | null,
  signal?: AbortSignal,
  page = 1,
): Promise<QuestionAnalysisResponse> {
  const query = new URLSearchParams()
  if (className !== null) query.set('class_name', className)
  query.set('page', String(isInteger(page, true) ? page : 1))
  query.set('page_size', '100')
  return apiClient.request(
    `/api/sessions/${requireSessionId(sessionId)}/analysis/questions?${query.toString()}`,
    {
      decode: (value) => {
        const response = decodeQuestionAnalysisResponse(value)
        if (response.page !== page) throw new Error('Invalid question analysis')
        return response
      },
      signal,
    },
  )
}

export function fetchStudentAnalysis(
  sessionId: number,
  questionId: string,
  className: string | null,
  signal?: AbortSignal,
  page = 1,
): Promise<StudentAnalysisResponse> {
  const normalizedQuestionId = questionId.trim()
  if (!normalizedQuestionId) throw new Error('Invalid question id')
  const normalizedClassName = className === null ? null : className.trim()
  const query = new URLSearchParams()
  if (className !== null) query.set('class_name', className)
  query.set('page', String(isInteger(page, true) ? page : 1))
  query.set('page_size', '100')
  return apiClient.request(
    `/api/sessions/${requireSessionId(sessionId)}/analysis/questions/${encodeURIComponent(questionId)}/students?${query.toString()}`,
    {
      decode: (value) => {
        const response = decodeStudentAnalysisResponse(value, {
          sessionId,
          questionId: normalizedQuestionId,
          className: normalizedClassName,
        })
        if (response.page !== page) throw new Error('Invalid student analysis')
        return response
      },
      signal,
    },
  )
}
