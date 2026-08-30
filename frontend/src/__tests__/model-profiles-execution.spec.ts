import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  decodeModelExecutionStatus,
  decodeModelProfilesState,
  normalizeModelProfileInput,
} from '../api/model-profiles'

afterEach(() => vi.restoreAllMocks())

function profile(overrides: Record<string, unknown> = {}) {
  return {
    name: '校内模型',
    base_url: 'https://example.test/v1',
    has_api_key: true,
    ocr_model: 'ocr',
    grading_model: 'grading',
    config_base_url: '',
    has_config_api_key: false,
    config_model: '',
    teaching_prep_model: '',
    class_teacher_model: '',
    request_speed_mode: 'automatic',
    max_concurrent_requests: 20,
    requests_per_minute: 1000,
    max_auto_retries: null,
    request_timeout_seconds: null,
    batch_enabled: false,
    batch_model: '',
    batch_base_url: '',
    has_batch_api_key: false,
    ...overrides,
  }
}

function taskBindings() {
  return {
    content_generation: { profile_name: '校内模型', model: 'content-model' },
    grading: { profile_name: '校内模型', model: 'grading-model' },
    teaching_prep: { profile_name: '校内模型', model: 'prep-model' },
    class_teacher: { profile_name: '校内模型', model: 'teacher-model' },
  }
}

describe('model profile request speed contract', () => {
  it('accepts the public automatic execution settings', () => {
    const active = profile()
    expect(decodeModelProfilesState({
      profiles: [active],
      active_profile_name: '校内模型',
      active_profile: active,
      task_bindings: taskBindings(),
    }).active_profile).toMatchObject({
      request_speed_mode: 'automatic',
      max_concurrent_requests: 20,
      requests_per_minute: 1000,
    })
  })

  it('normalizes a custom concurrency ceiling and RPM', () => {
    expect(normalizeModelProfileInput({
      name: '高并发模型',
      base_url: 'https://example.test/v1',
      api_key: 'secret',
      ocr_model: 'ocr',
      grading_model: 'grading',
      config_base_url: '',
      config_model: '',
      request_speed_mode: 'custom',
      max_concurrent_requests: 37,
      requests_per_minute: 10000,
    })).toMatchObject({
      request_speed_mode: 'custom',
      max_concurrent_requests: 37,
      requests_per_minute: 10000,
    })
  })

  it('rejects out-of-range custom concurrency', () => {
    expect(() => normalizeModelProfileInput({
      name: '高并发模型',
      base_url: 'https://example.test/v1',
      api_key: 'secret',
      ocr_model: 'ocr',
      grading_model: 'grading',
      config_base_url: '',
      config_model: '',
      request_speed_mode: 'custom',
      max_concurrent_requests: 101,
      requests_per_minute: 10000,
    })).toThrow('同时请求数')
  })

  it('normalizes the channel auto retry budget and accepts 0', () => {
    expect(normalizeModelProfileInput({
      name: '高并发模型',
      base_url: 'https://example.test/v1',
      api_key: 'secret',
      ocr_model: 'ocr',
      grading_model: 'grading',
      config_base_url: '',
      config_model: '',
      request_speed_mode: 'custom',
      max_concurrent_requests: 20,
      requests_per_minute: 1000,
      max_auto_retries: 0,
    })).toMatchObject({ max_auto_retries: 0 })

    expect(normalizeModelProfileInput({
      name: '高并发模型',
      base_url: 'https://example.test/v1',
      api_key: 'secret',
      ocr_model: 'ocr',
      grading_model: 'grading',
      config_base_url: '',
      config_model: '',
      request_speed_mode: 'custom',
      max_concurrent_requests: 20,
      requests_per_minute: 1000,
      max_auto_retries: 5,
    })).toMatchObject({ max_auto_retries: 5 })
  })

  it('rejects out-of-range auto retry budgets', () => {
    expect(() => normalizeModelProfileInput({
      name: '高并发模型',
      base_url: 'https://example.test/v1',
      api_key: 'secret',
      ocr_model: 'ocr',
      grading_model: 'grading',
      config_base_url: '',
      config_model: '',
      request_speed_mode: 'custom',
      max_concurrent_requests: 20,
      requests_per_minute: 1000,
      max_auto_retries: 6,
    })).toThrow('允许自动重试次数')
  })

  it('normalizes the request timeout within 30–600 and keeps null', () => {
    const base = {
      name: '高并发模型',
      base_url: 'https://example.test/v1',
      api_key: 'secret',
      ocr_model: 'ocr',
      grading_model: 'grading',
      config_base_url: '',
      config_model: '',
      request_speed_mode: 'custom' as const,
      max_concurrent_requests: 20,
      requests_per_minute: 1000,
    }
    expect(normalizeModelProfileInput({
      ...base,
      request_timeout_seconds: 30,
    })).toMatchObject({ request_timeout_seconds: 30 })
    expect(normalizeModelProfileInput({
      ...base,
      request_timeout_seconds: 600,
    })).toMatchObject({ request_timeout_seconds: 600 })
    expect(normalizeModelProfileInput({
      ...base,
      request_timeout_seconds: null,
    })).toMatchObject({ request_timeout_seconds: null })
  })

  it('rejects out-of-range request timeouts', () => {
    const base = {
      name: '高并发模型',
      base_url: 'https://example.test/v1',
      api_key: 'secret',
      ocr_model: 'ocr',
      grading_model: 'grading',
      config_base_url: '',
      config_model: '',
      request_speed_mode: 'custom' as const,
      max_concurrent_requests: 20,
      requests_per_minute: 1000,
    }
    expect(() => normalizeModelProfileInput({
      ...base,
      request_timeout_seconds: 29,
    })).toThrow('单次请求超时')
    expect(() => normalizeModelProfileInput({
      ...base,
      request_timeout_seconds: 601,
    })).toThrow('单次请求超时')
    expect(() => normalizeModelProfileInput({
      ...base,
      request_timeout_seconds: 45.5,
    })).toThrow('单次请求超时')
  })

  it('sends explicit null when the request timeout is cleared', async () => {
    const { modelProfilesApi } = await import('../api/model-profiles')
    const active = profile({ request_timeout_seconds: null })
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(
      JSON.stringify({
        profiles: [active],
        active_profile_name: '校内模型',
        active_profile: active,
        task_bindings: taskBindings(),
      }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ))

    await modelProfilesApi.saveProfile('校内模型', {
      name: '校内模型',
      base_url: 'https://example.test/v1',
      api_key: 'secret',
      ocr_model: 'ocr',
      grading_model: 'grading',
      config_base_url: '',
      config_model: '',
      request_speed_mode: 'automatic',
      max_concurrent_requests: 20,
      requests_per_minute: 1000,
      request_timeout_seconds: null,
    })
    const body = JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))
    expect(body).toHaveProperty('request_timeout_seconds', null)

    await modelProfilesApi.saveProfile('校内模型', {
      name: '校内模型',
      base_url: 'https://example.test/v1',
      api_key: 'secret',
      ocr_model: 'ocr',
      grading_model: 'grading',
      config_base_url: '',
      config_model: '',
      request_speed_mode: 'automatic',
      max_concurrent_requests: 20,
      requests_per_minute: 1000,
      request_timeout_seconds: 90,
    })
    const secondBody = JSON.parse(String(fetchSpy.mock.calls[1]?.[1]?.body))
    expect(secondBody).toHaveProperty('request_timeout_seconds', 90)
  })

  it('sends explicit null when the auto retry budget is cleared', async () => {
    const { modelProfilesApi } = await import('../api/model-profiles')
    const active = profile({ max_auto_retries: null })
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(
      JSON.stringify({
        profiles: [active],
        active_profile_name: '校内模型',
        active_profile: active,
        task_bindings: taskBindings(),
      }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ))

    await modelProfilesApi.saveProfile('校内模型', {
      name: '校内模型',
      base_url: 'https://example.test/v1',
      api_key: 'secret',
      ocr_model: 'ocr',
      grading_model: 'grading',
      config_base_url: '',
      config_model: '',
      request_speed_mode: 'automatic',
      max_concurrent_requests: 20,
      requests_per_minute: 1000,
      max_auto_retries: null,
    })
    const body = JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))
    expect(body).toHaveProperty('max_auto_retries', null)

    await modelProfilesApi.saveProfile('校内模型', {
      name: '校内模型',
      base_url: 'https://example.test/v1',
      api_key: 'secret',
      ocr_model: 'ocr',
      grading_model: 'grading',
      config_base_url: '',
      config_model: '',
      request_speed_mode: 'automatic',
      max_concurrent_requests: 20,
      requests_per_minute: 1000,
      max_auto_retries: 2,
    })
    const secondBody = JSON.parse(String(fetchSpy.mock.calls[1]?.[1]?.body))
    expect(secondBody).toHaveProperty('max_auto_retries', 2)
  })

  it('accepts a safe shared runtime status without exposing profile secrets', () => {
    expect(decodeModelExecutionStatus({
      mode: 'automatic',
      configured_max_in_flight: 20,
      effective_max_in_flight: 6,
      requests_per_minute: 1000,
      active: 4,
      queued: 96,
      peak_active: 6,
      physical_request_count: 120,
      limiting_reason: 'configured',
    })).toEqual({
      mode: 'automatic',
      configured_max_in_flight: 20,
      effective_max_in_flight: 6,
      requests_per_minute: 1000,
      active: 4,
      queued: 96,
      peak_active: 6,
      physical_request_count: 120,
      limiting_reason: 'configured',
    })
  })

  it('saves four work bindings without returning or sending API keys', async () => {
    const { modelProfilesApi } = await import('../api/model-profiles')
    const active = profile()
    const bindings = taskBindings()
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify({
        profiles: [active],
        active_profile_name: '校内模型',
        active_profile: active,
        task_bindings: bindings,
      }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ))

    await expect(modelProfilesApi.saveTaskBindings(bindings)).resolves.toMatchObject({
      task_bindings: bindings,
    })
    expect(String(fetchSpy.mock.calls[0]?.[0])).toBe('/api/model-profiles/routing/task-bindings')
    expect(JSON.stringify(fetchSpy.mock.calls[0]?.[1]?.body)).not.toContain('api_key')
  })

  it('accepts a profile with batch inference enabled', () => {
    const active = profile({
      batch_enabled: true,
      batch_model: 'ep-bi-abc123',
      batch_base_url: 'https://ark.cn-beijing.volces.com/api/v3/batch',
      has_batch_api_key: true,
    })
    expect(decodeModelProfilesState({
      profiles: [active],
      active_profile_name: '校内模型',
      active_profile: active,
      task_bindings: taskBindings(),
    }).active_profile).toMatchObject({
      batch_enabled: true,
      batch_model: 'ep-bi-abc123',
      batch_base_url: 'https://ark.cn-beijing.volces.com/api/v3/batch',
      has_batch_api_key: true,
    })
  })

  it('rejects enabling batch inference without an endpoint id', () => {
    expect(() => normalizeModelProfileInput({
      name: '校内模型',
      base_url: 'https://example.test/v1',
      api_key: 'secret',
      ocr_model: 'ocr',
      grading_model: 'grading',
      config_base_url: '',
      config_model: '',
      request_speed_mode: 'automatic',
      max_concurrent_requests: 20,
      requests_per_minute: 1000,
      batch_enabled: true,
      batch_model: '  ',
    })).toThrow('启用批量推理时请填写批量推理接入点 ID')
  })

  it('sends batch settings in the save body and only non-empty secrets', async () => {
    const { modelProfilesApi } = await import('../api/model-profiles')
    const active = profile({ batch_enabled: true, batch_model: 'ep-bi-abc123' })
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify({
        profiles: [active],
        active_profile_name: '校内模型',
        active_profile: active,
        task_bindings: taskBindings(),
      }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ))

    await modelProfilesApi.saveProfile('校内模型', {
      name: '校内模型',
      base_url: 'https://example.test/v1',
      api_key: 'secret',
      ocr_model: 'ocr',
      grading_model: 'grading',
      config_base_url: '',
      config_model: '',
      request_speed_mode: 'automatic',
      max_concurrent_requests: 20,
      requests_per_minute: 1000,
      batch_enabled: true,
      batch_model: 'ep-bi-abc123',
    })
    const body = JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))
    expect(body.batch_enabled).toBe(true)
    expect(body.batch_model).toBe('ep-bi-abc123')
    expect('batch_base_url' in body).toBe(false)
    expect('batch_api_key' in body).toBe(false)
  })
})
