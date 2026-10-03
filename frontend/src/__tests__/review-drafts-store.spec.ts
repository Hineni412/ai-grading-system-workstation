import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it } from 'vitest';

import type { ReviewItem } from '../api/review';
import { useReviewDraftStore, stepNeedsReview, aiStepFor } from '../stores/review-drafts';

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

  it('restores saved teacher steps and retains changed attribution even if total is unchanged', () => {
    const store = useReviewDraftStore()
    const saved = item({ review_item_id: 'batch:1:Q1', revision: 2, score_awarded: 3,
      metadata: { teacher_review: { revision: 2, steps: [
        { part_id: 'Q1', step_id: 'S1', max_score: 3, score_awarded: 3 },
        { part_id: 'Q1', step_id: 'S2', max_score: 2, score_awarded: 0 },
      ] } } })
    const draft = store.ensureDraft(saved)
    expect(draft.stepScores?.map((step) => step.scoreText)).toEqual(['3', '0'])
    expect(draft.dirty).toBe(false)
    store.updateSteps(draft.key, draft.stepScores!.map((step, index) => ({ ...step, scoreText: index ? '1' : '2' })))
    expect(store.drafts[draft.key]?.dirty).toBe(true)
    expect(store.ensureDraft(saved).stepScores?.map((step) => step.scoreText)).toEqual(['2', '1'])
    store.updateScore(draft.key, '3')
    expect(store.drafts[draft.key]?.stepScores).toBeUndefined()
    expect(store.drafts[draft.key]?.dirty).toBe(true)
  })

  it('auto step initialisation stays clean until the teacher enters a score', () => {
    const store = useReviewDraftStore()
    const draft = store.ensureDraft(item({ review_item_id: 'batch:1:Q1', score_awarded: 3 }))
    store.initSteps(draft.key, [
      { partId: 'Q1', stepId: 'S1', maxScore: 3, scoreText: '3', note: '', carriedFrom: null, carryExplicit: false },
      { partId: 'Q1', stepId: 'S2', maxScore: 2, scoreText: '0', note: '', carriedFrom: null, carryExplicit: false },
    ])

    expect(store.drafts[draft.key]?.scoreText).toBe('3')
    expect(store.drafts[draft.key]?.dirty).toBe(false)
    expect(store.dirtyCount).toBe(0)

    const steps = store.drafts[draft.key]!.stepScores!
    store.updateSteps(draft.key, steps.map((step, index) => index === 0 ? { ...step, scoreText: '2' } : step))
    expect(store.drafts[draft.key]?.dirty).toBe(true)
    expect(store.dirtyCount).toBe(1)

    // 点回原值后恢复为非草稿状态
    store.updateSteps(draft.key, steps.map((step, index) => index === 0 ? { ...step, scoreText: '3' } : step))
    expect(store.drafts[draft.key]?.dirty).toBe(false)
  })

  it('marks only uncertain steps, with status fallbacks and no per-step confidence', () => {
    const store = useReviewDraftStore()
    const source = item({ review_item_id: 'batch:1:Q1', metadata: { step_assessments: [
      { part_id: 'Q1', step_id: 'S1', score_awarded: 3, achievement: 'full' },
      { part_id: 'Q1', step_id: 'S2', score_awarded: 0, achievement: 'uncertain' },
    ] } })
    const draft = store.ensureDraft(source)
    store.initSteps(draft.key, [
      { partId: 'Q1', stepId: 'S1', maxScore: 3, scoreText: '3', note: '', carriedFrom: null, carryExplicit: false },
      { partId: 'Q1', stepId: 'S2', maxScore: 2, scoreText: '0', note: '', carriedFrom: null, carryExplicit: false },
    ])
    const steps = draft.stepScores!
    expect(steps.map(step => stepNeedsReview(source, step).red)).toEqual([false, true])
    for (const status of ['ungraded', 'failed', 'ai_review', 'ai_ready', 'teacher_final'] as const) {
      const current = { ...source, score_status: status, teacher_locked: status === 'teacher_final', metadata: {} }
      expect(steps.map(step => stepNeedsReview(current, step).red)).toEqual(status === 'ai_ready' || status === 'teacher_final' ? [false, false] : [true, true])
    }
    expect(aiStepFor({ ...source, metadata: { step_assessments: [
      { step_id: 'S1', score_awarded: 3 }, { step_id: 'S1', score_awarded: 0 },
    ] } }, steps[0]!)).toBeUndefined()
  })

  it('rechecks subsequent steps, restores automatic carry, and preserves explicit teacher choices', () => {
    const store = useReviewDraftStore()
    const source = item({ review_item_id: 'batch:1:Q1', max_score: 7, metadata: { step_assessments: [
      { part_id: 'Q1', step_id: 'S1', score_awarded: 0 },
      { part_id: 'Q1', step_id: 'S2', score_awarded: 0, carried_error_from: 'S1' },
      { part_id: 'Q1', step_id: 'S3', score_awarded: 3 },
    ] } })
    const rubric = { question_id: 'Q1', parent_question_id: 'Q1', question_type: 'calculation', max_score: 7, knowledge_labels: [],
      points: [2, 2, 3].map((score, index) => ({ part_id: 'Q1', part_label: '', step_id: `S${index + 1}`, core_goal: '', score,
        standard_answer: '', accepted_answers: [], match_rule: '', required_elements: [], deduction_rules: [], answer_only_max_score: null, require_final_answer: null, final_answer_rule: '' })) }
    store.ensureSteps(source, rubric)
    const draft = store.ensureDraft(source)
    expect(draft.stepScores![1]!.carriedFrom).toBe('S1')
    expect(draft.dirty).toBe(false)
    store.updateStep(draft.key, 0, { scoreText: '2' })
    expect(draft.stepScores![1]!.carriedFrom).toBeNull()
    expect(draft.stepScores![1]!.recheck).toContain('已改为达成')
    store.updateStep(draft.key, 0, { scoreText: '0' })
    expect(draft.stepScores![1]!.carriedFrom).toBe('S1')
    expect(draft.stepScores![1]!.recheck).toBe('')
    store.updateStep(draft.key, 1, { carriedFrom: null, carryExplicit: true })
    store.updateStep(draft.key, 0, { scoreText: '2' })
    store.updateStep(draft.key, 0, { scoreText: '0' })
    expect(draft.stepScores![1]!.carriedFrom).toBeNull()
    store.updateStep(draft.key, 2, { scoreText: '0' })
    // 改分过的后步不自动替教师决定分数或沿用。
    expect(draft.stepScores![2]!.scoreText).toBe('0')
    store.updateStep(draft.key, 0, { scoreText: '2' })
    store.updateStep(draft.key, 0, { scoreText: '0' })
    expect(draft.stepScores![2]!.recheck).toBe('')
  })

  it('rechecks full subsequent steps when a previously full step loses points', () => {
    const store = useReviewDraftStore()
    const draft = store.ensureDraft(item({ review_item_id: 'batch:1:Q1', score_awarded: 5 }))
    store.initSteps(draft.key, [
      { partId: 'Q1', stepId: 'S1', maxScore: 3, scoreText: '3', initialScoreText: '3', note: '', carriedFrom: null, carryExplicit: false },
      { partId: 'Q1', stepId: 'S2', maxScore: 2, scoreText: '2', initialScoreText: '2', note: '', carriedFrom: null, carryExplicit: false },
    ])
    store.updateStep(draft.key, 0, { scoreText: '2' })
    expect(draft.stepScores![1]!.recheck).toContain('已改为未达成')
    store.updateStep(draft.key, 0, { scoreText: '3' })
    expect(draft.stepScores![1]!.recheck).toBe('')
    store.updateStep(draft.key, 1, { note: '  教师说明  ' })
    expect(draft.dirty).toBe(true)
  })

  it('rechecks invalidated manual carry without restoring the teacher choice automatically', () => {
    const store = useReviewDraftStore()
    const draft = store.ensureDraft(item({ review_item_id: 'manual-carry:Q1', score_awarded: 0 }))
    store.initSteps(draft.key, [
      { partId: 'Q1', stepId: 'S1', maxScore: 3, scoreText: '0', initialScoreText: '0', note: '', carriedFrom: null, carryExplicit: false },
      { partId: 'Q1', stepId: 'S2', maxScore: 2, scoreText: '0', initialScoreText: '0', note: '', carriedFrom: null, carryExplicit: false },
    ])
    store.updateStep(draft.key, 1, { carriedFrom: 'S1', carryExplicit: true })
    store.updateStep(draft.key, 0, { scoreText: '3' })
    expect(draft.stepScores![1]!.carriedFrom).toBeNull()
    expect(draft.stepScores![1]!.recheck).toContain('①已改为达成')
    store.updateStep(draft.key, 0, { scoreText: '0' })
    expect(draft.stepScores![1]!.carriedFrom).toBeNull()
    expect(draft.stepScores![1]!.recheck).toBe('')
    store.updateStep(draft.key, 0, { scoreText: '3' })
    store.updateStep(draft.key, 1, { scoreText: '2' })
    expect(draft.stepScores![1]!.recheck).toBe('')
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

})
