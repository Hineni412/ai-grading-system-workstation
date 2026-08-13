import { createApp, defineComponent, h, nextTick, reactive, type App } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ReviewItem } from '../api/review'

vi.mock('../components/review/ReviewEvidenceViewer.vue', () => ({
  default: defineComponent({
    name: 'EvidenceStub',
    props: {
      source: { type: String, required: true },
    },
    setup: (props) => () => h('div', {
      'data-testid': 'evidence-stub',
      'data-source': props.source,
    }, '五类证据'),
  }),
}))

vi.mock('../components/review/ReviewScoringInspector.vue', () => ({
  default: defineComponent({
    name: 'ScoringStub',
    emits: ['confirmed'],
    setup: () => () => h('div', { 'data-testid': 'scoring-stub' }),
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

function mountWorkspace(overrides: Partial<ReviewItem> = {}) {
  const host = document.createElement('div')
  document.body.append(host)
  const props = reactive({
    item: { ...item, ...overrides },
    previousItem: null,
    nextItem: null,
    registerAnnotationRetry: vi.fn(),
  })
  const Root = defineComponent({
    setup: () => () => h(ReviewDeepWorkspace, props),
  })
  const app = createApp(Root)
  app.mount(host)
  mountedApps.push(app)
  return { app, host, props }
}

function sourceButtons(host: HTMLElement): HTMLButtonElement[] {
  return [...host.querySelectorAll<HTMLButtonElement>('[aria-label="证据来源"] button')]
}

describe('review deep workspace compare mode', () => {
  it('offers a compare toggle only when both front images exist', () => {
    const { host } = mountWorkspace()
    const toggle = sourceButtons(host).find((button) => button.textContent?.trim() === '对比')
    expect(toggle?.getAttribute('aria-pressed')).toBe('false')

    const { host: hostWithoutAnnotated } = mountWorkspace({
      media: { ...item.media, annotated_front_url: null },
    })
    expect(
      sourceButtons(hostWithoutAnnotated).find((button) => button.textContent?.trim() === '对比'),
    ).toBeUndefined()
    expect(hostWithoutAnnotated.querySelector('[data-testid="evidence-stub"]')).not.toBeNull()
  })

  it('stacks original and annotated front images while comparing and exits back to the viewer', async () => {
    const { host } = mountWorkspace()
    const toggle = sourceButtons(host).find((button) => button.textContent?.trim() === '对比')!

    toggle.click()
    await nextTick()
    expect(toggle.getAttribute('aria-pressed')).toBe('true')
    expect(host.querySelector('[data-testid="evidence-stub"]')).toBeNull()
    const compare = host.querySelector<HTMLElement>('.image-compare')!
    expect(compare).not.toBeNull()
    const sources = [...compare.querySelectorAll('img')].map((image) => image.getAttribute('src'))
    expect(sources).toEqual(['/api/front', '/api/annotated-front'])

    toggle.click()
    await nextTick()
    expect(host.querySelector('.image-compare')).toBeNull()
    expect(host.querySelector('[data-testid="evidence-stub"]')?.getAttribute('data-source'))
      .toBe('crop')
  })

  it('leaves compare mode when a single source is picked or the item changes', async () => {
    const { host, props } = mountWorkspace()
    const toggle = sourceButtons(host).find((button) => button.textContent?.trim() === '对比')!
    toggle.click()
    await nextTick()

    sourceButtons(host).find((button) => button.textContent?.trim() === '原卷正面')!.click()
    await nextTick()
    expect(host.querySelector('.image-compare')).toBeNull()
    expect(host.querySelector('[data-testid="evidence-stub"]')?.getAttribute('data-source'))
      .toBe('original_front')

    toggle.click()
    await nextTick()
    expect(host.querySelector('.image-compare')).not.toBeNull()

    props.item = { ...item, result_id: 12, detail_id: 22, student_name: '下一位学生' }
    await nextTick()
    expect(host.querySelector('.image-compare')).toBeNull()
    expect(host.querySelector('[data-testid="evidence-stub"]')?.getAttribute('data-source'))
      .toBe('crop')
  })
})
