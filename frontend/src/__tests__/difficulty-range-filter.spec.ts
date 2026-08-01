import { createApp, h, nextTick } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import DifficultyRangeFilter from '../components/question-bank/DifficultyRangeFilter.vue'

const apps: Array<ReturnType<typeof createApp>> = []

function mountFilter(min = 1, max = 8) {
  const host = document.createElement('div')
  document.body.append(host)
  const updates: Array<[string, number]> = []
  const app = createApp({
    setup() {
      return () => h(DifficultyRangeFilter, {
        min,
        max,
        'onUpdate:min': (value: number) => updates.push(['min', value]),
        'onUpdate:max': (value: number) => updates.push(['max', value]),
      })
    },
  })
  app.mount(host)
  apps.push(app)
  return { host, updates }
}

afterEach(() => {
  for (const app of apps.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('difficulty range filter', () => {
  it('shows every numeric tick and marks 8–10 as the final-exam challenge band', () => {
    const { host } = mountFilter()
    expect(host.querySelector('legend')?.textContent).toContain('难度区间1–8')
    expect([...host.querySelectorAll('.difficulty-range__ticks b')].map(node => node.textContent))
      .toEqual(['1', '2', '3', '4', '5', '6', '7', '8', '9', '10'])
    expect([...host.querySelectorAll('.difficulty-range__bands span')].map(node => node.textContent?.trim()))
      .toEqual(['1–2入门补缺', '3–4基础巩固', '5–6中档提升', '7综合突破', '8–10压轴拔高'])
    expect(host.textContent).toContain('压轴拔高为 8 到 10')
  })

  it('keeps the lower and upper thumbs from crossing', async () => {
    const { host, updates } = mountFilter(4, 7)
    const [lower, upper] = host.querySelectorAll<HTMLInputElement>('input[type="range"]')

    lower!.value = '9'
    lower!.dispatchEvent(new Event('input', { bubbles: true }))
    upper!.value = '2'
    upper!.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()

    expect(updates).toEqual([['min', 7], ['max', 4]])
    expect(lower?.getAttribute('aria-describedby')).toBeTruthy()
    expect(upper?.getAttribute('aria-describedby')).toBe(lower?.getAttribute('aria-describedby'))
  })

  it('moves both bounds with arrow, Home, and End keys', async () => {
    const { host, updates } = mountFilter(4, 7)
    const [lower, upper] = host.querySelectorAll<HTMLInputElement>('input[type="range"]')

    const lowerArrow = new KeyboardEvent('keydown', {
      key: 'ArrowRight',
      bubbles: true,
      cancelable: true,
    })
    const upperHome = new KeyboardEvent('keydown', {
      key: 'Home',
      bubbles: true,
      cancelable: true,
    })
    const upperEnd = new KeyboardEvent('keydown', {
      key: 'End',
      bubbles: true,
      cancelable: true,
    })

    lower!.dispatchEvent(lowerArrow)
    upper!.dispatchEvent(upperHome)
    upper!.dispatchEvent(upperEnd)
    await nextTick()

    expect(lowerArrow.defaultPrevented).toBe(true)
    expect(upperHome.defaultPrevented).toBe(true)
    expect(upperEnd.defaultPrevented).toBe(true)
    expect(updates).toEqual([
      ['min', 5],
      ['max', 4],
      ['max', 10],
    ])
  })

  it('hands pointer priority to the opposite thumb after collapsing to one value', async () => {
    const { host } = mountFilter(8, 8)
    const [lower, upper] = host.querySelectorAll<HTMLInputElement>('input[type="range"]')

    expect(Number(upper?.style.zIndex)).toBeGreaterThan(Number(lower?.style.zIndex))
    upper!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
    upper!.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()

    expect(Number(lower?.style.zIndex)).toBeGreaterThan(Number(upper?.style.zIndex))
  })

  it.each([[1, 10], [2, 8], [8, 10]])(
    'renders two independent visible handles at %i–%i',
    (min, max) => {
      const { host } = mountFilter(min, max)
      const handles = host.querySelectorAll<HTMLElement>('.difficulty-range__thumb')

      expect(handles).toHaveLength(2)
      expect(handles[0]?.style.left).not.toBe(handles[1]?.style.left)
      expect(handles[0]?.getAttribute('aria-hidden')).toBe('true')
      expect(handles[1]?.getAttribute('aria-hidden')).toBe('true')
    },
  )

  it('shows both handles with a small offset when both bounds are equal', () => {
    const { host } = mountFilter(8, 8)
    const handles = host.querySelectorAll<HTMLElement>('.difficulty-range__thumb.is-overlapping')

    expect(handles).toHaveLength(2)
    expect(handles[0]?.style.left).toBe(handles[1]?.style.left)
  })
})
