import { createApp, defineComponent, h, nextTick, reactive, type App } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ReviewItem } from '../api/review'
import ReviewEvidenceViewer from '../components/review/ReviewEvidenceViewer.vue'

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
}: Partial<{
  item: ReviewItem
  previousItem: ReviewItem | null
  nextItem: ReviewItem | null
}> = {}) {
  const props = reactive({ item, previousItem, nextItem })
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

function loadActiveImage(host: HTMLElement, width = 1600, height = 1200): HTMLImageElement {
  const image = host.querySelector<HTMLImageElement>('.review-evidence-canvas img')!
  Object.defineProperty(image, 'naturalWidth', { configurable: true, value: width })
  Object.defineProperty(image, 'naturalHeight', { configurable: true, value: height })
  image.dispatchEvent(new Event('load'))
  return image
}

function clickButton(host: HTMLElement, name: string): void {
  const button = [...host.querySelectorAll<HTMLButtonElement>('button')]
    .find((candidate) => candidate.textContent?.trim() === name)
  expect(button).toBeDefined()
  button!.click()
}

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

describe('P2-06 review evidence viewer', () => {
  it('loads the crop by default and renders exactly one active image', async () => {
    const { host } = await mountViewer()
    const images = host.querySelectorAll<HTMLImageElement>('.review-evidence-viewer img')
    expect(images).toHaveLength(1)
    expect(images[0]?.getAttribute('src')).toBe('/api/crop/2')
    expect(images[0]?.getAttribute('alt')).toBe('匿名学生2 的裁剪证据')
    expect(host.textContent).toContain('裁剪证据')
  })

  it('switches to original front and back while resetting transform state', async () => {
    const { host } = await mountViewer()
    ResizeObserverStub.instances[0]?.emit()
    loadActiveImage(host)
    await settle()
    clickButton(host, '放大')
    clickButton(host, '向右旋转')
    clickButton(host, '原卷正面')
    await settle()
    expect(host.querySelector('img')?.getAttribute('src')).toBe('/api/front/2')
    expect(host.querySelector('img')?.getAttribute('style')).toContain('rotate(0deg)')
    expect(host.textContent).toContain('适应宽度')

    clickButton(host, '原卷反面')
    await settle()
    expect(host.querySelector('img')?.getAttribute('src')).toBe('/api/back/2')
  })

  it('shows a safe retry state and remounts the same controlled URL', async () => {
    const { host } = await mountViewer()
    const failedImage = host.querySelector<HTMLImageElement>('img')!
    const failedUrl = failedImage.getAttribute('src')
    const failedKey = failedImage.dataset.loadKey
    failedImage.dispatchEvent(new Event('error'))
    await settle()
    expect(host.textContent).toContain('裁剪图暂时无法读取。')
    expect(host.textContent).not.toContain(failedUrl)

    const retryButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '重新加载')!
    const retryPointerDown = new Event('pointerdown', { bubbles: true })
    Object.assign(retryPointerDown, { pointerId: 9, clientX: 100, clientY: 100, button: 0 })
    retryButton.dispatchEvent(retryPointerDown)
    expect(host.querySelector<HTMLElement>('.review-evidence-canvas')!.setPointerCapture)
      .not.toHaveBeenCalled()
    retryButton.click()
    await settle()
    const retriedImage = host.querySelector<HTMLImageElement>('img')!
    expect(retriedImage).not.toBe(failedImage)
    expect(retriedImage.getAttribute('src')).toBe(failedUrl)
    expect(retriedImage.dataset.loadKey).not.toBe(failedKey)
  })

  it('ignores stale load events after a rapid item change', async () => {
    const { host, props } = await mountViewer()
    const staleImage = host.querySelector<HTMLImageElement>('img')!
    props.item = makeItem(4)
    await settle()
    staleImage.dispatchEvent(new Event('load'))
    await settle()
    expect(host.querySelector('img')?.getAttribute('src')).toBe('/api/crop/4')
    expect(host.querySelector('.review-evidence-canvas')?.textContent).toContain('正在读取图片')
  })

  it('preloads only previous and next crop URLs and releases them on unmount', async () => {
    const { unmount } = await mountViewer()
    expect(PreloadImageStub.instances).toHaveLength(2)
    expect(PreloadImageStub.instances.map((image) => image.src)).toEqual([
      '/api/crop/1',
      '/api/crop/3',
    ])
    unmount()
    expect(PreloadImageStub.instances.every((image) => image.src === '')).toBe(true)
    expect(PreloadImageStub.instances.every((image) => image.onerror === null)).toBe(true)
  })

  it('supports wheel-centered zoom, pointer drag, and focused canvas keys', async () => {
    const { host } = await mountViewer()
    const canvas = host.querySelector<HTMLElement>('.review-evidence-canvas')!
    Object.defineProperty(canvas, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ left: 0, top: 0, width: 800, height: 600 }),
    })
    ResizeObserverStub.instances[0]?.emit()
    loadActiveImage(host, 800, 600)
    await settle()

    const wheel = new WheelEvent('wheel', {
      deltaY: -1,
      clientX: 200,
      clientY: 100,
      bubbles: true,
      cancelable: true,
    })
    canvas.dispatchEvent(wheel)
    expect(wheel.defaultPrevented).toBe(true)
    await settle()
    expect(host.textContent).toContain('119%')

    const beforeDrag = host.querySelector('img')?.getAttribute('style')
    const down = new Event('pointerdown', { bubbles: true })
    Object.assign(down, { pointerId: 1, clientX: 200, clientY: 200, button: 0 })
    canvas.dispatchEvent(down)
    const move = new Event('pointermove', { bubbles: true })
    Object.assign(move, { pointerId: 1, clientX: 260, clientY: 240 })
    canvas.dispatchEvent(move)
    await nextTick()
    expect(canvas.classList.contains('is-dragging')).toBe(true)
    expect(host.querySelector('img')?.getAttribute('style')).not.toBe(beforeDrag)

    const fitKey = new KeyboardEvent('keydown', { key: 'z', bubbles: true, cancelable: true })
    canvas.dispatchEvent(fitKey)
    expect(fitKey.defaultPrevented).toBe(true)
    const rightKey = new KeyboardEvent('keydown', {
      key: 'ArrowRight',
      bubbles: true,
      cancelable: true,
    })
    canvas.dispatchEvent(rightKey)
    expect(rightKey.defaultPrevented).toBe(true)
  })
})
