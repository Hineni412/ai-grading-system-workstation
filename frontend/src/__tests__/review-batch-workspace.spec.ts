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
  it('selects compact or expanded answer layouts from the rubric question type', async () => {
    const compact = await mountWorkspace([item(1)])
    expect(compact.host.querySelector('[data-testid="review-contact-sheet"]')
      ?.getAttribute('data-question-layout')).toBe('compact')
    compact.app.unmount()

    const expanded = await mountWorkspace(
      [item(1, { question_id: 'Q2' })],
      { selectedQuestionId: 'Q2' },
    )
    expect(expanded.host.querySelector('[data-testid="review-contact-sheet"]')
      ?.getAttribute('data-question-layout')).toBe('expanded')
    expanded.app.unmount()

    const unknown = await mountWorkspace(
      [item(1)],
      {
        questions: [{ ...questions[0]!, question_type: null }],
      },
    )
    expect(unknown.host.querySelector('[data-testid="review-contact-sheet"]')
      ?.getAttribute('data-question-layout')).toBe('expanded')
    unknown.app.unmount()
  })

  it('renders question progress and only the provided batch answers', async () => {
    const confirmed = item(3, { needs_review: false, deduction_reason: '人工复核已确认' })
    const { app, host } = await mountWorkspace([item(1), item(2), confirmed])

    expect(host.querySelector('[data-testid="question-strip"]')?.textContent).toContain('Q1')
    expect(host.querySelector('[data-testid="question-strip"]')?.textContent)
      .toContain('待人工 0 · 待复核 2 · AI 已评 0')
    expect(host.querySelector('[data-testid="question-strip"]')?.textContent)
      .toContain('教师确认 0 / 总计 3')
    expect(host.querySelector('[data-question-id="Q1"]')?.getAttribute('aria-current')).toBe('true')
    expect(host.querySelectorAll('[data-testid="review-answer-sheet"]')).toHaveLength(3)
    expect(host.querySelector('[data-detail-id="3"] input')).toHaveProperty('disabled', true)

    app.unmount()
  })

  it('prioritizes failed question cards ahead of needs-review cards', async () => {
    const failedFirst: ReviewQuestionSummary[] = [
      { ...questions[0]!, question_id: 'Q1', needs_review_count: 2, failed_count: 0 },
      { ...questions[1]!, question_id: 'Q2', needs_review_count: 0, failed_count: 1 },
      {
        question_id: 'Q3',
        question_type: 'proof',
        total_count: 1,
        needs_review_count: 0,
        ungraded_count: 0,
        failed_count: 0,
        teacher_confirmed_count: 1,
        max_score: 6,
      },
    ]
    const { app, host } = await mountWorkspace([item(1)], { questions: failedFirst })

    const strip = host.querySelector('[data-testid="question-strip"]')!
    const orderedIds = [...strip.querySelectorAll('[data-question-id]')]
      .map((entry) => entry.getAttribute('data-question-id'))
    expect(orderedIds).toEqual(['Q2', 'Q1', 'Q3'])
    const failedCard = strip.querySelector('[data-question-id="Q2"]')
    expect(failedCard?.getAttribute('data-has-failed')).toBe('true')
    expect(failedCard?.textContent).toContain('失败 1')
    expect(failedCard?.getAttribute('aria-label')).toContain('AI 评分失败')
    expect(strip.querySelector('[data-question-id="Q1"]')?.getAttribute('data-has-failed')).toBeNull()

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
        score_awarded: 2,
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

  it('moves Tab and Shift+Tab directly between editable student scores', async () => {
    const locked = item(2, {
      needs_review: false,
      score_status: 'teacher_final',
      score_source: 'teacher',
      teacher_locked: true,
    })
    const { app, host, confirmBatch } = await mountWorkspace([
      item(1),
      locked,
      item(3),
    ])
    const first = host.querySelector<HTMLInputElement>('[data-testid="teacher-score-0"]')!
    const third = host.querySelector<HTMLInputElement>('[data-testid="teacher-score-2"]')!

    first.focus()
    const forward = new KeyboardEvent('keydown', {
      key: 'Tab',
      bubbles: true,
      cancelable: true,
    })
    first.dispatchEvent(forward)
    await nextTick()

    expect(forward.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(third)

    const backward = new KeyboardEvent('keydown', {
      key: 'Tab',
      shiftKey: true,
      bubbles: true,
      cancelable: true,
    })
    third.dispatchEvent(backward)
    await nextTick()

    expect(backward.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(first)
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

  it('does not submit unchanged high-confidence AI scores and disables entry while saving', async () => {
    const highConfidence = item(1, {
      needs_review: false,
      score_status: 'ai_ready',
      score_source: 'ai',
      teacher_locked: false,
    })
    const pending = item(2)
    const active = await mountWorkspace([highConfidence, pending])
    active.host.querySelector<HTMLInputElement>('[data-testid="teacher-score-1"]')!
      .dispatchEvent(new KeyboardEvent('keydown', {
        key: 'Enter',
        bubbles: true,
        cancelable: true,
      }))
    await nextTick()

    expect(active.confirmBatch).toHaveBeenCalledTimes(1)
    expect(active.confirmBatch.mock.calls[0]?.[2].map((entry: ReviewItem) => entry.detail_id))
      .toEqual([2])
    active.app.unmount()

    const saving = await mountWorkspace([item(3)], { submitting: true })
    expect(saving.host.querySelector<HTMLInputElement>('[data-testid="teacher-score-0"]')?.disabled)
      .toBe(true)
    saving.app.unmount()
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
