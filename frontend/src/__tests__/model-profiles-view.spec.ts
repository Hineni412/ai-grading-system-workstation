import { createApp, nextTick, type App as VueApp } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../App.vue'
import { modelProfilesApi } from '../api/model-profiles'
import { createAppRouter } from '../router'

vi.mock('../api/sessions', () => ({
  fetchSessions: vi.fn(async () => []),
}))

const mounted: VueApp[] = []

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

afterEach(() => {
  mounted.splice(0).forEach((app) => app.unmount())
  document.body.replaceChildren()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('ModelProfilesView', () => {
  it('opens normally before the first API site is configured', async () => {
    const nativeStructuredClone = structuredClone
    let taskBindingCloneCount = 0
    vi.stubGlobal('structuredClone', (value: unknown) => {
      const keys = value !== null && typeof value === 'object'
        ? Object.keys(value)
        : []
      if (keys.includes('content_generation') && keys.includes('class_teacher')) {
        taskBindingCloneCount += 1
      }
      if (taskBindingCloneCount === 2) {
        throw new DOMException('Reactive objects cannot be cloned', 'DataCloneError')
      }
      return nativeStructuredClone(value)
    })
    vi.spyOn(modelProfilesApi, 'getState').mockResolvedValue({
      profiles: [],
      active_profile_name: null,
      active_profile: null,
      task_bindings: {
        content_generation: { profile_name: null, model: '' },
        grading: { profile_name: null, model: '' },
        teaching_prep: { profile_name: null, model: '' },
        class_teacher: { profile_name: null, model: '' },
      },
    })

    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createAppRouter(createMemoryHistory())
    await router.push('/model-profiles')
    await router.isReady()
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(App)
    app.use(pinia)
    app.use(router)
    app.mount(host)
    mounted.push(app)

    await settle()
    await vi.waitFor(() => {
      expect(host.textContent).toContain('请先在下方新增一个 API 站点')
    })

    expect(host.querySelector('h1')?.textContent).toBe('API 站点与工作模型')
    expect(host.querySelectorAll('.model-task-row')).toHaveLength(4)
    expect(host.querySelectorAll('.model-task-row select:disabled')).toHaveLength(4)
    expect(host.querySelectorAll('.model-task-row input:disabled')).toHaveLength(4)
  })
})
