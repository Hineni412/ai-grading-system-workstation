import { TERMINAL_JOB_STATUSES, type JobResponse } from '../../api/jobs'
import { reportTypeLabel } from '../file-center/report-format'

const names: Record<string, string> = {
  personal_report_bundle: '导出学生个人报告',
  ops_backup: '数据备份', ops_restore_prepare: '准备恢复备份',
  ops_transfer_export: '导出迁机数据', ops_transfer_import_prepare: '准备导入迁机数据',
  ops_migration_prepare: '准备升级数据', scan_analysis: '扫描预检',
  grading_run: '答卷批改', grading: '答卷批改',
  config_generation: '试卷分析与本场赋分', config_generate: '生成评分依据',
  class_analysis_generate: '生成班级分析', class_analysis: '生成班级分析',
  individual_report: '生成学生个人报告', question_bank_sync: '考试试题入库与分析',
  question_import: '试卷导入', tagging_sync: '题目标签与判定点分析',
  criterion_backfill: '补齐题目判定点', question_bank_repair: '补齐题库资料',
  knowledge_link: '关联知识与技能', taxonomy_suggestion: '整理知识标准建议',
  assembly_export: '导出组卷', wrong_question_export: '导出错题本',
  personalized_handout_export: '导出训练讲义', answer_draft: '生成答案草稿',
  ai_assembly_spec: '生成组卷要求',
}

export function taskName(job: JobResponse): string {
  if (job.job_type === 'report_export') return `生成${reportTypeLabel(job.payload.report_type)}`
  if (job.job_type.startsWith('workspace_ai.')) return '工作台智能处理'
  return names[job.job_type] ?? '后台处理'
}

function count(value: unknown): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? value : 0
}

function items(value: unknown): number { return Array.isArray(value) ? value.length : 0 }

function mapping(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown> : {}
}

export interface TaskOutcome { label: string; tone: 'neutral' | 'success' | 'warning' | 'error'; note: string }

export function taskOutcome(job: JobResponse): TaskOutcome {
  if (job.cancel_requested && !TERMINAL_JOB_STATUSES.has(job.status)) {
    return { label: '正在取消', tone: 'neutral', note: '已请求取消，等待当前处理阶段停止。' }
  }
  if (job.status !== 'succeeded') {
    const label = { queued: '等待中', running: '进行中', paused: '已暂停', failed: '未完成', cancelled: '已取消' }[job.status]
    return { label, tone: job.status === 'failed' ? 'error' : 'neutral', note: '' }
  }
  const result = job.result
  const summary = mapping(result.summary)
  const failed = count(result.failed_count) || count(result.failed) || items(result.failed_question_ids)
    || items(result.failed_students) || count(summary.failed)
  const review = count(result.review_count) || count(result.taxonomy_review_count)
    || count(result.criteria_needs_review_count) || count(result.review_note_count)
    || items(result.review_question_ids) || items(result.criteria_needs_review_question_ids)
  const partial = result.outcome === 'partial' || result.status === 'partial'
    || failed > 0 || items(result.remaining) > 0 || count(result.failed_batch_count) > 0
    || result.exam_intake_complete === false || result.score_allocation_failed === true
    || result.score_allocation_pending === true
    || ['failed', 'partial', 'pending', 'queued', 'running'].includes(String(result.question_bank_sync_state ?? ''))
  if (result.outcome === 'failed' || result.status === 'failed') {
    return { label: '未完成', tone: 'error', note: '本次处理已结束，但业务结果未完成；请返回原操作页面检查。' }
  }
  if (['class_analysis_generate', 'class_analysis'].includes(job.job_type) && result.status === 'not_configured') {
    return { label: '未完成', tone: 'warning', note: '班级分析尚未生成，请在设置中的 AI 服务配置所需模型。' }
  }
  if (partial) {
    const reason = result.exam_intake_complete === false ? '题库入库尚未完成。'
      : result.score_allocation_pending === true || result.score_allocation_failed === true ? '本场赋分尚未完成。'
        : items(result.remaining) > 0 ? `仍有 ${items(result.remaining)} 道题的资料未补齐。`
          : failed > 0 && ['tagging_sync', 'question_bank_sync', 'config_generation', 'criterion_backfill'].includes(job.job_type)
            ? `仍有 ${failed} 道题未完成。` : '仍有未完成项目。'
    return { label: '部分完成', tone: 'warning', note: `${reason}${review > 0 ? '另有结果需要教师核对。' : ''}请返回原操作页面处理。` }
  }
  if (review > 0 || result.needs_teacher_resolution === true
    || (job.job_type === 'scan_analysis' && count(summary.issues) > 0)
    || (job.job_type === 'grading_run' && (count(summary.conflicts) + count(summary.scan_issues) + count(summary.unmatched) > 0))) {
    return { label: '等待教师核对', tone: 'warning', note: '本次处理已结束，仍有结果需要教师核对；请返回原操作页面查看。' }
  }
  if (result.outcome === 'prepared_restart_required') {
    return { label: '等待重启完成', tone: 'warning', note: '准备工作已完成，请回到数据与空间查看后续步骤。' }
  }
  if (['question_import', 'tagging_sync', 'config_generation', 'question_bank_sync', 'question_bank_repair'].includes(job.job_type)
    && result.outcome !== 'complete') {
    return { label: '处理已结束', tone: 'neutral', note: '任务记录缺少完整业务结果，请返回原操作页面核对。' }
  }
  if ((!names[job.job_type] && job.job_type !== 'report_export') || job.job_type.startsWith('workspace_ai.')) {
    return { label: '处理已结束', tone: 'neutral', note: '请返回原操作页面查看业务结果。' }
  }
  return { label: '全部完成', tone: 'success', note: '' }
}

const detailLabels: Record<string, string> = {
  preparing: '正在准备', starting: '正在启动', processing: '正在处理',
  loading: '正在读取资料', indexing: '正在登记导入结果', importing: '正在导入试卷',
  binding: '正在关联资料', ready_to_publish: '正在检查并保存结果',
  publishing: '正在保存结果', published: '结果已保存', complete: '本次处理已结束',
  completed: '本次处理已结束', finished: '本次处理已结束', partial: '本次处理已结束，仍有未完成项目',
  failed: '本次处理未完成', paused: '处理已暂停', cancelled: '处理已取消',
  restart_required: '准备工作已完成，等待重启', mapping: '正在关联评分依据与题目',
  generating_reports: '正在生成报告', creating_safety_backup: '正在创建操作前备份',
  'Waiting to generate grading configuration.': '正在等待分析试卷与生成评分依据',
  'Generating grading configuration.': '正在分析试卷与生成评分依据',
  'Preparing source questions.': '正在准备试卷题目',
  'Finalizing grading configuration.': '正在完成本场赋分与校验',
  'Grading configuration generated.': '本场评分依据已生成',
}

export function taskDetail(job: JobResponse): string {
  const outcome = taskOutcome(job)
  if (outcome.note) return outcome.note
  const detail = job.detail.trim()
  const graded = /^graded=(\d+) failed=(\d+)$/.exec(detail)
  if (graded) return `已批改 ${graded[1]} 份答卷，失败 ${graded[2]} 份。`
  const batch = /^(?:processing )?batch (\d+)(?:\/| of )(\d+)$/.exec(detail)
  if (batch) return `正在处理第 ${batch[1]} / ${batch[2]} 批。`
  if (detailLabels[detail]) return detailLabels[detail]
  // 保留已有中文业务说明；机器状态、英文异常及文件名使用中文兜底。
  const words = detail.match(/[A-Za-z]+/g) ?? []
  if (/[\u3400-\u9fff]/u.test(detail) && !/[A-Za-z]:[\\/]|https?:|[/\\]|\.(?:zip|docx|pdf|xlsx)\b/i.test(detail)
    && words.every(word => ['AI', 'PDF', 'Word', 'JSON', 'OCR'].includes(word))) return detail
  if (job.status === 'failed') return '本次处理未完成，请返回原操作页面检查。'
  if (job.status === 'cancelled') return '本次处理已取消。'
  if (job.status === 'succeeded') return '本次任务已完成。'
  return job.status === 'queued' ? '正在等待处理。' : `正在${taskName(job).replace(/^生成|^导出|^准备/u, '')}。`
}

export function taskScope(job: JobResponse, examNames: Map<number, string>, paperLabel?: string): string {
  if (job.job_type === 'personal_report_bundle') {
    const ids = Array.isArray(job.payload.session_ids) ? job.payload.session_ids : []
    return `考试：${ids.map(id => examNames.get(Number(id)) ?? `第 ${id} 场考试`).join('、')} · ${String(job.payload.scope_label ?? '所选学生')}`
  }
  const id = job.payload.session_id ?? job.result.session_id
  if (typeof id === 'number' && Number.isSafeInteger(id) && id > 0) {
    return `考试：${examNames.get(id) ?? `第 ${id} 场考试（名称暂不可用）`}`
  }
  if (paperLabel) return paperLabel
  if (job.job_type.startsWith('ops_')) return '系统数据'
  if (job.job_type === 'personalized_handout_export') return `训练讲义 · 推荐草稿 ${job.payload.draft_id ?? '编号未记录'}`
  if (job.job_type === 'assembly_export') return '组卷草稿'
  if (job.job_type === 'wrong_question_export') return '所选学生的本学期错题'
  if (['taxonomy_suggestion', 'knowledge_link', 'criterion_backfill'].includes(job.job_type)) return '题库知识与判定标准'
  if (job.job_type.startsWith('workspace_ai.')) return '工作台'
  return '所属对象未记录'
}
