import { apiClient } from './client'
import { assertNoPathLikeKeys, isNullableString, isRecord } from './validation'

export const JOB_STATUSES = [
  'queued',
  'running',
  'paused',
  'succeeded',
  'failed',
  'cancelled',
] as const

export type JobStatus = (typeof JOB_STATUSES)[number]

export const TERMINAL_JOB_STATUSES = new Set<JobStatus>([
  'succeeded',
  'failed',
  'cancelled',
])

export interface JobResponse {
  id: number
  job_type: string
  payload: Record<string, unknown>
  result: Record<string, unknown>
  status: JobStatus
  progress: number
  stage: string
  detail: string
  error: string | null
  cancel_requested: boolean
  created_at: string
  started_at: string | null
  updated_at: string
  finished_at: string | null
}

export interface JobApi {
  getJob(id: number, signal?: AbortSignal): Promise<JobResponse>
  cancelJob(id: number, signal?: AbortSignal): Promise<JobResponse>
  getJobStatusBatch(ids: number[], signal?: AbortSignal): Promise<JobStatusBatchItem[]>
}

export interface JobStatusBatchItem {
  id: number
  found: boolean
  job: JobResponse | null
}

interface JobIdListResponse {
  items: Array<{ id: number; job_type: string }>
}

function isJobStatus(value: unknown): value is JobStatus {
  return typeof value === 'string' && JOB_STATUSES.some((status) => status === value)
}

export function decodeJobResponse(value: unknown): JobResponse {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value) ||
    !Number.isSafeInteger(value.id) ||
    Number(value.id) <= 0 ||
    typeof value.job_type !== 'string' ||
    !value.job_type.trim() ||
    !isRecord(value.payload) ||
    !isRecord(value.result) ||
    !isJobStatus(value.status) ||
    typeof value.progress !== 'number' ||
    !Number.isFinite(value.progress) ||
    typeof value.stage !== 'string' ||
    typeof value.detail !== 'string' ||
    !isNullableString(value.error) ||
    typeof value.cancel_requested !== 'boolean' ||
    typeof value.created_at !== 'string' ||
    !value.created_at ||
    !isNullableString(value.started_at) ||
    typeof value.updated_at !== 'string' ||
    !value.updated_at ||
    !isNullableString(value.finished_at)
  ) {
    throw new Error('Invalid Job response')
  }
  return value as unknown as JobResponse
}

function requireJobId(id: number): number {
  if (!Number.isSafeInteger(id) || id <= 0) throw new Error('Invalid Job id')
  return id
}

function decodeJobStatusBatch(value: unknown): JobStatusBatchItem[] {
  assertNoPathLikeKeys(value)
  if (!isRecord(value) || !Array.isArray(value.items)) {
    throw new Error('Invalid job status batch response')
  }
  return value.items.map((item) => {
    if (
      !isRecord(item) ||
      !Number.isSafeInteger(item.id) ||
      Number(item.id) <= 0 ||
      typeof item.found !== 'boolean'
    ) {
      throw new Error('Invalid job status batch response')
    }
    if (item.found) {
      return { id: Number(item.id), found: true, job: decodeJobResponse(item.job) }
    }
    if (item.job !== null && item.job !== undefined) {
      throw new Error('Invalid job status batch response')
    }
    return { id: Number(item.id), found: false, job: null }
  })
}

function decodeJobIdList(value: unknown): JobIdListResponse {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || !Array.isArray(value.items)
    || !value.items.every((item) => (
      isRecord(item)
      && Number.isSafeInteger(item.id)
      && Number(item.id) > 0
      && typeof item.job_type === 'string'
      && item.job_type.trim().length > 0
    ))
  ) {
    throw new Error('Invalid job list response')
  }
  return {
    items: value.items.map((item) => ({
      id: Number(item.id),
      job_type: String(item.job_type),
    })),
  }
}

export async function findLatestJob(
  sessionId: number,
  jobType: string,
): Promise<JobResponse | null> {
  if (!Number.isSafeInteger(sessionId) || sessionId <= 0) {
    throw new Error('Invalid session id')
  }
  const cleanJobType = jobType.trim()
  if (!cleanJobType) throw new Error('Invalid job type')
  const query = new URLSearchParams({
    session_id: String(sessionId),
    job_type: cleanJobType,
    page: '1',
    page_size: '1',
  })
  const page = await apiClient.request(`/api/jobs?${query.toString()}`, {
    decode: decodeJobIdList,
  })
  const summary = page.items[0]
  if (!summary || summary.job_type !== cleanJobType) return null
  return jobApi.getJob(summary.id)
}

export const jobApi: JobApi = {
  async getJob(id, signal) {
    const jobId = requireJobId(id)
    return apiClient.request(`/api/jobs/${jobId}`, {
      decode: decodeJobResponse,
      signal,
    })
  },
  async getJobStatusBatch(ids, signal) {
    const unique = [...new Set(ids.map(requireJobId))]
    if (unique.length === 0 || unique.length > 50) {
      throw new Error('Invalid job id batch')
    }
    return apiClient.request('/api/jobs/status-batch', {
      method: 'POST',
      body: { ids: unique },
      decode: decodeJobStatusBatch,
      signal,
    })
  },
  async cancelJob(id, signal) {
    const jobId = requireJobId(id)
    return apiClient.request(`/api/jobs/${jobId}/cancel`, {
      method: 'POST',
      decode: decodeJobResponse,
      signal,
    })
  },
}
