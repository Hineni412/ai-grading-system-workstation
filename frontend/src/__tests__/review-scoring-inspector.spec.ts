import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick } from 'vue';

import type { ResultsCenterItem, ResultsCenterResponse, ResultsCenterStudent } from '../api/results-center';
import type { ReviewConfirmResponse, ReviewItem, ReviewRubricSection } from '../api/review';
import { confirmReviewItem, fetchReviewItems, fetchReviewRubric } from '../api/review';
import { fetchRegionWorkspace, type RegionWorkspace } from '../api/template-regions';
import { clearReviewRubrics } from '../components/review/review-rubric-cache';
import ReviewScoringInspector from '../components/review/ReviewScoringInspector.vue'
import StudentPaperPanel from '../components/results-center/StudentPaperPanel.vue'
import { resetRegionWorkspaceCache } from '../components/results-center/student-paper';
import { useReviewDraftStore } from '../stores/review-drafts';
import { useReviewQueueStore } from '../stores/review-queue';

vi.mock('../api/review', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/review')>(),
  fetchReviewRubric: vi.fn(),
  fetchReviewItems: vi.fn(),
  confirmReviewItem: vi.fn(),
}))

vi.mock('../api/template-regions', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/template-regions')>(),
  fetchRegionWorkspace: vi.fn(),
}))

const media = {
  crop_url: '/api/crop',
  original_front_url: '/api/front',
  original_back_url: '/api/back',
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

// 成绩抽屉逐题改分：解答题带可分步评分标准、AI 步骤分齐全。
const panelItem: ResultsCenterItem = {
  review_item_id: 'review-1',
  question_id: 'Q2',
  score_awarded: 3,
  max_score: 6,
  score_status: 'ai_review',
  score_source: 'ai',
  confidence_score: null,
  needs_review: true,
  review_reason: null,
  result_id: 11,
  detail_id: 21,
}

const panelReviewItem: ReviewItem = {
  session_id: 7,
  review_item_id: 'review-1',
  revision: 4,
  student_id: 3,
  result_id: 11,
  detail_id: 21,
  question_id: 'Q2',
  student_code: 'S002',
  student_name: '学生丙',
  class_name: '七年级一班',
  score_awarded: 3,
  max_score: 6,
  deduction_reason: '第二步未达成',
  error_category: null,
  error_summary: null,
  confidence_score: null,
  needs_review: true,
  score_status: 'ai_review',
  candidate_scores: [],
  metadata: {
    step_assessments: [
      { part_id: 'Q2', step_id: 'S1', score_awarded: 3 },
      { part_id: 'Q2', step_id: 'S2', score_awarded: 0 },
    ],
  },
  media,
}

const panelRubric: ReviewRubricSection = {
  question_id: 'Q2',
  parent_question_id: 'Q2',
  question_type: 'comprehensive',
  max_score: 6,
  knowledge_labels: [],
  points: ['S1', 'S2'].map((stepId) => ({
    part_id: 'Q2',
    part_label: '整题',
    step_id: stepId,
    core_goal: stepId === 'S1' ? '列出关系式' : '求出结果',
    score: 3,
    standard_answer: '标准解法',
    accepted_answers: [],
    match_rule: '按关键步骤评分',
    required_elements: [],
    deduction_rules: [],
    answer_only_max_score: null,
    require_final_answer: null,
    final_answer_rule: '',
  })),
}

const panelStudent: ResultsCenterStudent = {
  student_id: 3,
  student_code: 'S002',
  student_name: '学生丙',
  class_name: '七年级一班',
  pinyin_initials: '',
  pinyin_full: '',
  current_score: 3,
  max_score: 6,
  ungraded_count: 0,
  failed_count: 0,
  needs_review_count: 1,
  status: 'needs_review',
  items: [panelItem],
}

const panelResults: ResultsCenterResponse = {
  session_id: 7,
  session_name: '测试考试',
  summary: {
    student_count: 1,
    complete_student_count: 0,
    average_sample_count: 0,
    average_score: null,
    highest_score: null,
    lowest_score: null,
    max_score: 6,
    ungraded_item_count: 0,
    failed_item_count: 0,
    needs_review_item_count: 1,
    ai_ready_item_count: 0,
    teacher_final_item_count: 0,
  },
  questions: [{
    question_id: 'Q2',
    max_score: 6,
    total_count: 1,
    ungraded_count: 0,
    failed_count: 0,
    needs_review_count: 1,
    ai_ready_count: 0,
    teacher_final_count: 0,
    average_score: 3,
  }],
  students: [panelStudent],
}

const panelWorkspace: RegionWorkspace = {
  session_id: 7,
  template: {
    session_id: 7,
    template_id: 1,
    template_fingerprint: 'fingerprint',
    first_page_role: 'front',
    pages: {
      front: { url: '/api/sessions/7/template/pages/front', width: 100, height: 100 },
      back: { url: '/api/sessions/7/template/pages/back', width: 100, height: 100 },
    },
    is_confirmed: true,
    regions_snapshot_pending: false,
  },
  formal_regions: [],
  draft: { status: 'missing', revision: 0, regions: [] },
  automatic_candidates: [],
  manual_question_options: [],
  issues: [],
  template_ready: true,
}

async function mountPaperPanel(pinia: ReturnType<typeof createPinia>) {
  const host = document.createElement('div')
  const saved = vi.fn()
  const app = createApp(StudentPaperPanel, {
    sessionId: 7,
    results: panelResults,
    student: panelStudent,
    items: [panelItem],
    editable: true,
    onSaved: saved,
  })
  app.use(pinia)
  app.mount(host)
  await nextTick()
  return { app, host, saved }
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
  const app = createApp(ReviewScoringInspector, {
    onConfirmed: confirmed,
  })
  app.use(pinia)
  app.mount(host)
  await vi.waitFor(() => expect(fetchReviewRubric).toHaveBeenCalledWith(7, 'Q1', expect.any(AbortSignal)))
  // 共享评分标准缓存的多步异步链需要一次宏任务才能完全落回组件状态。
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
  return { app, host, pinia, confirmed }
}

describe('review scoring inspector', () => {

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

  // 成绩抽屉的逐题改分走同一条确认接口：步骤分编辑后保存携带修订号与完整步骤分。
  it('saves step scores with the current revision from the student paper panel', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    resetRegionWorkspaceCache()
    vi.mocked(fetchRegionWorkspace).mockResolvedValue(panelWorkspace)
    vi.mocked(fetchReviewItems).mockResolvedValue([panelReviewItem])
    vi.mocked(fetchReviewRubric).mockResolvedValue(panelRubric)
    const { app, host, saved } = await mountPaperPanel(pinia)
    host.querySelector<HTMLElement>('.paper-list__row')!.click()
    const stepInputs = await vi.waitFor(() => {
      const found = host.querySelectorAll<HTMLInputElement>('.paper-rubric-point__input input')
      expect(found).toHaveLength(2)
      return found
    })
    expect(stepInputs[0]!.value).toBe('3')
    stepInputs[1]!.value = '2'
    stepInputs[1]!.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    const saveButton = [...host.querySelectorAll<HTMLButtonElement>('.paper-list__actions button')]
      .find((button) => button.textContent?.trim() === '保存')!
    saveButton.click()

    await vi.waitFor(() => expect(saved).toHaveBeenCalledTimes(1))
    const calls = vi.mocked(confirmReviewItem).mock.calls
      .filter(([sessionId, questionId]) => sessionId === 7 && questionId === 'Q2')
    expect(calls).toHaveLength(1)
    expect(calls[0]![2]).toEqual({
      review_item_id: 'review-1',
      expected_revision: 4,
      student_id: 3,
      result_id: 11,
      detail_id: 21,
      score_awarded: 5,
      step_scores: [
        { part_id: 'Q2', step_id: 'S1', score_awarded: 3, teacher_note: null, carried_error_from: null },
        { part_id: 'Q2', step_id: 'S2', score_awarded: 2, teacher_note: null, carried_error_from: null },
      ],
    })
    app.unmount()
  })

  it('does not override a teacher selection changed while confirmation is pending', async () => {
    const second = { ...item, result_id: 12, detail_id: 22, student_code: 'S002', student_name: '学生乙' }
    let resolveConfirmation: ((value: ReviewConfirmResponse) => void) | undefined
    vi.mocked(confirmReviewItem).mockImplementation(() => new Promise((resolve) => {
      resolveConfirmation = resolve
    }))
    const { app, host, pinia, confirmed } = await mountInspector([item, second])
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
    })

    await vi.waitFor(() => expect(confirmed).toHaveBeenCalledTimes(1))
    expect(document.body.querySelectorAll('[data-testid="review-feedback-toast"]')).toHaveLength(0)
    expect(useReviewDraftStore(pinia).drafts['review-item:7:Q1:21']).toBeUndefined()
    expect(queue.selectedDetailId).toBe(22)
    expect(fetchReviewItems).not.toHaveBeenCalled()
    app.unmount()
  })

  it('does not patch a new session item that reuses the submitted detail id', async () => {
    let resolveConfirmation: ((value: ReviewConfirmResponse) => void) | undefined
    vi.mocked(confirmReviewItem).mockImplementation(() => new Promise((resolve) => {
      resolveConfirmation = resolve
    }))
    const { app, host, pinia, confirmed } = await mountInspector()
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
    resolveConfirmation!({ updated_details: 1, updated_results: 1 })

    await vi.waitFor(() => expect(confirmed).toHaveBeenCalledTimes(1))
    expect(document.body.querySelectorAll('[data-testid="review-feedback-toast"]')).toHaveLength(0)
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
