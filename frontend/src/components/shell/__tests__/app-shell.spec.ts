import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AppShell from '../../../layouts/AppShell.vue'
import { createAppRouter } from '../../../router'
import { useSessionStore } from '../../../stores/session'
import { useCurriculumScopeStore } from '../../../stores/curriculum-scope'
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

function sessionSummary(
  overrides: Pick<SessionSummary, 'id' | 'name'> & Partial<SessionSummary>,
): SessionSummary {
  return {
    status: 'created',
    curriculum_volume_id: null,
    is_deleted: false,
    deleted_at: null,
    created_at: null,
    updated_at: null,
    ...overrides,
  }
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
    expect(host.querySelector('[data-testid="app-shell"]')?.textContent).toContain('知衡')
    expect(host.querySelector('.app-sidebar__brand-copy small')?.textContent).toBe('教师教学工作台')
    expect(host.querySelector('[data-testid="app-topbar"]')?.textContent).toContain('考试批改')
    expect(host.querySelector('.app-sidebar__brand')).not.toBeNull()
    expect(host.querySelector('.app-sidebar__navigation')).not.toBeNull()
    expect(host.querySelector('.app-topbar__session')).not.toBeNull()
    expect(
      [...host.querySelectorAll<HTMLAnchorElement>('[data-testid="app-navigation"] a')].map(
        (link) => [link.textContent, link.getAttribute('href')],
      ),
    ).toEqual([
      ['工作台', '/workbench'],
      ['考试配置', '/sessions'],
      ['考试批改', '/grading'],
      ['成绩中心', '/results'],
      ['题库管理', '/question-bank'],
      ['组卷工作台', '/question-assembly'],
      ['知识与训练', '/knowledge-graph'],
      ['备课工作台', '/teaching-prep'],
      ['班主任工作台', '/class-teacher'],
    ])
    expect(
      [...host.querySelectorAll<HTMLAnchorElement>('[data-testid="app-navigation"] a')].map(
        link => link.getAttribute('aria-label'),
      ),
    ).toEqual([
      '工作台',
      '考试配置',
      '考试批改',
      '成绩中心',
      '题库管理',
      '组卷工作台',
      '知识与训练',
      '备课工作台',
      '班主任工作台',
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
    expect(host.querySelector('label[for="current-curriculum-volume"]')?.textContent).toBe('教学学期')
    expect(host.querySelector('#current-curriculum-volume')).not.toBeNull()
    expect([...host.querySelectorAll('nav a')].map((link) => link.textContent)).toEqual([
      '工作台',
      '考试配置',
      '考试批改',
      '成绩中心',
      '题库管理',
      '组卷工作台',
      '知识与训练',
      '备课工作台',
      '班主任工作台',
      '学生管理',
      '设置',
    ])
    expect(host.querySelector('[data-testid="navigation-toggle"]')).toBeNull()
    expect(host.querySelector('[data-testid="inspector-toggle"]')).toBeNull()

    app.unmount()
  })

  it.each(['/teaching-prep', '/class-teacher'])(
    'lets the workspace own its topbar context at %s',
    async (path) => {
      const { app, host } = await mountShell({ path })

      const topbar = host.querySelector('[data-testid="app-topbar"]')
      expect(topbar?.classList.contains('app-topbar--workspace-context')).toBe(true)
      expect(topbar?.querySelector('.app-topbar__session')).toBeNull()
      expect(topbar?.querySelector('label[for="current-session"]')).toBeNull()
      expect(topbar?.querySelector('#current-session')).toBeNull()
      expect(topbar?.textContent).not.toContain('当前考试')
      const workspaceNavigation = topbar?.querySelector('#workspace-topbar-tabs')
      expect(workspaceNavigation !== null).toBe(path === '/teaching-prep')
      expect([...workspaceNavigation?.querySelectorAll('button') ?? []].map(
        button => button.textContent?.trim(),
      )).toEqual(path === '/teaching-prep' ? ['备课首页', '资料库'] : [])
      expect(topbar?.querySelector('#current-curriculum-volume') !== null).toBe(
        path === '/teaching-prep',
      )

      app.unmount()
    },
    10_000,
  )

  it('navigates between truthful destinations and updates the current page', async () => {
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
    const tabletStart = css.indexOf('@media (max-width: 900px)')
    const compactStart = css.indexOf('@media (max-width: 620px)')

    expect(css).toMatch(
      /\.app-topbar\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+minmax\(280px,\s*380px\)\s+auto;/s,
    )
    expect(css).toContain('"page session tasks"')
    expect(css).toContain('"status status status"')
    expect(css).toMatch(/\.app-topbar__page\s*\{[^}]*grid-area:\s*page;/s)
    expect(css).toMatch(/\.app-topbar__session\s*\{[^}]*grid-area:\s*session;/s)
    expect(css).toMatch(/\.app-topbar__status\s*\{[^}]*grid-area:\s*status;/s)
    expect(css).toMatch(/\.app-topbar__tasks\s*\{[^}]*grid-area:\s*tasks;/s)
    expect(css).toMatch(/\.app-sidebar__link\s*\{/)
    expect(css).toMatch(/\.app-sidebar__link\[aria-current='page'\]\s*\{/)
    expect(css).toMatch(/\.app-sidebar__link:focus-visible/)
    expect(css).not.toMatch(
      /\.app-sidebar__group\s*>\s*p\s*\{[^}]*height:\s*0[^}]*\}/s,
    )
    expect(css).not.toMatch(/#[\da-f]{3,8}\b|(?:rgb|hsl)a?\s*\(/i)

    expect(tabletStart).toBeGreaterThanOrEqual(0)
    expect(compactStart).toBeGreaterThan(tabletStart)
    const tabletRules = css.slice(tabletStart, compactStart)
    expect(tabletRules).toContain('"topbar"')
    expect(tabletRules).toContain('"sidebar"')
    expect(tabletRules).toContain('"main"')

    const compactRules = css.slice(compactStart)
    expect(compactRules).toContain('grid-template-columns: minmax(0, 1fr)')
    expect(compactRules).toContain('"page tasks"')
    expect(compactRules).toContain('"session session"')
    expect(compactRules).toContain('"status status"')
  })

  it('keeps desktop sidebar icons on the same horizontal anchor while expanding', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/styles/app-shell.css'), 'utf-8')
    const desktopStart = css.indexOf('@media (min-width: 901px)')
    const tabletStart = css.indexOf('@media (max-width: 900px)')
    const desktopRules = css.slice(desktopStart, tabletStart)

    expect(desktopRules).toMatch(
      /\.app-sidebar__link\s*\{[^}]*justify-content:\s*flex-start;[^}]*padding-inline:\s*var\(--sidebar-icon-inset\);/s,
    )
    expect(desktopRules).toMatch(
      /\.app-sidebar__brand\s*\{[^}]*justify-content:\s*flex-start;[^}]*padding-inline:\s*calc\(var\(--sidebar-icon-inset\) - 6\.5px\);/s,
    )
    expect(desktopRules).toMatch(
      /\.app-sidebar__settings-toggle\s*\{[^}]*justify-content:\s*flex-start;[^}]*padding-inline:\s*var\(--sidebar-icon-inset\);/s,
    )
    expect(desktopRules).not.toMatch(
      /\.app-sidebar:(?:hover|focus-within)[^{]*\.app-sidebar__(?:brand|link)[^{]*\{[^}]*padding-inline/s,
    )
  })

  it('keeps desktop sidebar group headings on one line while collapsed', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/styles/app-shell.css'), 'utf-8')

    expect(css).toMatch(
      /\.app-sidebar__group\s*>\s*p\s*\{[^}]*white-space:\s*nowrap;/s,
    )
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

  it('folds other-term exams after a global teaching term is selected', async () => {
    const { app, host } = await mountShell()
    const sessionStore = useSessionStore()
    const curriculumScope = useCurriculumScopeStore()
    const volumeId = 'xkw-bnu-math-8-first'
    curriculumScope.volumes = [{
      id: volumeId,
      order: 1,
      label: '八年级上册',
      grade: '八年级',
      semester: '上册',
      textbook_version: '北师大版',
      source: {},
      statistics: { raw_nodes: 0, excluded_nodes: 0, retained_nodes: 0 },
      chapters: [],
    }]
    curriculumScope.loadState = 'ready'
    sessionStore.sessions = [
      sessionSummary({ id: 7, name: '八年级上册期中', curriculum_volume_id: volumeId }),
      sessionSummary({ id: 9, name: '七年级下册期末', curriculum_volume_id: 'other-volume' }),
      sessionSummary({ id: 11, name: '历史未归类考试' }),
    ]

    curriculumScope.selectVolume(volumeId)
    await settleUi()

    const selector = host.querySelector<HTMLSelectElement>('#current-session')!
    expect([...selector.options].map(option => option.textContent)).toEqual([
      '未选择',
      '八年级上册期中',
    ])
    const otherButton = host.querySelector<HTMLButtonElement>('.app-topbar__other-sessions')!
    expect(otherButton.textContent).toContain('其他学期 2')

    otherButton.click()
    await settleUi()
    expect([...selector.options].map(option => option.textContent)).toEqual([
      '未选择',
      '八年级上册期中',
      '七年级下册期末',
      '历史未归类考试',
    ])
    app.unmount()
  })

  it('moves a selected other-term exam into the folded list when the teaching term changes', async () => {
    const { app, host } = await mountShell()
    const sessionStore = useSessionStore()
    const configStore = useConfigWorkspaceStore()
    const curriculumScope = useCurriculumScopeStore()
    const volumeId = 'xkw-bnu-math-7-first'
    curriculumScope.volumes = [{
      id: volumeId,
      order: 1,
      label: '七年级上册',
      grade: '七年级',
      semester: '上册',
      textbook_version: '北师大版',
      source: {},
      statistics: { raw_nodes: 0, excluded_nodes: 0, retained_nodes: 0 },
      chapters: [],
    }]
    curriculumScope.loadState = 'ready'
    sessionStore.sessions = [
      sessionSummary({ id: 7, name: '七年级上册期中', curriculum_volume_id: volumeId }),
      sessionSummary({ id: 9, name: '七年级下册期末', curriculum_volume_id: 'other-volume' }),
    ]
    sessionStore.selectSession(9)
    configStore.selectSession(9)
    await settleUi()

    const scopeSelector = host.querySelector<HTMLSelectElement>('#current-curriculum-volume')!
    scopeSelector.value = volumeId
    scopeSelector.dispatchEvent(new Event('change'))
    await settleUi()

    expect(curriculumScope.selectedVolumeId).toBe(volumeId)
    expect(sessionStore.selectedSessionId).toBeNull()
    expect(configStore.sessionId).toBeNull()
    expect([...host.querySelectorAll<HTMLOptionElement>('#current-session option')].map(
      option => option.textContent,
    )).toEqual(['未选择', '七年级上册期中'])
    expect(host.querySelector('.app-topbar__other-sessions')?.textContent).toContain('其他学期 1')
    app.unmount()
  })

  it('keeps the previous teaching term when folding the current exam would discard edits', async () => {
    const { app, host } = await mountShell()
    const sessionStore = useSessionStore()
    const configStore = useConfigWorkspaceStore()
    const curriculumScope = useCurriculumScopeStore()
    const volumeId = 'xkw-bnu-math-7-first'
    curriculumScope.volumes = [{
      id: volumeId,
      order: 1,
      label: '七年级上册',
      grade: '七年级',
      semester: '上册',
      textbook_version: '北师大版',
      source: {},
      statistics: { raw_nodes: 0, excluded_nodes: 0, retained_nodes: 0 },
      chapters: [],
    }]
    curriculumScope.loadState = 'ready'
    sessionStore.sessions = [
      sessionSummary({ id: 9, name: '七年级下册期末', curriculum_volume_id: 'other-volume' }),
    ]
    sessionStore.selectSession(9)
    configStore.selectSession(9)
    configStore.setEditor({
      session_id: 9,
      configured: true,
      revision: 'a'.repeat(64),
      rows: [],
      total_score: 0,
      issues: [],
      source: null,
    })
    configStore.updateEditor({ row_id: 'row-1', standard_answer: '未保存答案' })
    vi.stubGlobal('confirm', vi.fn(() => false))
    await settleUi()

    const scopeSelector = host.querySelector<HTMLSelectElement>('#current-curriculum-volume')!
    scopeSelector.value = volumeId
    scopeSelector.dispatchEvent(new Event('change'))
    await settleUi()

    expect(curriculumScope.selectedVolumeId).toBeNull()
    expect(scopeSelector.value).toBe('')
    expect(sessionStore.selectedSessionId).toBe(9)
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
      .find((button) => button.textContent === '重新加载')!
    retry.click()
    await settleUi()
    expect(initialize).toHaveBeenCalledTimes(2)

    app.unmount()
  })
})
