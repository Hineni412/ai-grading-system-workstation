import { apiClient } from './client'
import { isRecord } from './validation'

export type AiDiagnosticOutcome = 'pending' | 'success' | 'failure'

export interface AiDiagnosticSummary {
  call_id: string
  operation_id: string
  request_id: string
  attempt: number
  request_kind: string
  workspace_module: string
  workspace_task_kind: string
  protocol: string
  model: string
  started_at_utc: string
  finished_at_utc: string
  outcome: AiDiagnosticOutcome
  elapsed_ms: number
  image_count: number
  response_chars: number
  will_retry: boolean
}


export interface AiDiagnosticList {
  items: AiDiagnosticSummary[]
  returned: number
  matching: number
  scanned_event_count: number
  truncated: boolean
}

export interface AiDiagnosticAttachment {
  id: string
  purpose: string
  mime_type: string
  bytes: number
  sha256: string
}

export interface AiDiagnosticError {
  category: string
  exception_type: string
  http_status_code: number
  message: string
}

export interface AiDiagnosticDetail extends AiDiagnosticSummary {
  endpoint_host: string
  retry_limit: number
  retry_index: number
  retry_delay_ms: number
  request: Record<string, unknown>
  attachments: AiDiagnosticAttachment[]
  raw_response: string
  response_sha256: string
  parse_status: string
  parse_operations: string[]
  parse_error: string
  parsed_result: unknown
  validation_issue_codes: string[]
  error: AiDiagnosticError | null
}

const SUMMARY_KEYS = [
  'call_id',
  'operation_id',
  'request_id',
  'attempt',
  'request_kind',
  'workspace_module',
  'workspace_task_kind',
  'protocol',
  'model',
  'started_at_utc',
  'finished_at_utc',
  'outcome',
  'elapsed_ms',
  'image_count',
  'response_chars',
  'will_retry',
] as const

const DETAIL_KEYS = [
  ...SUMMARY_KEYS,
  'endpoint_host',
  'retry_limit',
  'retry_index',
  'retry_delay_ms',
  'request',
  'attachments',
  'raw_response',
  'response_sha256',
  'parse_status',
  'parse_operations',
  'parse_error',
  'parsed_result',
  'validation_issue_codes',
  'error',
] as const

function hasExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value)
  return actual.length === expected.length
    && expected.every((key) => Object.prototype.hasOwnProperty.call(value, key))
}

function isNonnegativeInteger(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) >= 0
}

const DIAGNOSTIC_TEXT_LIMIT = 32 * 1024 * 1024

function isText(
  value: unknown,
  maximum = DIAGNOSTIC_TEXT_LIMIT,
): value is string {
  return typeof value === 'string' && value.length <= maximum
}

function isOutcome(value: unknown): value is AiDiagnosticOutcome {
  return value === 'pending' || value === 'success' || value === 'failure'
}

function isSummary(value: unknown): value is AiDiagnosticSummary {
  if (!isRecord(value) || !hasExactKeys(value, SUMMARY_KEYS)) return false
  return (
    /^[0-9a-f]{24}$/.test(String(value.call_id))
    && isText(value.operation_id, 200)
    && isText(value.request_id, 200)
    && isNonnegativeInteger(value.attempt)
    && isText(value.request_kind, 80)
    && isText(value.workspace_module, 80)
    && isText(value.workspace_task_kind, 80)
    && isText(value.protocol, 80)
    && isText(value.model, 200)
    && isText(value.started_at_utc, 64)
    && isText(value.finished_at_utc, 64)
    && isOutcome(value.outcome)
    && isNonnegativeInteger(value.elapsed_ms)
    && isNonnegativeInteger(value.image_count)
    && isNonnegativeInteger(value.response_chars)
    && typeof value.will_retry === 'boolean'
  )
}

function isAttachment(value: unknown): value is AiDiagnosticAttachment {
  if (
    !isRecord(value)
    || !hasExactKeys(value, ['id', 'purpose', 'mime_type', 'bytes', 'sha256'])
  ) return false
  return (
    isText(value.id, 80)
    && isText(value.purpose, 80)
    && isText(value.mime_type, 120)
    && isNonnegativeInteger(value.bytes)
    && /^[0-9a-f]{64}$/.test(String(value.sha256))
  )
}

function isDiagnosticError(value: unknown): value is AiDiagnosticError {
  if (
    !isRecord(value)
    || !hasExactKeys(
      value,
      ['category', 'exception_type', 'http_status_code', 'message'],
    )
  ) return false
  return (
    isText(value.category, 80)
    && isText(value.exception_type, 120)
    && isNonnegativeInteger(value.http_status_code)
    && Number(value.http_status_code) <= 599
    && isText(value.message)
  )
}

function assertSecretsAreRedacted(value: unknown): void {
  if (Array.isArray(value)) {
    value.forEach(assertSecretsAreRedacted)
    return
  }
  if (!isRecord(value)) return
  for (const [key, child] of Object.entries(value)) {
    const compact = key.replace(/[^a-z0-9]/gi, '').toLowerCase()
    if (
      (
        compact === 'authorization'
        || compact === 'proxyauthorization'
        || compact === 'xapikey'
        || compact === 'cookie'
        || compact === 'setcookie'
        || compact === 'password'
        || compact === 'passwd'
        || compact === 'accesstoken'
        || compact === 'refreshtoken'
        || compact === 'idtoken'
        || compact === 'sessiontoken'
        || compact.endsWith('apikey')
      )
      && child !== '[REDACTED]'
    ) {
      throw new Error('AI diagnostic response exposed a secret')
    }
    assertSecretsAreRedacted(child)
  }
}


export function decodeAiDiagnosticList(value: unknown): AiDiagnosticList {
  assertSecretsAreRedacted(value)
  if (
    !isRecord(value)
    || !hasExactKeys(
      value,
      ['items', 'returned', 'matching', 'scanned_event_count', 'truncated'],
    )
    || !Array.isArray(value.items)
    || !value.items.every(isSummary)
    || !isNonnegativeInteger(value.returned)
    || !isNonnegativeInteger(value.matching)
    || !isNonnegativeInteger(value.scanned_event_count)
    || typeof value.truncated !== 'boolean'
  ) {
    throw new Error('Invalid AI diagnostic list')
  }
  return value as unknown as AiDiagnosticList
}

export function decodeAiDiagnosticDetail(
  value: unknown,
): AiDiagnosticDetail {
  assertSecretsAreRedacted(value)
  if (
    !isRecord(value)
    || !hasExactKeys(value, DETAIL_KEYS)
    || !isSummary(Object.fromEntries(
      SUMMARY_KEYS.map((key) => [key, value[key]]),
    ))
    || !isText(value.endpoint_host, 253)
    || !isNonnegativeInteger(value.retry_limit)
    || !isNonnegativeInteger(value.retry_index)
    || !isNonnegativeInteger(value.retry_delay_ms)
    || !isRecord(value.request)
    || !Array.isArray(value.attachments)
    || !value.attachments.every(isAttachment)
    || !isText(value.raw_response)
    || !isText(value.response_sha256, 64)
    || !isText(value.parse_status, 80)
    || !Array.isArray(value.parse_operations)
    || !value.parse_operations.every((item) => isText(item, 120))
    || !isText(value.parse_error)
    || !Array.isArray(value.validation_issue_codes)
    || !value.validation_issue_codes.every((item) => isText(item, 80))
    || !(value.error === null || isDiagnosticError(value.error))
  ) {
    throw new Error('Invalid AI diagnostic detail')
  }
  return value as unknown as AiDiagnosticDetail
}

export const aiDiagnosticsApi = {
  list(
    options: {
      limit?: number
      offset?: number
      requestKind?: string
      outcome?: '' | AiDiagnosticOutcome
      workspaceModule?: string
      workspaceTaskKind?: string
      signal?: AbortSignal
    } = {},
  ): Promise<AiDiagnosticList> {
    const query = new URLSearchParams()
    query.set('limit', String(Math.min(100, Math.max(1, options.limit ?? 50))))
    if (options.offset) {
      query.set('offset', String(Math.max(0, options.offset)))
    }
    if (options.requestKind) {
      query.set('request_kind', options.requestKind)
    }
    if (options.outcome) query.set('outcome', options.outcome)
    if (options.workspaceModule) {
      query.set('workspace_module', options.workspaceModule)
    }
    if (options.workspaceTaskKind) {
      query.set('workspace_task_kind', options.workspaceTaskKind)
    }
    return apiClient.request(`/api/ai-diagnostics?${query.toString()}`, {
      decode: decodeAiDiagnosticList,
      signal: options.signal,
    })
  },

  detail(
    callId: string,
    signal?: AbortSignal,
  ): Promise<AiDiagnosticDetail> {
    if (!/^[0-9a-f]{24}$/.test(callId)) {
      return Promise.reject(new Error('Invalid AI diagnostic call id'))
    }
    return apiClient.request(`/api/ai-diagnostics/${callId}`, {
      decode: decodeAiDiagnosticDetail,
      signal,
    })
  },

}
