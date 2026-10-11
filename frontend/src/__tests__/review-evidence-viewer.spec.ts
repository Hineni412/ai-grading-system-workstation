import { createApp, defineComponent, h, nextTick, reactive, type App } from 'vue';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import type { ReviewItem } from '../api/review';
import ReviewEvidenceViewer from '../components/review/ReviewEvidenceViewer.vue'

import { fetchResultsCenter } from '../api/results-center'
import { fetchRegionWorkspace, type RegionWorkspace } from '../api/template-regions'
import type { EvidenceSource } from '../composables/use-evidence-viewer';

vi.mock('../api/results-center', () => ({ fetchResultsCenter: vi.fn() }))
vi.mock('../api/template-regions', () => ({ fetchRegionWorkspace: vi.fn() }))

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
  vi.mocked(fetchResultsCenter).mockRejectedValue(new Error('no session results'))
  vi.mocked(fetchRegionWorkspace).mockRejectedValue(new Error('no regions'))
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

it('overlays paper marks on the original page and toggles them off', async () => {
  vi.mocked(fetchResultsCenter).mockResolvedValue({
    session_id: 7,
    session_name: '测试考试',
    summary: {
      student_count: 1, complete_student_count: 1, average_sample_count: 1,
      average_score: 3, highest_score: 3, lowest_score: 3, max_score: 5,
      ungraded_item_count: 0, failed_item_count: 0, needs_review_item_count: 0,
      ai_ready_item_count: 0, teacher_final_item_count: 1,
    },
    questions: [],
    students: [{
      student_id: 11, student_code: 'S2', student_name: '匿名学生2', class_name: '匿名班级',
      pinyin_initials: '', pinyin_full: '', current_score: 3, max_score: 5,
      ungraded_count: 0, failed_count: 0, needs_review_count: 0, status: 'complete',
      items: [{
        review_item_id: '7:Q1:2', question_id: 'Q1', score_awarded: 3, max_score: 5,
        score_status: 'teacher_final', score_source: 'teacher', confidence_score: null,
        needs_review: false, review_reason: null, result_id: 2, detail_id: 2,
      }],
    }],
  })
  vi.mocked(fetchRegionWorkspace).mockResolvedValue({
    formal_regions: [{
      region_uuid: 'r1', page: 'front', region_order: 1,
      x: 100, y: 200, w: 800, h: 300,
      mapped_question_id: 'Q1', mapping_status: 'manual',
      is_confirmed: true, multi_region_confirmed: false,
    }],
    template: { pages: { front: { width: 1000, height: 1400 }, back: { width: 1000, height: 1400 } } },
  } as unknown as RegionWorkspace)

  const item = { ...makeItem(2), student_id: 11 }
  const { host } = await mountViewer({ item, source: 'original_front' })

  await vi.waitFor(() => expect(host.querySelector('.paper-mark')).not.toBeNull())
  expect(host.querySelector('.paper-mark__label')?.textContent).toContain('Q1 3/5')
  expect(host.querySelector('.paper-mark')?.classList.contains('is-selected')).toBe(true)

  clickButton(host, '批改标记')
  await settle()
  expect(host.querySelector('.paper-mark')).toBeNull()
})

it('shows a quiet placeholder without requesting cleared originals', async () => {
  const item = makeItem(2)
  item.media = { originals_available: false, crop_url: null, original_front_url: null, original_back_url: null }
  const { host } = await mountViewer({ item })
  expect(host.textContent).toContain('原卷已清理')
  expect(host.querySelector('img')).toBeNull()
  expect(host.querySelector('button')).toBeNull()
  expect(PreloadImageStub.instances).toHaveLength(0)
})
