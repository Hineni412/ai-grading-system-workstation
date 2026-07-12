import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AppShell from '../../../layouts/AppShell.vue'
import { createAppRouter } from '../../../router'
import { useSessionStore } from '../../../stores/session'

function installMatchMedia(matches: boolean) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>()
  const mediaQuery = {
    matches,
    media: '(max-width: 1023px)',
    onchange: null,
    addEventListener: vi.fn((_type: string, listener: (event: MediaQueryListEvent) => void) => {
      listeners.add(listener)
    }),
    removeEventListener: vi.fn((_type: string, listener: (event: MediaQueryListEvent) => void) => {
      listeners.delete(listener)
    }),
    dispatchEvent: vi.fn(),
  } as unknown as MediaQueryList

  vi.stubGlobal('matchMedia', vi.fn(() => mediaQuery))
  return mediaQuery
}

async function mountShell() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useSessionStore()
  await store.initialize(async () => [])
  const initialize = vi.spyOn(store, 'initialize').mockResolvedValue()
  const router = createAppRouter(createMemoryHistory())
  await router.push('/workbench')
  await router.isReady()

  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(AppShell)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  await nextTick()

  return { app, host, initialize }
}

beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('AppShell', () => {
  it('renders the application landmarks and session context', async () => {
    installMatchMedia(true)
    const { app, host, initialize } = await mountShell()

    expect(initialize).toHaveBeenCalledTimes(1)
    expect(host.querySelector('[data-testid="app-shell"]')).not.toBeNull()
    expect(host.querySelectorAll('[data-testid="primary-navigation"] a')).toHaveLength(6)
    expect(host.textContent).toContain('智能体与自动化')
    expect(host.querySelector('[aria-disabled="true"]')?.textContent).toContain('智能体与自动化')
    expect(host.querySelector('label[for="current-session"]')?.textContent).toBe('当前考试')
    expect(host.querySelector('[data-testid="session-inspector"]')?.textContent).toContain(
      '未选择当前考试',
    )

    app.unmount()
  })

  it('keeps overlay panels mutually exclusive and restores trigger focus on Escape', async () => {
    installMatchMedia(true)
    const { app, host } = await mountShell()
    const navigationTrigger = host.querySelector<HTMLButtonElement>('[data-testid="navigation-toggle"]')!
    const inspectorTrigger = host.querySelector<HTMLButtonElement>('[data-testid="inspector-toggle"]')!

    expect(navigationTrigger.getAttribute('aria-expanded')).toBe('false')
    expect(inspectorTrigger.getAttribute('aria-expanded')).toBe('false')

    navigationTrigger.click()
    await nextTick()
    expect(navigationTrigger.getAttribute('aria-expanded')).toBe('true')

    inspectorTrigger.focus()
    inspectorTrigger.click()
    await nextTick()
    expect(navigationTrigger.getAttribute('aria-expanded')).toBe('false')
    expect(inspectorTrigger.getAttribute('aria-expanded')).toBe('true')

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    expect(inspectorTrigger.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(inspectorTrigger)

    app.unmount()
  })
})
