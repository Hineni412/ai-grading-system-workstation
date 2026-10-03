import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  resolveReviewItem,
  type ReviewItemLike,
  type ReviewRubricSection,
} from '../api/review'

export interface ReviewStepDraft {
  partId: string
  stepId: string
  maxScore: number
  scoreText: string
  note: string
  carriedFrom: string | null
  carryExplicit: boolean
  initialScoreText?: string
  initialCarriedFrom?: string | null
  invalidatedCarriedFrom?: string | null
  scoreTouched?: boolean
  recheck?: string
}

export interface ReviewDraft {
  key: string
  sessionId: number
  questionId: string
  reviewItemId?: string
  baseRevision?: number
  detailId?: number
  scoreText: string
  note: string
  baseScoreText: string
  baseNote: string
  dirty: boolean
  updatedAt: number
  stepScores?: ReviewStepDraft[]
  baseStepsJson?: string
  stepNotice?: string
}

export function stepNumber(index: number): string {
  return ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩'][index] ?? `${index + 1}`
}

export function aiStepFor(item: ReviewItemLike, step: { partId: string; stepId: string }): Record<string, unknown> | undefined {
  const assessments = item.metadata.step_assessments
  if (!Array.isArray(assessments)) return undefined
  const matches = assessments.filter((entry) => entry && typeof entry === 'object'
    && entry.step_id === step.stepId && (!entry.part_id || entry.part_id === step.partId))
  return matches.length === 1 ? matches[0] : undefined
}

export function canReviewSteps(item: ReviewItemLike, rubric: ReviewRubricSection | null): boolean {
  const resolved = resolveReviewItem(item)
  return Boolean(resolved.result_id && resolved.detail_id && !resolved.review_item_id.startsWith('legacy:')
    && rubric?.question_id === resolved.question_id && rubric?.points.length && rubric.points.every((point) => point.step_id && Number.isInteger(point.score) && point.score > 0)
    && rubric.points.reduce((total, point) => total + point.score, 0) === resolved.max_score)
}

export function reviewDraftIssue(draft: ReviewDraft, maxScore: number): string | null {
  const index = draft.stepScores?.findIndex((step) => scoreIssue(step.scoreText, step.maxScore) !== null) ?? -1
  if (index >= 0) return `第${stepNumber(index)}步请填 0–${draft.stepScores![index]!.maxScore} 的整数`
  return scoreIssue(draft.scoreText, maxScore)
}

export function stepNeedsReview(item: ReviewItemLike, step?: ReviewStepDraft): { red: boolean; label: string } {
  const resolved = resolveReviewItem(item)
  if (step?.recheck) return { red: true, label: '需重核' }
  if (resolved.teacher_locked) return { red: false, label: '' }
  if (resolved.score_status === 'failed' || resolved.score_status === 'ungraded') return { red: true, label: '待评分' }
  if (resolved.score_status !== 'ai_review') return { red: false, label: '' }
  const uncertain = Array.isArray(item.metadata.step_assessments)
    && item.metadata.step_assessments.some((entry) => entry?.achievement === 'uncertain')
  const isUncertain = step && aiStepFor(item, step)?.achievement === 'uncertain'
  return { red: !step || !uncertain || Boolean(isUncertain), label: isUncertain ? '拿不准' : '需核对' }
}

export function previousFailedSteps(steps: ReviewStepDraft[], index: number): ReviewStepDraft[] {
  return steps.slice(0, index).filter((step) => step.partId === steps[index]?.partId
    && scoreIssue(step.scoreText, step.maxScore) === null && Number(step.scoreText) < step.maxScore)
}

function refreshStepLinks(steps: ReviewStepDraft[]): void {
  steps.forEach((step, index) => {
    const previous = previousFailedSteps(steps, index)
    const candidate = step.carryExplicit ? step.carriedFrom : step.initialCarriedFrom
    const candidateIndex = steps.findIndex((entry, i) => i < index && entry.partId === step.partId && entry.stepId === candidate)
    if (step.scoreText === '0' && candidateIndex >= 0
      && steps[candidateIndex]!.scoreText === String(steps[candidateIndex]!.maxScore)) {
      step.invalidatedCarriedFrom = candidate
    }
    step.carriedFrom = step.scoreText === '0' && previous.some((entry) => entry.stepId === candidate) ? candidate ?? null : null
    step.recheck = ''
    const sourceIndex = steps.findIndex((entry, i) => i < index && entry.partId === step.partId
      && entry.stepId === step.invalidatedCarriedFrom)
    if (!step.scoreTouched && sourceIndex >= 0
      && steps[sourceIndex]!.scoreText === String(steps[sourceIndex]!.maxScore)) {
      step.recheck = `${stepNumber(sourceIndex)}已改为达成，本步原先因沿用${stepNumber(sourceIndex)}而扣分，请重新核对`
    }
    if (!step.scoreTouched && step.scoreText === String(step.maxScore)) {
      const changedIndex = steps.findIndex((entry, i) => i < index && entry.partId === step.partId
        && entry.initialScoreText === String(entry.maxScore) && scoreIssue(entry.scoreText, entry.maxScore) === null
        && Number(entry.scoreText) < entry.maxScore)
      if (changedIndex >= 0) step.recheck = `${stepNumber(changedIndex)}已改为未达成，若本步沿用了${stepNumber(changedIndex)}的结果，也可能要扣分`
    }
  })
}

export function submittedSteps(draft: ReviewDraft) {
  return draft.stepScores?.map((step) => ({ part_id: step.partId, step_id: step.stepId,
    score_awarded: Number(step.scoreText), teacher_note: step.note.trim() || null,
    carried_error_from: step.carriedFrom }))
}

export function reviewDraftKey(item: ReviewItemLike): string {
  return item.review_item_id
    ? `review-item:${item.review_item_id}`
    : `${item.session_id}:${item.question_id}:${String(item.detail_id)}`
}

export function reviewDraftRevision(draft: ReviewDraft): number {
  return draft.baseRevision ?? 0
}

export function scoreIssue(scoreText: string, maxScore: number): string | null {
  const clean = scoreText.trim()
  if (!clean) return '请输入教师最终分'
  const score = Number(clean)
  if (!Number.isFinite(score)) return '教师最终分必须是有效数字'
  if (score < 0) return '教师最终分不能低于 0 分'
  if (!Number.isFinite(maxScore) || maxScore < 0) return '评分标准满分暂不可用'
  if (score > maxScore) return `教师最终分不能超过 ${String(maxScore)} 分`
  if (!Number.isInteger(score)) return '教师最终分必须是整数'
  return null
}

function scoreText(value: number | null): string {
  return value !== null && Number.isFinite(value) ? String(value) : ''
}

function baseNote(item: ReviewItemLike): string {
  return item.score_source === 'teacher' || item.teacher_locked
    ? (item.deduction_reason ?? '').trim()
    : ''
}

function refreshDirty(draft: ReviewDraft): void {
  draft.dirty = draft.scoreText !== draft.baseScoreText || draft.note !== draft.baseNote
    || stepsJson(draft.stepScores) !== (draft.baseStepsJson ?? 'null')
  draft.updatedAt = Date.now()
}

function stepsJson(steps?: ReviewStepDraft[]): string {
  return JSON.stringify(steps?.map(({ partId, stepId, maxScore, scoreText, note, carriedFrom, carryExplicit }) =>
    ({ partId, stepId, maxScore, scoreText, note, carriedFrom, carryExplicit })) ?? null)
}

export const useReviewDraftStore = defineStore('review-drafts', () => {
  const drafts = ref<Record<string, ReviewDraft>>({})
  const dirtyCount = computed(() =>
    Object.values(drafts.value).filter((draft) => draft.dirty).length,
  )
  const hasDirtyDrafts = computed(() => dirtyCount.value > 0)

  function ensureDraft(item: ReviewItemLike): ReviewDraft {
    const key = reviewDraftKey(item)
    const current = drafts.value[key]
    if (current?.dirty) return current

    const resolved = resolveReviewItem(item)
    if (current && current.baseRevision === resolved.revision) return current
    const nextScore = scoreText(resolved.score_awarded)
    const nextNote = baseNote(resolved)
    const review = resolved.metadata.teacher_review as { revision?: number; steps?: Record<string, unknown>[] } | undefined
    const savedSteps = review?.revision === resolved.revision && Array.isArray(review.steps)
      ? review.steps.map((step) => ({ partId: String(step.part_id ?? ''), stepId: String(step.step_id ?? ''),
        maxScore: Number(step.max_score), scoreText: String(step.score_awarded), note: String(step.teacher_note ?? ''),
        carriedFrom: typeof step.carried_error_from === 'string' ? step.carried_error_from : null,
        carryExplicit: false, initialScoreText: String(step.score_awarded),
        initialCarriedFrom: typeof step.carried_error_from === 'string' ? step.carried_error_from : null })) : undefined
    if (current) {
      current.scoreText = nextScore
      current.note = nextNote
      current.baseScoreText = nextScore
      current.baseNote = nextNote
      current.baseRevision = resolved.revision
      current.stepScores = savedSteps
      current.baseStepsJson = stepsJson(savedSteps)
      current.dirty = false
      current.updatedAt = Date.now()
      return current
    }

    const draft: ReviewDraft = {
      key,
      sessionId: resolved.session_id,
      questionId: resolved.question_id,
      reviewItemId: resolved.review_item_id,
      baseRevision: resolved.revision,
      ...(resolved.detail_id === null ? {} : { detailId: resolved.detail_id }),
      scoreText: nextScore,
      note: nextNote,
      baseScoreText: nextScore,
      baseNote: nextNote,
      dirty: false,
      updatedAt: Date.now(),
      stepScores: savedSteps,
      baseStepsJson: stepsJson(savedSteps),
    }
    drafts.value[key] = draft
    return draft
  }

  function updateScore(key: string, value: string): void {
    const draft = drafts.value[key]
    if (!draft) return
    draft.scoreText = value
    draft.stepScores = undefined
    refreshDirty(draft)
  }

  // 自动进入步骤模式写入的是 AI/现状投影，不是教师输入：
  // 直接作为基线记录，教师之后的改动才计入未确认草稿。
  function initSteps(key: string, steps: ReviewStepDraft[]): void {
    const draft = drafts.value[key]
    if (!draft || draft.dirty) return
    draft.stepScores = steps
    draft.scoreText = steps.every((step) => scoreIssue(step.scoreText, step.maxScore) === null)
      ? String(steps.reduce((total, step) => total + Number(step.scoreText), 0)) : ''
    draft.baseScoreText = draft.scoreText
    draft.baseStepsJson = stepsJson(steps)
    draft.dirty = false
    draft.updatedAt = Date.now()
  }

  function ensureSteps(item: ReviewItemLike, rubric: ReviewRubricSection | null): void {
    const draft = ensureDraft(item)
    if (!canReviewSteps(item, rubric) || draft.stepScores || !rubric) return
    let steps = rubric.points.map((point) => {
      const identity = { partId: point.part_id, stepId: point.step_id }
      const ai = aiStepFor(item, identity)
      return { ...identity, maxScore: point.score,
        scoreText: typeof ai?.score_awarded === 'number' ? String(ai.score_awarded) : '',
        note: '', carriedFrom: typeof ai?.carried_error_from === 'string' ? ai.carried_error_from : null,
        carryExplicit: false } as ReviewStepDraft
    })
    const hasAi = steps.some((step) => step.scoreText !== '')
    const valid = steps.every((step) => scoreIssue(step.scoreText, step.maxScore) === null)
      && steps.reduce((sum, step) => sum + Number(step.scoreText), 0) === Number(draft.scoreText)
    draft.stepNotice = hasAi && !valid ? 'AI 步骤分与总分不一致，请逐步给分' : ''
    if (!valid) steps = steps.map((step) => ({ ...step, scoreText: '' }))
    steps.forEach((step) => { step.initialScoreText = step.scoreText; step.initialCarriedFrom = step.carriedFrom })
    refreshStepLinks(steps)
    initSteps(draft.key, steps)
  }

  function updateStep(key: string, index: number, patch: Partial<ReviewStepDraft>): void {
    const steps = drafts.value[key]?.stepScores
    if (!steps) return
    const next = steps.map((step, i) => i === index ? { ...step, ...patch,
      ...('scoreText' in patch || 'carriedFrom' in patch ? { invalidatedCarriedFrom: null } : {}),
      ...('scoreText' in patch ? { scoreTouched: patch.scoreText !== step.initialScoreText } : {}) } : { ...step })
    refreshStepLinks(next)
    updateSteps(key, next)
  }

  function updateSteps(key: string, steps: ReviewStepDraft[] | undefined): void {
    const draft = drafts.value[key]
    if (!draft) return
    draft.stepScores = steps
    if (steps) {
      draft.scoreText = steps.every((step) => scoreIssue(step.scoreText, step.maxScore) === null)
        ? String(steps.reduce((total, step) => total + Number(step.scoreText), 0)) : ''
    }
    refreshDirty(draft)
  }

  function updateNote(key: string, value: string): void {
    const draft = drafts.value[key]
    if (!draft) return
    draft.note = value
    refreshDirty(draft)
  }

  function markConfirmed(key: string): void {
    if (!(key in drafts.value)) return
    const next = { ...drafts.value }
    delete next[key]
    drafts.value = next
  }

  function markConfirmedMany(keys: readonly string[]): void {
    const confirmed = new Set(keys)
    drafts.value = Object.fromEntries(
      Object.entries(drafts.value).filter(([key]) => !confirmed.has(key)),
    )
  }

  function reset(): void {
    drafts.value = {}
  }

  return {
    drafts,
    dirtyCount,
    hasDirtyDrafts,
    ensureDraft,
    updateScore,
    initSteps,
    ensureSteps,
    updateStep,
    updateSteps,
    updateNote,
    markConfirmed,
    markConfirmedMany,
    reset,
  }
})
