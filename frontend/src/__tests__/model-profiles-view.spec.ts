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
      if (keys.includes('content_generation') && keys.includes('grading')) {
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

    expect(host.querySelector('.settings-hub__section-heading h2')?.textContent).toBe('AI 服务')
    expect(host.textContent).toContain('高级设置：API 站点、密钥与请求速度')
    expect(host.textContent).not.toContain('班主任工作台')
    expect(host.querySelectorAll('.model-task-row')).toHaveLength(2)
    expect(host.querySelectorAll('.model-task-row select:disabled')).toHaveLength(2)
    expect(host.querySelectorAll('.model-task-row input:disabled')).toHaveLength(2)
  })


  it('renders the batch inference controls and saves them with the profile', async () => {
    const savedProfile = {
      name: '校内模型',
      base_url: 'https://example.test/v1',
      has_api_key: true,
      ocr_model: 'ocr',
      grading_model: 'grading',
      config_base_url: '',
      has_config_api_key: false,
      config_model: '',
      class_teacher_model: '',
      request_speed_mode: 'automatic' as const,
      max_concurrent_requests: 20,
      requests_per_minute: 1000,
      max_auto_retries: null,
      request_timeout_seconds: null,
      batch_enabled: false,
      batch_model: '',
      batch_base_url: '',
      has_batch_api_key: false,
    }
    const taskBindings = {
      content_generation: { profile_name: '校内模型', model: 'content-model' },
      grading: { profile_name: '校内模型', model: 'grading-model' },
    }
    vi.spyOn(modelProfilesApi, 'getState').mockResolvedValue({
      profiles: [savedProfile],
      active_profile_name: '校内模型',
      active_profile: savedProfile,
      task_bindings: taskBindings,
    })
    vi.spyOn(modelProfilesApi, 'getExecutionStatus').mockImplementation(
      () => new Promise(() => {}),
    )
    const saveSpy = vi.spyOn(modelProfilesApi, 'saveProfile').mockResolvedValue({
      profiles: [{ ...savedProfile, batch_enabled: true, batch_model: 'ep-bi-abc123' }],
      active_profile_name: '校内模型',
      active_profile: { ...savedProfile, batch_enabled: true, batch_model: 'ep-bi-abc123' },
      task_bindings: taskBindings,
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

    await vi.waitFor(() => {
      expect(host.querySelector<HTMLInputElement>('[name="batch-enabled"]')).toBeTruthy()
    })

    expect(host.textContent).toContain('批量推理（阅卷省钱模式）')
    const checkbox = host.querySelector<HTMLInputElement>('[name="batch-enabled"]')!
    expect(checkbox.checked).toBe(false)
    expect(host.querySelector('[name="batch-model"]')).toBeNull()

    checkbox.click()
    await settle()
    expect(checkbox.checked).toBe(true)

    const endpointInput = host.querySelector<HTMLInputElement>('[name="batch-model"]')
    expect(endpointInput).toBeTruthy()
    expect(endpointInput!.required).toBe(true)
    endpointInput!.value = 'ep-bi-abc123'
    endpointInput!.dispatchEvent(new Event('input', { bubbles: true }))
    await settle()

    const saveButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('保存配置'))!
    expect(saveButton.disabled).toBe(false)
    saveButton.click()
    await vi.waitFor(() => expect(saveSpy).toHaveBeenCalledTimes(1))
    expect(saveSpy.mock.calls[0]?.[1]).toMatchObject({
      batch_enabled: true,
      batch_model: 'ep-bi-abc123',
    })
  })

  it('renders the request timeout input and saves it with the profile', async () => {
    const savedProfile = {
      name: '校内模型',
      base_url: 'https://example.test/v1',
      has_api_key: true,
      ocr_model: 'ocr',
      grading_model: 'grading',
      config_base_url: '',
      has_config_api_key: false,
      config_model: '',
      class_teacher_model: '',
      request_speed_mode: 'automatic' as const,
      max_concurrent_requests: 20,
      requests_per_minute: 1000,
      max_auto_retries: null,
      request_timeout_seconds: 90,
      batch_enabled: false,
      batch_model: '',
      batch_base_url: '',
      has_batch_api_key: false,
    }
    const taskBindings = {
      content_generation: { profile_name: '校内模型', model: 'content-model' },
      grading: { profile_name: '校内模型', model: 'grading-model' },
    }
    vi.spyOn(modelProfilesApi, 'getState').mockResolvedValue({
      profiles: [savedProfile],
      active_profile_name: '校内模型',
      active_profile: savedProfile,
      task_bindings: taskBindings,
    })
    vi.spyOn(modelProfilesApi, 'getExecutionStatus').mockImplementation(
      () => new Promise(() => {}),
    )
    const saveSpy = vi.spyOn(modelProfilesApi, 'saveProfile').mockResolvedValue({
      profiles: [{ ...savedProfile, request_timeout_seconds: 45 }],
      active_profile_name: '校内模型',
      active_profile: { ...savedProfile, request_timeout_seconds: 45 },
      task_bindings: taskBindings,
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

    await vi.waitFor(() => {
      expect(host.querySelector<HTMLInputElement>('[name="request-timeout-seconds"]')).toBeTruthy()
    })

    expect(host.textContent).toContain('单次请求超时（秒）')
    expect(host.textContent).toContain('批量推理不受此设置影响')
    const timeoutInput = host.querySelector<HTMLInputElement>('[name="request-timeout-seconds"]')!
    expect(timeoutInput.value).toBe('90')

    timeoutInput.value = '45'
    timeoutInput.dispatchEvent(new Event('input', { bubbles: true }))
    await settle()

    const saveButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('保存配置'))!
    expect(saveButton.disabled).toBe(false)
    saveButton.click()
    await vi.waitFor(() => expect(saveSpy).toHaveBeenCalledTimes(1))
    expect(saveSpy.mock.calls[0]?.[1]).toMatchObject({
      request_timeout_seconds: 45,
    })
  })
})
