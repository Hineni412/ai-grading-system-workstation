import type { WorkbenchOverview } from '../../api/workbench'
import type { RegionReadiness } from '../../api/template-regions'
import type { SessionQuestionBankAnalysisStatus } from '../../api/session-question-bank-sync'
import type { TrainingPendingSummary } from '../../api/training'
import { TERMINAL_JOB_STATUSES, type JobResponse } from '../../api/jobs'
import type { StepProgressStep } from '../design-system/StepProgress.vue'
import type { StatusTone } from '../design-system/StatusBadge.vue'

export interface HomeInputs {
  sessionId: number | null
  overview: WorkbenchOverview | null
  readiness: RegionReadiness | null
  analysis: SessionQuestionBankAnalysisStatus | null
  jobs: JobResponse[]
  training?: TrainingPendingSummary | null
}
export interface TodoRow {
  id: string; module: string; tone: StatusTone; title: string; fact: string; action: string; path: string
}
export function setupHint(readiness: RegionReadiness): string {
  return [!readiness.scoring_configured && '评分依据未完成', !readiness.template_ready && '样卷题框未确认']
    .filter(Boolean).join('，') || '已完成'
}
export function setupPath(sessionId: number, readiness: RegionReadiness | null): string {
  return readiness?.scoring_configured ? `/sessions/${sessionId}/regions` : '/sessions'
}
export function activeJobs(jobs: JobResponse[]): JobResponse[] {
  return jobs.filter(job => !TERMINAL_JOB_STATUSES.has(job.status))
}
export function buildTodoRows({ sessionId, overview, readiness, analysis, jobs, training }: HomeInputs) {
  const need: TodoRow[] = [], continued: TodoRow[] = []
  const row = (id: string, title: string, fact: string, action: string, path: string,
    module = '考试', tone: StatusTone = 'info'): TodoRow => ({ id, title, fact, action, path, module, tone })
  const trainingRows: TodoRow[] = []
  for (const item of training?.items ?? []) {
    const path = `/training?mode=paper&draft=${encodeURIComponent(item.draft_id)}`
    if (item.scan_page_count > 0) trainingRows.push(row(`scan-${item.draft_id}`,
      `核对 ${item.scan_page_count} 页训练卷扫描`, item.draft_name, '去核对', path, '训练', 'success'))
    if (item.review_submission_count > 0) trainingRows.push(row(`review-${item.draft_id}`,
      `复核 ${item.draft_name} 判定 ${item.review_submission_count} 份`,
      '不确定、无法辨认或缺失判定点仍需教师确认', '去复核', path, '训练', 'success'))
    if (item.publish_submission_count > 0) trainingRows.push(row(`publish-${item.draft_id}`,
      `发布 ${item.draft_name} 证据 ${item.publish_submission_count} 份`,
      '判定已完成，当前修订的训练证据尚未全部发布', '去发布', path, '训练', 'success'))
  }
  if (sessionId === null || overview?.current_session?.id !== sessionId) return { need: trainingRows, continued }
  const p = overview.progress, r = overview.review, a = overview.anomalies
  const gradingPath = `/sessions/${sessionId}/grading-run`
  if (readiness && !(readiness.scoring_configured && readiness.template_ready)) {
    need.push(row('setup', '完成考试配置', setupHint(readiness), '继续配置', setupPath(sessionId, readiness)))
  } else if (readiness && p?.total_papers === 0) {
    need.push(row('upload', '上传答卷', '评分依据与样卷题框已就绪', '上传答卷', gradingPath))
  }
  if (a && (a.unmatched_papers > 0 || a.scan_issue_students > 0 || a.failed_papers > 0)) {
    const fact = [a.unmatched_papers && `${a.unmatched_papers} 份答卷未匹配学生`,
      a.scan_issue_students && `${a.scan_issue_students} 名学生扫描页有问题`,
      a.failed_papers && `${a.failed_papers} 份批改失败`].filter(Boolean).join('，')
    need.push(row('anomalies', '处理答卷异常', fact, '处理异常', gradingPath))
  }
  const gradingActive = activeJobs(jobs).some(job => ['grading_run', 'grading'].includes(job.job_type)
    && Number(job.payload.session_id ?? job.result.session_id) === sessionId)
  if (p && p.matched_papers > 0 && p.graded_papers + p.failed_papers < p.matched_papers && !gradingActive) {
    need.push(row('grade', '继续批改', `已批改 ${p.graded_papers}/${p.matched_papers} 份`, '继续批改', gradingPath))
  }
  if (r && r.item_count > 0) {
    need.push(row('review', `复核 ${r.item_count} 项评分`, `涉及 ${r.question_count} 道题`, '开始复核', '/grading'))
  }
  if (analysis?.incomplete_question_ids.length) {
    need.push(row('bank', `补齐本场 ${analysis.incomplete_question_ids.length} 道题的题库资料`,
      '难度、标签、解题证据或判定点未完成', '去补齐', '/question-bank?tab=todo', '题库', 'ai'))
  }
  if (analysis && analysis.unlinked_skill_count > 0) {
    need.push(row('unlinked', `本场 ${analysis.unlinked_skill_count} 道题未挂技能`,
      '这些题目缺少可用判定版本或直接技能链接，暂不能形成技能掌握度证据',
      '去挂技能', '/question-bank?tab=todo', '题库', 'ai'))
  }
  need.push(...trainingRows)
  if (p && p.graded_papers > 0) {
    continued.push(row('walkthrough', '看卷 10 分钟', `${overview.current_session.name} · 已批改 ${p.graded_papers}/${p.matched_papers} 份`,
      '去看卷', '/results?tab=overview&open=walkthrough'))
  }
  const reports = overview.personal_reports
  if (reports && reports.stale > 0) {
    continued.push(row('reports', `个人报告：${reports.stale} 人需重新生成`,
      `${reports.current} 人已是最新`, '去查看', '/results?tab=details'))
  }
  if (p && p.matched_papers > 0 && p.graded_papers + p.failed_papers === p.matched_papers && r?.item_count === 0) {
    continued.push(row('assembly', '按失分题组讲义', '班级组卷默认依据最近两场考试，列出得分率低于 70% 的题',
      '去组卷', '/question-assembly?mode=assistant', '组卷', 'ai'))
  }
  return { need, continued }
}

export function buildExamSteps({ sessionId, overview, readiness }: HomeInputs): StepProgressStep[] {
  const p = overview?.progress, r = overview?.review
  const available = sessionId !== null && overview?.current_session?.id === sessionId
  const setupDone = !!readiness?.scoring_configured && readiness.template_ready
  const prepareDone = !!p && p.total_papers > 0 && p.unmatched_papers === 0 && p.scan_issue_students === 0
  const gradeDone = !!p && p.matched_papers > 0 && p.graded_papers + p.failed_papers === p.matched_papers && p.failed_papers === 0
  const reviewDone = gradeDone && r?.item_count === 0
  return [
    { id: 'setup', label: '考试配置', status: setupDone ? 'done' : readiness ? 'in_progress' : 'todo',
      hint: readiness ? setupHint(readiness) : '状态未读取' },
    { id: 'prepare', label: '答卷与预检', status: prepareDone ? 'done' : p && p.total_papers > 0 ? 'in_progress' : 'todo',
      hint: !p ? '状态未读取' : !p.total_papers ? '待上传' : prepareDone ? `${p.matched_papers} 份已匹配` : `${p.matched_papers}/${p.total_papers} 份已匹配` },
    { id: 'grade', label: '批改', status: gradeDone ? 'done' : p && (p.matched_papers > 0 || p.failed_papers > 0) ? 'in_progress' : 'todo',
      hint: !p || !p.matched_papers ? '—' : `${p.graded_papers}/${p.matched_papers}${p.failed_papers ? ` · 失败 ${p.failed_papers}` : ''}` },
    { id: 'review', label: '复核', status: reviewDone ? 'done' : r && r.item_count > 0 ? 'in_progress' : 'todo',
      hint: !r ? '状态未读取' : r.item_count > 0 ? `${r.item_count} 项待复核` : reviewDone ? '0 项' : '等待批改' },
    { id: 'results', label: '成绩与讲评', status: gradeDone && reviewDone ? 'done' : 'todo',
      hint: gradeDone && reviewDone ? '可查看' : '复核后可查看' },
  ].map(step => ({ ...step, status: step.status as StepProgressStep['status'], available }))
}
