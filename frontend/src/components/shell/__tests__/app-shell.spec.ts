import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AppShell from '../../../layouts/AppShell.vue'
import { createAppRouter } from '../../../router'
import { useSessionStore } from '../../../stores/session'

async function settleUi(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountShell({
  path = '/grading',
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
  it.each(['/grading', '/missing/deep/path'])(
    'renders one main landmark and no permanent side panels at %s',
    async (path) => {
      const { app, host } = await mountShell({ path })

      expect(host.querySelectorAll('main')).toHaveLength(1)
      expect(host.querySelector('main#main-workspace')).not.toBeNull()
      expect(host.querySelector('[data-testid="app-navigation"]')).not.toBeNull()
      expect(host.querySelector('[data-testid="session-inspector"]')).toBeNull()
      expect(host.querySelector('[data-testid="review-scoring-inspector"]')).toBeNull()

      app.unmount()
    },
  )

  it('renders product, page and current-exam context in the topbar', async () => {
    const { app, host, initialize } = await mountShell()

    expect(initialize).toHaveBeenCalledTimes(1)
    expect(host.querySelector('[data-testid="app-shell"]')).not.toBeNull()
    expect(host.querySelector('[data-testid="app-topbar"]')?.textContent).toContain('AI 阅卷系统')
    expect(host.querySelector('[data-testid="app-topbar"]')?.textContent).toContain('评分复核')
    expect(
      [...host.querySelectorAll<HTMLAnchorElement>('[data-testid="app-navigation"] a')].map(
        (link) => [link.textContent, link.getAttribute('href')],
      ),
    ).toEqual([
      ['工作台', '/workbench'],
      ['评分复核', '/grading'],
    ])
    expect(
      host.querySelector('[data-testid="app-navigation"] a[href="/grading"]')?.getAttribute(
        'aria-current',
      ),
    ).toBe('page')
    expect(
      host.querySelector('[data-testid="app-navigation"] a[href="/workbench"]')?.hasAttribute(
        'aria-current',
      ),
    ).toBe(false)
    expect(host.querySelector('label[for="current-session"]')?.textContent).toBe('当前考试')
    expect(host.querySelector('#current-session')).not.toBeNull()
    expect(host.querySelector('[data-testid="navigation-toggle"]')).toBeNull()
    expect(host.querySelector('[data-testid="inspector-toggle"]')).toBeNull()

    app.unmount()
  })

  it('navigates between the two truthful destinations and updates the current page', async () => {
    const { app, host, router } = await mountShell()
    const workbenchLink = host.querySelector<HTMLAnchorElement>(
      '[data-testid="app-navigation"] a[href="/workbench"]',
    )!

    workbenchLink.click()
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/workbench'))
    await settleUi()

    expect(workbenchLink.getAttribute('aria-current')).toBe('page')
    expect(
      host.querySelector('[data-testid="app-navigation"] a[href="/grading"]')?.hasAttribute(
        'aria-current',
      ),
    ).toBe(false)

    app.unmount()
  })

  it('focuses the route heading after navigation', async () => {
    const { app, host, router } = await mountShell({ path: '/design-system' })

    await router.push('/grading')
    await settleUi()

    expect(document.activeElement).toBe(host.querySelector('#main-workspace h1'))

    app.unmount()
  })

  it('shows a safe exam-list error in the topbar and retries through the store', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_path, init) => {
      const requestId = String((init?.headers as Record<string, string>)['x-request-id'])
      return new Response(
        JSON.stringify({
          error: {
            code: 'session_not_found',
            message: 'private network detail',
            details: {},
            request_id: requestId,
          },
        }),
        { status: 404, headers: { 'content-type': 'application/json', 'x-request-id': requestId } },
      )
    })
    const { app, host, initialize } = await mountShell({ prepareStore: false })

    await settleUi()
    expect(host.querySelector('[data-testid="app-topbar"] [role="alert"]')).not.toBeNull()
    expect(host.textContent).not.toContain('private network detail')
    expect(initialize).toHaveBeenCalledTimes(1)

    const retry = [...host.querySelectorAll<HTMLButtonElement>('[data-testid="app-topbar"] button')]
      .find((button) => button.textContent === '重新加载考试列表')!
    retry.click()
    await settleUi()
    expect(initialize).toHaveBeenCalledTimes(2)

    app.unmount()
  })
})
