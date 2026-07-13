import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ReviewItem } from '../api/review'
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
  queue.selectQuestion('Q1')
  queue.replaceItems(items, items[0]?.detail_id)
  const host = document.createElement('div')
  const app = createApp(ReviewScoringInspector)
  app.use(pinia)
  app.mount(host)
  await vi.waitFor(() => expect(fetchReviewRubric).toHaveBeenCalledWith(7, 'Q1', expect.any(AbortSignal)))
  await nextTick()
  return { app, host, pinia }
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

  it('keeps teacher decision above AI advice and exposes an honest activity placeholder', async () => {
    const { app, host } = await mountInspector()
    const inspector = host.querySelector('[data-testid="review-scoring-inspector"]')!
    expect(inspector.getAttribute('aria-label')).toBe('评分与复核检查器')
    expect(inspector.textContent).toContain('评分标准')
    expect(inspector.textContent).toContain('AI 初评')
    expect(inspector.textContent).toContain('教师最终分')
    expect(inspector.textContent).toContain('一次函数')
    expect(inspector.textContent).toContain('当前接口暂未提供历史活动列表')
    expect(inspector.textContent).not.toContain('AI 最终分')
    expect(host.querySelector('[data-testid="scoring-scroll-region"]')).not.toBeNull()
    expect(host.querySelector('[data-testid="scoring-footer"]')).not.toBeNull()
    app.unmount()
  })

  it('shows score boundaries next to the field and enables only a valid changed draft', async () => {
    const { app, host, pinia } = await mountInspector()
    const input = host.querySelector<HTMLInputElement>('[data-testid="teacher-score"]')!
    const button = host.querySelector<HTMLButtonElement>('[data-testid="confirm-next"]')!
    expect(input.getAttribute('aria-describedby')).toContain('teacher-score-help')
    expect(button.disabled).toBe(true)

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
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-next"]')!.click()

    await vi.waitFor(() => expect(host.textContent).toContain('确认失败，教师草稿已保留'))
    expect(host.textContent).not.toContain('private server detail')
    expect(useReviewDraftStore(pinia).drafts['7:Q1:21']?.scoreText).toBe('4')
    expect(useReviewQueueStore(pinia).selectedDetailId).toBe(21)
    app.unmount()
  })

  it('submits once, clears the draft and moves to the pre-submit next record', async () => {
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
    const { app, host, pinia } = await mountInspector([item, second])
    const input = host.querySelector<HTMLInputElement>('[data-testid="teacher-score"]')!
    input.value = '4'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    const button = host.querySelector<HTMLButtonElement>('[data-testid="confirm-next"]')!
    button.click()
    button.click()

    await vi.waitFor(() => expect(confirmReviewItem).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(useReviewQueueStore(pinia).selectedDetailId).toBe(22))
    await vi.waitFor(() => expect(host.textContent).toContain('分数已确认，标注图需要稍后刷新'))
    expect(useReviewDraftStore(pinia).drafts['7:Q1:21']).toBeUndefined()
    app.unmount()
  })

  it('does not override a teacher selection changed while confirmation is pending', async () => {
    const second = { ...item, result_id: 12, detail_id: 22, student_code: 'S002', student_name: '学生乙' }
    let resolveConfirmation: ((value: {
      updated_details: number
      updated_results: number
      annotation_outcomes: []
    }) => void) | undefined
    vi.mocked(confirmReviewItem).mockImplementation(() => new Promise((resolve) => {
      resolveConfirmation = resolve
    }))
    const { app, host, pinia } = await mountInspector([item, second])
    const queue = useReviewQueueStore(pinia)
    const input = host.querySelector<HTMLInputElement>('[data-testid="teacher-score"]')!
    input.value = '4'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-next"]')!.click()
    await vi.waitFor(() => expect(confirmReviewItem).toHaveBeenCalledTimes(1))

    queue.selectDetail(22)
    resolveConfirmation!({ updated_details: 1, updated_results: 1, annotation_outcomes: [] })

    await vi.waitFor(() => expect(host.textContent).toContain('先前记录的教师最终分已确认'))
    expect(useReviewDraftStore(pinia).drafts['7:Q1:21']).toBeUndefined()
    expect(queue.selectedDetailId).toBe(22)
    expect(fetchReviewItems).not.toHaveBeenCalled()
    app.unmount()
  })

  it('does not patch a new session item that reuses the submitted detail id', async () => {
    let resolveConfirmation: ((value: {
      updated_details: number
      updated_results: number
      annotation_outcomes: []
    }) => void) | undefined
    vi.mocked(confirmReviewItem).mockImplementation(() => new Promise((resolve) => {
      resolveConfirmation = resolve
    }))
    const { app, host, pinia } = await mountInspector()
    const queue = useReviewQueueStore(pinia)
    const input = host.querySelector<HTMLInputElement>('[data-testid="teacher-score"]')!
    input.value = '4'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-next"]')!.click()
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

    await vi.waitFor(() => expect(host.textContent).toContain('先前记录的教师最终分已确认'))
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
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-next"]')!.click()

    await vi.waitFor(() => expect(host.textContent).toContain('分数已确认，队列刷新失败'))
    expect(confirmReviewItem).toHaveBeenCalledTimes(1)
    expect(useReviewDraftStore(pinia).drafts['7:Q1:21']).toMatchObject({
      scoreText: '4',
      dirty: false,
    })
    expect(host.querySelector<HTMLButtonElement>('[data-testid="confirm-next"]')!.disabled).toBe(true)
    expect(useReviewQueueStore(pinia).items[0]).toMatchObject({
      detail_id: 21,
      score_awarded: 4,
      needs_review: false,
    })
    app.unmount()
  })
})
