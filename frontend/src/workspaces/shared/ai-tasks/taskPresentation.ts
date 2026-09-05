import { TERMINAL_JOB_STATUSES, type JobResponse } from '../../../api/jobs'
import {
  isQuestionBankLibraryJob,
  libraryJobDetailLine,
} from '../../../components/question-bank/paper-analysis-status'
import type { WorkspaceAITask } from './contracts'

export const JOB_TITLE_BY_TYPE: Record<string, string> = {
  config_generation: '生成评分依据',
  question_import: '试卷入库',
  tagging_sync: '题库分析',
  taxonomy_suggestion: '新词归并建议',
  scan_analysis: '答卷扫描预检',
  grading_run: '考试批改',
  question_bank_sync: '题库同步',
  training_export: '导出训练材料',
  assembly_export: '导出组卷',
  criterion_backfill: '补齐评分依据',
  report_export: '导出成绩报告',
}

export function jobTitle(job: JobResponse): string {
  return JOB_TITLE_BY_TYPE[job.job_type] ?? '后台任务'
}

export function jobStatusLabel(job: JobResponse): string {
  if (job.status === 'queued') return '等待开始'
  if (job.status === 'running') return '正在处理'
  if (job.status === 'paused') return '已暂停'
  if (job.status === 'succeeded') return '已完成'
  if (job.status === 'cancelled') return '已取消'
  if (job.status === 'failed') return '失败'
  return job.status
}

export function jobDetailLine(job: JobResponse): string {
  if (isQuestionBankLibraryJob(job)) return libraryJobDetailLine(job)
  const percent = Math.round(Math.max(0, Math.min(1, job.progress)) * 100)
  const detail = job.detail.trim() || job.stage.trim()
  if (detail) return `${percent}% · ${detail}`
  return `${percent}% · ${jobStatusLabel(job)}`
}

export function jobLocation(job: JobResponse): string {
  if (job.job_type === 'config_generation') return '/sessions'
  if ([
    'question_import',
    'tagging_sync',
    'taxonomy_suggestion',
    'question_bank_sync',
    'criterion_backfill',
  ].includes(job.job_type)) return '/question-bank'
  if (['scan_analysis', 'grading_run'].includes(job.job_type)) return '/grading'
  if (job.job_type === 'training_export') return '/training'
  if (job.job_type === 'assembly_export') return '/question-assembly'
  if (job.job_type === 'report_export') return '/results'
  return '/workbench'
}

export { isQuestionBankLibraryJob }

export function isAttentionJob(job: JobResponse): boolean {
  return job.status === 'failed' || job.status === 'paused' || !TERMINAL_JOB_STATUSES.has(job.status)
}

export function isArchivedJob(job: JobResponse): boolean {
  return job.status === 'succeeded' || job.status === 'cancelled'
}

export function isAttentionTask(task: WorkspaceAITask): boolean {
  return [
    'prepared',
    'queued',
    'running',
    'needs_input',
    'proposal_ready',
    'failed',
    'failed_before_dispatch',
    'result_unknown',
    'invalid_result',
  ].includes(task.status)
}

export function isArchivedTask(task: WorkspaceAITask): boolean {
  return task.status === 'discarded' || task.status === 'cancelled_before_dispatch'
}

export function dispatchEvidenceLabel(task: WorkspaceAITask): string {
  if (task.dispatch_evidence === 'response_persisted') return '响应已保存'
  if (task.dispatch_evidence === 'may_have_started') return '可能已发送'
  return '尚未发送'
}

export function handoffSummary(task: WorkspaceAITask): string {
  if (task.handoff_total === 0) return '暂无交接项'
  const handled = task.adopted_count + task.discarded_count + task.stale_count
  return `${handled}/${task.handoff_total} 已处理`
}

export function canCancelTask(task: WorkspaceAITask): boolean {
  return ['prepared', 'queued', 'running'].includes(task.status)
}

export function cancelTaskLabel(task: WorkspaceAITask): string {
  return task.dispatch_evidence === 'not_started' ? '取消' : '停止后续处理'
}

export function returnLocation(task: WorkspaceAITask): {
  path: string
  query: Record<string, string>
} {
  return {
    path: '/class-teacher',
    query: {
      destination: task.return_target,
      source_task_id: task.task_id,
      source_ref: task.source_ref.id,
    },
  }
}
