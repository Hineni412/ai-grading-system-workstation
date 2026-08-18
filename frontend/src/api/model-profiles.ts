import { apiClient } from './client'
import { isRecord } from './validation'

export const MODEL_PROFILE_LIMITS = {
  name: 80,
  url: 2048,
  model: 200,
  apiKey: 8192,
  concurrentRequests: 100,
  requestsPerMinute: 10000,
} as const

export type RequestSpeedMode = 'automatic' | 'conservative' | 'custom'
export type ModelTaskKey =
  | 'content_generation'
  | 'grading'
  | 'teaching_prep'
  | 'class_teacher'

export interface ModelTaskBinding {
  profile_name: string | null
  model: string
}

export type ModelTaskBindings = Record<ModelTaskKey, ModelTaskBinding>

export function copyModelTaskBindings(
  bindings: ModelTaskBindings,
): ModelTaskBindings {
  return {
    content_generation: { ...bindings.content_generation },
    grading: { ...bindings.grading },
    teaching_prep: { ...bindings.teaching_prep },
    class_teacher: { ...bindings.class_teacher },
  }
}

export interface ModelProfile {
  name: string
  base_url: string
  has_api_key: boolean
  ocr_model: string
  grading_model: string
  config_base_url: string
  has_config_api_key: boolean
  config_model: string
  teaching_prep_model: string
  class_teacher_model: string
  request_speed_mode: RequestSpeedMode
  max_concurrent_requests: number
  requests_per_minute: number
  batch_enabled: boolean
  batch_model: string
  batch_base_url: string
  has_batch_api_key: boolean
}

export interface ModelProfilesState {
  profiles: ModelProfile[]
  active_profile_name: string | null
  active_profile: ModelProfile | null
  task_bindings: ModelTaskBindings
}

export type ModelExecutionLimitingReason =
  | 'configured'
  | 'provider_overload'
  | 'recovering'

export interface ModelExecutionStatus {
  mode: RequestSpeedMode
  configured_max_in_flight: number
  effective_max_in_flight: number
  requests_per_minute: number
  active: number
  queued: number
  peak_active: number
  physical_request_count: number
  limiting_reason: ModelExecutionLimitingReason
}

export interface ModelProfileUpsertInput {
  name: string
  base_url: string
  api_key?: string
  ocr_model: string
  grading_model: string
  config_base_url: string
  config_api_key?: string
  config_model: string
  teaching_prep_model?: string
  class_teacher_model?: string
  request_speed_mode: RequestSpeedMode
  max_concurrent_requests: number
  requests_per_minute: number
  batch_enabled?: boolean
  batch_model?: string
  batch_base_url?: string
  batch_api_key?: string
}

export interface ModelProfileSaveOptions {
  requireApiKey?: boolean
  signal?: AbortSignal
}

export class ModelProfileInputError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ModelProfileInputError'
  }
}

const PROFILE_KEYS = [
  'name',
  'base_url',
  'has_api_key',
  'ocr_model',
  'grading_model',
  'config_base_url',
  'has_config_api_key',
  'config_model',
  'teaching_prep_model',
  'class_teacher_model',
  'request_speed_mode',
  'max_concurrent_requests',
  'requests_per_minute',
  'batch_enabled',
  'batch_model',
  'batch_base_url',
  'has_batch_api_key',
] as const

function hasExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value)
  return actual.length === expected.length
    && expected.every((key) => Object.prototype.hasOwnProperty.call(value, key))
}

function normalizeKeyName(key: string): string {
  return key
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1_$2')
    .replace(/[^a-z0-9]+/gi, '_')
    .toLowerCase()
}

function assertNoReturnedSecrets(value: unknown): void {
  if (Array.isArray(value)) {
    value.forEach(assertNoReturnedSecrets)
    return
  }
  if (!isRecord(value)) return
  for (const [key, child] of Object.entries(value)) {
    const normalized = normalizeKeyName(key)
    const compact = normalized.replace(/_/g, '')
    if (compact === 'apikey' || compact === 'configapikey') {
      throw new Error('Model profile response exposed a secret field')
    }
    assertNoReturnedSecrets(child)
  }
}

function isBoundedText(
  value: unknown,
  minimum: number,
  maximum: number,
): value is string {
  return typeof value === 'string'
    && value.length >= minimum
    && value.length <= maximum
    && !/[\u0000-\u001f\u007f]/.test(value)
}

function isHttpUrl(value: string, allowBlank: boolean): boolean {
  if (allowBlank && value === '') return true
  try {
    const parsed = new URL(value)
    return (parsed.protocol === 'http:' || parsed.protocol === 'https:')
      && parsed.username === ''
      && parsed.password === ''
      && parsed.search === ''
      && parsed.hash === ''
  } catch {
    return false
  }
}

function isModelProfile(value: unknown): value is ModelProfile {
  if (!isRecord(value) || !hasExactKeys(value, PROFILE_KEYS)) return false
  return (
    isBoundedText(value.name, 1, MODEL_PROFILE_LIMITS.name)
    && isBoundedText(value.base_url, 0, MODEL_PROFILE_LIMITS.url)
    && isHttpUrl(value.base_url, true)
    && typeof value.has_api_key === 'boolean'
    && isBoundedText(value.ocr_model, 0, MODEL_PROFILE_LIMITS.model)
    && isBoundedText(value.grading_model, 0, MODEL_PROFILE_LIMITS.model)
    && isBoundedText(value.config_base_url, 0, MODEL_PROFILE_LIMITS.url)
    && isHttpUrl(value.config_base_url, true)
    && typeof value.has_config_api_key === 'boolean'
    && isBoundedText(value.config_model, 0, MODEL_PROFILE_LIMITS.model)
    && isBoundedText(value.teaching_prep_model, 0, MODEL_PROFILE_LIMITS.model)
    && isBoundedText(value.class_teacher_model, 0, MODEL_PROFILE_LIMITS.model)
    && (
      value.request_speed_mode === 'automatic'
      || value.request_speed_mode === 'conservative'
      || value.request_speed_mode === 'custom'
    )
    && Number.isInteger(value.max_concurrent_requests)
    && Number(value.max_concurrent_requests) >= 1
    && Number(value.max_concurrent_requests)
      <= MODEL_PROFILE_LIMITS.concurrentRequests
    && Number.isInteger(value.requests_per_minute)
    && Number(value.requests_per_minute) >= 1
    && Number(value.requests_per_minute)
      <= MODEL_PROFILE_LIMITS.requestsPerMinute
    && typeof value.batch_enabled === 'boolean'
    && isBoundedText(value.batch_model, 0, MODEL_PROFILE_LIMITS.model)
    && isBoundedText(value.batch_base_url, 0, MODEL_PROFILE_LIMITS.url)
    && isHttpUrl(value.batch_base_url, true)
    && typeof value.has_batch_api_key === 'boolean'
  )
}

function profilesMatch(left: ModelProfile, right: ModelProfile): boolean {
  return PROFILE_KEYS.every((key) => left[key] === right[key])
}

export function decodeModelProfilesState(value: unknown): ModelProfilesState {
  assertNoReturnedSecrets(value)
  if (
    !isRecord(value)
    || !hasExactKeys(value, [
      'profiles',
      'active_profile_name',
      'active_profile',
      'task_bindings',
    ])
    || !Array.isArray(value.profiles)
    || !value.profiles.every(isModelProfile)
    || !(value.active_profile === null || isModelProfile(value.active_profile))
    || !isModelTaskBindings(value.task_bindings)
    || !(
      value.active_profile_name === null
      || isBoundedText(
        value.active_profile_name,
        1,
        MODEL_PROFILE_LIMITS.name,
      )
    )
  ) {
    throw new Error('Invalid model profile state')
  }

  const profiles = value.profiles as ModelProfile[]
  const activeProfileName = value.active_profile_name as string | null
  const activeProfile = value.active_profile as ModelProfile | null
  const names = new Set(profiles.map(({ name }) => name))
  const listedActiveProfile = activeProfileName === null
    ? null
    : profiles.find(({ name }) => name === activeProfileName) ?? null
  if (
    names.size !== profiles.length
    || (activeProfileName === null) !== (activeProfile === null)
    || (listedActiveProfile === null && activeProfileName !== null)
    || (
      listedActiveProfile !== null
      && activeProfile !== null
      && !profilesMatch(listedActiveProfile, activeProfile)
    )
  ) {
    throw new Error('Inconsistent model profile state')
  }

  return {
    profiles: profiles.map((profile) => ({ ...profile })),
    active_profile_name: activeProfileName,
    active_profile: activeProfile === null ? null : { ...activeProfile },
    task_bindings: copyModelTaskBindings(value.task_bindings as ModelTaskBindings),
  }
}

function isModelTaskBindings(value: unknown): value is ModelTaskBindings {
  if (!isRecord(value) || !hasExactKeys(value, [
    'content_generation', 'grading', 'teaching_prep', 'class_teacher',
  ])) return false
  return Object.values(value).every((binding) => (
    isRecord(binding)
    && hasExactKeys(binding, ['profile_name', 'model'])
    && (binding.profile_name === null || isBoundedText(binding.profile_name, 1, MODEL_PROFILE_LIMITS.name))
    && isBoundedText(binding.model, 0, MODEL_PROFILE_LIMITS.model)
  ))
}

const EXECUTION_STATUS_KEYS = [
  'mode',
  'configured_max_in_flight',
  'effective_max_in_flight',
  'requests_per_minute',
  'active',
  'queued',
  'peak_active',
  'physical_request_count',
  'limiting_reason',
] as const

function isNonnegativeInteger(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) >= 0
}

export function decodeModelExecutionStatus(
  value: unknown,
): ModelExecutionStatus {
  assertNoReturnedSecrets(value)
  if (
    !isRecord(value)
    || !hasExactKeys(value, EXECUTION_STATUS_KEYS)
    || !(
      value.mode === 'automatic'
      || value.mode === 'conservative'
      || value.mode === 'custom'
    )
    || !Number.isInteger(value.configured_max_in_flight)
    || Number(value.configured_max_in_flight) < 1
    || Number(value.configured_max_in_flight)
      > MODEL_PROFILE_LIMITS.concurrentRequests
    || !Number.isInteger(value.effective_max_in_flight)
    || Number(value.effective_max_in_flight) < 1
    || Number(value.effective_max_in_flight)
      > Number(value.configured_max_in_flight)
    || !Number.isInteger(value.requests_per_minute)
    || Number(value.requests_per_minute) < 1
    || Number(value.requests_per_minute)
      > MODEL_PROFILE_LIMITS.requestsPerMinute
    || !isNonnegativeInteger(value.active)
    || !isNonnegativeInteger(value.queued)
    || !isNonnegativeInteger(value.peak_active)
    || !isNonnegativeInteger(value.physical_request_count)
    || !(
      value.limiting_reason === 'configured'
      || value.limiting_reason === 'provider_overload'
      || value.limiting_reason === 'recovering'
    )
  ) {
    throw new Error('Invalid model execution status')
  }
  return {
    mode: value.mode,
    configured_max_in_flight: Number(value.configured_max_in_flight),
    effective_max_in_flight: Number(value.effective_max_in_flight),
    requests_per_minute: Number(value.requests_per_minute),
    active: value.active,
    queued: value.queued,
    peak_active: value.peak_active,
    physical_request_count: value.physical_request_count,
    limiting_reason: value.limiting_reason,
  }
}

function normalizeBoundedText(
  value: string,
  label: string,
  maximum: number,
  required = true,
): string {
  const normalized = value.trim()
  if (required && normalized === '') {
    throw new ModelProfileInputError(`请填写${label}。`)
  }
  if (normalized.length > maximum) {
    throw new ModelProfileInputError(`${label}过长，请缩短后再保存。`)
  }
  if (/[\u0000-\u001f\u007f]/.test(normalized)) {
    throw new ModelProfileInputError(`${label}包含无法使用的字符。`)
  }
  return normalized
}

function normalizeUrl(
  value: string,
  label: string,
  required: boolean,
): string {
  const normalized = normalizeBoundedText(
    value,
    label,
    MODEL_PROFILE_LIMITS.url,
    required,
  )
  if (normalized === '') return ''
  if (!isHttpUrl(normalized, false)) {
    throw new ModelProfileInputError(
      `${label}需要以 http:// 或 https:// 开头，且不能包含账号或密码。`,
    )
  }
  return normalized
}

function normalizeApiKey(value: string | undefined, label: string): string {
  const normalized = value?.trim() ?? ''
  if (normalized.length > MODEL_PROFILE_LIMITS.apiKey) {
    throw new ModelProfileInputError(`${label}过长，请检查后重新粘贴。`)
  }
  if (/[\r\n\u0000]/.test(normalized)) {
    throw new ModelProfileInputError(`${label}包含换行或无法使用的字符。`)
  }
  return normalized
}

function normalizeInteger(
  value: number,
  label: string,
  maximum: number,
): number {
  if (!Number.isInteger(value) || value < 1 || value > maximum) {
    throw new ModelProfileInputError(`${label}需要填写 1–${maximum} 的整数。`)
  }
  return value
}

export function normalizeModelProfileInput(
  input: ModelProfileUpsertInput,
  requireApiKey = false,
): ModelProfileUpsertInput {
  const apiKey = normalizeApiKey(input.api_key, 'API 密钥')
  const configApiKey = normalizeApiKey(input.config_api_key, '高级配置密钥')
  const batchApiKey = normalizeApiKey(input.batch_api_key, '批量推理密钥')
  if (requireApiKey && apiKey === '') {
    throw new ModelProfileInputError('新配置需要填写 API 密钥。')
  }
  const requestSpeedMode = input.request_speed_mode
  if (
    requestSpeedMode !== 'automatic'
    && requestSpeedMode !== 'conservative'
    && requestSpeedMode !== 'custom'
  ) {
    throw new ModelProfileInputError('请选择有效的 AI 请求速度模式。')
  }

  const normalized: ModelProfileUpsertInput = {
    name: normalizeBoundedText(
      input.name,
      '配置名称',
      MODEL_PROFILE_LIMITS.name,
    ),
    base_url: normalizeUrl(input.base_url, 'API 地址', true),
    ocr_model: normalizeBoundedText(
      input.ocr_model,
      '旧版姓名识别模型',
      MODEL_PROFILE_LIMITS.model,
      false,
    ),
    grading_model: normalizeBoundedText(
      input.grading_model,
      '批改模型',
      MODEL_PROFILE_LIMITS.model,
      false,
    ),
    config_base_url: normalizeUrl(
      input.config_base_url,
      '考试配置与题库标注 API 地址',
      false,
    ),
    config_model: normalizeBoundedText(
      input.config_model,
      '考试配置与题库标注模型',
      MODEL_PROFILE_LIMITS.model,
      false,
    ),
    teaching_prep_model: normalizeBoundedText(
      input.teaching_prep_model ?? '',
      '备课工作台模型',
      MODEL_PROFILE_LIMITS.model,
      false,
    ),
    class_teacher_model: normalizeBoundedText(
      input.class_teacher_model ?? '',
      '班主任工作台模型',
      MODEL_PROFILE_LIMITS.model,
      false,
    ),
    request_speed_mode: requestSpeedMode,
    max_concurrent_requests: normalizeInteger(
      input.max_concurrent_requests,
      '最多同时请求数',
      MODEL_PROFILE_LIMITS.concurrentRequests,
    ),
    requests_per_minute: normalizeInteger(
      input.requests_per_minute,
      '每分钟请求数',
      MODEL_PROFILE_LIMITS.requestsPerMinute,
    ),
    batch_enabled: input.batch_enabled === true,
    batch_model: normalizeBoundedText(
      input.batch_model ?? '',
      '批量推理接入点 ID',
      MODEL_PROFILE_LIMITS.model,
      false,
    ),
    batch_base_url: normalizeUrl(
      input.batch_base_url ?? '',
      '批量推理 API 地址',
      false,
    ),
  }
  if (normalized.batch_enabled && normalized.batch_model === '') {
    throw new ModelProfileInputError('启用批量推理时请填写批量推理接入点 ID。')
  }
  if (apiKey !== '') normalized.api_key = apiKey
  if (configApiKey !== '') normalized.config_api_key = configApiKey
  if (batchApiKey !== '') normalized.batch_api_key = batchApiKey
  return normalized
}

function requireProfilePathName(name: string): string {
  const normalized = normalizeBoundedText(
    name,
    '配置名称',
    MODEL_PROFILE_LIMITS.name,
  )
  if (/[\\/]/.test(normalized)) {
    throw new ModelProfileInputError('配置名称不能包含斜杠。')
  }
  return encodeURIComponent(normalized)
}

export const modelProfilesApi = {
  getState(signal?: AbortSignal): Promise<ModelProfilesState> {
    return apiClient.request('/api/model-profiles', {
      decode: decodeModelProfilesState,
      signal,
    })
  },

  saveProfile(
    profileName: string,
    input: ModelProfileUpsertInput,
    options: ModelProfileSaveOptions = {},
  ): Promise<ModelProfilesState> {
    const pathName = requireProfilePathName(profileName)
    const normalized = normalizeModelProfileInput(input, options.requireApiKey)
    const body: Record<string, string | number | boolean> = {
      base_url: normalized.base_url,
      ocr_model: normalized.ocr_model,
      grading_model: normalized.grading_model,
      config_base_url: normalized.config_base_url,
      config_model: normalized.config_model,
      teaching_prep_model: normalized.teaching_prep_model ?? '',
      class_teacher_model: normalized.class_teacher_model ?? '',
      request_speed_mode: normalized.request_speed_mode,
      max_concurrent_requests: normalized.max_concurrent_requests,
      requests_per_minute: normalized.requests_per_minute,
      batch_enabled: normalized.batch_enabled ?? false,
      batch_model: normalized.batch_model ?? '',
    }
    if (normalized.batch_base_url) {
      body.batch_base_url = normalized.batch_base_url
    }
    if (normalized.api_key !== undefined) body.api_key = normalized.api_key
    if (normalized.config_api_key !== undefined) {
      body.config_api_key = normalized.config_api_key
    }
    if (normalized.batch_api_key !== undefined) {
      body.batch_api_key = normalized.batch_api_key
    }
    return apiClient.request(`/api/model-profiles/${pathName}`, {
      method: 'PUT',
      body,
      decode: decodeModelProfilesState,
      signal: options.signal,
    })
  },

  activateProfile(
    profileName: string,
    signal?: AbortSignal,
  ): Promise<ModelProfilesState> {
    const pathName = requireProfilePathName(profileName)
    return apiClient.request(`/api/model-profiles/${pathName}/activate`, {
      method: 'POST',
      decode: decodeModelProfilesState,
      signal,
    })
  },

  deleteProfile(
    profileName: string,
    signal?: AbortSignal,
  ): Promise<ModelProfilesState> {
    const pathName = requireProfilePathName(profileName)
    return apiClient.request(`/api/model-profiles/${pathName}`, {
      method: 'DELETE',
      decode: decodeModelProfilesState,
      signal,
    })
  },

  saveTaskBindings(
    bindings: ModelTaskBindings,
    signal?: AbortSignal,
  ): Promise<ModelProfilesState> {
    for (const binding of Object.values(bindings)) {
      if (!binding.profile_name || !binding.model.trim()) {
        throw new ModelProfileInputError('请为四类工作分别选择 API 站点并填写模型名称。')
      }
    }
    return apiClient.request('/api/model-profiles/routing/task-bindings', {
      method: 'PUT',
      body: bindings,
      decode: decodeModelProfilesState,
      signal,
    })
  },

  getExecutionStatus(
    profileName: string,
    signal?: AbortSignal,
  ): Promise<ModelExecutionStatus> {
    const pathName = requireProfilePathName(profileName)
    return apiClient.request(
      `/api/model-profiles/${pathName}/execution-status`,
      {
        decode: decodeModelExecutionStatus,
        signal,
      },
    )
  },
}
