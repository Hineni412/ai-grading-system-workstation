import { createApp, h, nextTick, reactive, type App } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import { BorderBeam } from '../components/ui/border-beam'

const mountedApps: App[] = []

function mountBeam(options: { withClass?: boolean; toggleable?: boolean } = {}) {
  const state = reactive({ show: true })
  const host = document.createElement('div')
  const app = createApp({
    setup: () => () =>
      options.toggleable
        ? (state.show ? h(BorderBeam) : null)
        : h(BorderBeam, options.withClass ? { class: 'custom-beam' } : {}),
  })
  app.mount(host)
  mountedApps.push(app)
  return { host, state }
}

afterEach(() => {
  for (const app of mountedApps.splice(0)) app.unmount()
})

describe('BorderBeam', () => {
  it('renders a decorative, aria-hidden beam element', () => {
    const { host } = mountBeam()
    const beam = host.querySelector('.border-beam')
    expect(beam).not.toBeNull()
    expect(beam?.getAttribute('aria-hidden')).toBe('true')
    expect(beam?.classList.contains('pointer-events-none')).toBe(true)
    expect(beam?.textContent?.trim()).toBe('')
  })

  it('merges a custom class onto the beam element', () => {
    const { host } = mountBeam({ withClass: true })
    const beam = host.querySelector('.border-beam')
    expect(beam?.classList.contains('custom-beam')).toBe(true)
  })

  it('mounts and unmounts cleanly when toggled', async () => {
    const { host, state } = mountBeam({ toggleable: true })
    expect(host.querySelector('.border-beam')).not.toBeNull()
    state.show = false
    await nextTick()
    expect(host.querySelector('.border-beam')).toBeNull()
    state.show = true
    await nextTick()
    expect(host.querySelector('.border-beam')).not.toBeNull()
  })
})
