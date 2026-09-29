import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick } from 'vue';

import type { ReviewConfirmResponse, ReviewItem, ReviewRubricSection } from '../api/review';
import { confirmReviewItem, fetchReviewItems, fetchReviewRubric } from '../api/review';
import { clearReviewRubrics } from '../components/review/review-rubric-cache';
import ReviewScoringInspector from '../components/review/ReviewScoringInspector.vue'
import { useReviewDraftStore } from '../stores/review-drafts';
import { useReviewQueueStore } from '../stores/review-queue';

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
  // 共享评分标准缓存的多步异步链需要一次宏任务才能完全落回组件状态。
  await new Promise((resolve) => setTimeout(resolve, 0))
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
    // 可分步复核时自动进入步骤模式，AI 步骤分与总分一致则预填到快捷输入。
    const quick = await vi.waitFor(() => {
      const found = host.querySelectorAll<HTMLInputElement>('.review-quick-score input')
      expect(found).toHaveLength(2)
      return [...found]
    })
    const setQuick = async (input: HTMLInputElement, value: string) => {
      input.value = value
      input.dispatchEvent(new Event('input', { bubbles: true }))
      await nextTick()
    }
    expect(quick[0]!.value).toBe('3')
    await setQuick(quick[0]!, '2')
    await setQuick(quick[1]!, '2')
    expect(host.querySelector('.review-quick-score__total')!.textContent).toContain('4 / 5')
    expect(host.querySelector('#teacher-score')).toBeNull()
    const states = host.querySelectorAll('.review-rubric-point__state')
    expect(states[0]!.textContent).toBe('2/3 未达成')
    expect(states[1]!.textContent).toBe('2/2 达成')
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

  it('auto step initialisation stays clean until the teacher enters a step score', async () => {
    vi.mocked(fetchReviewRubric).mockResolvedValue({ ...rubricSection, points: [
      rubricSection.points[0]!, { ...rubricSection.points[0]!, step_id: 'S2', score: 2 },
    ] })
    const { app, host, pinia } = await mountInspector([{ ...item, review_item_id: 'batch:1:Q1',
      metadata: { step_assessments: [
        { part_id: 'Q1', step_id: 'S1', score_awarded: 3 },
        { part_id: 'Q1', step_id: 'S2', score_awarded: 0 },
      ] } }])
    const first = await vi.waitFor(() => {
      const found = host.querySelectorAll<HTMLInputElement>('.review-quick-score input')
      expect(found).toHaveLength(2)
      return found[0]!
    })
    // 自动预填不是教师输入：不产生未确认草稿。
    const drafts = useReviewDraftStore(pinia)
    const draftKey = Object.keys(drafts.drafts)[0]!
    expect(drafts.drafts[draftKey]?.dirty).toBe(false)
    expect(drafts.dirtyCount).toBe(0)
    expect(host.textContent).not.toContain('教师草稿未确认')

    first.value = '2'
    first.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(drafts.drafts[draftKey]?.dirty).toBe(true)
    expect(drafts.dirtyCount).toBe(1)
    expect(host.textContent).toContain('教师草稿未确认')
    app.unmount()
  })

  it('quick score inputs drive the step draft and Enter submits the same payload', async () => {
    vi.mocked(fetchReviewRubric).mockResolvedValue({ ...rubricSection, points: [
      rubricSection.points[0]!, { ...rubricSection.points[0]!, step_id: 'S2', score: 2 },
    ] })
    const { app, host } = await mountInspector([{ ...item, review_item_id: 'batch:1:Q1',
      metadata: { step_assessments: [
        { part_id: 'Q1', step_id: 'S1', score_awarded: 3 },
        { part_id: 'Q1', step_id: 'S2', score_awarded: 0 },
      ] } }])
    document.body.append(host)
    const quick = await vi.waitFor(() => {
      const found = host.querySelectorAll<HTMLInputElement>('.review-quick-score input')
      expect(found).toHaveLength(2)
      return [...found]
    })
    // 自动预填同步到快捷输入框
    expect(quick[0]!.value).toBe('3')
    expect(quick[1]!.value).toBe('0')
    expect(host.textContent).toContain('合计 3 / 5')

    // 输入合法数字：写入草稿并前进焦点，步骤卡状态同步
    quick[0]!.value = '2'
    quick[0]!.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(document.activeElement).toBe(quick[1])
    expect(host.querySelector('.review-rubric-point__state')?.textContent).toBe('2/3 未达成')
    expect(host.textContent).toContain('合计 2 / 5')

    // 聚焦快捷输入时高亮对应步骤卡
    quick[0]!.dispatchEvent(new FocusEvent('focus'))
    await nextTick()
    expect(
      host.querySelector('.review-rubric-point--active')?.getAttribute('data-step-key'),
    ).toBe('Q1:S1')
    quick[0]!.dispatchEvent(new FocusEvent('blur'))
    await nextTick()

    // 点击步骤卡聚焦回对应的快捷输入框
    host.querySelector<HTMLElement>('[data-step-key="Q1:S1"]')!.click()
    await nextTick()
    expect(document.activeElement).toBe(quick[0])

    // 改回 3 分继续验证提交
    quick[0]!.value = '3'
    quick[0]!.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(quick[0]!.value).toBe('3')

    // Enter 与确认按钮同一动作
    quick[1]!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await vi.waitFor(() => expect(confirmReviewItem).toHaveBeenCalledWith(7, 'Q1', expect.objectContaining({
      score_awarded: 3,
      step_scores: [
        { part_id: 'Q1', step_id: 'S1', score_awarded: 3 },
        { part_id: 'Q1', step_id: 'S2', score_awarded: 0 },
      ],
    })))
    app.unmount()
  })

  it('marks invalid quick input without writing and Enter respects the disabled state', async () => {
    vi.mocked(fetchReviewRubric).mockResolvedValue({ ...rubricSection,
      points: [{ ...rubricSection.points[0]!, score: 5 }] })
    const { app, host, pinia } = await mountInspector([{ ...item, review_item_id: 'batch:1:Q1',
      metadata: { step_assessments: [{ part_id: 'Q1', step_id: 'S1', score_awarded: 5 }] } }])
    document.body.append(host)
    const quick = await vi.waitFor(() => {
      const found = host.querySelectorAll<HTMLInputElement>('.review-quick-score input')
      expect(found).toHaveLength(1)
      return found[0]!
    })
    // 非法值（超过满分）不写入草稿，仅标红
    quick.value = '9'
    quick.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(quick.classList.contains('review-quick-score__input--invalid')).toBe(true)
    const draft = Object.values(useReviewDraftStore(pinia).drafts)[0]!
    expect(draft.stepScores![0]!.scoreText).toBe('')

    // 草稿未完整 → Enter 不提交，且内联显示禁用原因
    quick.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await nextTick()
    expect(confirmReviewItem).not.toHaveBeenCalled()
    expect(host.querySelector('.review-quick-score__notice')?.textContent).toContain('请输入教师最终分')
    app.unmount()
  })

  it('does not silently restore AI points when the teacher total differs', async () => {
    vi.mocked(fetchReviewRubric).mockResolvedValue({ ...rubricSection,
      points: [{ ...rubricSection.points[0]!, score: 5 }] })
    const { app, host } = await mountInspector([{ ...item, review_item_id: 'batch:1:Q1',
      metadata: { step_assessments: [{ part_id: 'Q1', step_id: 'S1', score_awarded: 5 }] } }])
    await vi.waitFor(() => expect(host.textContent).toContain('AI 步骤分与总分不一致'))
    expect(host.querySelector<HTMLInputElement>('.review-quick-score input')?.value).toBe('')
    expect(host.querySelector('.review-rubric-point__state')?.textContent).toContain('待评分')
    expect(host.querySelector<HTMLButtonElement>('[data-testid="scoring-footer"] button')!.disabled).toBe(true)
    app.unmount()
  })

  beforeEach(() => {
    document.body.innerHTML = ''
    clearReviewRubrics()
    vi.clearAllMocks()
    vi.mocked(fetchReviewRubric).mockResolvedValue(rubricSection)
    vi.mocked(fetchReviewItems).mockResolvedValue([item])
    vi.mocked(confirmReviewItem).mockResolvedValue({
      updated_details: 1,
      updated_results: 1,
      annotation_outcomes: [],
    })
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

})
