import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ReviewItem } from '../api/review'
import { fetchReviewRubric } from '../api/review'
import ReviewScoringInspector from '../components/review/ReviewScoringInspector.vue'
import { useReviewDraftStore } from '../stores/review-drafts'
import { useReviewQueueStore } from '../stores/review-queue'

vi.mock('../api/review', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/review')>(),
  fetchReviewRubric: vi.fn(),
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

async function mountInspector() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const queue = useReviewQueueStore(pinia)
  queue.selectQuestion('Q1')
  queue.replaceItems([item], item.detail_id)
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
})
