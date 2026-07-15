import { isRecord } from './validation'

export type ApiErrorKind =
  | 'validation'
  | 'not_found'
  | 'conflict'
  | 'server'
  | 'network'
  | 'timeout'
  | 'cancelled'
  | 'contract'

export interface ApiErrorInit {
  kind: ApiErrorKind
  status: number | null
  code: string
  message: string
  details: Record<string, unknown>
  requestId: string
  retryable: boolean
}

export interface UserSafeNotification {
  message: string
  impact: string
  retryable: boolean
  requestId: string
}

export class ApiError extends Error implements ApiErrorInit {
  readonly kind: ApiErrorKind
  readonly status: number | null
  readonly code: string
  readonly details: Record<string, unknown>
  readonly requestId: string
  readonly retryable: boolean

  constructor(init: ApiErrorInit) {
    super(init.message)
    this.name = 'ApiError'
    this.kind = init.kind
    this.status = init.status
    this.code = init.code
    this.details = init.details
    this.requestId = init.requestId
    this.retryable = init.retryable
  }
}

export function isAmbiguousWriteError(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === null
    && (error.kind === 'network' || error.kind === 'timeout' || error.kind === 'cancelled')
}

function contractError(status: number, requestId: string): ApiError {
  return new ApiError({
    kind: 'contract',
    status,
    code: 'invalid_error_contract',
    message: '服务器返回了无法识别的错误信息',
    details: {},
    requestId,
    retryable: false,
  })
}

function kindForStatus(status: number): ApiErrorKind | null {
  if (status === 400 || status === 422) return 'validation'
  if (status === 404) return 'not_found'
  if (status === 409) return 'conflict'
  if (status >= 500 && status <= 599) return 'server'
  return null
}

export function parseErrorResponse(
  payload: unknown,
  responseRequestId: string,
  status: number,
): ApiError {
  const safeRequestId = responseRequestId.trim()
  const kind = kindForStatus(status)
  if (!kind || !isRecord(payload) || !isRecord(payload.error)) {
    return contractError(status, safeRequestId)
  }

  const { code, message, details, request_id: requestId } = payload.error
  if (
    typeof code !== 'string' ||
    !code.trim() ||
    typeof message !== 'string' ||
    !message.trim() ||
    !isRecord(details) ||
    typeof requestId !== 'string' ||
    !requestId.trim() ||
    requestId !== safeRequestId
  ) {
    return contractError(status, safeRequestId)
  }

  return new ApiError({
    kind,
    status,
    code,
    message,
    details,
    requestId,
    retryable: kind === 'server',
  })
}

export function toNotification(
  error: ApiError,
  impact: string,
): UserSafeNotification {
  return {
    message: error.message,
    impact,
    retryable: error.retryable,
    requestId: error.requestId,
  }
}
