import type {
  ReportFileStatus,
  ReportHistoryJob,
  ReportType,
} from '../../api/exports'
import type { JobResponse, JobStatus } from '../../api/jobs'
import { formatTime as formatTimeValue } from '../../lib/format'

export type ReportDisplayStatus = ReportFileStatus | 'stale'

export interface ReportRow {
  type: ReportType
  kind: string
  title: string
  description: string
  jobs: ReportHistoryJob[]
  latest: ReportHistoryJob | null
  history: ReportHistoryJob[]
  liveJob: JobResponse | null
}

export function reportFilename(job: ReportHistoryJob): string {
  return typeof job.result.filename === 'string'
    ? job.result.filename
    : reportTypeLabel(job.payload.report_type)
}

export function reportTypeLabel(value: unknown): string {
  if (value === 'score_excel') return '成绩表'
  if (value === 'annotated_original_pdf') return '批注原卷'
  if (value === 'personal_analysis_html') return '学生个人分析报告'
  return '报表文件'
}

export function statusLabel(status: JobStatus): string {
  return {
    queued: '等待生成',
    running: '正在生成',
    paused: '已暂停',
    succeeded: '生成完成',
    failed: '生成失败',
    cancelled: '已取消',
  }[status]
}

export function reportDisplayStatus(job: ReportHistoryJob): ReportDisplayStatus {
  return job.is_current_revision ? job.file_status : 'stale'
}

export function fileStatusLabel(status: ReportDisplayStatus): string {
  return {
    pending: '正在准备',
    available: '可下载',
    expired: '文件已过期，可重新生成',
    failed: '生成失败',
    cancelled: '已取消',
    unavailable: '文件暂不可用',
    stale: '成绩已变化，需重新生成',
  }[status]
}

export function statusTone(status: JobStatus | ReportDisplayStatus): string {
  if (status === 'succeeded' || status === 'available') return 'success'
  if (
    status === 'failed'
    || status === 'expired'
    || status === 'unavailable'
    || status === 'stale'
  ) return 'danger'
  if (status === 'cancelled') return 'muted'
  return 'progress'
}

export function formatTime(value: string | null): string {
  return formatTimeValue(value) || '时间未记录'
}

export function formatShortTime(value: string | null): string {
  if (!value) return ''
  const normalized = value.replace('T', ' ').replace('Z', '')
  return normalized.slice(5, 16)
}

export function reportLiveDetail(job: JobResponse): string {
  return typeof job.detail === 'string' && job.detail.length > 0
    ? job.detail
    : ''
}

export function isRetainedReport(job: JobResponse | ReportHistoryJob): boolean {
  return job.payload.report_type === 'personal_analysis_html'
}

/** 个人报告导出结果中的"建议核对"条数；旧版结果无此字段时按 0 处理。 */
export function reviewNoteCount(job: JobResponse | ReportHistoryJob): number {
  const value = job.result.review_note_count
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0
    ? value
    : 0
}

export function formatTokenCount(value: number): string {
  return value.toLocaleString('zh-CN')
}
