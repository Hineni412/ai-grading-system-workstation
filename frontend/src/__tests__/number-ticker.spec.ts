import { createApp, h, nextTick, reactive, type App } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import { NumberTicker } from '../components/ui/number-ticker'

const mountedApps: App[] = []

function mountTicker(initial: { value: number; decimalPlaces?: number; duration?: number }) {
  const state = reactive({
    value: initial.value,
    decimalPlaces: initial.decimalPlaces ?? 0,
    duration: initial.duration ?? 1000,
  })
  const host = document.createElement('div')
  const app = createApp({
    setup: () => () => h(NumberTicker, {
      value: state.value,
      decimalPlaces: state.decimalPlaces,
      duration: state.duration,
    }),
  })
  app.mount(host)
  mountedApps.push(app)
  return { host, state }
}

afterEach(() => {
  for (const app of mountedApps.splice(0)) app.unmount()
})

describe('NumberTicker', () => {
  it('renders the final value immediately in jsdom (no IntersectionObserver fallback)', () => {
    const { host } = mountTicker({ value: 42 })
    const ticker = host.querySelector('span')
    expect(ticker?.textContent).toBe('42')
    expect(ticker?.classList.contains('tabular-nums')).toBe(true)
  })

  it('formats decimals with the requested decimal places', () => {
    const { host } = mountTicker({ value: 86.5, decimalPlaces: 1 })
    expect(host.querySelector('span')?.textContent).toBe('86.5')
  })

  it('follows value switches and stays on the latest final value', async () => {
    const { host, state } = mountTicker({ value: 42 })
    state.value = 57
    await nextTick()
    expect(host.querySelector('span')?.textContent).toBe('57')
  })

  it('renders the final value when duration is 0', () => {
    const { host } = mountTicker({ value: 7, duration: 0 })
    expect(host.querySelector('span')?.textContent).toBe('7')
  })
})
