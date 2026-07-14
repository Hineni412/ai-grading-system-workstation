import { createApp, defineComponent, h, type App } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ReviewItem } from '../api/review'

vi.mock('../components/review/ReviewEvidenceViewer.vue', () => ({
  default: defineComponent({
    name: 'EvidenceStub',
    setup: () => () => h('div', { 'data-testid': 'evidence-stub' }, '五类证据'),
  }),
}))

vi.mock('../components/review/ReviewScoringInspector.vue', () => ({
  default: defineComponent({
    name: 'ScoringStub',
    emits: ['confirmed'],
    setup: (_props, { emit }) => () => h('button', {
      'data-testid': 'scoring-stub',
      onClick: () => emit('confirmed', { detailId: 21, retryEntry: null }),
    }, '确认此份并返回'),
  }),
}))

import ReviewDeepWorkspace from '../components/review/ReviewDeepWorkspace.vue'

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
  deduction_reason: null,
  error_category: null,
  error_summary: null,
  confidence_score: 72,
  needs_review: true,
  candidate_scores: [],
  metadata: {},
  media: {
    crop_url: '/api/crop',
    original_front_url: '/api/front',
    original_back_url: '/api/back',
    annotated_front_url: '/api/annotated-front',
    annotated_back_url: '/api/annotated-back',
  },
}

const mountedApps: App[] = []

afterEach(() => {
  mountedApps.splice(0).forEach((app) => app.unmount())
})

describe('review deep workspace', () => {
  it('is an in-flow replacement with explicit return and confirmation events', () => {
    const back = vi.fn()
    const confirmed = vi.fn()
    const host = document.createElement('div')
    const app = createApp(ReviewDeepWorkspace, {
      item,
      previousItem: null,
      nextItem: null,
      onBack: back,
      onConfirmed: confirmed,
    })
    app.mount(host)
    mountedApps.push(app)

    const workspace = host.querySelector('[data-testid="review-deep-workspace"]')!
    expect(workspace.getAttribute('role')).toBeNull()
    expect(workspace.classList.contains('drawer')).toBe(false)
    expect(workspace.textContent).toContain('返回 Q1 批量复核')
    expect(workspace.textContent).toContain('返回后保留批次、筛选和未确认草稿')
    expect(host.querySelector('[data-testid="evidence-stub"]')).not.toBeNull()
    expect(host.querySelector('[data-testid="scoring-stub"]')).not.toBeNull()

    host.querySelector<HTMLButtonElement>('[data-testid="back-to-batch"]')!.click()
    host.querySelector<HTMLButtonElement>('[data-testid="scoring-stub"]')!.click()
    expect(back).toHaveBeenCalledTimes(1)
    expect(confirmed).toHaveBeenCalledWith({ detailId: 21, retryEntry: null })
  })
})
