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
import { useJobStore } from '../../../stores/jobs'
import type { SessionSummary } from '../../../api/sessions'
import { CONFIG_WORKSPACE_STORAGE_KEY } from '../../../stores/config-workspace'

async function settleUi(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

/* 侧栏考试切换卡 → reka Popover 内容 Teleport 到 body */
async function openExamSwitcher(host: HTMLElement): Promise<HTMLElement> {
  const trigger = host.querySelector<HTMLButtonElement>(
    '.exam-switcher__card, .exam-switcher__icon',
  )
  expect(trigger).not.toBeNull()
  trigger!.click()
  await settleUi()
  const popover = document.body.querySelector<HTMLElement>('.exam-switcher-popover')
  expect(popover).not.toBeNull()
  return popover!
}

function switcherRows(popover: ParentNode): HTMLElement[] {
  return [...popover.querySelectorAll<HTMLElement>('.exam-switcher-popover__row')]
}

function switcherRowNames(popover: ParentNode): (string | null | undefined)[] {
  return switcherRows(popover).map(
    row => row.querySelector('.exam-switcher-popover__row-name')?.textContent,
  )
}

/* wide=true → ≥1440px 展开模式（显示切换卡）；false → 图标轨 */
function stubWideViewport(matches: boolean): void {
  vi.stubGlobal('matchMedia', vi.fn((query: string) => ({
    matches: query === '(min-width: 1440px)' ? matches : false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })))
}

/* ≤900px → 抽屉模式 */
function stubNarrowViewport(): void {
  vi.stubGlobal('matchMedia', vi.fn((query: string) => ({
    matches: query === '(max-width: 900px)',
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })))
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

  it('renders product, page and current-exam context in the shell', async () => {
    stubWideViewport(true)
    const { app, host, initialize } = await mountShell()

    expect(initialize).toHaveBeenCalledTimes(1)
    expect(host.querySelector('[data-testid="app-shell"]')).not.toBeNull()
    expect(host.querySelector('[data-testid="app-shell"]')?.textContent).toContain('知衡')
    expect(host.querySelector('.app-sidebar__brand-copy small')?.textContent).toBe('教师教学工作台')
    expect(host.querySelector('[data-testid="app-topbar"]')?.textContent).toContain('考试批改')
    expect(host.querySelector('.app-sidebar__brand')).not.toBeNull()
    expect(host.querySelector('.app-sidebar__navigation')).not.toBeNull()
    expect(host.querySelector('.exam-switcher__card')).not.toBeNull()
    expect(host.querySelector('.exam-switcher__card')?.textContent).toContain('当前考试')
    expect(host.querySelector('.exam-switcher__card')?.textContent).toContain('未选择考试')
    expect(
      [...host.querySelectorAll<HTMLAnchorElement>('[data-testid="app-navigation"] a')].map(
        (link) => [link.textContent, link.getAttribute('href')],
      ),
    ).toEqual([
      ['考试配置', '/sessions'],
      ['考试批改', '/grading'],
      ['成绩中心', '/results'],
      ['题库管理', '/question-bank'],
      ['组卷工作台', '/question-assembly'],
      ['命题练习', '/authoring'],
      ['知识与训练', '/knowledge-overview'],
    ])
    expect(
      [...host.querySelectorAll<HTMLAnchorElement>('[data-testid="app-navigation"] a')].map(
        link => link.getAttribute('aria-label'),
      ),
    ).toEqual([
      '考试配置',
      '考试批改',
      '成绩中心',
      '题库管理',
      '组卷工作台',
      '命题练习',
      '知识与训练',
    ])
    expect(
      host.querySelector('[data-testid="app-navigation"] a[href="/grading"]')?.getAttribute(
        'aria-current',
      ),
    ).toBe('page')
    const popover = await openExamSwitcher(host)
    expect(popover.querySelector('label[for="current-curriculum-volume"]')?.textContent).toBe('教学学期')
    expect(popover.querySelector('#current-curriculum-volume')).not.toBeNull()
    expect([...host.querySelectorAll('nav a')].map((link) => link.textContent)).toEqual([
      '考试配置',
      '考试批改',
      '成绩中心',
      '题库管理',
      '组卷工作台',
      '命题练习',
      '知识与训练',
      '学生管理',
      '调用记录',
      '设置',
    ])
    expect(host.querySelector('[data-testid="navigation-toggle"]')).toBeNull()
    expect(host.querySelector('[data-testid="inspector-toggle"]')).toBeNull()

    app.unmount()
  })


  it('navigates between truthful destinations and updates the current page', async () => {
    const { app, host, router } = await mountShell()
    const brandLink = host.querySelector<HTMLAnchorElement>('.app-sidebar__brand')!
    expect(brandLink.getAttribute('href')).toBe('/workbench')

    brandLink.click()
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/workbench'))
    await settleUi()

    expect(
      host.querySelector('[data-testid="app-navigation"] a[href="/grading"]')?.hasAttribute(
        'aria-current',
      ),
    ).toBe(false)
    /* 页面过渡期间旧视图（page-leave-active）尚未卸载，等待其移除后取新页标题 */
    await vi.waitFor(() => {
      const headings = [...host.querySelectorAll<HTMLElement>('#main-workspace h1')]
      const current = headings.find(heading => heading.closest('.page-leave-active') === null)
      expect(document.activeElement).toBe(current)
    })

    app.unmount()
  })

  it('defines explicit responsive areas and token-only navigation states for shell blocks', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/styles/app-shell.css'), 'utf-8')
    const tabletStart = css.indexOf('@media (max-width: 900px)')
    const compactStart = css.indexOf('@media (max-width: 620px)')

    /* 顶栏精简后：无子导航时桌面零高度，仅保留跳过链接与可选子导航条 */
    expect(css).toMatch(/\.app-topbar\s*\{[^}]*grid-area:\s*topbar;/s)
    expect(css).toMatch(/\.app-topbar--with-tabs\s*\{[^}]*border-block-end/s)
    expect(css).toMatch(/\.app-topbar__workspace-navigation\s*\{/)
    expect(css).toMatch(/\.tp-workspace-tabs__pill\s*\{/)
    expect(css).toMatch(/\.app-topbar__skip-link\s*\{[^}]*position:\s*fixed;/s)
    expect(css).toMatch(/\.app-sidebar__link\s*\{/)
    expect(css).toMatch(/\.app-sidebar__link\[aria-current='page'\]\s*\{/)
    expect(css).toMatch(/\.app-sidebar__link:focus-visible/)
    expect(css).toMatch(/\.exam-switcher__card\s*\{/)
    expect(css).toMatch(/\.exam-switcher__icon\s*\{/)
    expect(css).toMatch(/\.exam-switcher-popover\s*\{/)
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
    expect(tabletRules).toMatch(/\.app-topbar__navigation-toggle\s*\{[^}]*display:\s*inline-grid/s)

    const compactRules = css.slice(compactStart)
    expect(compactRules).toContain('grid-template-columns: minmax(0, 1fr)')
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

  it('does not let the exam switcher silently discard config edits', async () => {
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

    const popover = await openExamSwitcher(host)
    const row = switcherRows(popover).find(el => el.textContent?.includes('考试二'))!
    row.click()
    await settleUi()

    /* 守卫拒绝：浮层保持打开、选择不变 */
    expect(document.body.querySelector('.exam-switcher-popover')).not.toBeNull()
    expect(sessionStore.selectedSessionId).toBe(7)
    expect(configStore.sessionId).toBe(7)
    expect(configStore.editorEdits[0]?.standard_answer).toBe('未保存答案')
    app.unmount()
  })

  it('switches exams through the switcher listbox when nothing is dirty', async () => {
    const { app, host } = await mountShell()
    const sessionStore = useSessionStore()
    const configStore = useConfigWorkspaceStore()
    sessionStore.sessions = [
      sessionSummary({ id: 7, name: '考试一' }),
      sessionSummary({ id: 9, name: '考试二' }),
    ]
    sessionStore.selectSession(7)
    configStore.selectSession(7)
    const loadWorkspace = vi
      .spyOn(configStore, 'loadSelectedSessionWorkspace')
      .mockResolvedValue()
    await settleUi()

    const popover = await openExamSwitcher(host)
    const row = switcherRows(popover).find(el => el.textContent?.includes('考试二'))!
    row.click()
    await settleUi()

    expect(sessionStore.selectedSessionId).toBe(9)
    expect(configStore.sessionId).toBe(9)
    expect(loadWorkspace).toHaveBeenCalledWith(9)
    expect(document.body.querySelector('.exam-switcher-popover')).toBeNull()
    app.unmount()
  })

  it('shows the current exam name, status and semester on the switcher card', async () => {
    stubWideViewport(true)
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
    } as never]
    sessionStore.sessions = [
      sessionSummary({ id: 7, name: '八年级上册期中', curriculum_volume_id: volumeId }),
    ]
    sessionStore.selectSession(7)
    await settleUi()

    const card = host.querySelector<HTMLElement>('.exam-switcher__card')!
    expect(card.textContent).toContain('八年级上册期中')
    expect(card.textContent).toContain('八年级上册')
    expect(card.textContent).toContain('待开始')

    /* 状态文字在标签行右侧，元信息行带完整 title */
    const labelRow = card.querySelector('.exam-switcher__label-row')!
    expect(labelRow.firstElementChild?.textContent?.trim()).toBe('当前考试')
    const status = labelRow.querySelector<HTMLElement>('.exam-switcher__status')!
    expect(status.textContent?.trim()).toBe('待开始')
    expect(status.dataset.tone).toBe('run')
    const meta = card.querySelector<HTMLElement>('.exam-switcher__meta')!
    expect(meta.getAttribute('title')).toBe(meta.textContent)
    app.unmount()
  })

  it('renders no status text on the card for unknown session statuses', async () => {
    stubWideViewport(true)
    const { app, host } = await mountShell()
    const sessionStore = useSessionStore()
    sessionStore.sessions = [
      sessionSummary({ id: 7, name: '八年级上册期中', status: 'archived' as never }),
    ]
    sessionStore.selectSession(7)
    await settleUi()

    const card = host.querySelector<HTMLElement>('.exam-switcher__card')!
    expect(card.querySelector('.exam-switcher__status')).toBeNull()
    expect(card.querySelector('.exam-switcher__label-row')?.textContent).toContain('当前考试')
    app.unmount()
  })

  it('focuses the search input on open and returns focus to the trigger on close', async () => {
    stubWideViewport(true)
    const { app, host } = await mountShell()
    const popover = await openExamSwitcher(host)

    const input = popover.querySelector<HTMLInputElement>('.exam-switcher-popover__search-input')!
    expect(document.activeElement).toBe(input)

    /* Esc 关闭后焦点回到当前 trigger（切换卡可能在模式翻转后重建过） */
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await settleUi()
    expect(document.body.querySelector('.exam-switcher-popover')).toBeNull()
    const trigger = host.querySelector<HTMLButtonElement>('.exam-switcher__card')!
    expect(document.activeElement).toBe(trigger)
    app.unmount()
  })

  it('sets --shell-sidebar-offset per sidebar mode and removes it on unmount', async () => {
    const rootStyle = document.documentElement.style

    stubWideViewport(true)
    const expanded = await mountShell()
    expect(expanded.host.querySelector('[data-testid="app-shell"]')!.classList).toContain('app-shell--expanded')
    expect(rootStyle.getPropertyValue('--shell-sidebar-offset')).toBe('180px')
    expanded.app.unmount()
    expect(rootStyle.getPropertyValue('--shell-sidebar-offset')).toBe('')

    stubWideViewport(false)
    const rail = await mountShell()
    expect(rail.host.querySelector('[data-testid="app-shell"]')!.classList).toContain('app-shell--rail')
    expect(rootStyle.getPropertyValue('--shell-sidebar-offset')).toBe('56px')
    rail.app.unmount()

    stubNarrowViewport()
    const drawer = await mountShell()
    expect(drawer.host.querySelector('[data-testid="app-shell"]')!.classList).toContain('app-shell--drawer')
    expect(rootStyle.getPropertyValue('--shell-sidebar-offset')).toBe('0px')
    drawer.app.unmount()
  })

  it('opens the session management drawer from the switcher footer', async () => {
    const { app, host } = await mountShell()
    const popover = await openExamSwitcher(host)
    const manageButton = [...popover.querySelectorAll<HTMLElement>('.exam-switcher-popover__footer-link')]
      .find(el => el.textContent?.includes('考试管理'))!
    manageButton.click()
    await settleUi()

    expect(document.body.querySelector('.exam-switcher-popover')).toBeNull()
    expect(document.body.querySelector('.session-management-drawer')).not.toBeNull()
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

    const popover = await openExamSwitcher(host)
    expect(switcherRowNames(popover)).toEqual([
      '不选择考试',
      '八年级上册期中',
    ])
    const otherButton = popover.querySelector<HTMLButtonElement>('.exam-switcher-popover__toggle')!
    expect(otherButton.textContent).toContain('其他学期 2')

    otherButton.click()
    await settleUi()
    expect(switcherRowNames(popover)).toEqual([
      '不选择考试',
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

    const popover = await openExamSwitcher(host)
    const scopeSelector = popover.querySelector<HTMLSelectElement>('#current-curriculum-volume')!
    scopeSelector.value = volumeId
    scopeSelector.dispatchEvent(new Event('change'))
    await settleUi()

    expect(curriculumScope.selectedVolumeId).toBe(volumeId)
    expect(sessionStore.selectedSessionId).toBeNull()
    expect(configStore.sessionId).toBeNull()
    expect(switcherRowNames(popover)).toEqual(['不选择考试', '七年级上册期中'])
    expect(popover.querySelector('.exam-switcher-popover__toggle')?.textContent).toContain('其他学期 1')
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

    const popover = await openExamSwitcher(host)
    const scopeSelector = popover.querySelector<HTMLSelectElement>('#current-curriculum-volume')!
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

      const popover = await openExamSwitcher(host)
      const row = switcherRows(popover).find(el => el.textContent?.includes('考试二'))!
      row.click()
      await settleUi()

      expect(alert).toHaveBeenCalledWith(expect.stringContaining('核对'))
      expect(document.body.querySelector('.exam-switcher-popover')).not.toBeNull()
      expect(sessionStore.selectedSessionId).toBe(7)
      expect(configStore.sessionId).toBe(7)
      app.unmount()
    },
  )
  it('focuses the route heading after navigation', async () => {
    const { app, host, router } = await mountShell({ path: '/design-system' })

    await router.push('/grading')
    await settleUi()

    /* 页面过渡期间旧视图（page-leave-active）尚未卸载，等待其移除后取新页标题 */
    await vi.waitFor(() => {
      const headings = [...host.querySelectorAll<HTMLElement>('#main-workspace h1')]
      const current = headings.find(heading => heading.closest('.page-leave-active') === null)
      expect(document.activeElement).toBe(current)
    })

    app.unmount()
  })

  it('shows a safe exam-list error in the switcher and retries through the store', async () => {
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
    const popover = await openExamSwitcher(host)
    const sessionAlert = [...popover.querySelectorAll<HTMLElement>('[role="alert"]')]
      .find(el => el.textContent?.includes('考试列表加载失败'))
    expect(sessionAlert).not.toBeNull()
    expect(popover.textContent).not.toContain('private network detail')
    expect(initialize).toHaveBeenCalledTimes(1)

    const retry = sessionAlert!.querySelector<HTMLButtonElement>('button')!
    retry.click()
    await settleUi()
    expect(initialize).toHaveBeenCalledTimes(2)

    app.unmount()
  })

  describe('wide sidebar', () => {
    it('starts expanded at ≥1440px, persists collapse, and restores it on remount', async () => {
      stubWideViewport(true)
      const first = await mountShell()
      const shell = first.host.querySelector('[data-testid="app-shell"]')!
      expect(shell.classList).toContain('app-shell--expanded')
      const toggle = first.host.querySelector<HTMLButtonElement>(
        '[data-testid="sidebar-collapse-toggle"]',
      )!
      expect(toggle.getAttribute('aria-expanded')).toBe('true')

      toggle.click()
      await settleUi()
      expect(shell.classList).toContain('app-shell--rail')
      expect(localStorage.getItem('zhiheng.sidebar.collapsed')).toBe('1')
      expect(toggle.getAttribute('aria-expanded')).toBe('false')
      first.app.unmount()

      const second = await mountShell()
      const secondShell = second.host.querySelector('[data-testid="app-shell"]')!
      expect(secondShell.classList).toContain('app-shell--rail')
      const secondToggle = second.host.querySelector<HTMLButtonElement>(
        '[data-testid="sidebar-collapse-toggle"]',
      )!
      secondToggle.click()
      await settleUi()
      expect(secondShell.classList).toContain('app-shell--expanded')
      expect(localStorage.getItem('zhiheng.sidebar.collapsed')).toBe('0')
      second.app.unmount()
    })

    it('hides the collapse toggle outside the wide breakpoint', async () => {
      stubWideViewport(false)
      const { app, host } = await mountShell()
      expect(host.querySelector('[data-testid="sidebar-collapse-toggle"]')).toBeNull()
      app.unmount()
    })
  })

  describe('rail sidebar', () => {
    it('renders the exam switcher as an icon trigger with an aria-label', async () => {
      stubWideViewport(false)
      const { app, host } = await mountShell()
      const sessionStore = useSessionStore()
      sessionStore.sessions = [sessionSummary({ id: 7, name: '考试一' })]
      sessionStore.selectSession(7)
      await settleUi()

      const shell = host.querySelector('[data-testid="app-shell"]')!
      expect(shell.classList).toContain('app-shell--rail')
      const icon = host.querySelector<HTMLButtonElement>('.exam-switcher__icon')
      expect(icon).not.toBeNull()
      expect(icon?.getAttribute('aria-label')).toBe('当前考试：考试一')
      expect(host.querySelector('.exam-switcher__card')).toBeNull()
      app.unmount()
    })

    it('shows the semester abbreviation under the rail icon only when the exam has a term', async () => {
      stubWideViewport(false)
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
        sessionSummary({ id: 7, name: '八上期中', curriculum_volume_id: volumeId }),
      ]
      await settleUi()

      expect(host.querySelector('.exam-switcher__term')).toBeNull()
      sessionStore.selectSession(7)
      await settleUi()
      expect(host.querySelector('.exam-switcher__term')?.textContent).toBe('八上')
      app.unmount()
    })
  })

  describe('task center in the sidebar', () => {
    it('renders the task-center row while tasks exist', async () => {
      const { app, host } = await mountShell()
      useJobStore().track({
        id: 71,
        job_type: 'grading_run',
        payload: {},
        result: {},
        status: 'running',
        progress: 0.4,
        stage: '',
        detail: '',
        error: null,
        cancel_requested: false,
        created_at: '2026-08-08T00:00:00Z',
        started_at: '2026-08-08T00:00:01Z',
        updated_at: '2026-08-08T00:00:02Z',
        finished_at: null,
      })
      await settleUi()

      const toggle = host.querySelector<HTMLButtonElement>('.workspace-ai-drawer-toggle')
      expect(toggle).not.toBeNull()
      expect(toggle?.textContent).toContain('任务中心')
      app.unmount()
    })
  })

  describe('command palette', () => {
    async function openPalette(): Promise<HTMLInputElement> {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true }))
      await settleUi()
      const input = document.body.querySelector<HTMLInputElement>('.command-palette__input')
      expect(input).not.toBeNull()
      return input!
    }

    function paletteItems(): HTMLElement[] {
      return [...document.body.querySelectorAll<HTMLElement>('.command-palette__item')]
    }

    async function typeQuery(input: HTMLInputElement, value: string): Promise<void> {
      input.value = value
      input.dispatchEvent(new Event('input', { bubbles: true }))
      await settleUi()
    }

    it('opens with Ctrl+K and navigates to the highlighted page item', async () => {
      const { app, router } = await mountShell({ path: '/workbench' })
      const input = await openPalette()

      await typeQuery(input, '批改')
      expect(paletteItems().map(item => item.textContent)).toEqual(
        expect.arrayContaining([expect.stringContaining('考试批改')]),
      )
      expect(paletteItems().map(item => item.textContent)).not.toEqual(
        expect.arrayContaining([expect.stringContaining('成绩中心')]),
      )

      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/grading?scope=all'))
      expect(document.body.querySelector('.command-palette')).toBeNull()
      app.unmount()
    })

    it('resolves the grading item to the selected session workspace', async () => {
      const { app, router } = await mountShell({ path: '/workbench' })
      const store = useSessionStore()
      store.sessions = [
        sessionSummary({ id: 7, name: '考试一' }),
        sessionSummary({ id: 9, name: '考试二' }),
      ]
      store.selectSession(7)
      await settleUi()
      const input = await openPalette()
      await typeQuery(input, '批改')

      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/sessions/7/grading-run'))
      app.unmount()
    })

    it('keeps the palette open when a guarded exam switch is declined', async () => {
      const { app } = await mountShell()
      const sessionStore = useSessionStore()
      const configStore = useConfigWorkspaceStore()
      sessionStore.sessions = [
        sessionSummary({ id: 7, name: '考试一' }),
        sessionSummary({ id: 9, name: '考试二' }),
      ]
      sessionStore.selectSession(7)
      configStore.selectSession(7)
      configStore.setEditor({
        session_id: 7, configured: true, revision: 'a'.repeat(64), rows: [],
        total_score: 0, issues: [], source: null,
      })
      configStore.updateEditor({ row_id: 'row-1', standard_answer: '未保存答案' })
      vi.stubGlobal('confirm', vi.fn(() => false))
      await settleUi()

      await openPalette()
      const item = paletteItems().find(el => el.textContent?.includes('考试二'))!
      item.click()
      await settleUi()

      expect(document.body.querySelector('.command-palette')).not.toBeNull()
      expect(sessionStore.selectedSessionId).toBe(7)
      expect(configStore.sessionId).toBe(7)
      app.unmount()
    })

    it('switches exams through the palette when nothing is dirty', async () => {
      const { app } = await mountShell()
      const sessionStore = useSessionStore()
      const configStore = useConfigWorkspaceStore()
      sessionStore.sessions = [
        sessionSummary({ id: 7, name: '考试一' }),
        sessionSummary({ id: 9, name: '考试二' }),
      ]
      sessionStore.selectSession(7)
      configStore.selectSession(7)
      const loadWorkspace = vi
        .spyOn(configStore, 'loadSelectedSessionWorkspace')
        .mockResolvedValue()
      await settleUi()

      await openPalette()
      const item = paletteItems().find(el => el.textContent?.includes('考试二'))!
      item.click()
      await settleUi()

      expect(sessionStore.selectedSessionId).toBe(9)
      expect(configStore.sessionId).toBe(9)
      expect(loadWorkspace).toHaveBeenCalledWith(9)
      expect(document.body.querySelector('.command-palette')).toBeNull()
      app.unmount()
    })
  })
})
