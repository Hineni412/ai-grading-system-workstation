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
})
