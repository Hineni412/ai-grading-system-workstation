import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AppShell from '../../../layouts/AppShell.vue'
import { createAppRouter } from '../../../router'
import { useSessionStore } from '../../../stores/session'

function installMatchMedia(matches: boolean) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>()
  let currentMatches = matches
  const mediaQuery = {
    get matches() {
      return currentMatches
    },
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
  return {
    mediaQuery,
    change(nextMatches: boolean) {
      currentMatches = nextMatches
      const event = { matches: nextMatches, media: mediaQuery.media } as MediaQueryListEvent
      listeners.forEach((listener) => listener(event))
    },
  }
}

async function settleUi(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountShell({
  path = '/workbench',
  prepareStore = true,
}: { path?: string; prepareStore?: boolean } = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useSessionStore()
  if (prepareStore) await store.initialize(async () => [])
  const initialize = vi.spyOn(store, 'initialize')
  if (prepareStore) initialize.mockResolvedValue()
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()

  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(AppShell)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  await settleUi()

  return { app, host, initialize, router }
}

beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('AppShell', () => {
  it.each(['/workbench', '/missing/deep/path'])(
    'renders exactly one main landmark at %s',
    async (path) => {
      installMatchMedia(false)
      const { app, host } = await mountShell({ path })

      expect(host.querySelectorAll('main')).toHaveLength(1)
      expect(host.querySelector('main#main-workspace')).not.toBeNull()

      app.unmount()
    },
  )

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

  it('opens both side panels by default on desktop', async () => {
    installMatchMedia(false)
    const { app, host } = await mountShell()

    expect(host.querySelector('[data-testid="app-shell"]')?.getAttribute('data-overlay')).toBe(
      'false',
    )
    expect(
      host.querySelector('[data-testid="navigation-toggle"]')?.getAttribute('aria-expanded'),
    ).toBe('true')
    expect(
      host.querySelector('[data-testid="inspector-toggle"]')?.getAttribute('aria-expanded'),
    ).toBe('true')

    app.unmount()
  })

  it('starts compact navigation collapsed and toggles its real expanded state', async () => {
    const compactQuery = '(min-width: 1024px) and (max-width: 1279px)'
    const mediaQueries: MediaQueryList[] = []
    vi.stubGlobal(
      'matchMedia',
      vi.fn((query: string) => {
        const mediaQuery = {
          matches: query === compactQuery,
          media: query,
          onchange: null,
          addEventListener: vi.fn(),
          removeEventListener: vi.fn(),
          dispatchEvent: vi.fn(),
        } as unknown as MediaQueryList
        mediaQueries.push(mediaQuery)
        return mediaQuery
      }),
    )
    const { app, host } = await mountShell()
    const navigationTrigger = host.querySelector<HTMLButtonElement>('[data-testid="navigation-toggle"]')!

    expect(navigationTrigger.getAttribute('aria-expanded')).toBe('false')
    navigationTrigger.click()
    await nextTick()
    expect(navigationTrigger.getAttribute('aria-expanded')).toBe('true')
    navigationTrigger.click()
    await nextTick()
    expect(navigationTrigger.getAttribute('aria-expanded')).toBe('false')

    app.unmount()
    expect(mediaQueries).toHaveLength(2)
    expect(mediaQueries.every((query) => vi.mocked(query.removeEventListener).mock.calls.length === 1)).toBe(true)
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

  it('restores trigger focus when the backdrop closes an overlay', async () => {
    installMatchMedia(true)
    const { app, host } = await mountShell()
    const trigger = host.querySelector<HTMLButtonElement>('[data-testid="navigation-toggle"]')!

    trigger.focus()
    trigger.click()
    await nextTick()
    host.querySelector<HTMLButtonElement>('[data-testid="shell-backdrop"]')!.click()
    await nextTick()

    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(trigger)

    app.unmount()
  })

  it('applies media-query changes and preserves overlay exclusivity', async () => {
    const { change } = installMatchMedia(false)
    const { app, host } = await mountShell()
    const navigationTrigger = host.querySelector<HTMLButtonElement>('[data-testid="navigation-toggle"]')!
    const inspectorTrigger = host.querySelector<HTMLButtonElement>('[data-testid="inspector-toggle"]')!

    change(true)
    await nextTick()
    expect(navigationTrigger.getAttribute('aria-expanded')).toBe('false')
    expect(inspectorTrigger.getAttribute('aria-expanded')).toBe('false')

    navigationTrigger.click()
    inspectorTrigger.click()
    await nextTick()
    expect(navigationTrigger.getAttribute('aria-expanded')).toBe('false')
    expect(inspectorTrigger.getAttribute('aria-expanded')).toBe('true')

    change(false)
    await nextTick()
    expect(navigationTrigger.getAttribute('aria-expanded')).toBe('true')
    expect(inspectorTrigger.getAttribute('aria-expanded')).toBe('true')

    app.unmount()
  })

  it('removes media-query and keyboard listeners on unmount', async () => {
    const { mediaQuery } = installMatchMedia(false)
    const addWindowListener = vi.spyOn(window, 'addEventListener')
    const removeWindowListener = vi.spyOn(window, 'removeEventListener')
    const { app } = await mountShell()
    const keydownRegistration = addWindowListener.mock.calls.find(([type]) => type === 'keydown')

    app.unmount()

    expect(mediaQuery.removeEventListener).toHaveBeenCalledWith('change', expect.any(Function))
    expect(keydownRegistration).toBeDefined()
    expect(removeWindowListener).toHaveBeenCalledWith('keydown', keydownRegistration![1])
  })

  it('focuses the route heading after navigation', async () => {
    installMatchMedia(false)
    const { app, host, router } = await mountShell()

    await router.push('/grading')
    await settleUi()

    expect(document.activeElement).toBe(host.querySelector('#main-workspace h1'))

    app.unmount()
  })

  it('renders startup load failure and retries only through the Store action', async () => {
    installMatchMedia(false)
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('private network detail'))
    const { app, host, initialize } = await mountShell({ prepareStore: false })

    await settleUi()
    expect(host.querySelector('[data-testid="session-inspector"] [role="alert"]')).not.toBeNull()
    expect(host.textContent).not.toContain('private network detail')
    expect(initialize).toHaveBeenCalledTimes(1)

    const retry = [...host.querySelectorAll<HTMLButtonElement>('[data-testid="session-inspector"] button')]
      .find((button) => button.textContent === '重新加载考试列表')!
    retry.click()
    await settleUi()
    expect(initialize).toHaveBeenCalledTimes(2)

    app.unmount()
  })
})
