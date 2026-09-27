import { createApp, defineComponent, h, nextTick, reactive, type App } from 'vue';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import type { ReviewItem } from '../api/review';
import ReviewEvidenceViewer from '../components/review/ReviewEvidenceViewer.vue'

import type { EvidenceSource } from '../composables/use-evidence-viewer';

const makeItem = (detailId: number): ReviewItem => ({
  session_id: 7,
  result_id: detailId,
  detail_id: detailId,
  question_id: 'Q1',
  student_code: `S${detailId}`,
  student_name: `匿名学生${detailId}`,
  class_name: '匿名班级',
  score_awarded: 3,
  max_score: 5,
  deduction_reason: null,
  error_category: null,
  error_summary: null,
  confidence_score: 90,
  needs_review: false,
  candidate_scores: [],
  metadata: {},
  media: {
    crop_url: `/api/crop/${detailId}`,
    original_front_url: `/api/front/${detailId}`,
    original_back_url: `/api/back/${detailId}`,
    annotated_front_url: `/api/front/${detailId}?variant=annotated`,
    annotated_back_url: `/api/back/${detailId}?variant=annotated`,
  },
})

class ResizeObserverStub {
  static instances: ResizeObserverStub[] = []
  readonly observe = vi.fn()
  readonly disconnect = vi.fn()

  constructor(readonly callback: ResizeObserverCallback) {
    ResizeObserverStub.instances.push(this)
  }

  emit(width = 800, height = 600): void {
    this.callback([{
      contentRect: { width, height },
    } as ResizeObserverEntry], this as unknown as ResizeObserver)
  }
}

class PreloadImageStub {
  static instances: PreloadImageStub[] = []
  onerror: OnErrorEventHandler | null = null
  onload: ((this: GlobalEventHandlers, ev: Event) => unknown) | null = null
  src = ''

  constructor() {
    PreloadImageStub.instances.push(this)
  }
}

const mountedApps: App[] = []

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountViewer({
  item = makeItem(2),
  previousItem = makeItem(1),
  nextItem = makeItem(3),
  source = 'crop',
  expandable = true,
  onExpand = vi.fn(),
}: Partial<{
  item: ReviewItem
  previousItem: ReviewItem | null
  nextItem: ReviewItem | null
  source: EvidenceSource
  expandable: boolean
  onExpand: () => void
}> = {}) {
  const props = reactive({ item, previousItem, nextItem, source, expandable, onExpand })
  const Root = defineComponent({
    setup: () => () => h(ReviewEvidenceViewer, props),
  })
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(Root)
  app.mount(host)
  mountedApps.push(app)
  await settle()

  return {
    host,
    props,
    unmount: () => {
      const index = mountedApps.indexOf(app)
      if (index >= 0) mountedApps.splice(index, 1)
      app.unmount()
    },
  }
}

function clickButton(host: HTMLElement, name: string): void {
  const button = [...host.querySelectorAll<HTMLButtonElement>('button')]
    .find((candidate) => candidate.textContent?.trim() === name)
  expect(button).toBeDefined()
  button!.click()
}

it('opens the larger viewer from a click and suppresses nested expansion', async () => {
  const onExpand = vi.fn()
  const { host, props } = await mountViewer({ onExpand })
  clickButton(host, '弹窗放大')
  expect(onExpand).toHaveBeenCalledTimes(1)
  host.querySelector<HTMLElement>('.review-evidence-canvas')!.click()
  expect(onExpand).toHaveBeenCalledTimes(2)
  props.expandable = false
  await settle()
  expect(host.textContent).not.toContain('弹窗放大')
  host.querySelector<HTMLElement>('.review-evidence-canvas')!.click()
  expect(onExpand).toHaveBeenCalledTimes(2)
})

beforeEach(() => {
  document.body.innerHTML = ''
  ResizeObserverStub.instances = []
  PreloadImageStub.instances = []
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  vi.stubGlobal('Image', PreloadImageStub)
  Object.defineProperties(HTMLElement.prototype, {
    setPointerCapture: { configurable: true, value: vi.fn() },
    releasePointerCapture: { configurable: true, value: vi.fn() },
    hasPointerCapture: { configurable: true, value: vi.fn(() => true) },
  })
})

afterEach(() => {
  for (const app of mountedApps.splice(0)) app.unmount()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})
