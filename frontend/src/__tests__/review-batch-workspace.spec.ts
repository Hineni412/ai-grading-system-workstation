import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchReviewRubric, type ReviewItem, type ReviewQuestionSummary, type ReviewRubricSection } from '../api/review'
import ReviewBatchWorkspace from '../components/review/ReviewBatchWorkspace.vue'
import { useReviewDraftStore } from '../stores/review-drafts'

vi.mock('../api/review', async (importOriginal) => ({ ...await importOriginal<typeof import('../api/review')>(), fetchReviewRubric: vi.fn() }))
import { clearReviewRubrics } from '../components/review/review-rubric-cache'

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
  clearReviewRubrics()
  vi.mocked(fetchReviewRubric).mockResolvedValue(null)
})

describe('question batch review workspace', () => {

  it('skips non-red and locked steps across cards and submits complete explicit step attribution', async () => {
    const rubric: ReviewRubricSection = { question_id: 'Q1', parent_question_id: 'Q1', question_type: 'calculation', max_score: 5, knowledge_labels: [],
      points: [3, 2].map((score, index) => ({ part_id: 'Q1', part_label: '', step_id: `S${index + 1}`, core_goal: '测试步骤', score,
        standard_answer: '', accepted_answers: [], match_rule: '', required_elements: [], deduction_rules: [], answer_only_max_score: null, require_final_answer: null, final_answer_rule: '' })) }
    vi.mocked(fetchReviewRubric).mockResolvedValue(rubric)
    const assessments = [{ part_id: 'Q1', step_id: 'S1', score_awarded: 3, achievement: 'full' },
      { part_id: 'Q1', step_id: 'S2', score_awarded: 0, achievement: 'uncertain' }]
    const items = [item(1, { review_item_id: 'batch:1', score_awarded: 3, metadata: { step_assessments: assessments } }),
      item(2, { review_item_id: 'batch:2', score_awarded: 3, score_status: 'ai_ready', metadata: { step_assessments: assessments } }),
      item(3, { review_item_id: 'batch:3', score_awarded: 3, score_status: 'failed', metadata: { step_assessments: assessments } }),
      item(4, { review_item_id: 'batch:4', score_awarded: 3, score_status: 'teacher_final', teacher_locked: true, metadata: { step_assessments: assessments } })]
    const { app, host, confirmBatch } = await mountWorkspace(items, { questions: [{ ...questions[0]!, question_type: 'calculation' }] })
    await vi.waitFor(() => expect(host.querySelectorAll('.review-step-cell > input')).toHaveLength(8))
    const inputs = [...host.querySelectorAll<HTMLInputElement>('.review-step-cell > input')]
    const press = async (input: HTMLInputElement, key: string, shiftKey = false) => {
      input.focus(); input.dispatchEvent(new KeyboardEvent('keydown', { key, shiftKey, bubbles: true, cancelable: true })); await nextTick()
    }
    await press(inputs[0]!, 'Tab')
    expect(document.activeElement).toBe(inputs[1])
    await press(inputs[1]!, 'Tab')
    expect(document.activeElement).toBe(inputs[4])
    await press(inputs[4]!, 'Tab', true)
    expect(document.activeElement).toBe(inputs[1])
    expect([...host.querySelectorAll<HTMLInputElement>('.review-step-record input')].every(input => input.tabIndex === -1)).toBe(true)
    await press(inputs[5]!, 'Enter')
    expect(confirmBatch).toHaveBeenCalledTimes(1)
    const payload = confirmBatch.mock.calls[0]![0]
    expect(payload).toHaveLength(2)
    expect(payload.every((input: { step_scores: unknown[] }) => input.step_scores.length === 2)).toBe(true)
    expect(payload[0].step_scores[1]).toEqual({ part_id: 'Q1', step_id: 'S2', score_awarded: 0, teacher_note: null, carried_error_from: null })
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
