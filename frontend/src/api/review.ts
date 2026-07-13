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

export interface ReviewRubricLine {
  label: string
  score: number | null
}

export interface ReviewRubricSection {
  questionId: string
  parentQuestionId: string | null
  title: string
  maxScore: number
  questionType: string | null
  knowledgeLabels: string[]
  lines: ReviewRubricLine[]
}

export interface ReviewConfirmInput {
  result_id: number
  detail_id: number
  score_awarded: number
  deduction_reason?: string
}

export interface ReviewAnnotationOutcome {
  result_id: number
  status: 'succeeded' | 'retry_required'
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

function cleanText(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const text = value.trim()
  return text.length > 0 ? text : null
}

function finiteNonnegative(value: unknown): number | null {
  return isFiniteNumber(value) && value >= 0 ? value : null
}

function uniqueTexts(values: unknown[]): string[] {
  return [...new Set(values.map(cleanText).filter((value): value is string => value !== null))]
}

function knowledgeLabels(question: Record<string, unknown>): string[] {
  const labels: unknown[] = []
  const points = question.knowledge_points
  if (Array.isArray(points)) {
    for (const point of points) {
      if (!isRecord(point)) continue
      labels.push(
        cleanText(point.knowledge_name) ??
        cleanText(point.name) ??
        cleanText(point.knowledge_id) ??
        cleanText(point.id),
      )
    }
  }
  if (Array.isArray(question.knowledge_ids)) labels.push(...question.knowledge_ids)
  if (labels.length === 0) labels.push(question.knowledge_name, question.knowledge_id)
  return uniqueTexts(labels)
}

function rubricLines(container: Record<string, unknown>): ReviewRubricLine[] {
  const lines: ReviewRubricLine[] = []
  const seen = new Set<string>()
  const append = (label: unknown, score: unknown = null) => {
    const text = cleanText(label)
    if (text === null || seen.has(text)) return
    seen.add(text)
    lines.push({ label: text, score: finiteNonnegative(score) })
  }
  append(container.core_goal, container.step_score)
  append(container.analysis)
  for (const key of ['required_elements', 'step_milestones', 'grading_points']) {
    const values = container[key]
    if (Array.isArray(values)) values.forEach((value) => append(value))
  }
  const steps = container.steps
  if (Array.isArray(steps)) {
    for (const step of steps) {
      if (!isRecord(step)) continue
      append(step.core_goal, step.step_score)
      append(step.analysis, step.step_score)
      for (const key of ['required_elements', 'step_milestones']) {
        const values = step[key]
        if (Array.isArray(values)) values.forEach((value) => append(value))
      }
    }
  }
  return lines
}

export function extractReviewRubricSection(
  rubric: unknown,
  requestedQuestionId: string,
): ReviewRubricSection | null {
  if (!isRecord(rubric) || !Array.isArray(rubric.questions)) return null
  const requested = requestedQuestionId.trim()
  if (!requested) return null

  for (const rawQuestion of rubric.questions) {
    if (!isRecord(rawQuestion)) continue
    const questionId = cleanText(rawQuestion.question_id)
    if (questionId === null) continue
    const parts = Array.isArray(rawQuestion.parts)
      ? rawQuestion.parts.filter(isRecord)
      : []
    const part = parts.find((entry) => cleanText(entry.part_id) === requested)
    if (questionId !== requested && part === undefined) continue

    const target = part ?? rawQuestion
    const maxScore = finiteNonnegative(
      part === undefined ? rawQuestion.max_score : part.part_score,
    ) ?? 0
    const lineSources = part === undefined ? [rawQuestion, ...parts] : [part]
    const lines = lineSources.flatMap(rubricLines)
    return {
      questionId: requested,
      parentQuestionId: part === undefined ? null : questionId,
      title: requested,
      maxScore,
      questionType: cleanText(rawQuestion.question_type),
      knowledgeLabels: knowledgeLabels(rawQuestion),
      lines: lines.filter(
        (line, index) => lines.findIndex((candidate) => candidate.label === line.label) === index,
      ),
    }
  }
  return null
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
    (outcome.status === 'succeeded' || outcome.status === 'retry_required') &&
    (outcome.message === undefined || typeof outcome.message === 'string'),
  )
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

export async function fetchReviewRubric(
  sessionId: number,
  questionId: string,
  signal?: AbortSignal,
): Promise<ReviewRubricSection | null> {
  const requested = questionId.trim()
  if (!requested) throw new Error('question id is required')
  return apiClient.request(`/api/sessions/${sessionId}/config`, {
    signal,
    decode: (value) => {
      if (!isRecord(value) || !isRecord(value.rubric)) {
        throw new Error('invalid review rubric')
      }
      return extractReviewRubricSection(value.rubric, requested)
    },
  })
}

export async function confirmReviewItem(
  sessionId: number,
  questionId: string,
  input: ReviewConfirmInput,
  signal?: AbortSignal,
): Promise<ReviewConfirmResponse> {
  const requested = questionId.trim()
  if (!requested) throw new Error('question id is required')
  const encodedQuestion = encodeURIComponent(requested)
  return apiClient.request(
    `/api/sessions/${sessionId}/review/questions/${encodedQuestion}/confirm`,
    {
      method: 'POST',
      body: { items: [input] },
      signal,
      decode: (value) => {
        if (!isReviewConfirmResponse(value)) throw new Error('invalid review confirmation')
        return value
      },
    },
  )
}
