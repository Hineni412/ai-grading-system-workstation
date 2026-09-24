import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ReviewConfirmResponse,
  ReviewItem,
  ReviewRubricSection,
} from '../api/review'
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

const rubricSection: ReviewRubricSection = {
  question_id: 'Q1',
  parent_question_id: 'Q1',
  question_type: 'comprehensive',
  max_score: 5,
  knowledge_labels: ['一次函数'],
  points: [{
    part_id: 'Q1',
    part_label: '整题',
    step_id: 'S1',
    core_goal: '列出正确关系式',
    score: 3,
    standard_answer: 'y = 2x + 1',
    accepted_answers: ['y=2x+1'],
    match_rule: '按关键步骤评分',
    required_elements: ['定义变量', '列出等量关系'],
    deduction_rules: ['漏写结论扣 1 分'],
    answer_only_max_score: 1,
    require_final_answer: true,
    final_answer_rule: '必须写出最终解析式',
  }],
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
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
      question_type: 'comprehensive',
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
  it('saves integer partial step scores and marks only full points as achieved', async () => {
    vi.mocked(fetchReviewRubric).mockResolvedValue({ ...rubricSection, points: [
      rubricSection.points[0]!, { ...rubricSection.points[0]!, step_id: 'S2', score: 2 },
    ] })
    const { app, host } = await mountInspector([{ ...item, review_item_id: 'batch:1:Q1',
      metadata: { step_assessments: [
        { part_id: 'Q1', step_id: 'S1', score_awarded: 3 },
        { part_id: 'Q1', step_id: 'S2', score_awarded: 0 },
      ] } }])
    const begin = Array.from(host.querySelectorAll('button')).find((button) => button.textContent === '按步骤复核')!
    begin.click()
    await nextTick()
    const inputs = host.querySelectorAll<HTMLInputElement>('.review-step-score input')
    expect(inputs).toHaveLength(2)
    inputs[0]!.value = '2'
    inputs[0]!.dispatchEvent(new Event('input'))
    inputs[1]!.value = '2'
    inputs[1]!.dispatchEvent(new Event('input'))
    await nextTick()
    expect(host.querySelector<HTMLInputElement>('#teacher-score')!.value).toBe('4')
    expect(host.querySelector<HTMLInputElement>('#teacher-score')!.readOnly).toBe(true)
    expect(host.querySelectorAll('.review-step-score > span')[0]!.textContent).toBe('未达成')
    expect(host.querySelectorAll('.review-step-score > span')[1]!.textContent).toBe('达成')
    host.querySelector<HTMLButtonElement>('[data-testid="scoring-footer"] button')!.click()
    await vi.waitFor(() => expect(confirmReviewItem).toHaveBeenCalledWith(7, 'Q1', expect.objectContaining({
      score_awarded: 4,
      step_scores: [
        { part_id: 'Q1', step_id: 'S1', score_awarded: 2 },
        { part_id: 'Q1', step_id: 'S2', score_awarded: 2 },
      ],
    })))
    app.unmount()
  })

  it('does not silently restore AI points when the teacher total differs', async () => {
    vi.mocked(fetchReviewRubric).mockResolvedValue({ ...rubricSection,
      points: [{ ...rubricSection.points[0]!, score: 5 }] })
    const { app, host } = await mountInspector([{ ...item, review_item_id: 'batch:1:Q1',
      metadata: { step_assessments: [{ part_id: 'Q1', step_id: 'S1', score_awarded: 5 }] } }])
    Array.from(host.querySelectorAll('button')).find((button) => button.textContent === '按步骤复核')!.click()
    await nextTick()
    expect(host.querySelector<HTMLInputElement>('.review-step-score input')!.value).toBe('')
    expect(host.textContent).toContain('原步骤分与当前总分不一致')
    expect(host.querySelector<HTMLButtonElement>('[data-testid="scoring-footer"] button')!.disabled).toBe(true)
    app.unmount()
  })

  beforeEach(() => {
    document.body.innerHTML = ''
    vi.clearAllMocks()
    vi.mocked(fetchReviewRubric).mockResolvedValue(rubricSection)
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
    expect(inspector.textContent).toContain('列出正确关系式')
    expect(inspector.textContent).toContain('y = 2x + 1')
    expect(inspector.textContent).toContain('y=2x+1')
    expect(inspector.textContent).toContain('按关键步骤评分')
    expect(inspector.textContent).toContain('定义变量；列出等量关系')
    expect(inspector.textContent).toContain('漏写结论扣 1 分')
    expect(inspector.textContent).toContain('仅有答案')
    expect(inspector.textContent).toContain('最高 1 分')
    expect(inspector.textContent).toContain('必须明确写出')
    expect(inspector.textContent).toContain('必须写出最终解析式')
    expect(host.querySelectorAll('.review-rubric-point')).toHaveLength(1)
    expect(inspector.textContent).not.toContain('活动记录')
    expect(inspector.textContent).not.toContain('AI 最终分')
    const scrollRegion = host.querySelector('[data-testid="scoring-scroll-region"]')!
    const scorePanel = host.querySelector('[data-testid="teacher-score-panel"]')!
    expect(scrollRegion).not.toBeNull()
    expect(scorePanel).not.toBeNull()
    expect(scrollRegion.contains(scorePanel)).toBe(false)
    expect(host.querySelector('#teacher-note')).toBeNull()
    expect(host.querySelector('[data-testid="scoring-footer"]')).not.toBeNull()
    app.unmount()
  })

  it('reuses the loaded rubric while moving between students on the same question', async () => {
    const second = {
      ...item,
      result_id: 12,
      detail_id: 22,
      student_code: 'S002',
      student_name: '第二位学生',
    }
    const { app, pinia } = await mountInspector([item, second])
    const queue = useReviewQueueStore(pinia)

    await vi.waitFor(() => expect(fetchReviewRubric).toHaveBeenCalledTimes(1))
    queue.selectDetail(22)
    await nextTick()

    expect(queue.selectedDetailId).toBe(22)
    expect(fetchReviewRubric).toHaveBeenCalledTimes(1)
    app.unmount()
  })

  it('aborts a previous question request and ignores its late response', async () => {
    const firstRequest = deferred<ReviewRubricSection | null>()
    const secondRequest = deferred<ReviewRubricSection | null>()
    vi.mocked(fetchReviewRubric).mockImplementation((_sessionId, questionId) =>
      questionId === 'Q1' ? firstRequest.promise : secondRequest.promise,
    )
    const { app, host, pinia } = await mountInspector()
    const firstSignal = vi.mocked(fetchReviewRubric).mock.calls[0]?.[2]
    const queue = useReviewQueueStore(pinia)
    const secondItem = {
      ...item,
      result_id: 12,
      detail_id: 22,
      question_id: 'Q2',
      student_code: 'S002',
      student_name: '第二位学生',
    }
    const secondRubric: ReviewRubricSection = {
      ...rubricSection,
      question_id: 'Q2',
      parent_question_id: 'Q2',
      points: [{
        ...rubricSection.points[0]!,
        part_id: 'Q2',
        core_goal: 'Q2 当前评分标准',
      }],
    }

    queue.selectQuestion('Q2')
    queue.replaceItems([secondItem], secondItem.detail_id)
    await vi.waitFor(() => expect(fetchReviewRubric).toHaveBeenCalledTimes(2))
    expect(firstSignal?.aborted).toBe(true)

    secondRequest.resolve(secondRubric)
    await vi.waitFor(() => expect(host.textContent).toContain('Q2 当前评分标准'))

    firstRequest.resolve({
      ...rubricSection,
      points: [{
        ...rubricSection.points[0]!,
        core_goal: 'Q1 过期评分标准',
      }],
    })
    await firstRequest.promise
    await nextTick()

    expect(host.textContent).toContain('Q2 当前评分标准')
    expect(host.textContent).not.toContain('Q1 过期评分标准')
    app.unmount()
  })

  it('does not show labels for rubric details that are absent', async () => {
    vi.mocked(fetchReviewRubric).mockResolvedValue({
      ...rubricSection,
      knowledge_labels: [],
      points: [{
        ...rubricSection.points[0]!,
        standard_answer: '',
        accepted_answers: [],
        match_rule: '',
        required_elements: [],
        deduction_rules: [],
        answer_only_max_score: null,
        require_final_answer: null,
        final_answer_rule: '',
      }],
    })
    const { app, host } = await mountInspector()

    await vi.waitFor(() => expect(host.querySelector('.review-rubric-point')).not.toBeNull())
    const details = host.querySelector('.review-rubric-point__details')!
    expect(details.querySelectorAll('dt')).toHaveLength(0)
    expect(details.textContent?.trim()).toBe('')
    expect(host.querySelector('[aria-label="知识点"]')).toBeNull()
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
    expect(host.textContent).toContain('教师最终分必须是整数')
    expect(button.disabled).toBe(true)

    input.value = '4'
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
    expect(useReviewDraftStore(pinia).drafts['review-item:7:Q1:21']?.scoreText).toBe('4')
    expect(useReviewQueueStore(pinia).selectedDetailId).toBe(21)

    toast.querySelector<HTMLButtonElement>('[aria-label="关闭通知"]')!.click()
    await nextTick()
    expect(document.body.querySelector('[data-testid="review-feedback-toast"]')).toBeNull()
    expect(useReviewDraftStore(pinia).drafts['review-item:7:Q1:21']?.scoreText).toBe('4')
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
      reviewItemId: '7:Q1:21',
      annotationRetry: true,
    }))
    expect(annotationRetry).toHaveBeenCalledWith({
      input: {
        review_item_id: '7:Q1:21',
        expected_revision: 0,
        student_id: 11,
        result_id: 11,
        detail_id: 21,
        score_awarded: 4,
      },
      item: expect.objectContaining(item),
    })
    expect(useReviewQueueStore(pinia).questions[0]?.needs_review_count).toBe(2)
    await vi.waitFor(() => expect(
      document.body.querySelector('[data-testid="review-feedback-toast"]')?.textContent,
    ).toContain('分数已确认，标注图需要稍后刷新'))
    expect(useReviewDraftStore(pinia).drafts['review-item:7:Q1:21']).toBeUndefined()
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
    const confirmedItem = {
      ...item,
      needs_review: false,
      score_status: 'teacher_final' as const,
      score_source: 'teacher' as const,
      teacher_locked: true,
      deduction_reason: '教师既有说明',
      error_category: '已复核',
    }
    const pendingItem = { ...item, result_id: 12, detail_id: 22, student_name: '待复核学生' }
    vi.mocked(fetchReviewItems).mockResolvedValue([confirmedItem, pendingItem])
    const { app, host, pinia } = await mountInspector([confirmedItem, pendingItem])

    host.querySelector<HTMLButtonElement>('[data-testid="confirm-single"]')!.click()
    await vi.waitFor(() => expect(confirmReviewItem).toHaveBeenCalledTimes(1))

    expect(confirmReviewItem).toHaveBeenCalledWith(7, 'Q1', expect.objectContaining({
      deduction_reason: '教师既有说明',
    }))
    expect(useReviewQueueStore(pinia).questions[0]?.needs_review_count).toBe(1)
    app.unmount()
  })

  it('never confirms from score-field Enter', async () => {
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
    ).toContain('分数已确认，标注图需要稍后刷新'))
    expect(useReviewDraftStore(pinia).drafts['review-item:7:Q1:21']).toBeUndefined()
    expect(queue.selectedDetailId).toBe(22)
    expect(fetchReviewItems).not.toHaveBeenCalled()
    expect(annotationRetry).toHaveBeenCalledWith({
      input: {
        review_item_id: '7:Q1:21',
        expected_revision: 0,
        student_id: 11,
        result_id: 11,
        detail_id: 21,
        score_awarded: 4,
      },
      item: expect.objectContaining(item),
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
    ).toContain('教师最终分已确认'))
    expect(useReviewDraftStore(pinia).drafts['review-item:7:Q1:21']).toBeUndefined()
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

  it('emits confirmation without depending on a queue refresh', async () => {
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
    ).toContain('教师最终分已确认'))
    expect(useReviewDraftStore(pinia).drafts['review-item:7:Q1:21']).toBeUndefined()
    expect(fetchReviewItems).not.toHaveBeenCalled()
    expect(useReviewQueueStore(pinia).items[0]).toMatchObject({
      detail_id: 21,
      score_awarded: 3,
      needs_review: true,
    })
    app.unmount()
  })
})
