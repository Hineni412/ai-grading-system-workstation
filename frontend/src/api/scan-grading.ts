import { apiClient } from './client'
import { decodeJobResponse, type JobResponse } from './jobs'
import { assertNoPathLikeKeys, isRecord } from './validation'

export type UploadBatchState = 'draft' | 'frozen'
export type GradingMode = 'full_paper' | 'hybrid_batch'

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
  job_status?: string | null
  progress?: number | null
  started_at?: string | null
  updated_at?: string | null
  mode: GradingMode
  state: string
  counts: Record<'graded' | 'grading' | 'pending' | 'skipped' | 'failed' | 'conflict' | 'total', number>
  allowed_actions: string[]
}

export interface GradingWorkspace {
  session_id: number
  upload_batch: ScanUploadBatch
  grading_run: GradingRunSummary | null
}

export interface ScanDecision {
  target_type: 'group' | 'issue'
  target_id: string
  action: 'match' | 'invalid' | 'pending'
  student_id?: number | null
}

export interface ScanPreflight {
  revision: number
  summary: Record<string, number>
  groups: Record<string, unknown>[]
  issues: Record<string, unknown>[]
  absent_students: Record<string, unknown>[]
  warnings: string[]
  decisions: ScanDecision[]
  pending_issue_count: number
}

function positiveSessionId(value: number): number {
  if (!Number.isSafeInteger(value) || value <= 0) throw new Error('Invalid session id')
  return value
}

function finiteInteger(value: unknown, minimum = 0): value is number {
  return Number.isSafeInteger(value) && Number(value) >= minimum
}

function decodeUploadFile(value: unknown): ScanUploadFile {
  if (!isRecord(value)
    || typeof value.id !== 'string' || !value.id
    || typeof value.name !== 'string' || !value.name
    || typeof value.media_type !== 'string'
    || !finiteInteger(value.size_bytes)
    || typeof value.sha256_prefix !== 'string' || !/^[0-9a-f]{12}$/.test(value.sha256_prefix)
    || typeof value.added_at !== 'string') throw new Error('Invalid scan upload file')
  return value as unknown as ScanUploadFile
}

function decodeUploadBatch(value: unknown): ScanUploadBatch {
  if (!isRecord(value)
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
  if (!isRecord(value) || !finiteInteger(value.run_id, 1)
    || (value.mode !== 'full_paper' && value.mode !== 'hybrid_batch')
    || typeof value.state !== 'string' || !isRecord(value.counts)
    || !Array.isArray(value.allowed_actions) || !value.allowed_actions.every((item) => typeof item === 'string')) {
    throw new Error('Invalid grading run')
  }
  if (value.job_id !== undefined && value.job_id !== null && !finiteInteger(value.job_id, 1)) throw new Error('Invalid grading job id')
  if (value.progress !== undefined && value.progress !== null
    && (typeof value.progress !== 'number' || value.progress < 0 || value.progress > 1)) throw new Error('Invalid grading progress')
  const counts = value.counts
  const keys = ['graded', 'grading', 'pending', 'skipped', 'failed', 'conflict', 'total'] as const
  if (!keys.every((key) => finiteInteger(counts[key]))) throw new Error('Invalid grading counts')
  return value as unknown as GradingRunSummary
}

export function decodeGradingWorkspace(value: unknown): GradingWorkspace {
  if (!isRecord(value) || !finiteInteger(value.session_id, 1) || !('upload_batch' in value)
    || !(value.grading_run === null || isRecord(value.grading_run))) throw new Error('Invalid grading workspace')
  const batchForSafety = isRecord(value.upload_batch) ? { ...value.upload_batch } : value.upload_batch
  if (isRecord(batchForSafety)) {
    delete batchForSafety.files
    delete batchForSafety.file_count
  }
  assertNoPathLikeKeys({ ...value, upload_batch: batchForSafety })
  return {
    session_id: value.session_id,
    upload_batch: decodeUploadBatch(value.upload_batch),
    grading_run: value.grading_run === null ? null : decodeRun(value.grading_run),
  }
}

function decodePreflight(value: unknown): ScanPreflight {
  assertNoPathLikeKeys(value)
  if (!isRecord(value) || !finiteInteger(value.revision) || !isRecord(value.summary)
    || !Array.isArray(value.groups) || !Array.isArray(value.issues)
    || !Array.isArray(value.absent_students) || !Array.isArray(value.warnings)
    || !Array.isArray(value.decisions) || !finiteInteger(value.pending_issue_count)) {
    throw new Error('Invalid scan preflight')
  }
  return value as unknown as ScanPreflight
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

export async function uploadScan(sessionId: number, file: File, signal?: AbortSignal): Promise<{ duplicate: boolean, file: ScanUploadFile }> {
  const digest = await sha256(file)
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/scan-uploads`, {
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

export function removeScan(sessionId: number, uploadId: string, revision: number): Promise<ScanUploadBatch> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/scan-uploads/${encodeURIComponent(uploadId)}?expected_revision=${revision}`, {
    method: 'DELETE', decode: decodeUploadBatch,
  })
}

export function clearScans(sessionId: number, revision: number): Promise<ScanUploadBatch> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/scan-uploads?expected_revision=${revision}`, {
    method: 'DELETE', decode: decodeUploadBatch,
  })
}

export function freezeScans(sessionId: number, revision: number): Promise<ScanUploadBatch> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/scan-uploads/freeze`, {
    method: 'POST', body: { expected_revision: revision }, decode: decodeUploadBatch,
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

export function saveScanDecisions(sessionId: number, revision: number, decisions: ScanDecision[]): Promise<{ revision: number, decisions: ScanDecision[], pending_issue_count: number }> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/scan/preflight/decisions`, {
    method: 'PUT', body: { expected_revision: revision, decisions },
    decode(value) {
      assertNoPathLikeKeys(value)
      if (!isRecord(value) || !finiteInteger(value.revision) || !Array.isArray(value.decisions)
        || !finiteInteger(value.pending_issue_count)) throw new Error('Invalid scan decisions')
      return value as { revision: number, decisions: ScanDecision[], pending_issue_count: number }
    },
  })
}

export function startGrading(sessionId: number, mode: GradingMode, uploadRevision: number, decisionRevision: number, confirmPendingIssues: boolean): Promise<JobResponse> {
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

export function cancelGrading(sessionId: number, runId: number, jobId: number): Promise<GradingRunSummary> {
  return apiClient.request(`/api/sessions/${positiveSessionId(sessionId)}/grading/runs/${runId}/cancel`, {
    method: 'POST', body: { job_id: jobId }, decode: decodeRun,
  })
}
