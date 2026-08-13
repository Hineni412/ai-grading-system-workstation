import { createApp, defineComponent, h, nextTick, type App } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ImageCompare from './ImageCompare.vue'

const mountedApps: App[] = []

afterEach(() => {
  mountedApps.splice(0).forEach((app) => app.unmount())
  vi.restoreAllMocks()
})

function mountCompare(props: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.append(host)
  const Root = defineComponent({
    setup: () => () => h(ImageCompare, {
      firstImage: '/api/front',
      secondImage: '/api/annotated-front',
      firstImageAlt: '原卷正面',
      secondImageAlt: '标注正面',
      ...props,
    }),
  })
  const app = createApp(Root)
  app.mount(host)
  mountedApps.push(app)
  const slider = host.querySelector<HTMLElement>('.image-compare')!
  return { app, host, slider }
}

function mockSliderLayout(slider: HTMLElement, { left = 0, width = 200 } = {}) {
  vi.spyOn(slider, 'getBoundingClientRect').mockReturnValue({
    left,
    width,
    top: 0,
    right: left + width,
    bottom: 100,
    height: 100,
    x: left,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect)
}

function pointerAt(slider: HTMLElement, type: string, clientX: number) {
  slider.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, clientX, button: 0 }))
}

describe('ImageCompare', () => {
  it('renders both images with the slider at the initial terminal position', () => {
    const { host, slider } = mountCompare()

    const images = [...host.querySelectorAll<HTMLImageElement>('img')]
    expect(images.map((image) => image.getAttribute('src'))).toEqual([
      '/api/front',
      '/api/annotated-front',
    ])
    expect(slider.getAttribute('role')).toBe('slider')
    expect(slider.getAttribute('aria-valuenow')).toBe('50')
    expect(host.querySelector<HTMLElement>('.via-primary')?.style.left).toBe('50%')
  })

  it('moves the slider while dragging and emits the percentage', async () => {
    const onPercentage = vi.fn()
    const { slider } = mountCompare({ 'onUpdate:percentage': onPercentage })
    mockSliderLayout(slider)

    pointerAt(slider, 'pointerdown', 150)
    await nextTick()
    expect(slider.getAttribute('aria-valuenow')).toBe('75')
    expect(onPercentage).toHaveBeenLastCalledWith(75)

    pointerAt(slider, 'pointermove', 50)
    await nextTick()
    expect(slider.getAttribute('aria-valuenow')).toBe('25')

    pointerAt(slider, 'pointermove', 300)
    await nextTick()
    expect(slider.getAttribute('aria-valuenow')).toBe('100')

    pointerAt(slider, 'pointerup', 300)
    pointerAt(slider, 'pointermove', 150)
    await nextTick()
    expect(slider.getAttribute('aria-valuenow')).toBe('100')
  })

  it('stays at the terminal position in jsdom where layout metrics are zero', async () => {
    const onPercentage = vi.fn()
    const { slider } = mountCompare({ 'onUpdate:percentage': onPercentage })

    pointerAt(slider, 'pointerdown', 120)
    pointerAt(slider, 'pointermove', 180)
    await nextTick()

    expect(slider.getAttribute('aria-valuenow')).toBe('50')
    expect(onPercentage).not.toHaveBeenCalled()
  })

  it('adjusts the slider with arrow keys', async () => {
    const { slider } = mountCompare()

    slider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
    await nextTick()
    expect(slider.getAttribute('aria-valuenow')).toBe('52')

    slider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true, shiftKey: true }))
    await nextTick()
    expect(slider.getAttribute('aria-valuenow')).toBe('42')
  })
})
