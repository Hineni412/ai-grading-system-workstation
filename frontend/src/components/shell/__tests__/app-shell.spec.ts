
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick } from 'vue';
import { createMemoryHistory } from 'vue-router';

import type { SessionSummary } from '../../../api/sessions';
import AppShell from '../../../layouts/AppShell.vue'
import { createAppRouter } from '../../../router';
import { useConfigWorkspaceStore } from '../../../stores/config-workspace';

import { useReviewDraftStore } from '../../../stores/review-drafts';
import { useSessionStore } from '../../../stores/session';

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

/* wide=true → ≥1440px 展开模式（显示切换卡）；false → 图标轨 */

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
      if (kind === 'generation') configStore.markJobSubmissionPending('1'.repeat(32), 'retry')
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

  })
})
