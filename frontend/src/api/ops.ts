import { apiClient, type ApiBinaryResponse } from './client'
import { decodeJobResponse, type JobResponse } from './jobs'
import { isRecord } from './validation'

export const OPS_OPERATIONS = [
  'backup',
  'restore',
  'migration',
  'transfer_import',
  'transfer_export',
] as const

export type OpsOperation = (typeof OPS_OPERATIONS)[number]
export type OpsCheckStatus = 'ok' | 'warning' | 'error'
export type OpsOfflineStatus =
  | 'prepared'
  | 'restart_required'
  | 'applying'
  | 'applied'
  | 'rolled_back'
  | 'failed'
  | 'cancelled'

export interface OpsDirectoryCheck {
  key: string
  exists: boolean
  writable: boolean
  status: OpsCheckStatus
}

export interface OpsDatabaseCheck {
  key: string
  exists: boolean
  size_bytes: number
  integrity: 'ok' | 'missing' | 'unavailable'
  migration_version: string
  pending_migrations: number
  status: OpsCheckStatus
}

export interface OpsToolCheck {
  key: string
  available: boolean
  status: 'ok' | 'warning'
}

export interface OpsSelfCheck {
  version: string
  status: OpsCheckStatus
  api_configured: boolean
  directories: OpsDirectoryCheck[]
  databases: OpsDatabaseCheck[]
  tools: OpsToolCheck[]
  warnings: string[]
}

export interface OpsBackupItem {
  kind: 'zip' | 'database'
  filename: string
  created_at: string
  reason: string
  size_bytes: number
}

export interface OpsBackupList {
  items: OpsBackupItem[]
  returned: number
  limit: number
}

export interface OpsImportUpload {
  upload_id: string
  filename: string
  size_bytes: number
  sha256: string
}

export type OpsPreflightRequest =
  | {
    operation: 'backup'
    reason: 'before_exam' | 'before_update' | 'before_import'
      | 'before_restore' | 'manual' | 'after_exam'
    scopes?: Array<'grading' | 'class_teacher'>
  }
  | { operation: 'restore'; backup_filename: string }
  | { operation: 'migration'; target: 'grading' | 'question_bank' | 'all' }
  | { operation: 'transfer_import'; upload_id: string }
  | { operation: 'transfer_export'; scope: 'lean' | 'full' }

export interface OpsPreflightSummary {
  file_count: number | null
  total_size_bytes: number | null
  total_expanded_bytes: number | null
  database_count: number | null
  skipped_count: number | null
  sensitive_skipped_count: number | null
  target: 'grading' | 'question_bank' | 'all' | null
  pending_migrations: number | null
  applied_in_preview: number | null
  integrity: 'ok' | null
  scope: 'lean' | 'full' | null
  warnings: string[]
}

export interface OpsPreflight {
  operation: OpsOperation
  confirmation_token: string
  expires_at: string
  requires_restart: boolean
  summary: OpsPreflightSummary
}

export interface OpsOperationState {
  operation_id: string
  operation: OpsOperation
  status: OpsOfflineStatus
  result_code: string
  created_at: string
  updated_at: string
  recovery: {
    code: string
    backup_filename: string | null
  }
}

export interface OpsApi {
  getSelfCheck(signal?: AbortSignal): Promise<OpsSelfCheck>
  getBackups(limit?: number, signal?: AbortSignal): Promise<OpsBackupList>
  stageImport(file: File, signal?: AbortSignal): Promise<OpsImportUpload>
  preflight(request: OpsPreflightRequest, signal?: AbortSignal): Promise<OpsPreflight>
  submit(confirmationToken: string, signal?: AbortSignal): Promise<JobResponse>
  getOperation(operationId: string, signal?: AbortSignal): Promise<OpsOperationState>
  cancelOperation(operationId: string, signal?: AbortSignal): Promise<OpsOperationState>
  downloadJob(jobId: number, signal?: AbortSignal): Promise<ApiBinaryResponse>
}

const CHECK_STATUSES = new Set<OpsCheckStatus>(['ok', 'warning', 'error'])
const OFFLINE_STATUSES = new Set<OpsOfflineStatus>([
  'prepared',
  'restart_required',
  'applying',
  'applied',
  'rolled_back',
  'failed',
  'cancelled',
])
const DATABASE_INTEGRITIES = new Set(['ok', 'missing', 'unavailable'])
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const UPLOAD_ID_PATTERN = /^[0-9a-f]{32}$/
const SHA256_PATTERN = /^[0-9a-f]{64}$/
const SAFE_FILENAME_PATTERN = /^[^\\/:*?"<>|\u0000-\u001f]+$/
const OPS_MAX_UPLOAD_BYTES = 200 * 1024 * 1024

function exactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const keys = Object.keys(value).sort()
  return keys.length === expected.length
    && keys.every((key, index) => key === [...expected].sort()[index])
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function safeFilename(value: unknown): value is string {
  return nonEmptyString(value)
    && value.length <= 255
    && SAFE_FILENAME_PATTERN.test(value)
    && value !== '.'
    && value !== '..'
}

function nonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0
}

function nullableNonNegativeInteger(value: unknown): value is number | null {
  return value === null || nonNegativeInteger(value)
}

function stringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(nonEmptyString)
}

function isOperation(value: unknown): value is OpsOperation {
  return typeof value === 'string'
    && OPS_OPERATIONS.some((operation) => operation === value)
}

function invalid(message: string): never {
  throw new Error(message)
}

export function decodeOpsSelfCheck(value: unknown): OpsSelfCheck {
  if (
    !isRecord(value)
    || !exactKeys(value, [
      'version',
      'status',
      'api_configured',
      'directories',
      'databases',
      'tools',
      'warnings',
    ])
    || !nonEmptyString(value.version)
    || !CHECK_STATUSES.has(value.status as OpsCheckStatus)
    || typeof value.api_configured !== 'boolean'
    || !Array.isArray(value.directories)
    || !Array.isArray(value.databases)
    || !Array.isArray(value.tools)
    || !stringList(value.warnings)
  ) return invalid('Invalid Ops self-check')

  for (const item of value.directories) {
    if (
      !isRecord(item)
      || !exactKeys(item, ['key', 'exists', 'writable', 'status'])
      || !nonEmptyString(item.key)
      || typeof item.exists !== 'boolean'
      || typeof item.writable !== 'boolean'
      || !CHECK_STATUSES.has(item.status as OpsCheckStatus)
    ) return invalid('Invalid Ops self-check')
  }
  for (const item of value.databases) {
    if (
      !isRecord(item)
      || !exactKeys(item, [
        'key',
        'exists',
        'size_bytes',
        'integrity',
        'migration_version',
        'pending_migrations',
        'status',
      ])
      || !nonEmptyString(item.key)
      || typeof item.exists !== 'boolean'
      || !nonNegativeInteger(item.size_bytes)
      || !DATABASE_INTEGRITIES.has(String(item.integrity))
      || !nonEmptyString(item.migration_version)
      || !nonNegativeInteger(item.pending_migrations)
      || !CHECK_STATUSES.has(item.status as OpsCheckStatus)
    ) return invalid('Invalid Ops self-check')
  }
  for (const item of value.tools) {
    if (
      !isRecord(item)
      || !exactKeys(item, ['key', 'available', 'status'])
      || !nonEmptyString(item.key)
      || typeof item.available !== 'boolean'
      || !['ok', 'warning'].includes(String(item.status))
    ) return invalid('Invalid Ops self-check')
  }
  return value as unknown as OpsSelfCheck
}

export function decodeOpsBackups(value: unknown): OpsBackupList {
  if (
    !isRecord(value)
    || !exactKeys(value, ['items', 'returned', 'limit'])
    || !Array.isArray(value.items)
    || !nonNegativeInteger(value.returned)
    || value.returned !== value.items.length
    || !Number.isSafeInteger(value.limit)
    || Number(value.limit) < 1
    || Number(value.limit) > 100
  ) return invalid('Invalid Ops backups')
  for (const item of value.items) {
    if (
      !isRecord(item)
      || !exactKeys(item, ['kind', 'filename', 'created_at', 'reason', 'size_bytes'])
      || !['zip', 'database'].includes(String(item.kind))
      || !safeFilename(item.filename)
      || !nonEmptyString(item.created_at)
      || !nonEmptyString(item.reason)
      || !nonNegativeInteger(item.size_bytes)
    ) return invalid('Invalid Ops backups')
  }
  return value as unknown as OpsBackupList
}

export function decodeOpsImportUpload(value: unknown): OpsImportUpload {
  if (
    !isRecord(value)
    || !exactKeys(value, ['upload_id', 'filename', 'size_bytes', 'sha256'])
    || typeof value.upload_id !== 'string'
    || !UPLOAD_ID_PATTERN.test(value.upload_id)
    || !safeFilename(value.filename)
    || !nonNegativeInteger(value.size_bytes)
    || typeof value.sha256 !== 'string'
    || !SHA256_PATTERN.test(value.sha256)
  ) return invalid('Invalid Ops import upload')
  return value as unknown as OpsImportUpload
}

export function decodeOpsPreflight(value: unknown): OpsPreflight {
  if (
    !isRecord(value)
    || !exactKeys(value, [
      'operation',
      'confirmation_token',
      'expires_at',
      'requires_restart',
      'summary',
    ])
    || !isOperation(value.operation)
    || !nonEmptyString(value.confirmation_token)
    || !nonEmptyString(value.expires_at)
    || !Number.isFinite(Date.parse(value.expires_at))
    || typeof value.requires_restart !== 'boolean'
    || !isRecord(value.summary)
  ) return invalid('Invalid Ops preflight')
  const summary = value.summary
  if (
    !exactKeys(summary, [
      'file_count',
      'total_size_bytes',
      'total_expanded_bytes',
      'database_count',
      'skipped_count',
      'sensitive_skipped_count',
      'target',
      'pending_migrations',
      'applied_in_preview',
      'integrity',
      'scope',
      'warnings',
    ])
    || !nullableNonNegativeInteger(summary.file_count)
    || !nullableNonNegativeInteger(summary.total_size_bytes)
    || !nullableNonNegativeInteger(summary.total_expanded_bytes)
    || !nullableNonNegativeInteger(summary.database_count)
    || !nullableNonNegativeInteger(summary.skipped_count)
    || !nullableNonNegativeInteger(summary.sensitive_skipped_count)
    || !(summary.target === null || ['grading', 'question_bank', 'all'].includes(String(summary.target)))
    || !nullableNonNegativeInteger(summary.pending_migrations)
    || !nullableNonNegativeInteger(summary.applied_in_preview)
    || !(summary.integrity === null || summary.integrity === 'ok')
    || !(summary.scope === null || ['lean', 'full'].includes(String(summary.scope)))
    || !stringList(summary.warnings)
  ) return invalid('Invalid Ops preflight')
  return value as unknown as OpsPreflight
}

export function decodeOpsOperation(value: unknown): OpsOperationState {
  if (
    !isRecord(value)
    || !exactKeys(value, [
      'operation_id',
      'operation',
      'status',
      'result_code',
      'created_at',
      'updated_at',
      'recovery',
    ])
    || typeof value.operation_id !== 'string'
    || !UUID_PATTERN.test(value.operation_id)
    || !isOperation(value.operation)
    || !OFFLINE_STATUSES.has(value.status as OpsOfflineStatus)
    || !nonEmptyString(value.result_code)
    || !nonEmptyString(value.created_at)
    || !nonEmptyString(value.updated_at)
    || !isRecord(value.recovery)
    || !exactKeys(value.recovery, ['code', 'backup_filename'])
    || !nonEmptyString(value.recovery.code)
    || !(value.recovery.backup_filename === null || safeFilename(value.recovery.backup_filename))
  ) return invalid('Invalid Ops operation')
  return value as unknown as OpsOperationState
}

function requireBackupLimit(limit: number): number {
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
    throw new Error('Invalid Ops backup limit')
  }
  return limit
}

function requireConfirmationToken(value: string): string {
  const token = String(value || '').trim()
  if (!token || token.length > 512) throw new Error('Invalid Ops confirmation token')
  return token
}

function requireOperationId(value: string): string {
  const operationId = String(value || '').trim()
  if (!UUID_PATTERN.test(operationId)) throw new Error('Invalid Ops operation id')
  return operationId
}

function requireJobId(value: number): number {
  if (!Number.isSafeInteger(value) || value <= 0) throw new Error('Invalid Ops Job id')
  return value
}

function requirePreflightRequest(request: OpsPreflightRequest): OpsPreflightRequest {
  if (!isOperation(request.operation)) throw new Error('Invalid Ops preflight request')
  if (request.operation === 'restore' && !safeFilename(request.backup_filename)) {
    throw new Error('Invalid Ops backup filename')
  }
  if (request.operation === 'backup') {
    const allowed = new Set(['grading', 'class_teacher'])
    const scopes = request.scopes
    if (scopes === undefined) return request
    if (
      scopes.length < 1
      || scopes.length > 3
      || new Set(scopes).size !== scopes.length
      || scopes.some((scope) => !allowed.has(scope))
    ) throw new Error('Invalid Ops backup scopes')
  }
  if (request.operation === 'transfer_import' && !UPLOAD_ID_PATTERN.test(request.upload_id)) {
    throw new Error('Invalid Ops upload id')
  }
  return request
}

export const opsApi: OpsApi = {
  async getSelfCheck(signal) {
    return apiClient.request('/api/ops/self-check', {
      decode: decodeOpsSelfCheck,
      signal,
    })
  },
  async getBackups(limit = 50, signal) {
    const bounded = requireBackupLimit(limit)
    return apiClient.request(`/api/ops/backups?limit=${bounded}`, {
      decode: decodeOpsBackups,
      signal,
    })
  },
  async stageImport(file, signal) {
    if (!(file instanceof File) || !file.name.toLowerCase().endsWith('.zip')) {
      throw new Error('Ops import must be a ZIP file')
    }
    if (file.size > OPS_MAX_UPLOAD_BYTES) throw new Error('Ops import exceeds 200 MiB')
    return apiClient.request(
      `/api/ops/transfer-import/uploads?filename=${encodeURIComponent(file.name)}`,
      {
        method: 'POST',
        rawBody: file,
        headers: { 'content-type': 'application/zip' },
        decode: decodeOpsImportUpload,
        signal,
        timeoutMs: 120_000,
      },
    )
  },
  async preflight(request, signal) {
    return apiClient.request('/api/ops/preflights', {
      method: 'POST',
      body: requirePreflightRequest(request),
      decode: decodeOpsPreflight,
      signal,
      timeoutMs: 120_000,
    })
  },
  async submit(confirmationToken, signal) {
    return apiClient.request('/api/ops/jobs', {
      method: 'POST',
      body: { confirmation_token: requireConfirmationToken(confirmationToken) },
      decode: decodeJobResponse,
      signal,
    })
  },
  async getOperation(operationId, signal) {
    const id = requireOperationId(operationId)
    return apiClient.request(`/api/ops/operations/${id}`, {
      decode: decodeOpsOperation,
      signal,
    })
  },
  async cancelOperation(operationId, signal) {
    const id = requireOperationId(operationId)
    return apiClient.request(`/api/ops/operations/${id}/cancel`, {
      method: 'POST',
      decode: decodeOpsOperation,
      signal,
    })
  },
  async downloadJob(jobId, signal) {
    return apiClient.download(`/api/jobs/${requireJobId(jobId)}/download`, { signal })
  },
}
