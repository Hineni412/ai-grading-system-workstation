import { apiClient } from './client'
import { isNullableString, isRecord } from './validation'

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
}

function isJobStatus(value: unknown): value is JobStatus {
  return typeof value === 'string' && JOB_STATUSES.some((status) => status === value)
}

export function decodeJobResponse(value: unknown): JobResponse {
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

export const jobApi: JobApi = {
  async getJob(id, signal) {
    const jobId = requireJobId(id)
    return apiClient.request(`/api/jobs/${jobId}`, {
      decode: decodeJobResponse,
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
