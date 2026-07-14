import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import type { ReviewItem } from '../api/review'
import { scoreIssue, useReviewDraftStore } from '../stores/review-drafts'

const media = {
  crop_url: '/api/crop',
  original_front_url: '/api/front',
  original_back_url: '/api/back',
  annotated_front_url: '/api/annotated-front',
  annotated_back_url: '/api/annotated-back',
}

function item(overrides: Partial<ReviewItem> = {}): ReviewItem {
  return {
    session_id: 7,
    result_id: 11,
    detail_id: 21,
    question_id: 'Q1',
    student_code: 'S001',
    student_name: '测试学生',
    class_name: '七年级一班',
    score_awarded: 3,
    max_score: 5,
    deduction_reason: 'AI 扣分原因',
    error_category: '需复核',
    error_summary: null,
    confidence_score: 72,
    needs_review: true,
    candidate_scores: [],
    metadata: {},
    media,
    ...overrides,
  }
}

describe('review draft store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('preserves dirty drafts across record switches without storing identity text', () => {
    const store = useReviewDraftStore()
    const first = store.ensureDraft(item())
    store.updateScore(first.key, '4.5')
    store.updateNote(first.key, '步骤二符号错误')
    store.ensureDraft(item({ detail_id: 22, result_id: 12, student_name: '另一名学生' }))
    store.ensureDraft(item({ score_awarded: 1 }))

    expect(store.drafts[first.key]).toMatchObject({
      scoreText: '4.5',
      note: '步骤二符号错误',
      dirty: true,
    })
    expect(JSON.stringify(store.drafts)).not.toContain('测试学生')
    expect(store.hasDirtyDrafts).toBe(true)
    expect(store.dirtyCount).toBe(1)
  })

  it('refreshes clean drafts from the server and keeps dirty drafts authoritative', () => {
    const store = useReviewDraftStore()
    const draft = store.ensureDraft(item())
    store.ensureDraft(item({ score_awarded: 4 }))
    expect(store.drafts[draft.key]?.scoreText).toBe('4')

    store.updateScore(draft.key, '4.5')
    store.ensureDraft(item({ score_awarded: 2 }))
    expect(store.drafts[draft.key]?.scoreText).toBe('4.5')
  })

  it.each([
    ['', '请输入教师最终分'],
    ['NaN', '教师最终分必须是有效数字'],
    ['Infinity', '教师最终分必须是有效数字'],
    ['-0.1', '教师最终分不能低于 0 分'],
    ['5.1', '教师最终分不能超过 5 分'],
    ['4.25', null],
  ])('validates score %s against the existing maximum', (scoreText, expected) => {
    expect(scoreIssue(scoreText, 5)).toBe(expected)
  })

  it('isolates drafts by session, question and detail and clears only confirmed work', () => {
    const store = useReviewDraftStore()
    const first = store.ensureDraft(item())
    const second = store.ensureDraft(item({ session_id: 8 }))
    const third = store.ensureDraft(item({ question_id: 'Q2' }))
    const fourth = store.ensureDraft(item({ detail_id: 22 }))
    store.updateScore(first.key, '4')
    store.updateScore(second.key, '4')

    expect(new Set([first.key, second.key, third.key, fourth.key]).size).toBe(4)
    store.markConfirmed(first.key)
    expect(store.drafts[first.key]).toBeUndefined()
    expect(store.drafts[second.key]?.scoreText).toBe('4')
  })

  it('clears an explicitly confirmed batch without touching other drafts', () => {
    const store = useReviewDraftStore()
    const first = store.ensureDraft(item())
    const second = store.ensureDraft(item({ detail_id: 22, result_id: 12 }))
    const retained = store.ensureDraft(item({ detail_id: 23, result_id: 13 }))

    store.markConfirmedMany([first.key, second.key, 'missing:key'])

    expect(store.drafts[first.key]).toBeUndefined()
    expect(store.drafts[second.key]).toBeUndefined()
    expect(store.drafts[retained.key]).toBeDefined()
  })

  it('does not seed an AI deduction reason as a teacher note', () => {
    const store = useReviewDraftStore()
    expect(store.ensureDraft(item()).note).toBe('')
    expect(store.ensureDraft(item({ detail_id: 22, needs_review: false })).note).toBe('AI 扣分原因')
  })
})
