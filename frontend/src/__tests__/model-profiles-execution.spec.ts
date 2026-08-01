import { describe, expect, it } from 'vitest'

import {
  decodeModelExecutionStatus,
  decodeModelProfilesState,
  normalizeModelProfileInput,
} from '../api/model-profiles'

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

describe('model profile request speed contract', () => {
  it('accepts the public automatic execution settings', () => {
    const active = profile()
    expect(decodeModelProfilesState({
      profiles: [active],
      active_profile_name: '校内模型',
      active_profile: active,
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
})
