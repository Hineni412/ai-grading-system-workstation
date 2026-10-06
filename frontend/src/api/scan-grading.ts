import { apiClient } from './client'
import { JOB_STATUSES, decodeJobResponse, type JobResponse, type JobStatus } from './jobs'
import { assertNoPathLikeKeys, hasExactKeys, isRecord } from './validation'

export type UploadBatchState = 'draft' | 'frozen'
export type GradingMode = 'ai' | 'manual' | 'full_paper' | 'hybrid_batch'
export type SelectableGradingMode = 'ai' | 'manual'
export type AutomatedGradingMode = Exclude<GradingMode, 'manual'>
export type GradingPlanStatus = 'ready' | 'blocked'
export type GradingPlanMetricValue = number | string | boolean | null
export type GradingPlanMetrics = Record<string, GradingPlanMetricValue>

export interface GradingPlanIssue {
  code: string
  message: string
  count?: number
  question_ids?: string[]
}

export interface GradingPlan {
  mode: GradingMode
  status: GradingPlanStatus
  counts: GradingPlanMetrics
  requests: GradingPlanMetrics
  batching: GradingPlanMetrics
  warnings: GradingPlanIssue[]
  blockers: GradingPlanIssue[]
}

export interface ScanUploadFile {
  id: string
  name: string
  media_type: string
  size_bytes: number
  sha256_prefix: string
  added_at: string
}

export interface ScanUploadBatch {
  batch_id: string
  revision: number
  state: UploadBatchState
  files: ScanUploadFile[]
  file_count: number
  total_bytes: number
  frozen_at: string | null
}

export interface GradingRunSummary {
  run_id: number
  job_id?: number | null
  job_status?: JobStatus | null
  progress?: number | null
  started_at?: string | null
  updated_at?: string | null
  mode: AutomatedGradingMode
  state: string
  counts: Record<'graded' | 'grading' | 'pending' | 'skipped' | 'failed' | 'conflict' | 'total', number>
  allowed_actions: string[]
  incomplete_result_count?: number
  incomplete_item_count?: number
}

export interface ScanAnalysisJobSummary {
  id: number
  status: JobStatus
  progress: number
  updated_at: string
  cancel_requested: boolean
  scan_batch_id: string
}

export type GradingJobSummary = ScanAnalysisJobSummary

export interface GradingWorkspace {
  session_id: number
  upload_batch: ScanUploadBatch
  replacement_batch?: ScanUploadBatch | null
  grading_run: GradingRunSummary | null
  grading_job?: GradingJobSummary | null
  scan_analysis_job?: ScanAnalysisJobSummary | null
}

export interface ScanDecision {
  target_type: 'group' | 'issue'
  target_id: string
  action: 'match' | 'invalid' | 'pending'
  student_id?: number | null
}

export interface ScanPageAssignment {
  first_page_role: 'front' | 'back'
  front_page_parity: 'odd' | 'even'
}

export interface ScanIssueSuggestion {
  student_id: number
  student_name: string
  class_name: string
  score: number
}

export interface ScanPreflightIdentity {
  method: string
  auto: number
  needs_confirmation: number
  model_requests: number
}

export interface ScanPreflight {
  revision: number
  summary: Record<string, number>
  page_assignment?: ScanPageAssignment
  groups: Record<string, unknown>[]
  issues: Record<string, unknown>[]
  absent_students: Record<string, unknown>[]
  warnings: string[]
  decisions: ScanDecision[]
  pending_issue_count: number
  match_conflicts?: ScanMatchConflict[]
  identity?: ScanPreflightIdentity | null
}

export interface ScanMatchConflict {
  code: string
  message: string
  student_id: number
  targets: { target_type: 'group' | 'issue'; target_id: string }[]
}

export interface ScanDecisionSaveResult {
  revision: number
  decisions: ScanDecision[]
  pending_issue_count: number
  ready_to_grade: number
  summary?: Record<string, number>
  absent_students?: Record<string, unknown>[]
  match_conflicts?: ScanMatchConflict[]
  rejected_conflicts?: ScanMatchConflict[]
}

export interface ScanStudentMatchOption {
  id: number
  student_code: string
  name: string
  class_name: string | null
  pinyin_initials: string
  pinyin_full: string
}

function positiveSessionId(value: number): number {
  if (!Number.isSafeInteger(value) || value <= 0) throw new Error('Invalid session id')
  return value
}

function finiteInteger(value: unknown, minimum = 0): value is number {
  return Number.isSafeInteger(value) && Number(value) >= minimum
}



function hasOnlyKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const allowed = new Set(keys)
  return Object.keys(value).every((key) => allowed.has(key))
}

function decodeUploadFile(value: unknown): ScanUploadFile {
  if (!isRecord(value) || !hasExactKeys(value, [
    'id', 'name', 'media_type', 'size_bytes', 'sha256_prefix', 'added_at',
  ])
    || typeof value.id !== 'string' || !value.id
    || typeof value.name !== 'string' || !value.name
    || typeof value.media_type !== 'string'
    || !finiteInteger(value.size_bytes)
    || typeof value.sha256_prefix !== 'string' || !/^[0-9a-f]{12}$/.test(value.sha256_prefix)
    || typeof value.added_at !== 'string') throw new Error('Invalid scan upload file')
  return value as unknown as ScanUploadFile
}

function decodeUploadBatch(value: unknown): ScanUploadBatch {
  if (!isRecord(value) || !hasExactKeys(value, [
    'batch_id', 'revision', 'state', 'files', 'file_count', 'total_bytes', 'frozen_at',
  ])
    || typeof value.batch_id !== 'string' || !value.batch_id
    || !finiteInteger(value.revision)
    || (value.state !== 'draft' && value.state !== 'frozen')
    || !Array.isArray(value.files)
    || !finiteInteger(value.file_count)
    || !finiteInteger(value.total_bytes)
    || !(typeof value.frozen_at === 'string' || value.frozen_at === null)) {
    throw new Error('Invalid scan upload batch')
  }
  return { ...value, files: value.files.map(decodeUploadFile) } as ScanUploadBatch
}

function decodeRun(value: unknown): GradingRunSummary {
  if (!isRecord(value) || !hasOnlyKeys(value, [
    'run_id', 'job_id', 'job_status', 'progress', 'started_at', 'updated_at',
    'mode', 'state', 'counts', 'allowed_actions',
    'incomplete_result_count', 'incomplete_item_count',
  ]) || !finiteInteger(value.run_id, 1)
    || (value.mode !== 'ai' && value.mode !== 'full_paper' && value.mode !== 'hybrid_batch')
    || typeof value.state !== 'string' || !isRecord(value.counts)
    || !Array.isArray(value.allowed_actions) || !value.allowed_actions.every((item) => typeof item === 'string')) {
    throw new Error('Invalid grading run')
  }
  if (value.incomplete_result_count !== undefined && !finiteInteger(value.incomplete_result_count, 0)) throw new Error('Invalid incomplete result count')
  if (value.incomplete_item_count !== undefined && !finiteInteger(value.incomplete_item_count, 0)) throw new Error('Invalid incomplete item count')
  if (value.job_id !== undefined && value.job_id !== null && !finiteInteger(value.job_id, 1)) throw new Error('Invalid grading job id')
  if (value.job_status !== undefined && value.job_status !== null
    && !JOB_STATUSES.some((status) => status === value.job_status)) throw new Error('Invalid grading job status')
  if (value.progress !== undefined && value.progress !== null
    && (typeof value.progress !== 'number' || value.progress < 0 || value.progress > 1)) throw new Error('Invalid grading progress')
  const counts = value.counts
  const keys = ['graded', 'grading', 'pending', 'skipped', 'failed', 'conflict', 'total'] as const
  if (!hasExactKeys(counts, keys) || !keys.every((key) => finiteInteger(counts[key]))) throw new Error('Invalid grading counts')
  return value as unknown as GradingRunSummary
}

function decodeScanAnalysisJob(value: unknown): ScanAnalysisJobSummary {
  if (!isRecord(value) || !hasExactKeys(value, [
    'id', 'status', 'progress', 'updated_at', 'cancel_requested', 'scan_batch_id',
  ]) || !finiteInteger(value.id, 1)
    || !JOB_STATUSES.some((status) => status === value.status)
    || typeof value.progress !== 'number' || value.progress < 0 || value.progress > 1
    || typeof value.updated_at !== 'string' || typeof value.cancel_requested !== 'boolean'
    || typeof value.scan_batch_id !== 'string' || !value.scan_batch_id) {
    throw new Error('Invalid scan analysis job')
  }
  return value as unknown as ScanAnalysisJobSummary
}

export function decodeGradingWorkspace(value: unknown): GradingWorkspace {
  if (!isRecord(value) || !hasOnlyKeys(value, [
    'session_id', 'upload_batch', 'replacement_batch', 'grading_run', 'grading_job', 'scan_analysis_job',
  ]) || !('session_id' in value) || !('upload_batch' in value) || !('grading_run' in value)
    || !finiteInteger(value.session_id, 1)
    || !(value.grading_run === null || isRecord(value.grading_run))
    || !(value.grading_job === undefined || value.grading_job === null
      || isRecord(value.grading_job))
    || !(value.scan_analysis_job === undefined || value.scan_analysis_job === null
      || isRecord(value.scan_analysis_job))) throw new Error('Invalid grading workspace')
  return {
    session_id: value.session_id,
    upload_batch: decodeUploadBatch(value.upload_batch),
    replacement_batch: value.replacement_batch === null || value.replacement_batch === undefined
      ? null : decodeUploadBatch(value.replacement_batch),
    grading_run: value.grading_run === null ? null : decodeRun(value.grading_run),
    grading_job: value.grading_job === undefined || value.grading_job === null
      ? null : decodeScanAnalysisJob(value.grading_job),
    scan_analysis_job: value.scan_analysis_job === undefined || value.scan_analysis_job === null
      ? null : decodeScanAnalysisJob(value.scan_analysis_job),
  }
}

function decodePreflight(value: unknown): ScanPreflight {
  assertNoPathLikeKeys(value)
  if (!isRecord(value) || !finiteInteger(value.revision)
    || !Array.isArray(value.groups) || !Array.isArray(value.issues)
    || !Array.isArray(value.absent_students) || !Array.isArray(value.warnings)
    || !Array.isArray(value.decisions) || !finiteInteger(value.pending_issue_count)) {
    throw new Error('Invalid scan preflight')
  }
  const pageAssignment = value.page_assignment
  if (pageAssignment !== undefined && (!isRecord(pageAssignment)
    || !hasExactKeys(pageAssignment, ['first_page_role', 'front_page_parity'])
    || (pageAssignment.first_page_role !== 'front' && pageAssignment.first_page_role !== 'back')
    || (pageAssignment.front_page_parity !== 'odd' && pageAssignment.front_page_parity !== 'even'))) {
    throw new Error('Invalid scan page assignment')
  }
  const summary = value.summary
  const summaryKeys = [
    'auto_matched', 'ready_to_grade', 'issues', 'absent_candidates', 'total_pages',
  ] as const
  if (!isRecord(summary) || !summaryKeys.every((key) => finiteInteger(summary[key]))) {
    throw new Error('Invalid scan preflight summary')
  }
  const identity = value.identity
  if (identity !== undefined && identity !== null && (!isRecord(identity)
    || typeof identity.method !== 'string'
    || !finiteInteger(identity.auto)
    || !finiteInteger(identity.needs_confirmation)
    || !finiteInteger(identity.model_requests))) {
    throw new Error('Invalid scan preflight identity')
  }
  for (const issue of value.issues) {
    const suggestions = isRecord(issue) ? issue.suggested_students : undefined
    if (suggestions === undefined) continue
    if (!Array.isArray(suggestions) || !suggestions.every((item) => isRecord(item)
      && finiteInteger(item.student_id, 1)
      && typeof item.student_name === 'string'
      && typeof item.class_name === 'string'
      && typeof item.score === 'number' && Number.isFinite(item.score))) {
      throw new Error('Invalid scan preflight suggestion')
    }
  }
  return {
    ...value,
    page_assignment: isRecord(pageAssignment)
      ? pageAssignment as unknown as ScanPageAssignment
      : { first_page_role: 'front', front_page_parity: 'odd' },
  } as unknown as ScanPreflight
}

function isGradingMode(value: unknown): value is GradingMode {
  return value === 'ai' || value === 'full_paper' || value === 'manual' || value === 'hybrid_batch'
}

function decodePlanMetrics(value: unknown, label: string): GradingPlanMetrics {
  if (!isRecord(value)) throw new Error(`Invalid grading plan ${label}`)
  const decoded: GradingPlanMetrics = {}
  for (const [key, item] of Object.entries(value)) {
    if (item === null || typeof item === 'string' || typeof item === 'boolean') {
      decoded[key] = item
    } else if (typeof item === 'number' && Number.isFinite(item)) {
      decoded[key] = item
    }
  }
  return decoded
}

function decodePlanIssue(value: unknown, index: number): GradingPlanIssue {
  if (typeof value === 'string') {
    return { code: `plan_issue_${index + 1}`, message: value }
  }
  if (!isRecord(value)) throw new Error('Invalid grading plan issue')
  const code = typeof value.code === 'string' && value.code
    ? value.code
    : `plan_issue_${index + 1}`
  const messageCandidates = [value.message, value.detail, value.title, value.reason, value.code]
  const message = messageCandidates.find((item): item is string => (
    typeof item === 'string' && item.trim().length > 0
  )) ?? '计划中有一项需要教师确认。'
  const countCandidate = value.count ?? value.affected_count
  const questionIds = Array.isArray(value.question_ids)
    ? value.question_ids.filter((item): item is string => typeof item === 'string')
    : undefined
  return {
    code,
    message,
    ...(finiteInteger(countCandidate) ? { count: countCandidate } : {}),
    ...(questionIds?.length ? { question_ids: questionIds } : {}),
  }
}

export function decodeGradingPlan(value: unknown): GradingPlan {
  assertNoPathLikeKeys(value)
  if (!isRecord(value)
    || !isGradingMode(value.mode)
    || (value.status !== 'ready' && value.status !== 'blocked')
    || !isRecord(value.counts)
    || !isRecord(value.requests)
    || !isRecord(value.batching)
    || !Array.isArray(value.warnings)
    || !Array.isArray(value.blockers)) {
    throw new Error('Invalid grading plan')
  }
  return {
    mode: value.mode,
    status: value.status,
    counts: decodePlanMetrics(value.counts, 'counts'),
    requests: decodePlanMetrics(value.requests, 'requests'),
    batching: decodePlanMetrics(value.batching, 'batching'),
    warnings: value.warnings.map(decodePlanIssue),
    blockers: value.blockers.map(decodePlanIssue),
  }
}

async function sha256(file: File): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, '0')).join('')
}

export function fetchGradingWorkspace(sessionId: number, signal?: AbortSignal): Promise<GradingWorkspace> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/grading-workspace`, {
    decode: decodeGradingWorkspace, signal,
  })
}

export async function uploadScan(sessionId: number, file: File, replacement = false, signal?: AbortSignal): Promise<{ duplicate: boolean, file: ScanUploadFile }> {
  const digest = await sha256(file)
  const suffix = replacement ? '?replacement=true' : ''
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/scan-uploads${suffix}`, {
    method: 'POST', rawBody: file, signal, timeoutMs: 120_000,
    headers: {
      'content-type': file.type,
      'x-upload-filename': encodeURIComponent(file.name),
      'x-content-sha256': digest,
    },
    decode(value) {
      if (!isRecord(value) || typeof value.duplicate !== 'boolean') throw new Error('Invalid upload response')
      return { duplicate: value.duplicate, file: decodeUploadFile(value.file) }
    },
  })
}

export function removeScan(sessionId: number, uploadId: string, revision: number, replacement = false): Promise<ScanUploadBatch> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/scan-uploads/${encodeURIComponent(uploadId)}?expected_revision=${revision}${replacement ? '&replacement=true' : ''}`, {
    method: 'DELETE', decode: decodeUploadBatch,
  })
}

export function clearScans(sessionId: number, revision: number, replacement = false): Promise<ScanUploadBatch> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/scan-uploads?expected_revision=${revision}${replacement ? '&replacement=true' : ''}`, {
    method: 'DELETE', decode: decodeUploadBatch,
  })
}

export function freezeScans(sessionId: number, revision: number): Promise<ScanUploadBatch> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/scan-uploads/freeze`, {
    method: 'POST', body: { expected_revision: revision }, decode: decodeUploadBatch,
  })
}

export function startNewScanBatch(sessionId: number): Promise<ScanUploadBatch> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/scan-uploads/new-batch`, {
    method: 'POST', decode: decodeUploadBatch,
  })
}

export function beginScanReplacement(sessionId: number): Promise<ScanUploadBatch> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/scan-uploads/replacement`, {
    method: 'POST', decode: decodeUploadBatch,
  })
}

export function cancelScanReplacement(sessionId: number): Promise<void> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/scan-uploads/replacement/cancel`, {
    method: 'POST',
    decode(value) {
      if (!isRecord(value) || !hasExactKeys(value, ['cancelled']) || value.cancelled !== true) {
        throw new Error('Invalid scan replacement cancellation')
      }
    },
  })
}

export function commitScanReplacement(sessionId: number, revision: number): Promise<ScanUploadBatch> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/scan-uploads/replacement/commit`, {
    method: 'POST', body: { expected_revision: revision }, decode: decodeUploadBatch,
    timeoutMs: 60_000,
  })
}

export function startPreflight(sessionId: number): Promise<JobResponse> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/scan/analyze`, {
    method: 'POST', body: { enhance_images: true }, decode: decodeJobResponse,
  })
}

export function fetchPreflight(sessionId: number): Promise<ScanPreflight> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/scan/preflight`, { decode: decodePreflight })
}

export function fetchScanStudentOptions(sessionId: number): Promise<ScanStudentMatchOption[]> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/scan/student-options`, {
    decode(value) {
      if (!isRecord(value) || !hasExactKeys(value, ['items']) || !Array.isArray(value.items)) {
        throw new Error('Invalid scan student options')
      }
      return value.items.map((item) => {
        if (!isRecord(item)
          || !hasExactKeys(item, ['id', 'student_code', 'name', 'class_name', 'pinyin_initials', 'pinyin_full'])
          || !finiteInteger(item.id, 1)
          || typeof item.student_code !== 'string'
          || typeof item.name !== 'string'
          || !(item.class_name === null || typeof item.class_name === 'string')
          || typeof item.pinyin_initials !== 'string'
          || typeof item.pinyin_full !== 'string') {
          throw new Error('Invalid scan student option')
        }
        return item as unknown as ScanStudentMatchOption
      })
    },
  })
}

export function fetchGradingPlan(sessionId: number, mode: SelectableGradingMode, signal?: AbortSignal): Promise<GradingPlan> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/grading/plan`, {
    method: 'POST',
    body: { grading_mode: mode },
    signal,
    decode: decodeGradingPlan,
  })
}

export function saveScanDecisions(sessionId: number, revision: number, decisions: ScanDecision[], allowPartialMatches = false): Promise<ScanDecisionSaveResult> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/scan/preflight/decisions`, {
    method: 'PUT', body: { expected_revision: revision, decisions, allow_partial_matches: allowPartialMatches },
    decode(value) {
      assertNoPathLikeKeys(value)
      if (!isRecord(value) || !finiteInteger(value.revision) || !Array.isArray(value.decisions)
        || !finiteInteger(value.pending_issue_count) || !finiteInteger(value.ready_to_grade)) {
        throw new Error('Invalid scan decisions')
      }
      return value as unknown as ScanDecisionSaveResult
    },
  })
}

export function startGrading(sessionId: number, mode: 'ai', uploadRevision: number, decisionRevision: number, confirmPendingIssues: boolean): Promise<JobResponse> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/grading/run`, {
    method: 'POST',
    body: { grading_mode: mode, upload_revision: uploadRevision, decision_revision: decisionRevision,
      confirm_pending_issues: confirmPendingIssues, enhance_images: true },
    decode: decodeJobResponse,
  })
}

export function controlGrading(sessionId: number, runId: number, action: 'pause' | 'resume' | 'retry-failed'): Promise<GradingRunSummary | JobResponse> {
  if (action === 'pause') {
    return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/grading/runs/${runId}/pause`, {
      method: 'POST', decode: decodeRun,
    })
  }
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/grading/runs/${runId}/${action}`, {
    method: 'POST', decode: decodeJobResponse,
  })
}

export function cancelGrading(sessionId: number, runId: number, jobId: number | null): Promise<GradingRunSummary> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/grading/runs/${runId}/cancel`, {
    method: 'POST', body: { job_id: jobId }, decode: decodeRun,
  })
}

export function supplementGrading(sessionId: number, runId: number): Promise<JobResponse> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/grading/runs/${runId}/supplement-new-matches`, {
    method: 'POST', decode: decodeJobResponse,
  })
}
