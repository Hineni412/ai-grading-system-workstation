
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App as VueApp } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'

import { aiDiagnosticsApi } from '../api/ai-diagnostics'
import { modelProfilesApi } from '../api/model-profiles'
import { studentRosterApi } from '../api/students'
import { opsApi } from '../api/ops'
import SettingsHubView from '../views/SettingsHubView.vue'
import ConfirmDialogHost from '../components/design-system/ConfirmDialogHost.vue'

const mounted: VueApp[] = []

const emptyModelState = {
  profiles: [],
  active_profile_name: null,
  active_profile: null,
  task_bindings: {
    content_generation: { profile_name: null, model: '' },
    grading: { profile_name: null, model: '' },
  },
}

async function mountAt(path: string) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/settings', component: SettingsHubView }],
  })
  await router.push(path)
  await router.isReady()
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ render: () => h(RouterView) })
  app.use(pinia)
  app.use(router)
  app.mount(host)
  mounted.push(app)
  await vi.waitFor(() => expect(host.querySelector('.page-tabs')).toBeTruthy())
  await Promise.resolve()
  await nextTick()
  return { host, router }
}

beforeEach(() => {
  localStorage.clear()
  vi.spyOn(studentRosterApi, 'getWorkspace').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50, total_pages: 0, class_names: [], roster_revision: 'a'.repeat(64) })
  vi.spyOn(modelProfilesApi, 'getState').mockResolvedValue(emptyModelState)
  vi.spyOn(opsApi, 'getSelfCheck').mockImplementation(() => new Promise(() => {}))
  vi.spyOn(opsApi, 'getBackups').mockImplementation(() => new Promise(() => {}))
  vi.spyOn(aiDiagnosticsApi, 'list').mockResolvedValue({
    items: [],
    returned: 0,
    matching: 0,
    scanned_event_count: 0,
    truncated: false,
  })
})

afterEach(() => {
  mounted.splice(0).forEach((app) => app.unmount())
  document.body.replaceChildren()
  vi.restoreAllMocks()
})

describe('SettingsHubView', () => {
  it('defaults to students and places three tabs in the title row', async () => {
    const { host, router } = await mountAt('/settings')
    await vi.waitFor(() => expect(router.currentRoute.value.query.section).toBe('students'))
    expect([...host.querySelectorAll('.page-tabs button')].map(button => button.textContent)).toEqual(['学生名单', 'AI 服务', '数据与空间'])
    expect(host.querySelector('.page-header .page-tabs')).not.toBeNull()
  })
  it.each([['backup', 'data'], ['maintenance', 'students'], ['system', 'students'], ['models', 'ai'], ['ai-trace', 'ai']])('maps old %s to %s', async (old, section) => {
    const { host, router } = await mountAt(`/settings?section=${old}`)
    await vi.waitFor(() => expect(router.currentRoute.value.query.section).toBe(section))
    expect(router.currentRoute.value.hash).toBe(old === 'ai-trace' ? '#ai-call-log' : '')
    expect(host.textContent).not.toContain('系统状态')
    expect(opsApi.getSelfCheck).not.toHaveBeenCalled()
  })
  it('falls back to students when the remembered tab was removed', async () => {
    localStorage.setItem('ai-grading:settings-section:v2', 'system')
    const { router } = await mountAt('/settings')
    await vi.waitFor(() => expect(router.currentRoute.value.query.section).toBe('students'))
    expect(opsApi.getSelfCheck).not.toHaveBeenCalled()
  })
  it('loads AI call records only when their disclosure is opened', async () => {
    const { host } = await mountAt('/settings?section=ai')
    await vi.waitFor(() => expect(host.querySelector('#ai-call-log')).not.toBeNull(), { timeout: 5000 })
    const disclosure = host.querySelector<HTMLDetailsElement>('#ai-call-log')!
    expect(disclosure.open).toBe(false)
    expect(aiDiagnosticsApi.list).not.toHaveBeenCalled()
    disclosure.open = true
    disclosure.dispatchEvent(new Event('toggle'))
    await vi.waitFor(() => expect(aiDiagnosticsApi.list).toHaveBeenCalledOnce())
    expect(host.querySelector('.ai-diagnostics')).not.toBeNull()
    expect(opsApi.getSelfCheck).not.toHaveBeenCalled()
  })
  it.each(['ai', 'system', 'maintenance'])('opens AI call records from a %s bookmark', async section => {
    const { host, router } = await mountAt(`/settings?section=${section}#ai-call-log`)
    await vi.waitFor(() => expect(host.querySelector<HTMLDetailsElement>('#ai-call-log')?.open).toBe(true))
    expect(router.currentRoute.value.query.section).toBe('ai')
    await vi.waitFor(() => expect(aiDiagnosticsApi.list).toHaveBeenCalledOnce())
    expect(opsApi.getSelfCheck).not.toHaveBeenCalled()
  })
  it('keeps an unsaved service draft when leaving is declined', async () => {
    const { host, router } = await mountAt('/settings?section=ai')
    await vi.waitFor(() => expect(host.textContent).toContain('添加服务'), { timeout: 5000 })
    const add = [...host.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === '添加服务')!
    add.click()
    await vi.waitFor(() => expect(document.querySelector('[name="profile-name"]')).not.toBeNull())
    const input = document.querySelector<HTMLInputElement>('[name="profile-name"]')!
    input.value = '尚未保存的服务'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    const confirmEl = document.createElement('div')
    document.body.appendChild(confirmEl)
    const confirmApp = createApp(ConfirmDialogHost)
    confirmApp.mount(confirmEl)
    mounted.push(confirmApp)
    void router.replace('/settings?section=data')
    await vi.waitFor(() => expect(document.body.querySelector('[data-testid="app-confirm-dialog"]')).not.toBeNull())
    const dialog = document.body.querySelector('[data-testid="app-confirm-dialog"]')!
    expect(dialog.textContent).toContain('离开 AI 服务设置？')
    const cancel = [...dialog.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent?.trim() === '取消')!
    cancel.click()
    await vi.waitFor(() => expect(document.body.querySelector('[data-testid="app-confirm-dialog"]')).toBeNull())
    await nextTick()
    expect(router.currentRoute.value.query.section).toBe('ai')
    expect(input.value).toBe('尚未保存的服务')
  })
})
