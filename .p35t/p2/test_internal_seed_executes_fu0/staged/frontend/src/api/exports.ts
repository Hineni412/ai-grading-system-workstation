import { apiClient } from './client'
import {
  decodeJobResponse,
  jobApi,
  JOB_STATUSES,
  type JobResponse,
  type JobStatus,
} from './jobs'
import { assertNoPathLikeKeys, isNullableString, isRecord } from './validation'

export const REPORT_TYPES = [
  'score_excel',
  'annotated_original_pdf',
] as const

export type ReportType = (typeof REPORT_TYPES)[number]

export const REPORT_FILE_STATUSES = [
  'pending',
  'available',
  'expired',
  'failed',
  'cancelled',
  'unavailable',
] as const

export type ReportFileStatus = (typeof REPORT_FILE_STATUSES)[number]

export interface ReportHistoryJob extends JobResponse {
  is_current_revision: boolean
  file_status: ReportFileStatus
}

export interface ReportContext {
  score_revision: string
  has_results: boolean
  jobs: ReportHistoryJob[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface TrainingTaskSummary {
  id: number
  task_code: string
  created_by: string | null
  scope_snapshot: Record<string, unknown>
  exam_scope: Record<string, unknown>
  generation_config: Record<string, unknown>
  warnings: string[]
  status: 'draft' | 'ready' | 'exporting' | 'completed' | 'cancelled' | 'failed'
  created_at: string
  updated_at: string
}

export interface TrainingTaskList {
  items: TrainingTaskSummary[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface TrainingVariant {
  id: number
  variant_key: string
  variant_type: string
  students: Record<string, unknown>[]
  items: Record<string, unknown>[]
}

export interface TrainingTaskDetail extends TrainingTaskSummary {
  diagnosis_snapshot: Record<string, unknown>
  variants: TrainingVariant[]
  exports: Record<string, unknown>[]
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

export interface JobSummaryList {
  items: JobSummary[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface TrainingExportRequest {
  variant_id?: number
  format: 'docx' | 'markdown'
  audience?: 'student' | 'teacher'
}

export interface DownloadedJobFile {
  blob: Blob
  filename: string
}

function requirePositiveInteger(value: number, label: string): number {
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new Error(`Invalid ${label}`)
  }
  return value
}

function isReportFileStatus(value: unknown): value is ReportFileStatus {
  return REPORT_FILE_STATUSES.some((status) => status === value)
}

function isTrainingStatus(
  value: unknown,
): value is TrainingTaskSummary['status'] {
  return (
    value === 'draft'
    || value === 'ready'
    || value === 'exporting'
    || value === 'completed'
    || value === 'cancelled'
    || value === 'failed'
  )
}

function decodeReportHistoryJob(value: unknown): ReportHistoryJob {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || typeof value.is_current_revision !== 'boolean'
    || !isReportFileStatus(value.file_status)
  ) {
    throw new Error('Invalid report history job')
  }
  const job = decodeJobResponse(value)
  return {
    ...job,
    is_current_revision: value.is_current_revision,
    file_status: value.file_status,
  }
}

export function decodeReportContext(value: unknown): ReportContext {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || typeof value.score_revision !== 'string'
    || !/^[0-9a-f]{64}$/.test(value.score_revision)
    || typeof value.has_results !== 'boolean'
    || !Array.isArray(value.jobs)
    || !Number.isSafeInteger(value.total)
    || Number(value.total) < 0
    || !Number.isSafeInteger(value.page)
    || Number(value.page) <= 0
    || !Number.isSafeInteger(value.page_size)
    || Number(value.page_size) <= 0
    || !Number.isSafeInteger(value.total_pages)
    || Number(value.total_pages) <= 0
  ) {
    throw new Error('Invalid report context')
  }
  return {
    score_revision: value.score_revision,
    has_results: value.has_results,
    jobs: value.jobs.map(decodeReportHistoryJob),
    total: Number(value.total),
    page: Number(value.page),
    page_size: Number(value.page_size),
    total_pages: Number(value.total_pages),
  }
}

function decodeTrainingTaskSummary(value: unknown): TrainingTaskSummary {
  if (
    !isRecord(value)
    || !Number.isSafeInteger(value.id)
    || Number(value.id) <= 0
    || typeof value.task_code !== 'string'
    || !value.task_code.trim()
    || !isNullableString(value.created_by)
    || !isRecord(value.scope_snapshot)
    || !isRecord(value.exam_scope)
    || !isRecord(value.generation_config)
    || !Array.isArray(value.warnings)
    || !value.warnings.every((warning) => typeof warning === 'string')
    || !isTrainingStatus(value.status)
    || typeof value.created_at !== 'string'
    || !value.created_at
    || typeof value.updated_at !== 'string'
    || !value.updated_at
  ) {
    throw new Error('Invalid training task')
  }
  return value as unknown as TrainingTaskSummary
}

export function decodeTrainingTaskList(value: unknown): TrainingTaskList {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || !Array.isArray(value.items)
    || !Number.isSafeInteger(value.total)
    || Number(value.total) < 0
    || !Number.isSafeInteger(value.page)
    || Number(value.page) <= 0
    || !Number.isSafeInteger(value.page_size)
    || Number(value.page_size) <= 0
    || !Number.isSafeInteger(value.total_pages)
    || Number(value.total_pages) <= 0
  ) {
    throw new Error('Invalid training task list')
  }
  return {
    items: value.items.map(decodeTrainingTaskSummary),
    total: Number(value.total),
    page: Number(value.page),
    page_size: Number(value.page_size),
    total_pages: Number(value.total_pages),
  }
}

export function decodeTrainingTaskDetail(value: unknown): TrainingTaskDetail {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || !isRecord(value.diagnosis_snapshot)
    || !Array.isArray(value.variants)
    || !value.variants.every((variant) => (
      isRecord(variant)
      && Number.isSafeInteger(variant.id)
      && Number(variant.id) > 0
      && typeof variant.variant_key === 'string'
      && Boolean(variant.variant_key.trim())
      && typeof variant.variant_type === 'string'
      && Boolean(variant.variant_type.trim())
      && Array.isArray(variant.students)
      && variant.students.every(isRecord)
      && Array.isArray(variant.items)
      && variant.items.every(isRecord)
    ))
    || !Array.isArray(value.exports)
    || !value.exports.every(isRecord)
  ) {
    throw new Error('Invalid training task detail')
  }
  return {
    ...decodeTrainingTaskSummary(value),
    diagnosis_snapshot: value.diagnosis_snapshot,
    variants: value.variants as TrainingVariant[],
    exports: value.exports,
  }
}

function decodeJobSummary(value: unknown): JobSummary {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || !Number.isSafeInteger(value.id)
    || Number(value.id) <= 0
    || typeof value.job_type !== 'string'
    || !value.job_type.trim()
    || !JOB_STATUSES.some((status) => status === value.status)
    || typeof value.progress !== 'number'
    || !Number.isFinite(value.progress)
    || typeof value.stage !== 'string'
    || typeof value.detail !== 'string'
    || typeof value.created_at !== 'string'
    || !value.created_at
    || !isNullableString(value.started_at)
    || typeof value.updated_at !== 'string'
    || !value.updated_at
    || !isNullableString(value.finished_at)
  ) {
    throw new Error('Invalid job summary')
  }
  return value as unknown as JobSummary
}

export function decodeJobSummaryList(value: unknown): JobSummaryList {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || !Array.isArray(value.items)
    || !Number.isSafeInteger(value.total)
    || Number(value.total) < 0
    || !Number.isSafeInteger(value.page)
    || Number(value.page) <= 0
    || !Number.isSafeInteger(value.page_size)
    || Number(value.page_size) <= 0
    || !Number.isSafeInteger(value.total_pages)
    || Number(value.total_pages) < 0
  ) {
    throw new Error('Invalid job summary list')
  }
  return {
    items: value.items.map(decodeJobSummary),
    total: Number(value.total),
    page: Number(value.page),
    page_size: Number(value.page_size),
    total_pages: Number(value.total_pages),
  }
}

function filenameFromDisposition(disposition: string | null, jobId: number): string {
  const encoded = disposition?.match(/filename\*\s*=\s*utf-8''([^;]+)/i)?.[1]
  let decoded = ''
  if (encoded) {
    try {
      decoded = decodeURIComponent(encoded.trim())
    } catch {
      decoded = ''
    }
  }
  const plain = disposition?.match(/filename\s*=\s*"?([^";]+)"?/i)?.[1]?.trim()
  const candidate = decoded || plain || `download-${jobId}`
  const basename = candidate.replace(/\\/g, '/').split('/').pop()?.trim()
  return basename || `download-${jobId}`
}

async function downloadJobFile(jobId: number): Promise<DownloadedJobFile> {
  const response = await apiClient.download(`/api/jobs/${jobId}/download`)
  return {
    blob: response.blob,
    filename: filenameFromDisposition(
      response.contentDisposition,
      jobId,
    ),
  }
}

export const exportsApi = {
  async getReportContext(
    sessionId: number,
    page = 1,
    pageSize = 100,
    signal?: AbortSignal,
  ): Promise<ReportContext> {
    const id = requirePositiveInteger(sessionId, 'session id')
    const safePage = requirePositiveInteger(page, 'page')
    const safePageSize = requirePositiveInteger(pageSize, 'page size')
    return apiClient.request(
      `/api/sessions/${id}/reports/context?page=${safePage}&page_size=${safePageSize}`,
      {
      decode: decodeReportContext,
      signal,
      },
    )
  },

  async submitReport(
    sessionId: number,
    reportType: ReportType,
    forceRegenerate = false,
    signal?: AbortSignal,
  ): Promise<JobResponse> {
    const id = requirePositiveInteger(sessionId, 'session id')
    if (!REPORT_TYPES.some((type) => type === reportType)) {
      throw new Error('Invalid report type')
    }
    return apiClient.request(`/api/sessions/${id}/reports/export`, {
      method: 'POST',
      body: {
        report_type: reportType,
        force_regenerate: forceRegenerate,
      },
      decode: decodeJobResponse,
      signal,
    })
  },

  async listTrainingTasks(
    page = 1,
    pageSize = 20,
    signal?: AbortSignal,
  ): Promise<TrainingTaskList> {
    const safePage = requirePositiveInteger(page, 'page')
    const safePageSize = requirePositiveInteger(pageSize, 'page size')
    return apiClient.request(
      `/api/training/tasks?page=${safePage}&page_size=${safePageSize}`,
      {
        decode: decodeTrainingTaskList,
        signal,
      },
    )
  },

  async getTrainingTask(
    taskId: number,
    signal?: AbortSignal,
  ): Promise<TrainingTaskDetail> {
    const id = requirePositiveInteger(taskId, 'training task id')
    return apiClient.request(`/api/training/tasks/${id}`, {
      decode: decodeTrainingTaskDetail,
      signal,
    })
  },

  async submitTrainingExport(
    taskId: number,
    body: TrainingExportRequest,
    signal?: AbortSignal,
  ): Promise<JobResponse> {
    const id = requirePositiveInteger(taskId, 'training task id')
    if (body.variant_id !== undefined) {
      requirePositiveInteger(body.variant_id, 'training variant id')
    }
    if (body.format !== 'docx' && body.format !== 'markdown') {
      throw new Error('Invalid training export format')
    }
    if (
      body.audience !== undefined
      && body.audience !== 'student'
      && body.audience !== 'teacher'
    ) {
      throw new Error('Invalid training export audience')
    }
    if (body.variant_id === undefined && body.audience !== undefined) {
      throw new Error('Task bundle export does not accept audience')
    }
    if (body.variant_id !== undefined && body.audience === undefined) {
      throw new Error('Variant export requires audience')
    }
    return apiClient.request(`/api/training/tasks/${id}/exports`, {
      method: 'POST',
      body,
      decode: decodeJobResponse,
      signal,
    })
  },

  async retryTrainingExport(
    jobId: number,
    signal?: AbortSignal,
  ): Promise<JobResponse> {
    const id = requirePositiveInteger(jobId, 'job id')
    return apiClient.request(`/api/training/exports/jobs/${id}/retry`, {
      method: 'POST',
      decode: decodeJobResponse,
      signal,
    })
  },

  async listTrainingExportJobs(
    page = 1,
    pageSize = 20,
    signal?: AbortSignal,
  ): Promise<JobSummaryList> {
    const safePage = requirePositiveInteger(page, 'page')
    const safePageSize = requirePositiveInteger(pageSize, 'page size')
    return apiClient.request(
      `/api/jobs?job_type=training_export&page=${safePage}&page_size=${safePageSize}`,
      {
        decode: decodeJobSummaryList,
        signal,
      },
    )
  },

  async getJob(id: number, signal?: AbortSignal): Promise<JobResponse> {
    return jobApi.getJob(requirePositiveInteger(id, 'job id'), signal)
  },

  async downloadJobFile(
    id: number,
  ): Promise<DownloadedJobFile> {
    return downloadJobFile(requirePositiveInteger(id, 'job id'))
  },
}
