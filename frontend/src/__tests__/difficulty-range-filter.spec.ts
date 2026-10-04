import { createApp, h, nextTick } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import DifficultyRangeFilter from '../components/question-bank/DifficultyRangeFilter.vue'

const apps: Array<ReturnType<typeof createApp>> = []

function mountFilter(min = 1, max = 8, ceiling = 10, compact = false) {
  const host = document.createElement('div')
  document.body.append(host)
  const updates: Array<[string, number]> = []
  let changes = 0
  const app = createApp({
    setup() {
      return () => h(DifficultyRangeFilter, {
        min,
        max,
        ceiling,
        compact,
        'onUpdate:min': (value: number) => updates.push(['min', value]),
        'onUpdate:max': (value: number) => updates.push(['max', value]),
        onChange: () => { changes += 1 },
      })
    },
  })
  app.mount(host)
  apps.push(app)
  return { host, updates, changes: () => changes }
}

afterEach(() => {
  for (const app of apps.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('difficulty range filter', () => {
  it('summarizes the compact range and quick-picks a band', async () => {
    const { host, updates, changes } = mountFilter(1, 10, 10, true)
    const popover = host.querySelector<HTMLDetailsElement>('.qb-difficulty-filter')!
    expect(popover.querySelector('summary')!.textContent).toContain('难度 1–10')
    popover.open = true
    await nextTick()
    const band = [...popover.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.includes('中档提升'))!
    expect(band.getAttribute('aria-pressed')).toBe('false')
    band.click()
    expect(updates).toEqual([['min', 4.5], ['max', 6.4]])
    expect(changes()).toBe(1)
  })

  it('names an exact band in the compact summary and toggles it back to the full range', async () => {
    const { host, updates, changes } = mountFilter(4.5, 6.4, 10, true)
    const popover = host.querySelector<HTMLDetailsElement>('.qb-difficulty-filter')!
    expect(popover.querySelector('summary')!.textContent).toContain('难度 · 中档提升')
    popover.open = true
    await nextTick()
    const band = [...popover.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.includes('中档提升'))!
    expect(band.getAttribute('aria-pressed')).toBe('true')
    band.click()
    expect(updates).toEqual([['min', 1], ['max', 10]])
    expect(changes()).toBe(1)
  })

  it('shows the raw bounds in the compact summary when no band matches', () => {
    const { host } = mountFilter(2.5, 6.5, 10, true)
    expect(host.querySelector('.qb-difficulty-filter summary')!.textContent).toContain('难度 2.5–6.5')
  })

  it('caps practice at eight while retaining the bank ten-level scale', async () => {
    const bank = mountFilter(1, 8)
    const practice = mountFilter(1, 7, 8)
    const bankMax = bank.host.querySelectorAll<HTMLInputElement>('input[type="range"]')[1]!
    const practiceMax = practice.host.querySelectorAll<HTMLInputElement>('input[type="range"]')[1]!
    expect(bankMax.max).toBe('10')
    expect(practiceMax.max).toBe('8')
    practiceMax.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }))
    await nextTick()
    expect(practice.updates).toContainEqual(['max', 8])
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

})
