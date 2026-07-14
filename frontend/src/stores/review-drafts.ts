import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import type { ReviewItem } from '../api/review'

export interface ReviewDraft {
  key: string
  sessionId: number
  questionId: string
  detailId: number
  scoreText: string
  note: string
  baseScoreText: string
  baseNote: string
  dirty: boolean
  updatedAt: number
}

export function reviewDraftKey(item: Pick<ReviewItem, 'session_id' | 'question_id' | 'detail_id'>): string {
  return `${item.session_id}:${item.question_id}:${item.detail_id}`
}

export function scoreIssue(scoreText: string, maxScore: number): string | null {
  const clean = scoreText.trim()
  if (!clean) return '请输入教师最终分'
  const score = Number(clean)
  if (!Number.isFinite(score)) return '教师最终分必须是有效数字'
  if (score < 0) return '教师最终分不能低于 0 分'
  if (!Number.isFinite(maxScore) || maxScore < 0) return '评分标准满分暂不可用'
  if (score > maxScore) return `教师最终分不能超过 ${String(maxScore)} 分`
  return null
}

function scoreText(value: number): string {
  return Number.isFinite(value) ? String(value) : ''
}

function baseNote(item: ReviewItem): string {
  return item.needs_review ? '' : (item.deduction_reason ?? '').trim()
}

function refreshDirty(draft: ReviewDraft): void {
  draft.dirty = draft.scoreText !== draft.baseScoreText || draft.note !== draft.baseNote
  draft.updatedAt = Date.now()
}

export const useReviewDraftStore = defineStore('review-drafts', () => {
  const drafts = ref<Record<string, ReviewDraft>>({})
  const dirtyCount = computed(() =>
    Object.values(drafts.value).filter((draft) => draft.dirty).length,
  )
  const hasDirtyDrafts = computed(() => dirtyCount.value > 0)

  function ensureDraft(item: ReviewItem): ReviewDraft {
    const key = reviewDraftKey(item)
    const current = drafts.value[key]
    if (current?.dirty) return current

    const nextScore = scoreText(item.score_awarded)
    const nextNote = baseNote(item)
    if (current) {
      current.scoreText = nextScore
      current.note = nextNote
      current.baseScoreText = nextScore
      current.baseNote = nextNote
      current.dirty = false
      current.updatedAt = Date.now()
      return current
    }

    const draft: ReviewDraft = {
      key,
      sessionId: item.session_id,
      questionId: item.question_id,
      detailId: item.detail_id,
      scoreText: nextScore,
      note: nextNote,
      baseScoreText: nextScore,
      baseNote: nextNote,
      dirty: false,
      updatedAt: Date.now(),
    }
    drafts.value[key] = draft
    return draft
  }

  function updateScore(key: string, value: string): void {
    const draft = drafts.value[key]
    if (!draft) return
    draft.scoreText = value
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
    updateNote,
    markConfirmed,
    markConfirmedMany,
    reset,
  }
})
