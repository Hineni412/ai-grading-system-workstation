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
    score_awarded: index === 1 ? 3 : 2.5,
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
    total_count: 3,
    needs_review_count: 2,
    ungraded_count: 0,
    teacher_confirmed_count: 0,
    max_score: 5,
  },
  {
    question_id: 'Q2',
    total_count: 2,
    needs_review_count: 1,
    ungraded_count: 0,
    teacher_confirmed_count: 0,
    max_score: 8,
  },
]

async function mountWorkspace(items: ReviewItem[] = [item(1), item(2)]) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const confirmBatch = vi.fn()
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(ReviewBatchWorkspace, {
    questions,
    selectedQuestionId: 'Q1',
    items,
    search: '',
    scope: 'teacher_pending',
    sort: 'risk',
    page: 1,
    totalPages: 1,
    filteredTotal: items.length,
    loading: false,
    submitting: false,
    onConfirmBatch: confirmBatch,
  })
  app.use(pinia)
  app.mount(host)
  await nextTick()
  return { app, host, pinia, confirmBatch }
}

beforeEach(() => {
  document.body.innerHTML = ''
})

describe('question batch review workspace', () => {
  it('renders question progress and only the provided batch answers', async () => {
    const confirmed = item(3, { needs_review: false, deduction_reason: '人工复核已确认' })
    const { app, host } = await mountWorkspace([item(1), item(2), confirmed])

    expect(host.querySelector('[data-testid="question-strip"]')?.textContent).toContain('Q1')
    expect(host.querySelector('[data-testid="question-strip"]')?.textContent).toContain('未批 0 · 教师确认 0 / 3')
    expect(host.querySelector('[data-question-id="Q1"]')?.getAttribute('aria-current')).toBe('true')
    expect(host.querySelectorAll('[data-testid="review-answer-sheet"]')).toHaveLength(3)
    expect(host.querySelector('[data-detail-id="3"] input')).toHaveProperty('disabled', true)

    app.unmount()
  })

  it('emits one batch containing valid unchanged pending scores', async () => {
    const { app, host, confirmBatch } = await mountWorkspace()

    const button = host.querySelector<HTMLButtonElement>('[data-testid="confirm-batch"]')!
    expect(button.disabled).toBe(false)
    button.click()
    await nextTick()

    expect(confirmBatch).toHaveBeenCalledTimes(1)
    expect(confirmBatch.mock.calls[0]?.[0]).toEqual([
      {
        review_item_id: '7:Q1:1',
        expected_revision: 0,
        student_id: 101,
        result_id: 101,
        detail_id: 1,
        score_awarded: 3,
      },
      {
        review_item_id: '7:Q1:2',
        expected_revision: 0,
        student_id: 102,
        result_id: 102,
        detail_id: 2,
        score_awarded: 2.5,
      },
    ])
    expect(confirmBatch.mock.calls[0]?.[1]).toEqual(['7:Q1:1', '7:Q1:2'])
    expect(confirmBatch.mock.calls[0]?.[2].map((entry: ReviewItem) => entry.detail_id)).toEqual([1, 2])

    app.unmount()
  })

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

  it('moves Enter to the next score without confirming the batch', async () => {
    const { app, host, confirmBatch } = await mountWorkspace()
    const first = host.querySelector<HTMLInputElement>('[data-testid="teacher-score-0"]')!
    const second = host.querySelector<HTMLInputElement>('[data-testid="teacher-score-1"]')!
    const focus = vi.spyOn(second, 'focus')

    first.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Enter',
      bubbles: true,
      cancelable: true,
    }))
    await nextTick()

    expect(focus).toHaveBeenCalledTimes(1)
    expect(confirmBatch).not.toHaveBeenCalled()

    app.unmount()
  })

  it('keeps image failure local and remounts the same controlled crop URL on retry', async () => {
    const { app, host } = await mountWorkspace([item(1)])
    const image = host.querySelector<HTMLImageElement>('[data-testid="answer-crop-0"]')!
    image.dispatchEvent(new Event('error'))
    await nextTick()

    expect(host.textContent).toContain('答卷图片暂时无法显示')
    const retry = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((entry) => entry.textContent === '重新加载答卷图片')!
    retry.click()
    await nextTick()

    expect(host.querySelector<HTMLImageElement>('[data-testid="answer-crop-0"]')?.getAttribute('src')).toBe('/api/crop/1')

    app.unmount()
  })
})
