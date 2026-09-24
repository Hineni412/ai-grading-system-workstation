import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  resolveReviewItem,
  type ReviewItemLike,
} from '../api/review'

export interface ReviewStepDraft {
  partId: string
  stepId: string
  maxScore: number
  scoreText: string
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
    || JSON.stringify(draft.stepScores ?? null) !== (draft.baseStepsJson ?? 'null')
  draft.updatedAt = Date.now()
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
    const nextScore = scoreText(resolved.score_awarded)
    const nextNote = baseNote(resolved)
    const review = resolved.metadata.teacher_review as { revision?: number; steps?: Record<string, unknown>[] } | undefined
    const savedSteps = review?.revision === resolved.revision && Array.isArray(review.steps)
      ? review.steps.map((step) => ({ partId: String(step.part_id ?? ''), stepId: String(step.step_id ?? ''),
        maxScore: Number(step.max_score), scoreText: String(step.score_awarded) })) : undefined
    if (current) {
      current.scoreText = nextScore
      current.note = nextNote
      current.baseScoreText = nextScore
      current.baseNote = nextNote
      current.baseRevision = resolved.revision
      current.stepScores = savedSteps
      current.baseStepsJson = JSON.stringify(savedSteps ?? null)
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
      baseStepsJson: JSON.stringify(savedSteps ?? null),
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
    updateSteps,
    updateNote,
    markConfirmed,
    markConfirmedMany,
    reset,
  }
})
