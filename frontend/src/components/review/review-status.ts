import type { ReviewQuestionSummary, ReviewScoreStatus } from '../../api/review'

export const reviewStatus = {
  ungraded: { label: '待人工', tone: 'info', color: 'var(--color-info)' },
  failed: { label: '处理失败', tone: 'danger', color: 'var(--color-danger)' },
  ai_review: { label: '待复核', tone: 'warning', color: 'var(--color-warning)' },
  ai_ready: { label: 'AI 已评', tone: 'ai', color: 'var(--color-ai)' },
  teacher_final: { label: '教师已确认', tone: 'teacher', color: 'var(--color-teacher)' },
} as const

export function pendingCount(question: ReviewQuestionSummary): number {
  return (question.failed_count ?? 0) + (question.ungraded_count ?? 0) + question.needs_review_count
}

export function questionStatus(question: ReviewQuestionSummary): ReviewScoreStatus {
  return (question.failed_count ?? 0) > 0 ? 'failed' : (question.ungraded_count ?? 0) > 0
    ? 'ungraded' : question.needs_review_count > 0 ? 'ai_review'
      : (question.teacher_confirmed_count ?? 0) === question.total_count ? 'teacher_final' : 'ai_ready'
}
