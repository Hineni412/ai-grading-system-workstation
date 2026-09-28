import { createApp, h, nextTick } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import DifficultyRangeFilter from '../components/question-bank/DifficultyRangeFilter.vue'

const apps: Array<ReturnType<typeof createApp>> = []

function mountFilter(min = 1, max = 8, ceiling = 10) {
  const host = document.createElement('div')
  document.body.append(host)
  const updates: Array<[string, number]> = []
  const app = createApp({
    setup() {
      return () => h(DifficultyRangeFilter, {
        min,
        max,
        ceiling,
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
