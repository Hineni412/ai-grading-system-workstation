import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { createApp, nextTick, type App as VueApp } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { modelProfilesApi } from '../api/model-profiles'
import { opsApi } from '../api/ops'
import { aiDiagnosticsApi } from '../api/ai-diagnostics'
import { workspaceAITaskApi } from '../workspaces/shared/ai-tasks/api'
import SettingsHubView from '../views/SettingsHubView.vue'

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
  const app = createApp(SettingsHubView)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  mounted.push(app)
  await vi.waitFor(() => expect(host.querySelector('.settings-hub__section-heading h2')).toBeTruthy())
  await Promise.resolve()
  await nextTick()
  return { host, router }
}

beforeEach(() => {
  localStorage.clear()
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
  vi.spyOn(workspaceAITaskApi, 'list').mockResolvedValue([])
})

afterEach(() => {
  mounted.splice(0).forEach((app) => app.unmount())
  document.body.replaceChildren()
  vi.restoreAllMocks()
})

describe('SettingsHubView', () => {
  it('keeps mutually exclusive settings sections out of the same page file', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/views/SettingsHubView.vue'), 'utf-8')

    expect(source).not.toContain("import ModelProfilesView from './ModelProfilesView.vue'")
    expect(source).not.toContain("import SettingsOpsView from './SettingsOpsView.vue'")
    expect(source).toContain("() => import('./ModelProfilesView.vue')")
    expect(source).toContain("() => import('./SettingsOpsView.vue')")
    expect(source).toContain("() => import('../components/settings/AiDiagnosticsPanel.vue')")
  })

  it.each([
    ['/settings', 'AI 服务'],
    ['/settings?section=ai-trace', 'AI 调用记录'],
    ['/settings?section=backup', '备份与恢复'],
    ['/settings?section=maintenance', '检查与维护'],
  ])('opens %s at the same requested section', async (path, heading) => {
    const { host } = await mountAt(path)

    expect(host.querySelector('.settings-hub__section-heading h2')?.textContent).toBe(heading)
  })

  it('keeps an unsaved AI service draft in place when the teacher declines to leave', async () => {
    const { host, router } = await mountAt('/settings')
    await vi.waitFor(() => expect(host.querySelector('[name="profile-name"]')).toBeTruthy(), { timeout: 5000 })
    const input = host.querySelector<HTMLInputElement>('[name="profile-name"]')!
    input.value = '尚未保存的站点'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    const confirm = vi.spyOn(globalThis, 'confirm').mockReturnValue(false)

    const backupButton = [...host.querySelectorAll<HTMLButtonElement>('.settings-hub__menu button')]
      .find((button) => button.textContent?.includes('备份与恢复'))!
    backupButton.click()
    await nextTick()

    expect(confirm).toHaveBeenCalledOnce()
    expect(router.currentRoute.value.query.section).not.toBe('backup')
    expect(host.querySelector('.settings-hub__section-heading h2')?.textContent).toBe('AI 服务')
  })

  it('keeps the original section switch when the teacher accepts losing an unsaved draft', async () => {
    const { host, router } = await mountAt('/settings')
    await vi.waitFor(() => expect(host.querySelector('[name="profile-name"]')).toBeTruthy(), { timeout: 5000 })
    const input = host.querySelector<HTMLInputElement>('[name="profile-name"]')!
    input.value = '尚未保存的站点'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    const confirm = vi.spyOn(globalThis, 'confirm').mockReturnValue(true)

    const maintenanceButton = [...host.querySelectorAll<HTMLButtonElement>('.settings-hub__menu button')]
      .find((button) => button.textContent?.includes('检查与维护'))!
    maintenanceButton.click()

    await vi.waitFor(() => {
      expect(router.currentRoute.value.query.section).toBe('maintenance')
      expect(host.querySelector('.settings-hub__section-heading h2')?.textContent).toBe('检查与维护')
    })
    expect(confirm).toHaveBeenCalledOnce()
  })
})
