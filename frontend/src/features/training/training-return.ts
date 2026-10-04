import type { TrainingAssessmentOutcome, TrainingAssessmentPoint, TrainingAssessmentQuestion, TrainingFeedback, TrainingPointState, TrainingScanBatch, TrainingScanIssue, TrainingScanPage, TrainingSubmission } from '../../api/training'
import type { StatusTone } from '../../components/design-system/StatusBadge.vue'

export type ReturnStage = 'scan_issue' | 'failed' | 'review' | 'unjudged' | 'update' | 'unknown' | 'running' | 'done' | 'cancelled'
export type InitialFocus = 'scan' | 'review' | 'publish'
export interface ReturnRecord {
  assessment: TrainingAssessmentOutcome | null
  feedback: TrainingFeedback | null
  loading: boolean
  readError: boolean
  waiting: boolean
  queryPaused: boolean
  busy: string
  error: string
  message: string
  onlyPending: boolean
  page: number
  view: 'points' | 'feedback'
}
export interface ReturnRow {
  id: string
  stage: ReturnStage | 'issue'
  label: string
  tone: StatusTone
  submission?: TrainingSubmission
  page?: TrainingScanPage
  record?: ReturnRecord
}
export const stageOrder: ReturnStage[] = ['scan_issue', 'failed', 'review', 'unjudged', 'update', 'unknown', 'running', 'done', 'cancelled']
export const stageLabels: Record<ReturnStage | 'issue', string> = {
  issue: '异常页', scan_issue: '缺页/待核对', failed: '判定失败', review: '待复核', unjudged: '待判定', update: '待更新', unknown: '状态未读出', running: '判定中', done: '已完成', cancelled: '已取消',
}
const tones: Record<ReturnStage | 'issue', StatusTone> = {
  issue: 'warning', scan_issue: 'warning', failed: 'danger', review: 'warning', unjudged: 'info', update: 'teacher', unknown: 'neutral', running: 'info', done: 'success', cancelled: 'neutral',
}
export function submissionKey(s: TrainingSubmission): string { return `${s.submission_id}:${s.revision}` }
export function pointKey(s: TrainingSubmission, q: TrainingAssessmentQuestion, p: TrainingAssessmentPoint): string { return `${submissionKey(s)}:${q.task_item_code}:${p.point_id}` }
export function needsReview(p: TrainingAssessmentPoint): boolean {
  return !p.state || (!p.teacher_locked && (p.state === 'uncertain' || p.state === 'unreadable'))
}
export function resultTotals(a?: TrainingAssessmentOutcome | null) {
  return { met: a?.questions.reduce((n, q) => n + q.met_count, 0) ?? 0, total: a?.expected_point_count ?? 0,
    pending: a?.questions.reduce((n, q) => n + q.review_points.filter(needsReview).length, 0) ?? 0,
    completed: a?.questions.filter(q => q.review_status === 'completed').length ?? 0 }
}
export function submissionStage(s: TrainingSubmission, a: TrainingAssessmentOutcome | null, f: TrainingFeedback | null, readError = false, waiting = false): ReturnStage {
  if (s.status === 'cancelled') return 'cancelled'
  if (s.status === 'manual_review') return 'scan_issue'
  if (readError) return 'unknown'
  if (waiting || a?.status === 'running') return 'running'
  if (!a) return 'unjudged'
  if (['failed', 'cancelled'].includes(a.status) && a.questions.every(q => q.review_points.every(p => !p.state))) return 'failed'
  if (a.questions.some(q => q.review_status !== 'completed') || a.questions.length < a.expected_question_count) return 'review'
  return f?.status === 'complete' && f.source_review_revision === a.review_revision ? 'done' : 'update'
}
export function issuePages(batch: TrainingScanBatch): TrainingScanPage[] {
  const cancelled = new Set(batch.submissions.filter(s => s.status === 'cancelled').map(s => s.submission_id))
  return batch.pages.filter(p => p.issue_code && !['replaced', 'dismissed'].includes(p.state) && !cancelled.has(p.submission_id ?? ''))
}
export function buildQueue(batch: TrainingScanBatch | null, records: Record<string, ReturnRecord>): ReturnRow[] {
  if (!batch) return []
  const issues: ReturnRow[] = issuePages(batch).map(page => ({ id: `page:${page.scan_page_id}`, page, stage: 'issue', label: stageLabels.issue, tone: tones.issue }))
  const students: ReturnRow[] = batch.submissions.map(submission => {
    const record = records[submissionKey(submission)]
    const stage = submissionStage(submission, record?.assessment ?? null, record?.feedback ?? null, !record || record.loading || record.readError, record?.waiting)
    const k = resultTotals(record?.assessment).pending
    const label = stage === 'scan_issue' ? submission.missing_pages.length ? `缺 ${submission.missing_pages.length} 页` : '待核对'
      : stage === 'review' && k ? `待复核 ${k} 点` : stageLabels[stage]
    return { id: submission.submission_id, submission, record, stage, label, tone: tones[stage] }
  })
  students.sort((a, b) => stageOrder.indexOf(a.stage as ReturnStage) - stageOrder.indexOf(b.stage as ReturnStage)
    || (a.submission?.student_code ?? '').localeCompare(b.submission?.student_code ?? '', 'zh-CN', { numeric: true })
    || (a.submission?.student_name ?? '').localeCompare(b.submission?.student_name ?? '', 'zh-CN'))
  return [...issues, ...students]
}
export function actionable(row: ReturnRow): boolean { return ['issue', 'scan_issue', 'failed', 'review', 'unjudged', 'update'].includes(row.stage) }
export function nextPending(rows: ReturnRow[], id: string): string {
  const start = rows.findIndex(r => r.id === id)
  for (let offset = 1; offset <= rows.length; offset++) {
    const row = rows[(Math.max(start, -1) + offset) % rows.length]
    if (row && actionable(row)) return row.id
  }
  return ''
}
export function initialSelection(rows: ReturnRow[], focus?: InitialFocus): string {
  const targeted = focus === 'scan' ? rows.find(r => r.stage === 'issue') ?? rows.find(r => r.stage === 'scan_issue')
    : focus === 'review' ? rows.find(r => r.stage === 'failed' || r.stage === 'review')
      : focus === 'publish' ? rows.find(r => r.stage === 'update') : undefined
  return targeted?.id ?? rows.find(actionable)?.id ?? rows[0]?.id ?? ''
}
export function queueCounts(rows: ReturnRow[]) {
  const counts = Object.fromEntries(['issue', ...stageOrder].map(s => [s, rows.filter(r => r.stage === s).length])) as Record<ReturnStage | 'issue', number>
  return { counts, total: rows.filter(r => r.submission && r.stage !== 'cancelled').length, done: counts.done }
}
export function summaryActions(rows: ReturnRow[]) {
  const { counts } = queueCounts(rows)
  return { primary: counts.issue ? 'issue' : counts.unjudged ? 'assess' : counts.update ? 'publish' : null,
    secondaryPublish: counts.issue === 0 && counts.unjudged > 0 && counts.update > 0 }
}
export function stateLabel(state?: TrainingPointState | null): string {
  return state ? { met: '达成', not_met: '未达成', uncertain: '不确定', unreadable: '无法辨认' }[state] : '未判定'
}
export function reviewBody(q: TrainingAssessmentQuestion, p: TrainingAssessmentPoint, final: TrainingPointState, note: string, token: string) {
  const candidate = p.candidate_state
  const reason = `${candidate ? `AI ${stateLabel(candidate)}` : 'AI 未判定'} → 教师${stateLabel(final)}`
  const clean = note.trim()
  return { task_item_code: q.task_item_code, point_id: p.point_id, final_state: final, operation_token: token,
    teacher_reason: `${reason}${clean ? `；${clean}` : ''}`.slice(0, 500),
    teacher_evidence: (clean || (final === candidate && p.evidence ? p.evidence : '教师查看原始训练答卷后判定')).slice(0, 500) }
}
export function requestToken(): string {
  const bytes = new Uint8Array(16)
  globalThis.crypto.getRandomValues(bytes)
  return [...bytes].map(v => v.toString(16).padStart(2, '0')).join('')
}
export function issueLabel(issue?: TrainingScanIssue | null): string {
  return issue ? { identity_unreadable: '页面身份无法读取', invalid_identity: '页面身份或签名无效', unexpected_paper: '不属于本扫描批次', duplicate_page: '检测到重复页面', page_content_conflict: '同一页码出现不同图像', image_blurry: '页面疑似明显模糊', severe_crop: '页面疑似严重裁切' }[issue] : ''
}
export function batchLabel(item: { created_at: string; status: string; submission_count: number }): string {
  return `${new Date(item.created_at).toLocaleString('zh-CN', { hour12: false })} · ${item.submission_count} 人 · ${item.status === 'ready' ? '归组完成' : item.status === 'cancelled' ? '已取消' : '需要检查'}`
}
