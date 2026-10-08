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


export interface ChapterTypeJobSummary {
  note: string
  chapters: { label: string; newTypeCount: number; questionCount: number; unclassifiedCount: number; types: { name: string; questionCount: number }[] }[]
}

/** Only public chapter counts and type names are shown in parent task results. */
export function chapterTypeJobSummary(parent: JobResponse | null, jobs: Readonly<Record<number, JobResponse>>): ChapterTypeJobSummary | null {
  if (!parent) return null
  const syncId = parent.result.question_bank_sync_job_id
  const directId = parent.result.chapter_type_job_id
  const source = Number.isSafeInteger(directId) && Number(directId) > 0 ? parent
    : Number.isSafeInteger(syncId) && Number(syncId) > 0 ? jobs[Number(syncId)] ?? parent : parent
  const id = source.result.chapter_type_job_id
  const child = parent.job_type === 'chapter_type_organize' ? parent
    : Number.isSafeInteger(id) && Number(id) > 0 ? jobs[Number(id)] : undefined
  const state = source.result.chapter_type_state
  if (!child && !(Number.isSafeInteger(id) && Number(id) > 0) && typeof state !== 'string') return null
  const empty = (note: string): ChapterTypeJobSummary => ({ note, chapters: [] })
  if (child?.result.outcome === 'failed') return empty('题型整理未完成，原标准已保留。')
  if (state === 'submission_failed') return empty('整理任务未能确认提交；不会自动追加请求。')
  if (child?.status === 'failed' || child?.result.outcome === 'outcome_unknown') return empty('题型整理任务状态需要核对；不会自动追加请求。')
  if (child?.status === 'cancelled') return empty('题型整理已取消；请核对现有标准，不会自动追加请求。')
  if (child?.status !== 'succeeded') return empty(child?.status === 'running' ? '题型整理进行中。' : child?.status === 'paused' ? '题型整理已暂停。' : '等待题型整理完成。')
  if (child.result.outcome === 'skipped') return empty('本次可用题目尚未达到章节整理条件，原标准保持。')
  if (child.result.outcome !== 'published') return empty('题型整理已结束，请核对整理结果。')
  const count = (value: unknown) => Number.isSafeInteger(value) && Number(value) >= 0 ? Number(value) : 0
  const chapters = (Array.isArray(child.result.chapter_summaries) ? child.result.chapter_summaries : [])
    .filter(isRecord).filter(item => typeof item.label === 'string')
    .map(item => ({ label: String(item.label), newTypeCount: count(item.new_type_count), questionCount: count(item.question_count),
      unclassifiedCount: count(item.unclassified_count), types: (Array.isArray(item.types) ? item.types : [])
        .filter(isRecord).filter(type => typeof type.name === 'string').map(type => ({ name: String(type.name), questionCount: count(type.question_count) })) }))
  return { note: '本次章节题型已整理。', chapters }
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
