import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ReviewConfirmResponse, ReviewItem } from '../api/review'
import { confirmReviewItem, fetchReviewItems, fetchReviewRubric } from '../api/review'
import ReviewScoringInspector from '../components/review/ReviewScoringInspector.vue'
import { useReviewDraftStore } from '../stores/review-drafts'
import { useReviewQueueStore } from '../stores/review-queue'

vi.mock('../api/review', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/review')>(),
  fetchReviewRubric: vi.fn(),
  fetchReviewItems: vi.fn(),
  confirmReviewItem: vi.fn(),
}))

const media = {
  crop_url: '/api/crop',
  original_front_url: '/api/front',
  original_back_url: '/api/back',
  annotated_front_url: '/api/annotated-front',
  annotated_back_url: '/api/annotated-back',
}

const item: ReviewItem = {
  session_id: 7,
  result_id: 11,
  detail_id: 21,
  question_id: 'Q1',
  student_code: 'S001',
  student_name: '测试学生',
  class_name: '七年级一班',
  score_awarded: 3,
  max_score: 5,
  deduction_reason: '步骤不完整',
  error_category: '需复核',
  error_summary: '缺少关键关系式',
  confidence_score: 72,
  needs_review: true,
  candidate_scores: [{ score: 2.5, confidence: 0.6, reason: '另一种判定' }],
  metadata: { evidence_steps: ['写出变量'], missing_steps: ['列出关系式'] },
  media,
}

async function mountInspector(items: ReviewItem[] = [item]) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const queue = useReviewQueueStore(pinia)
  if (items[0] && !items[0].needs_review) {
    queue.setScope('all')
  }
  queue.$patch({
    questions: [{
      question_id: 'Q1',
      total_count: items.length,
      needs_review_count: items.filter((candidate) => candidate.needs_review).length,
      max_score: 5,
    }],
  })
  queue.selectQuestion('Q1')
  queue.replaceItems(items, items[0]?.detail_id)
  const host = document.createElement('div')
  const confirmed = vi.fn()
  const annotationRetry = vi.fn()
  const app = createApp(ReviewScoringInspector, {
    registerAnnotationRetry: annotationRetry,
    onConfirmed: confirmed,
  })
  app.use(pinia)
  app.mount(host)
  await vi.waitFor(() => expect(fetchReviewRubric).toHaveBeenCalledWith(7, 'Q1', expect.any(AbortSignal)))
  await nextTick()
  return { app, host, pinia, confirmed, annotationRetry }
}

describe('review scoring inspector', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchReviewRubric).mockResolvedValue({
      questionId: 'Q1',
      parentQuestionId: null,
      title: 'Q1',
      maxScore: 5,
      questionType: 'comprehensive',
      knowledgeLabels: ['一次函数'],
      lines: [{ label: '列出正确关系式'.repeat(20), score: 3 }],
    })
    vi.mocked(fetchReviewItems).mockResolvedValue([item])
    vi.mocked(confirmReviewItem).mockResolvedValue({
      updated_details: 1,
      updated_results: 1,
      annotation_outcomes: [],
    })
  })

  it('keeps teacher decision above AI advice without inventing an activity history', async () => {
    const { app, host } = await mountInspector()
    const inspector = host.querySelector('[data-testid="review-scoring-inspector"]')!
    expect(inspector.getAttribute('aria-label')).toBe('评分与复核检查器')
    expect(inspector.textContent).toContain('评分标准')
    expect(inspector.textContent).toContain('AI 初评')
    expect(inspector.textContent).toContain('教师最终分')
    expect(inspector.textContent).toContain('一次函数')
    expect(inspector.textContent).not.toContain('活动记录')
    expect(inspector.textContent).not.toContain('AI 最终分')
    expect(host.querySelector('[data-testid="scoring-scroll-region"]')).not.toBeNull()
    expect(host.querySelector('[data-testid="scoring-footer"]')).not.toBeNull()
    app.unmount()
  })

  it('allows the unchanged valid score to be explicitly confirmed and blocks invalid scores', async () => {
    const { app, host, pinia } = await mountInspector()
    const input = host.querySelector<HTMLInputElement>('[data-testid="teacher-score"]')!
    const button = host.querySelector<HTMLButtonElement>('[data-testid="confirm-single"]')!
    expect(input.getAttribute('aria-describedby')).toContain('teacher-score-help')
    expect(button.disabled).toBe(false)

    input.value = '6'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(host.textContent).toContain('教师最终分不能超过 5 分')
    expect(button.disabled).toBe(true)

    input.value = '4.5'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(button.disabled).toBe(false)
    expect(useReviewDraftStore(pinia).dirtyCount).toBe(1)
    app.unmount()
  })

  it('keeps the draft and current record when confirmation fails', async () => {
    vi.mocked(confirmReviewItem).mockRejectedValue(new Error('private server detail'))
    const { app, host, pinia } = await mountInspector()
    const input = host.querySelector<HTMLInputElement>('[data-testid="teacher-score"]')!
    input.value = '4'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-single"]')!.click()

    const toast = await vi.waitFor(() => {
      const found = document.body.querySelector<HTMLElement>('[data-testid="review-feedback-toast"]')
      expect(found).not.toBeNull()
      return found!
    })
    expect(toast.textContent).toContain('确认失败，教师草稿已保留')
    expect(toast.textContent).not.toContain('private server detail')
    expect(host.querySelector('[data-testid="scoring-scroll-region"]')?.contains(toast)).toBe(false)
    expect(host.querySelector('.review-scoring-feedback')).toBeNull()
    expect(useReviewDraftStore(pinia).drafts['7:Q1:21']?.scoreText).toBe('4')
    expect(useReviewQueueStore(pinia).selectedDetailId).toBe(21)

    toast.querySelector<HTMLButtonElement>('[aria-label="关闭通知"]')!.click()
    await nextTick()
    expect(document.body.querySelector('[data-testid="review-feedback-toast"]')).toBeNull()
    expect(useReviewDraftStore(pinia).drafts['7:Q1:21']?.scoreText).toBe('4')
    app.unmount()
  })

  it('submits once, clears the draft and reports the confirmed record without advancing', async () => {
    const second = { ...item, result_id: 12, detail_id: 22, student_code: 'S002', student_name: '学生乙' }
    vi.mocked(fetchReviewItems).mockResolvedValue([
      { ...item, score_awarded: 4, needs_review: false, deduction_reason: '教师调整' },
      second,
    ])
    vi.mocked(confirmReviewItem).mockResolvedValue({
      updated_details: 1,
      updated_results: 1,
      annotation_outcomes: [{ result_id: 11, status: 'retry_required' }],
    })
    const { app, host, pinia, confirmed, annotationRetry } = await mountInspector([item, second])
    const input = host.querySelector<HTMLInputElement>('[data-testid="teacher-score"]')!
    input.value = '4'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    const button = host.querySelector<HTMLButtonElement>('[data-testid="confirm-single"]')!
    button.click()
    button.click()

    await vi.waitFor(() => expect(confirmReviewItem).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(confirmed).toHaveBeenCalledWith({
      detailId: 21,
      annotationRetry: true,
    }))
    expect(annotationRetry).toHaveBeenCalledWith({
      input: { result_id: 11, detail_id: 21, score_awarded: 4 },
      item,
    })
    expect(useReviewQueueStore(pinia).questions[0]?.needs_review_count).toBe(1)
    await vi.waitFor(() => expect(
      document.body.querySelector('[data-testid="review-feedback-toast"]')?.textContent,
    ).toContain('分数已确认，标注图需要稍后刷新'))
    expect(useReviewDraftStore(pinia).drafts['7:Q1:21']).toBeUndefined()
    app.unmount()
  })

  it('selects the full score when the final-score field receives focus', async () => {
    const { app, host } = await mountInspector()
    const input = host.querySelector<HTMLInputElement>('[data-testid="teacher-score"]')!
    const select = vi.spyOn(input, 'select')

    input.dispatchEvent(new FocusEvent('focus'))

    expect(select).toHaveBeenCalledTimes(1)
    app.unmount()
  })

  it('does not decrement pending count when reconfirming an already reviewed item', async () => {
    const confirmedItem = { ...item, needs_review: false, error_category: '已复核' }
    const pendingItem = { ...item, result_id: 12, detail_id: 22, student_name: '待复核学生' }
    vi.mocked(fetchReviewItems).mockResolvedValue([confirmedItem, pendingItem])
    const { app, host, pinia } = await mountInspector([confirmedItem, pendingItem])

    host.querySelector<HTMLButtonElement>('[data-testid="confirm-single"]')!.click()
    await vi.waitFor(() => expect(confirmReviewItem).toHaveBeenCalledTimes(1))

    expect(useReviewQueueStore(pinia).questions[0]?.needs_review_count).toBe(1)
    app.unmount()
  })

  it('never confirms from score-field or note Enter', async () => {
    const second = {
      ...item,
      result_id: 12,
      detail_id: 22,
      student_code: 'S002',
      student_name: '学生乙',
    }
    vi.mocked(fetchReviewItems).mockResolvedValue([
      { ...item, score_awarded: 4, needs_review: false, deduction_reason: '教师调整' },
      second,
    ])
    const { app, host } = await mountInspector([item, second])
    const input = host.querySelector<HTMLInputElement>('[data-testid="teacher-score"]')!

    input.value = '4'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    const enter = new KeyboardEvent('keydown', {
      key: 'Enter',
      bubbles: true,
      cancelable: true,
    })
    input.dispatchEvent(enter)
    expect(enter.defaultPrevented).toBe(false)
    input.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Enter',
      shiftKey: true,
      bubbles: true,
      cancelable: true,
    }))
    const note = host.querySelector<HTMLTextAreaElement>('#teacher-note')!
    note.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Enter',
      bubbles: true,
      cancelable: true,
    }))
    await nextTick()
    expect(confirmReviewItem).not.toHaveBeenCalled()

    app.unmount()
  })

  it('does not override a teacher selection changed while confirmation is pending', async () => {
    const second = { ...item, result_id: 12, detail_id: 22, student_code: 'S002', student_name: '学生乙' }
    let resolveConfirmation: ((value: ReviewConfirmResponse) => void) | undefined
    vi.mocked(confirmReviewItem).mockImplementation(() => new Promise((resolve) => {
      resolveConfirmation = resolve
    }))
    const { app, host, pinia, annotationRetry } = await mountInspector([item, second])
    const queue = useReviewQueueStore(pinia)
    const input = host.querySelector<HTMLInputElement>('[data-testid="teacher-score"]')!
    input.value = '4'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-single"]')!.click()
    await vi.waitFor(() => expect(confirmReviewItem).toHaveBeenCalledTimes(1))

    queue.selectDetail(22)
    resolveConfirmation!({
      updated_details: 1,
      updated_results: 1,
      annotation_outcomes: [{ result_id: 11, status: 'retry_required' }],
    })

    await vi.waitFor(() => expect(
      document.body.querySelector('[data-testid="review-feedback-toast"]')?.textContent,
    ).toContain(
      '先前记录的分数已确认，标注图需要稍后刷新；当前选择未更改',
    ))
    expect(useReviewDraftStore(pinia).drafts['7:Q1:21']).toBeUndefined()
    expect(queue.selectedDetailId).toBe(22)
    expect(fetchReviewItems).not.toHaveBeenCalled()
    expect(annotationRetry).toHaveBeenCalledWith({
      input: { result_id: 11, detail_id: 21, score_awarded: 4 },
      item,
    })
    app.unmount()
  })

  it('does not patch a new session item that reuses the submitted detail id', async () => {
    let resolveConfirmation: ((value: ReviewConfirmResponse) => void) | undefined
    vi.mocked(confirmReviewItem).mockImplementation(() => new Promise((resolve) => {
      resolveConfirmation = resolve
    }))
    const { app, host, pinia } = await mountInspector()
    const queue = useReviewQueueStore(pinia)
    const input = host.querySelector<HTMLInputElement>('[data-testid="teacher-score"]')!
    input.value = '4'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-single"]')!.click()
    await vi.waitFor(() => expect(confirmReviewItem).toHaveBeenCalledTimes(1))

    const reusedDetail = {
      ...item,
      session_id: 8,
      result_id: 81,
      question_id: 'Q2',
      score_awarded: 1,
      student_name: '另一考试学生',
    }
    queue.selectQuestion('Q2')
    queue.replaceItems([reusedDetail], reusedDetail.detail_id)
    await nextTick()
    resolveConfirmation!({ updated_details: 1, updated_results: 1, annotation_outcomes: [] })

    await vi.waitFor(() => expect(
      document.body.querySelector('[data-testid="review-feedback-toast"]')?.textContent,
    ).toContain('先前记录的教师最终分已确认'))
    expect(useReviewDraftStore(pinia).drafts['7:Q1:21']).toBeUndefined()
    expect(queue.currentItem).toMatchObject({
      session_id: 8,
      question_id: 'Q2',
      detail_id: 21,
      score_awarded: 1,
      needs_review: true,
    })
    expect(fetchReviewItems).not.toHaveBeenCalled()
    app.unmount()
  })

  it('keeps the confirmed result when the queue refresh fails', async () => {
    vi.mocked(fetchReviewItems).mockRejectedValue(new Error('refresh unavailable'))
    const { app, host, pinia } = await mountInspector()
    const input = host.querySelector<HTMLInputElement>('[data-testid="teacher-score"]')!
    input.value = '4'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-single"]')!.click()

    await vi.waitFor(() => expect(confirmReviewItem).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(
      document.body.querySelector('[data-testid="review-feedback-toast"]')?.textContent,
    ).toContain('分数已确认，队列刷新失败'))
    expect(useReviewDraftStore(pinia).drafts['7:Q1:21']).toBeUndefined()
    expect(host.querySelector<HTMLButtonElement>('[data-testid="confirm-single"]')).toBeNull()
    expect(useReviewQueueStore(pinia).items[0]).toMatchObject({
      detail_id: 21,
      score_awarded: 4,
      needs_review: false,
    })
    app.unmount()
  })
})
