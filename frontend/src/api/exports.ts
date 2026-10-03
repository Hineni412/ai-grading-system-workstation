import { apiClient } from './client'
import {
  decodeJobResponse,
  type JobResponse,
} from './jobs'
import { assertNoPathLikeKeys, isNullableString, isRecord } from './validation'

export const REPORT_TYPES = [
  'score_excel',
  'annotated_original_pdf',
  'personal_analysis_html',
] as const

export type ReportType = (typeof REPORT_TYPES)[number]

export interface ScoreExcelOptions {
  hide_bottom_enabled: boolean
  hide_bottom_n: number
  manual_hidden_student_ids: number[]
}

export interface AnalysisPreflight {
  report_type: string
  configured: boolean
  service_name: string | null
  model_name: string | null
  call_count: number
  estimated_total_tokens: number
  cache_hits: number
  /** 前置错因整理预计新增调用次数；旧版后端不返回时按 0 处理。 */
  cause_call_count: number
  /** 本场需要错因整理的失分题总数（含已有结果不重复调用的题）。 */
  cause_total_questions: number
  cause_estimated_tokens: number
}

export const REPORT_FILE_STATUSES = [
  'pending',
  'available',
  'expired',
  'failed',
  'cancelled',
  'unavailable',
] as const

export type ReportFileStatus = (typeof REPORT_FILE_STATUSES)[number]

export interface ReportFileDeleteResult {
  job_id: number
  deleted: boolean
  freed_bytes: number
}

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

export type AnalysisReviewNoteStatus = 'pending' | 'confirmed'

export interface AnalysisReviewNoteItem {
  student_id: number
  student_code: string | null
  student_name: string
  class_name: string | null
  question_id: string
  display_label: string
  note: string
  lock_revision: number
  status: AnalysisReviewNoteStatus
  review_item_id: string | null
}

export interface AnalysisReviewNotes {
  session_id: number
  generated_at: string | null
  items: AnalysisReviewNoteItem[]
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

export function decodeReportFileDelete(value: unknown): ReportFileDeleteResult {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || !Number.isSafeInteger(value.job_id)
    || Number(value.job_id) <= 0
    || typeof value.deleted !== 'boolean'
    || !Number.isSafeInteger(value.freed_bytes)
    || Number(value.freed_bytes) < 0
  ) {
    throw new Error('Invalid report file delete result')
  }
  return {
    job_id: Number(value.job_id),
    deleted: value.deleted,
    freed_bytes: Number(value.freed_bytes),
  }
}

export function decodeAnalysisPreflight(value: unknown): AnalysisPreflight {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || typeof value.report_type !== 'string'
    || !value.report_type.trim()
    || typeof value.configured !== 'boolean'
    || !isNullableString(value.service_name)
    || !isNullableString(value.model_name)
    || !Number.isSafeInteger(value.call_count)
    || Number(value.call_count) < 0
    || !Number.isSafeInteger(value.estimated_total_tokens)
    || Number(value.estimated_total_tokens) < 0
    || !Number.isSafeInteger(value.cache_hits)
    || Number(value.cache_hits) < 0
    || !(value.cause_call_count === undefined
      || (Number.isSafeInteger(value.cause_call_count) && Number(value.cause_call_count) >= 0))
    || !(value.cause_total_questions === undefined
      || (Number.isSafeInteger(value.cause_total_questions) && Number(value.cause_total_questions) >= 0))
    || !(value.cause_estimated_tokens === undefined
      || (Number.isSafeInteger(value.cause_estimated_tokens) && Number(value.cause_estimated_tokens) >= 0))
  ) {
    throw new Error('Invalid analysis preflight')
  }
  return {
    report_type: value.report_type,
    configured: value.configured,
    service_name: value.service_name,
    model_name: value.model_name,
    call_count: Number(value.call_count),
    estimated_total_tokens: Number(value.estimated_total_tokens),
    cache_hits: Number(value.cache_hits),
    cause_call_count: Number(value.cause_call_count ?? 0),
    cause_total_questions: Number(value.cause_total_questions ?? 0),
    cause_estimated_tokens: Number(value.cause_estimated_tokens ?? 0),
  }
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

function decodeAnalysisReviewNoteItem(value: unknown): AnalysisReviewNoteItem {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || !Number.isSafeInteger(value.student_id)
    || Number(value.student_id) <= 0
    || !isNullableString(value.student_code)
    || typeof value.student_name !== 'string'
    || !isNullableString(value.class_name)
    || typeof value.question_id !== 'string'
    || !value.question_id.trim()
    || typeof value.display_label !== 'string'
    || typeof value.note !== 'string'
    || !Number.isSafeInteger(value.lock_revision)
    || Number(value.lock_revision) < 0
    || (value.status !== 'pending' && value.status !== 'confirmed')
    || !isNullableString(value.review_item_id)
  ) {
    throw new Error('Invalid analysis review note item')
  }
  return {
    student_id: Number(value.student_id),
    student_code: value.student_code,
    student_name: value.student_name,
    class_name: value.class_name,
    question_id: value.question_id,
    display_label: value.display_label,
    note: value.note,
    lock_revision: Number(value.lock_revision),
    status: value.status,
    review_item_id: value.review_item_id,
  }
}

export function decodeAnalysisReviewNotes(value: unknown): AnalysisReviewNotes {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || !Number.isSafeInteger(value.session_id)
    || Number(value.session_id) <= 0
    || !isNullableString(value.generated_at)
    || !Array.isArray(value.items)
  ) {
    throw new Error('Invalid analysis review notes')
  }
  return {
    session_id: Number(value.session_id),
    generated_at: value.generated_at,
    items: value.items.map(decodeAnalysisReviewNoteItem),
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
    excelOptions?: ScoreExcelOptions,
    signal?: AbortSignal,
    personalOptions?: {student_ids: number[]; publish: boolean},
  ): Promise<JobResponse> {
    const id = requirePositiveInteger(sessionId, 'session id')
    if (!REPORT_TYPES.some((type) => type === reportType)) {
      throw new Error('Invalid report type')
    }
    if (reportType !== 'score_excel' && excelOptions !== undefined) {
      throw new Error('Excel options are only valid for score reports')
    }
    let normalizedExcelOptions: ScoreExcelOptions | undefined
    if (excelOptions !== undefined) {
      if (
        typeof excelOptions.hide_bottom_enabled !== 'boolean'
        || !Number.isSafeInteger(excelOptions.hide_bottom_n)
        || excelOptions.hide_bottom_n < 0
        || excelOptions.hide_bottom_n > 100
        || !Array.isArray(excelOptions.manual_hidden_student_ids)
        || excelOptions.manual_hidden_student_ids.some(
          (studentId) => !Number.isSafeInteger(studentId) || studentId <= 0,
        )
      ) {
        throw new Error('Invalid Excel export options')
      }
      normalizedExcelOptions = {
        hide_bottom_enabled: excelOptions.hide_bottom_enabled,
        hide_bottom_n: excelOptions.hide_bottom_enabled
          ? excelOptions.hide_bottom_n
          : 0,
        manual_hidden_student_ids: [
          ...new Set(excelOptions.manual_hidden_student_ids),
        ].sort((left, right) => left - right),
      }
    }
    return apiClient.request(`/api/sessions/${id}/reports/export`, {
      method: 'POST',
      body: {
        report_type: reportType,
        ...(personalOptions ?? {}),
        force_regenerate: forceRegenerate,
        ...(normalizedExcelOptions === undefined
          ? {}
          : { excel_options: normalizedExcelOptions }),
      },
      decode: decodeJobResponse,
      signal,
    })
  },

  async deleteReportFile(
    sessionId: number,
    jobId: number,
    signal?: AbortSignal,
  ): Promise<ReportFileDeleteResult> {
    const id = requirePositiveInteger(sessionId, 'session id')
    const reportJobId = requirePositiveInteger(jobId, 'job id')
    return apiClient.request(
      `/api/sessions/${id}/reports/${reportJobId}/file`,
      {
        method: 'DELETE',
        decode: decodeReportFileDelete,
        signal,
      },
    )
  },

  async getAnalysisPreflight(
    sessionId: number,
    reportType: ReportType,
    signal?: AbortSignal,
    studentIds?: number[],
  ): Promise<AnalysisPreflight> {
    const id = requirePositiveInteger(sessionId, 'session id')
    if (!REPORT_TYPES.some((type) => type === reportType)) {
      throw new Error('Invalid report type')
    }
    return apiClient.request(
      `/api/sessions/${id}/reports/analysis-preflight?report_type=${encodeURIComponent(reportType)}${studentIds ? `&student_ids=${studentIds.join(',')}` : ''}`,
      {
        decode: decodeAnalysisPreflight,
        signal,
      },
    )
  },

  async downloadJobFile(
    id: number,
  ): Promise<DownloadedJobFile> {
    return downloadJobFile(requirePositiveInteger(id, 'job id'))
  },

  async getAnalysisReviewNotes(
    sessionId: number,
    signal?: AbortSignal,
  ): Promise<AnalysisReviewNotes> {
    const id = requirePositiveInteger(sessionId, 'session id')
    return apiClient.request(
      `/api/sessions/${id}/reports/analysis-review-notes`,
      {
        decode: decodeAnalysisReviewNotes,
        signal,
      },
    )
  },
}
