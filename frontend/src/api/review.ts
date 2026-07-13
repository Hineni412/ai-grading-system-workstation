import { apiClient } from './client'
import { isNullableString, isRecord } from './validation'

export interface ReviewQuestionSummary {
  question_id: string
  total_count: number
  needs_review_count: number
  max_score: number
}

export interface ReviewMediaLinks {
  crop_url: string
  original_front_url: string
  original_back_url: string
  annotated_front_url: string
  annotated_back_url: string
}

export interface ReviewItem {
  session_id: number
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
  candidate_scores: Record<string, unknown>[]
  metadata: Record<string, unknown>
  media: ReviewMediaLinks
}

export interface ReviewQuestionListResponse { items: ReviewQuestionSummary[]; total: number }
export interface ReviewItemListResponse { items: ReviewItem[]; total: number }

const isNonnegativeInteger = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0
const isFiniteNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const isApiUrl = (value: unknown): value is string => typeof value === 'string' && /^\/api\//.test(value)

function isReviewQuestion(value: unknown): value is ReviewQuestionSummary {
  return isRecord(value) && typeof value.question_id === 'string' && value.question_id.trim().length > 0 && isNonnegativeInteger(value.total_count) && isNonnegativeInteger(value.needs_review_count) && Number(value.needs_review_count) <= Number(value.total_count) && isFiniteNumber(value.max_score) && value.max_score >= 0
}

function isReviewMedia(value: unknown): value is ReviewMediaLinks {
  return isRecord(value) && isApiUrl(value.crop_url) && isApiUrl(value.original_front_url) && isApiUrl(value.original_back_url) && isApiUrl(value.annotated_front_url) && isApiUrl(value.annotated_back_url)
}

function isReviewItem(value: unknown): value is ReviewItem {
  return isRecord(value) && isNonnegativeInteger(value.session_id) && value.session_id > 0 && isNonnegativeInteger(value.result_id) && value.result_id > 0 && isNonnegativeInteger(value.detail_id) && value.detail_id > 0 && typeof value.question_id === 'string' && typeof value.student_name === 'string' && isNullableString(value.student_code) && isNullableString(value.class_name) && isFiniteNumber(value.score_awarded) && isFiniteNumber(value.max_score) && isNullableString(value.deduction_reason) && isNullableString(value.error_category) && isNullableString(value.error_summary) && (value.confidence_score === null || isFiniteNumber(value.confidence_score)) && typeof value.needs_review === 'boolean' && Array.isArray(value.candidate_scores) && value.candidate_scores.every(isRecord) && isRecord(value.metadata) && isReviewMedia(value.media)
}

export function isReviewQuestionListResponse(value: unknown): value is ReviewQuestionListResponse {
  return isRecord(value) && Array.isArray(value.items) && value.items.every(isReviewQuestion) && isNonnegativeInteger(value.total) && value.total === value.items.length
}

export function isReviewItemListResponse(value: unknown): value is ReviewItemListResponse {
  return isRecord(value) && Array.isArray(value.items) && value.items.every(isReviewItem) && isNonnegativeInteger(value.total) && value.total === value.items.length
}

export async function fetchReviewQuestions(sessionId: number, signal?: AbortSignal): Promise<ReviewQuestionSummary[]> {
  const payload = await apiClient.request(`/api/sessions/${sessionId}/review/questions`, { signal, decode: (value) => { if (!isReviewQuestionListResponse(value)) throw new Error('invalid review questions'); return value } })
  return payload.items
}

export async function fetchReviewItems(sessionId: number, questionId: string, signal?: AbortSignal): Promise<ReviewItem[]> {
  const encodedQuestion = encodeURIComponent(questionId)
  const payload = await apiClient.request(`/api/sessions/${sessionId}/review/questions/${encodedQuestion}/items?needs_review_only=false`, { signal, decode: (value) => { if (!isReviewItemListResponse(value)) throw new Error('invalid review items'); return value } })
  return payload.items
}
