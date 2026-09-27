import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ReviewItem, ReviewQuestionSummary } from '../api/review'
import ReviewBatchWorkspace from '../components/review/ReviewBatchWorkspace.vue'
import { useReviewDraftStore } from '../stores/review-drafts'

const media = {
  crop_url: '/api/crop',
  original_front_url: '/api/front',
  original_back_url: '/api/back',
  annotated_front_url: '/api/annotated-front',
  annotated_back_url: '/api/annotated-back',
}

function item(index: number, overrides: Partial<ReviewItem> = {}): ReviewItem {
  return {
    session_id: 7,
    result_id: 100 + index,
    detail_id: index,
    question_id: 'Q1',
    student_code: `S${index}`,
    student_name: `匿名学生${index}`,
    class_name: '匿名班级',
    score_awarded: index === 1 ? 3 : 2,
    max_score: 5,
    deduction_reason: '步骤不完整',
    error_category: '需复核',
    error_summary: null,
    confidence_score: 70,
    needs_review: true,
    candidate_scores: [],
    metadata: {},
    media: { ...media, crop_url: `/api/crop/${index}` },
    ...overrides,
  }
}

const questions: ReviewQuestionSummary[] = [
  {
    question_id: 'Q1',
    question_type: 'choice',
    total_count: 3,
    needs_review_count: 2,
    ungraded_count: 0,
    teacher_confirmed_count: 0,
    max_score: 5,
  },
  {
    question_id: 'Q2',
    question_type: 'proof',
    total_count: 2,
    needs_review_count: 1,
    ungraded_count: 0,
    teacher_confirmed_count: 0,
    max_score: 8,
  },
]

async function mountWorkspace(
  items: ReviewItem[] = [item(1), item(2)],
  options: {
    queueItems?: ReviewItem[]
    questions?: ReviewQuestionSummary[]
    selectedQuestionId?: string
    page?: number
    totalPages?: number
    submitting?: boolean
  } = {},
) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const confirmBatch = vi.fn()
  const focusScore = vi.fn()
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(ReviewBatchWorkspace, {
    questions: options.questions ?? questions,
    selectedQuestionId: options.selectedQuestionId ?? 'Q1',
    items,
    queueItems: options.queueItems ?? items,
    search: '',
    scope: 'teacher_pending',
    sort: 'risk',
    page: options.page ?? 1,
    totalPages: options.totalPages ?? 1,
    filteredTotal: (options.queueItems ?? items).length,
    loading: false,
    submitting: options.submitting ?? false,
    onConfirmBatch: confirmBatch,
    onFocusScore: focusScore,
  })
  app.use(pinia)
  app.mount(host)
  await nextTick()
  return { app, host, pinia, confirmBatch, focusScore }
}

beforeEach(() => {
  document.body.innerHTML = ''
})

describe('question batch review workspace', () => {

  it('blocks the whole batch when one score is invalid and retains every draft', async () => {
    const { app, host, pinia, confirmBatch } = await mountWorkspace()
    const invalid = host.querySelector<HTMLInputElement>('[data-testid="teacher-score-1"]')!
    invalid.value = '6'
    invalid.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()

    const button = host.querySelector<HTMLButtonElement>('[data-testid="confirm-batch"]')!
    expect(button.disabled).toBe(true)
    expect(host.textContent).toContain('教师最终分不能超过 5 分')
    expect(useReviewDraftStore(pinia).drafts['7:Q1:2']?.scoreText).toBe('6')
    expect(confirmBatch).not.toHaveBeenCalled()

    app.unmount()
  })

  it('submits the complete filtered queue once from the final score across pages', async () => {
    const queueItems = [item(1), item(2)]
    const { app, host, confirmBatch } = await mountWorkspace(
      [queueItems[1]!],
      { queueItems, page: 2, totalPages: 2 },
    )
    const finalScore = host.querySelector<HTMLInputElement>('[data-testid="teacher-score-0"]')!

    finalScore.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Enter',
      bubbles: true,
      cancelable: true,
    }))
    await nextTick()

    expect(confirmBatch).toHaveBeenCalledTimes(1)
    expect(confirmBatch.mock.calls[0]?.[0]).toHaveLength(2)
    expect(confirmBatch.mock.calls[0]?.[2].map((entry: ReviewItem) => entry.detail_id))
      .toEqual([1, 2])

    app.unmount()
  })

  it('focuses the first invalid score across pages and does not submit', async () => {
    const queueItems = [
      item(1, { score_awarded: 6 }),
      item(2),
    ]
    const { app, host, pinia, confirmBatch, focusScore } = await mountWorkspace(
      [queueItems[1]!],
      { queueItems, page: 2, totalPages: 2 },
    )

    host.querySelector<HTMLInputElement>('[data-testid="teacher-score-0"]')!
      .dispatchEvent(new KeyboardEvent('keydown', {
        key: 'Enter',
        bubbles: true,
        cancelable: true,
      }))
    await nextTick()

    expect(confirmBatch).not.toHaveBeenCalled()
    expect(focusScore).toHaveBeenCalledWith('7:Q1:1')
    expect(useReviewDraftStore(pinia).drafts['7:Q1:1']?.scoreText).toBe('6')

    app.unmount()
  })

})
