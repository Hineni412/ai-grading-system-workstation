import { ApiError, parseErrorResponse } from './errors'

export type ApiMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
export type ResponseDecoder<T> = (payload: unknown) => T

export interface ApiRequestOptions<T> {
  method?: ApiMethod
  body?: unknown
  rawBody?: BodyInit
  headers?: Readonly<Record<string, string>>
  decode: ResponseDecoder<T>
  signal?: AbortSignal
  timeoutMs?: number
}

export interface ApiClientDependencies {
  fetch: typeof globalThis.fetch
  createRequestId: () => string
  delay: (milliseconds: number, signal: AbortSignal) => Promise<void>
}

export interface ApiClient {
  request<T>(path: string, options: ApiRequestOptions<T>): Promise<T>
}

function abortAwareDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('aborted', 'AbortError'))
      return
    }
    const handle = setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, milliseconds)
    function onAbort() {
      clearTimeout(handle)
      reject(new DOMException('aborted', 'AbortError'))
    }
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

function generatedRequestId(): string {
  return globalThis.crypto.randomUUID()
}

function apiError(init: ConstructorParameters<typeof ApiError>[0]): ApiError {
  return new ApiError(init)
}

function abortError(requestId: string, timedOut: boolean): ApiError {
  return apiError({
    kind: timedOut ? 'timeout' : 'cancelled',
    status: null,
    code: timedOut ? 'request_timeout' : 'request_cancelled',
    message: timedOut ? '请求超时' : '请求已取消',
    details: {},
    requestId,
    retryable: false,
  })
}

function networkError(requestId: string): ApiError {
  return apiError({
    kind: 'network',
    status: null,
    code: 'network_error',
    message: '暂时无法连接服务器',
    details: {},
    requestId,
    retryable: true,
  })
}

function contractError(code: string, requestId: string, status: number | null): ApiError {
  return apiError({
    kind: 'contract',
    status,
    code,
    message: '服务器返回了无法识别的数据',
    details: {},
    requestId,
    retryable: false,
  })
}

function isSafeApiPath(path: string): boolean {
  if (!/^\/api\//.test(path)) return false
  try {
    const normalized = new URL(path, 'http://local.invalid')
    return (
      normalized.origin === 'http://local.invalid' &&
      normalized.pathname.startsWith('/api/')
    )
  } catch {
    return false
  }
}

export function createApiClient(
  overrides: Partial<ApiClientDependencies> = {},
): ApiClient {
  const dependencies: ApiClientDependencies = {
    fetch: overrides.fetch ?? ((input, init) => globalThis.fetch(input, init)),
    createRequestId: overrides.createRequestId ?? generatedRequestId,
    delay: overrides.delay ?? abortAwareDelay,
  }

  return {
    async request<T>(path: string, options: ApiRequestOptions<T>): Promise<T> {
      const requestId = dependencies.createRequestId()
      if (!isSafeApiPath(path)) {
        throw contractError('unsafe_api_path', requestId, null)
      }

      const method = options.method ?? 'GET'
      if (options.body !== undefined && options.rawBody !== undefined) {
        throw contractError('ambiguous_request_body', requestId, null)
      }
      const controller = new AbortController()
      let timedOut = false
      const timeoutMs = options.timeoutMs ?? 15_000
      const timeoutHandle = setTimeout(() => {
        timedOut = true
        controller.abort()
      }, timeoutMs)
      const onCallerAbort = () => controller.abort()
      options.signal?.addEventListener('abort', onCallerAbort, { once: true })
      if (options.signal?.aborted) controller.abort()

      const headers: Record<string, string> = {
        accept: 'application/json',
        'x-request-id': requestId,
      }
      for (const [name, value] of Object.entries(options.headers ?? {})) {
        const normalizedName = name.trim().toLowerCase()
        if (!normalizedName || normalizedName === 'accept' || normalizedName === 'x-request-id') continue
        headers[normalizedName] = value
      }
      if (options.body !== undefined) headers['content-type'] = 'application/json'
      const body = options.rawBody ?? (
        options.body === undefined ? undefined : JSON.stringify(options.body)
      )

      try {
        const attempts = method === 'GET' ? 3 : 1
        const waitBeforeRetry = async (milliseconds: number) => {
          try {
            await dependencies.delay(milliseconds, controller.signal)
          } catch {
            if (controller.signal.aborted) throw abortError(requestId, timedOut)
            throw networkError(requestId)
          }
        }
        for (let attempt = 0; attempt < attempts; attempt += 1) {
          if (controller.signal.aborted) throw abortError(requestId, timedOut)
          try {
            const response = await dependencies.fetch(path, {
              method,
              headers,
              body,
              signal: controller.signal,
            })
            const responseRequestId = response.headers.get('x-request-id')?.trim() || requestId
            let payload: unknown
            try {
              payload = await response.json()
            } catch {
              throw contractError(
                response.ok ? 'invalid_success_contract' : 'invalid_error_contract',
                responseRequestId,
                response.status,
              )
            }

            if (!response.ok) {
              const error = parseErrorResponse(payload, responseRequestId, response.status)
              if (method === 'GET' && error.kind === 'server' && attempt < attempts - 1) {
                await waitBeforeRetry(250 * 2 ** attempt)
                continue
              }
              throw error
            }

            try {
              return options.decode(payload)
            } catch {
              throw contractError('invalid_success_contract', responseRequestId, response.status)
            }
          } catch (error) {
            if (controller.signal.aborted) throw abortError(requestId, timedOut)
            if (error instanceof ApiError) throw error
            const normalized = networkError(requestId)
            if (method === 'GET' && attempt < attempts - 1) {
              await waitBeforeRetry(250 * 2 ** attempt)
              continue
            }
            throw normalized
          }
        }
        throw networkError(requestId)
      } finally {
        clearTimeout(timeoutHandle)
        options.signal?.removeEventListener('abort', onCallerAbort)
      }
    },
  }
}

export const apiClient = createApiClient()
