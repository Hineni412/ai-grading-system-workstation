import { createApp, h, nextTick, reactive, type App } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import { AnimatedCircularProgressBar } from '../components/ui/animated-circular-progress-bar'

const mountedApps: App[] = []

function mountProgress(initial: { value: number; duration?: number; showPercentage?: boolean }) {
  const state = reactive({
    value: initial.value,
    duration: initial.duration ?? 1,
    showPercentage: initial.showPercentage ?? true,
  })
  const host = document.createElement('div')
  const app = createApp({
    setup: () => () => h(AnimatedCircularProgressBar, {
      value: state.value,
      duration: state.duration,
      showPercentage: state.showPercentage,
    }),
  })
  app.mount(host)
  mountedApps.push(app)
  return { host, state }
}

afterEach(() => {
  for (const app of mountedApps.splice(0)) app.unmount()
})

describe('AnimatedCircularProgressBar', () => {
  it('renders the rounded final percentage immediately in jsdom', () => {
    const { host } = mountProgress({ value: 33.33 })
    const root = host.querySelector('.progress-circle-base')
    expect(root).not.toBeNull()
    const label = host.querySelector('[data-current-value]')
    expect(label?.textContent?.trim()).toBe('33')
    expect(label?.getAttribute('data-current-value')).toBe('33.33')
  })

  it('follows value switches to the new final percentage', async () => {
    const { host, state } = mountProgress({ value: 33 })
    state.value = 80
    await nextTick()
    expect(host.querySelector('[data-current-value]')?.textContent?.trim()).toBe('80')
  })

  it('renders the final value when duration is 0', () => {
    const { host } = mountProgress({ value: 65, duration: 0 })
    expect(host.querySelector('[data-current-value]')?.textContent?.trim()).toBe('65')
  })

  it('keeps the gauge svg when the percentage label is hidden', () => {
    const { host } = mountProgress({ value: 40, showPercentage: false })
    expect(host.querySelector('[data-current-value]')).toBeNull()
    expect(host.querySelectorAll('svg circle').length).toBeGreaterThan(0)
  })
})
