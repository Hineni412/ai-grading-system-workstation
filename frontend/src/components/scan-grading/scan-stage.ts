import type { ReviewQuestionSummary } from '../../api/review'

export type ScanStageId = 'prepare' | 'grade' | 'review'
export const SCAN_STAGE_ORDER: ScanStageId[] = ['prepare', 'grade', 'review']

export type InterventionState = 'idle' | 'loading' | 'ready' | 'error'

export interface InterventionSummary {
  total: number
  ungraded: number
  failed: number
  review: number
  aiReady: number
  teacher: number
}

export function summarizeIntervention(questions: ReviewQuestionSummary[]): InterventionSummary {
  return questions.reduce(
    (summary, question) => ({
      total: summary.total + question.total_count,
      ungraded: summary.ungraded + (question.ungraded_count ?? 0),
      failed: summary.failed + (question.failed_count ?? 0),
      review: summary.review + question.needs_review_count,
      aiReady: summary.aiReady + (question.ai_ready_count ?? 0),
      teacher: summary.teacher + (question.teacher_confirmed_count ?? 0),
    }),
    { total: 0, ungraded: 0, failed: 0, review: 0, aiReady: 0, teacher: 0 },
  )
}
