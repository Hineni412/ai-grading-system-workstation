import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AppShell from '../../../layouts/AppShell.vue'
import { createAppRouter } from '../../../router'
import { useSessionStore } from '../../../stores/session'
import { useConfigWorkspaceStore } from '../../../stores/config-workspace'
import { useReviewDraftStore } from '../../../stores/review-drafts'
import type { SessionSummary } from '../../../api/sessions'
import { CONFIG_WORKSPACE_STORAGE_KEY } from '../../../stores/config-workspace'

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
  it('keeps an unverified workspace candidate after a transient session-list failure', async () => {
    const candidate = {
      sessionId: 7, phase: 'editor', sourceId: 'd'.repeat(32),
      sourceRevision: 'b'.repeat(64), jobId: 31, decisions: [],
    }
    localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, JSON.stringify(candidate))
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('offline'))
    const { app } = await mountShell({ prepareStore: false })
    await settleUi()

    expect(localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)).toBe(JSON.stringify(candidate))
    expect(useConfigWorkspaceStore().sessionId).toBeNull()
    app.unmount()
  })

  it.each(['/workbench', '/sessions', '/grading', '/missing/deep/path'])(
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
    expect(host.querySelector('.app-topbar__identity')).not.toBeNull()
    expect(host.querySelector('.app-topbar__navigation')).not.toBeNull()
    expect(host.querySelector('.app-topbar__session')).not.toBeNull()
    expect(
      [...host.querySelectorAll<HTMLAnchorElement>('[data-testid="app-navigation"] a')].map(
        (link) => [link.textContent, link.getAttribute('href')],
      ),
    ).toEqual([
      ['工作台', '/workbench'],
      ['考试配置', '/sessions'],
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
    expect([...host.querySelectorAll('nav a')].map((link) => link.textContent)).toEqual([
      '工作台',
      '考试配置',
      '评分复核',
    ])
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
    expect(document.activeElement).toBe(host.querySelector('#main-workspace h1'))

    app.unmount()
  })

  it('defines explicit responsive areas and token-only navigation states for all topbar blocks', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/styles/app-shell.css'), 'utf-8')
    const tabletStart = css.indexOf('@media (max-width: 1100px)')
    const compactStart = css.indexOf('@media (max-width: 720px)')

    expect(css).toMatch(
      /\.app-topbar\s*\{[^}]*grid-template-columns:\s*minmax\(220px,\s*1fr\)\s+auto\s+minmax\(280px,\s*360px\);/s,
    )
    expect(css).toContain('"identity navigation session"')
    expect(css).toContain('"status status status"')
    expect(css).toMatch(/\.app-topbar__identity\s*\{[^}]*grid-area:\s*identity;/s)
    expect(css).toMatch(/\.app-topbar__navigation\s*\{[^}]*grid-area:\s*navigation;/s)
    expect(css).toMatch(/\.app-topbar__session\s*\{[^}]*grid-area:\s*session;/s)
    expect(css).toMatch(/\.app-topbar__status\s*\{[^}]*grid-area:\s*status;/s)
    expect(css).toMatch(/\.app-topbar__navigation a\s*\{/)
    expect(css).toMatch(/\.app-topbar__navigation a\[aria-current="page"\]\s*\{/)
    expect(css).toMatch(/\.app-topbar__navigation a:focus-visible\s*\{/)
    expect(css).not.toMatch(/#[\da-f]{3,8}\b|(?:rgb|hsl)a?\s*\(/i)

    expect(tabletStart).toBeGreaterThanOrEqual(0)
    expect(compactStart).toBeGreaterThan(tabletStart)
    const tabletRules = css.slice(tabletStart, compactStart)
    expect(tabletRules).toContain('"identity session"')
    expect(tabletRules).toContain('"navigation navigation"')
    expect(tabletRules).toContain('"status status"')

    const compactRules = css.slice(compactStart)
    expect(compactRules).toContain('grid-template-columns: minmax(0, 1fr)')
    expect(compactRules).toContain('"identity"')
    expect(compactRules).toContain('"navigation"')
    expect(compactRules).toContain('"session"')
    expect(compactRules).toContain('"status"')
  })

  it.each(['review', 'config'] as const)('warns before leaving with dirty %s work', async (kind) => {
    const { app } = await mountShell()
    if (kind === 'review') {
      const store = useReviewDraftStore()
      store.drafts['7:Q1:1'] = {
        key: '7:Q1:1', sessionId: 7, questionId: 'Q1', detailId: 1,
        scoreText: '4', note: '', baseScoreText: '3', baseNote: '',
        dirty: true, updatedAt: Date.now(),
      }
    } else {
      const store = useConfigWorkspaceStore()
      store.updateEditor({ row_id: 'row-1', standard_answer: '草稿答案' })
    }
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    app.unmount()
  })

  it('does not let the current-exam selector silently discard config edits', async () => {
    const { app, host } = await mountShell()
    const sessionStore = useSessionStore()
    const configStore = useConfigWorkspaceStore()
    const available: SessionSummary[] = [
      { id: 7, name: '考试一', status: 'created', is_deleted: false, deleted_at: null, created_at: null, updated_at: null },
      { id: 9, name: '考试二', status: 'created', is_deleted: false, deleted_at: null, created_at: null, updated_at: null },
    ]
    sessionStore.sessions = available
    sessionStore.selectSession(7)
    configStore.selectSession(7)
    configStore.setEditor({
      session_id: 7, configured: true, revision: 'a'.repeat(64), rows: [],
      total_score: 0, issues: [], source: null,
    })
    configStore.updateEditor({ row_id: 'row-1', standard_answer: '未保存答案' })
    vi.stubGlobal('confirm', vi.fn(() => false))

    const selector = host.querySelector<HTMLSelectElement>('#current-session')!
    selector.value = '9'
    selector.dispatchEvent(new Event('change'))
    await settleUi()

    expect(sessionStore.selectedSessionId).toBe(7)
    expect(configStore.sessionId).toBe(7)
    expect(configStore.editorEdits[0]?.standard_answer).toBe('未保存答案')
    app.unmount()
  })

  it.each(['generation', 'upload'] as const)(
    'blocks exam switching while an unknown %s result still needs reconciliation',
    async (kind) => {
      const { app, host } = await mountShell()
      const sessionStore = useSessionStore()
      const configStore = useConfigWorkspaceStore()
      sessionStore.sessions = [
        { id: 7, name: '考试一', status: 'created', is_deleted: false, deleted_at: null, created_at: null, updated_at: null },
        { id: 9, name: '考试二', status: 'created', is_deleted: false, deleted_at: null, created_at: null, updated_at: null },
      ]
      sessionStore.selectSession(7)
      configStore.selectSession(7)
      if (kind === 'generation') configStore.markJobSubmissionPending('1'.repeat(32), 'refine')
      else configStore.markUploadSubmissionPending('2'.repeat(32))
      const alert = vi.spyOn(window, 'alert').mockImplementation(() => undefined)

      const selector = host.querySelector<HTMLSelectElement>('#current-session')!
      selector.value = '9'
      selector.dispatchEvent(new Event('change'))
      await settleUi()

      expect(alert).toHaveBeenCalledWith(expect.stringContaining('核对'))
      expect(selector.value).toBe('7')
      expect(sessionStore.selectedSessionId).toBe(7)
      expect(configStore.sessionId).toBe(7)
      app.unmount()
    },
  )
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
