import type {
  ResultsCenterItem,
  ResultsCenterStudent,
  ResultsScoreStatus,
} from '../../api/results-center'
import {
  fetchRegionWorkspace,
  type Region,
  type RegionWorkspace,
} from '../../api/template-regions'
import { resolvedItemScore, subQuestionOf } from './results-overview'

export type PaperMarkState = 'full' | 'partial' | 'zero' | 'pending'

export interface PaperMark {
  key: string
  questionId: string
  page: 'front' | 'back'
  left: number
  top: number
  width: number
  height: number
  score: number | null
  maxScore: number
  state: PaperMarkState
  needsReview: boolean
  items: ResultsCenterItem[]
}

export function paperMarks(
  regions: readonly Region[],
  pages: Readonly<Record<'front' | 'back', { width: number; height: number }>>,
  items: readonly ResultsCenterItem[],
): PaperMark[] {
  const marks: PaperMark[] = []
  for (const region of regions) {
    const questionId = region.mapped_question_id
    if (!questionId) continue
    const page = pages[region.page]
    if (!page || page.width <= 0 || page.height <= 0) continue
    let covered = items.filter((item) => item.question_id === questionId)
    if (!covered.length) {
      covered = items.filter(
        (item) => subQuestionOf(item.question_id)?.parentId === questionId,
      )
    }
    if (!covered.length) continue
    const pending = covered.some((item) => resolvedItemScore(item) === null)
    const score = pending
      ? null
      : covered.reduce(
        (total, item) => total + (resolvedItemScore(item) ?? 0),
        0,
      )
    const maxScore = covered.reduce(
      (total, item) => total + item.max_score,
      0,
    )
    marks.push({
      key: region.region_uuid,
      questionId,
      page: region.page,
      left: (region.x / page.width) * 100,
      top: (region.y / page.height) * 100,
      width: (region.w / page.width) * 100,
      height: (region.h / page.height) * 100,
      score,
      maxScore,
      state: pending
        ? 'pending'
        : score === maxScore ? 'full' : score === 0 ? 'zero' : 'partial',
      needsReview: covered.some(
        (item) => item.needs_review || item.score_status === 'ai_review',
      ),
      items: covered,
    })
  }
  return marks
}

export function itemMarkState(item: ResultsCenterItem): PaperMarkState {
  const score = resolvedItemScore(item)
  if (score === null) return 'pending'
  if (score >= item.max_score) return 'full'
  return score === 0 ? 'zero' : 'partial'
}

export function resultIdOf(student: ResultsCenterStudent): number | null {
  const counts = new Map<number, number>()
  for (const item of student.items) {
    if (item.result_id === null) continue
    counts.set(item.result_id, (counts.get(item.result_id) ?? 0) + 1)
  }
  let best: number | null = null
  let bestCount = 0
  for (const [resultId, count] of counts) {
    if (count > bestCount) {
      best = resultId
      bestCount = count
    }
  }
  return best
}

export function scoreStatusLabel(status: ResultsScoreStatus): string {
  return {
    ungraded: '未评分',
    failed: '处理失败',
    ai_review: 'AI 待复核',
    ai_ready: 'AI 已评分',
    teacher_final: '教师已确认',
  }[status]
}

const workspacePromises = new Map<number, Promise<RegionWorkspace>>()

export function regionWorkspaceFor(sessionId: number): Promise<RegionWorkspace> {
  const cached = workspacePromises.get(sessionId)
  if (cached) return cached
  const promise = Promise.resolve().then(() => fetchRegionWorkspace(sessionId))
  workspacePromises.set(sessionId, promise)
  void promise.catch(() => {
    if (workspacePromises.get(sessionId) === promise) {
      workspacePromises.delete(sessionId)
    }
  })
  return promise
}

export function resetRegionWorkspaceCache(): void {
  workspacePromises.clear()
}
