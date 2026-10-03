import { apiClient } from './client'
import { JOB_STATUSES, type JobStatus } from './jobs'
import { isSessionSummary, type SessionSummary } from './sessions'
import { isNullableString, isRecord } from './validation'

export interface SessionProgress {
  total_papers: number
  matched_papers: number
  unmatched_papers: number
  graded_papers: number
  failed_papers: number
  grading_papers: number
  needs_human_review: number
  absent_students: number
  scan_issue_students: number
  progress_percent: number
}

export interface JobSummary {
  id: number
  job_type: string
  status: JobStatus
  progress: number
  stage: string
  detail: string
  created_at: string
  started_at: string | null
  updated_at: string
  finished_at: string | null
}

export interface RecentSessionSummary {
  session: SessionSummary
  progress: SessionProgress
}

export interface WorkbenchOverview {
  current_session: SessionSummary | null
  progress: SessionProgress | null
  review: { question_count: number; item_count: number } | null
  anomalies: {
    unmatched_papers: number
    scan_issue_students: number
    failed_papers: number
  } | null
  recent_jobs: JobSummary[]
  recent_sessions: RecentSessionSummary[]
  updated_at: string
}

export interface SessionAnomaly {
  anomaly_id: string
  anomaly_type: 'unmatched_paper' | 'scan_issue' | 'grading_failed'
  display_name: string
  student_code: string | null
  class_name: string | null
  status: string
  detail: string | null
  created_at: string | null
}

export interface SessionAnomalyResponse {
  items: SessionAnomaly[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value)
  return actual.length === keys.length && keys.every(
    (key) => Object.prototype.hasOwnProperty.call(value, key),
  )
}

function isStrictSessionSummary(value: unknown): value is SessionSummary {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'id', 'name', 'status', 'curriculum_volume_id', 'is_deleted', 'deleted_at',
      'created_at', 'updated_at',
    ]) &&
    isSessionSummary(value)
  )
}

function isSessionProgress(value: unknown): value is SessionProgress {
  if (!isRecord(value)) return false
  return (
    hasExactKeys(value, [
      'total_papers', 'matched_papers', 'unmatched_papers', 'graded_papers',
      'failed_papers', 'grading_papers', 'needs_human_review', 'absent_students',
      'scan_issue_students', 'progress_percent',
    ]) &&
    isNonNegativeInteger(value.total_papers) &&
    isNonNegativeInteger(value.matched_papers) &&
    isNonNegativeInteger(value.unmatched_papers) &&
    isNonNegativeInteger(value.graded_papers) &&
    isNonNegativeInteger(value.failed_papers) &&
    isNonNegativeInteger(value.grading_papers) &&
    isNonNegativeInteger(value.needs_human_review) &&
    isNonNegativeInteger(value.absent_students) &&
    isNonNegativeInteger(value.scan_issue_students) &&
    isFiniteNumber(value.progress_percent) &&
    value.progress_percent >= 0 &&
    value.progress_percent <= 100
  )
}

function isJobSummary(value: unknown): value is JobSummary {
  if (!isRecord(value)) return false
  return (
    hasExactKeys(value, [
      'id', 'job_type', 'status', 'progress', 'stage', 'detail', 'created_at',
      'started_at', 'updated_at', 'finished_at',
    ]) &&
    isPositiveInteger(value.id) &&
    typeof value.job_type === 'string' &&
    value.job_type.trim().length > 0 &&
    typeof value.status === 'string' &&
    JOB_STATUSES.some((status) => status === value.status) &&
    isFiniteNumber(value.progress) &&
    value.progress >= 0 &&
    value.progress <= 1 &&
    typeof value.stage === 'string' &&
    typeof value.detail === 'string' &&
    typeof value.created_at === 'string' &&
    value.created_at.length > 0 &&
    isNullableString(value.started_at) &&
    typeof value.updated_at === 'string' &&
    value.updated_at.length > 0 &&
    isNullableString(value.finished_at)
  )
}

function isRecentSessionSummary(value: unknown): value is RecentSessionSummary {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['session', 'progress']) &&
    isStrictSessionSummary(value.session) &&
    isSessionProgress(value.progress)
  )
}

function isNullableSummary(value: unknown, keys: string[]): boolean {
  if (value === null) return true
  return (
    isRecord(value) &&
    hasExactKeys(value, keys) &&
    keys.every((key) => isNonNegativeInteger(value[key]))
  )
}

export function decodeWorkbenchOverview(value: unknown): WorkbenchOverview {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      'current_session', 'progress', 'review', 'anomalies', 'recent_jobs',
      'recent_sessions', 'updated_at',
    ]) ||
    !(value.current_session === null || isStrictSessionSummary(value.current_session)) ||
    !(value.progress === null || isSessionProgress(value.progress)) ||
    !isNullableSummary(value.review, ['question_count', 'item_count']) ||
    !isNullableSummary(value.anomalies, [
      'unmatched_papers',
      'scan_issue_students',
      'failed_papers',
    ]) ||
    !Array.isArray(value.recent_jobs) ||
    !value.recent_jobs.every(isJobSummary) ||
    !Array.isArray(value.recent_sessions) ||
    !value.recent_sessions.every(isRecentSessionSummary) ||
    typeof value.updated_at !== 'string' ||
    value.updated_at.length === 0
  ) {
    throw new Error('Invalid workbench overview')
  }
  return value as unknown as WorkbenchOverview
}

function isSessionAnomaly(value: unknown): value is SessionAnomaly {
  if (!isRecord(value)) return false
  return (
    hasExactKeys(value, [
      'anomaly_id', 'anomaly_type', 'display_name', 'student_code', 'class_name',
      'status', 'detail', 'created_at',
    ]) &&
    typeof value.anomaly_id === 'string' &&
    value.anomaly_id.length > 0 &&
    (value.anomaly_type === 'unmatched_paper' ||
      value.anomaly_type === 'scan_issue' ||
      value.anomaly_type === 'grading_failed') &&
    typeof value.display_name === 'string' &&
    isNullableString(value.student_code) &&
    isNullableString(value.class_name) &&
    typeof value.status === 'string' &&
    isNullableString(value.detail) &&
    isNullableString(value.created_at)
  )
}

function isValidPage(itemsLength: number, total: unknown, page: unknown, pageSize: unknown, totalPages: unknown): boolean {
  if (!isNonNegativeInteger(total) || !isPositiveInteger(page) || !isPositiveInteger(pageSize) || !isNonNegativeInteger(totalPages)) return false
  const expectedPages = Math.ceil(total / pageSize)
  if (totalPages !== expectedPages || itemsLength > pageSize) return false
  const expectedItems = Math.max(0, Math.min(pageSize, total - (page - 1) * pageSize))
  return itemsLength === expectedItems
}

export function decodeSessionAnomalyResponse(value: unknown): SessionAnomalyResponse {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['items', 'total', 'page', 'page_size', 'total_pages']) ||
    !Array.isArray(value.items) ||
    !value.items.every(isSessionAnomaly) ||
    !isValidPage(value.items.length, value.total, value.page, value.page_size, value.total_pages)
  ) {
    throw new Error('Invalid session anomalies')
  }
  return value as unknown as SessionAnomalyResponse
}

function requireSessionId(sessionId: number): number {
  if (!isPositiveInteger(sessionId)) throw new Error('Invalid session id')
  return sessionId
}

export function fetchWorkbenchOverview(
  sessionId: number | null,
  curriculumVolumeId: string | null = null,
  signal?: AbortSignal,
): Promise<WorkbenchOverview> {
  const query = new URLSearchParams()
  if (sessionId !== null) query.set('session_id', String(requireSessionId(sessionId)))
  query.set('recent_limit', '5')
  if (curriculumVolumeId) query.set('curriculum_volume_id', curriculumVolumeId)
  return apiClient.request(`/api/workbench/overview?${query.toString()}`, {
    decode: decodeWorkbenchOverview,
    signal,
  })
}

export function fetchSessionAnomalies(
  sessionId: number,
  signal?: AbortSignal,
  page = 1,
): Promise<SessionAnomalyResponse> {
  const query = new URLSearchParams({ page: String(isPositiveInteger(page) ? page : 1), page_size: '100' })
  return apiClient.request(
    `/api/sessions/${requireSessionId(sessionId)}/anomalies?${query.toString()}`,
    {
      decode: (value) => {
        const response = decodeSessionAnomalyResponse(value)
        if (response.page !== page) throw new Error('Invalid session anomalies')
        return response
      },
      signal,
    },
  )
}
