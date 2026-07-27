import { apiClient } from './client'
import { isRecord } from './validation'

export const MODEL_PROFILE_LIMITS = {
  name: 80,
  url: 2048,
  model: 200,
  apiKey: 8192,
} as const

export interface ModelProfile {
  name: string
  base_url: string
  has_api_key: boolean
  ocr_model: string
  grading_model: string
  config_base_url: string
  has_config_api_key: boolean
  config_model: string
}

export interface ModelProfilesState {
  profiles: ModelProfile[]
  active_profile_name: string | null
  active_profile: ModelProfile | null
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
    ])
    || !Array.isArray(value.profiles)
    || !value.profiles.every(isModelProfile)
    || !(value.active_profile === null || isModelProfile(value.active_profile))
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

export function normalizeModelProfileInput(
  input: ModelProfileUpsertInput,
  requireApiKey = false,
): ModelProfileUpsertInput {
  const apiKey = normalizeApiKey(input.api_key, 'API 密钥')
  const configApiKey = normalizeApiKey(input.config_api_key, '高级配置密钥')
  if (requireApiKey && apiKey === '') {
    throw new ModelProfileInputError('新配置需要填写 API 密钥。')
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
      'OCR 模型',
      MODEL_PROFILE_LIMITS.model,
    ),
    grading_model: normalizeBoundedText(
      input.grading_model,
      '批改模型',
      MODEL_PROFILE_LIMITS.model,
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
  }
  if (apiKey !== '') normalized.api_key = apiKey
  if (configApiKey !== '') normalized.config_api_key = configApiKey
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
    const body: Record<string, string> = {
      base_url: normalized.base_url,
      ocr_model: normalized.ocr_model,
      grading_model: normalized.grading_model,
      config_base_url: normalized.config_base_url,
      config_model: normalized.config_model,
    }
    if (normalized.api_key !== undefined) body.api_key = normalized.api_key
    if (normalized.config_api_key !== undefined) {
      body.config_api_key = normalized.config_api_key
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
}
