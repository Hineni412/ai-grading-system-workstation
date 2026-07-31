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

export interface ApiBinaryResponse {
  blob: Blob
  contentDisposition: string | null
}

export interface ApiBinaryRequestOptions {
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
  download(path: string, options?: ApiBinaryRequestOptions): Promise<ApiBinaryResponse>
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
  const pendingMutationIds = new Map<string, string>()

  function mutationKey(
    method: ApiMethod,
    path: string,
    body: Record<string, unknown>,
  ): string {
    const stable = Object.fromEntries(
      Object.entries(body).filter(([name]) => (
        name !== 'operation_id'
        && name !== 'observed_at'
        && name !== 'reference_at'
      )),
    )
    return `${method}:${path}:${JSON.stringify(stable)}`
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
      let requestBody = options.body
      let pendingMutationKey: string | null = null
      if (
        method !== 'GET'
        && requestBody
        && typeof requestBody === 'object'
        && !Array.isArray(requestBody)
        && 'operation_id' in requestBody
      ) {
        const value = { ...(requestBody as Record<string, unknown>) }
        pendingMutationKey = mutationKey(method, path, value)
        const pending = pendingMutationIds.get(pendingMutationKey)
        if (pending) value.operation_id = pending
        else if (typeof value.operation_id === 'string') {
          pendingMutationIds.set(pendingMutationKey, value.operation_id)
        }
        requestBody = value
      }
      if (requestBody !== undefined) headers['content-type'] = 'application/json'
      const body = options.rawBody ?? (
        requestBody === undefined ? undefined : JSON.stringify(requestBody)
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
              const decoded = options.decode(payload)
              if (pendingMutationKey) pendingMutationIds.delete(pendingMutationKey)
              return decoded
            } catch {
              throw contractError('invalid_success_contract', responseRequestId, response.status)
            }
          } catch (error) {
            if (controller.signal.aborted) throw abortError(requestId, timedOut)
            if (error instanceof ApiError) {
              if (
                pendingMutationKey
                && error.kind !== 'network'
                && error.kind !== 'timeout'
                && error.kind !== 'cancelled'
              ) pendingMutationIds.delete(pendingMutationKey)
              throw error
            }
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

    async download(
      path: string,
      options: ApiBinaryRequestOptions = {},
    ): Promise<ApiBinaryResponse> {
      const requestId = dependencies.createRequestId()
      if (!isSafeApiPath(path)) {
        throw contractError('unsafe_api_path', requestId, null)
      }
      const controller = new AbortController()
      let timedOut = false
      const timeoutHandle = setTimeout(() => {
        timedOut = true
        controller.abort()
      }, options.timeoutMs ?? 15_000)
      const onCallerAbort = () => controller.abort()
      options.signal?.addEventListener('abort', onCallerAbort, { once: true })
      if (options.signal?.aborted) controller.abort()

      try {
        const response = await dependencies.fetch(path, {
          method: 'GET',
          headers: {
            accept: 'application/octet-stream',
            'x-request-id': requestId,
          },
          signal: controller.signal,
        })
        const responseRequestId = response.headers.get('x-request-id')?.trim() || requestId
        if (!response.ok) {
          let payload: unknown
          try {
            payload = await response.json()
          } catch {
            payload = null
          }
          throw parseErrorResponse(payload, responseRequestId, response.status)
        }
        return {
          blob: await response.blob(),
          contentDisposition: response.headers.get('content-disposition'),
        }
      } catch (error) {
        if (controller.signal.aborted) throw abortError(requestId, timedOut)
        if (error instanceof ApiError) throw error
        throw networkError(requestId)
      } finally {
        clearTimeout(timeoutHandle)
        options.signal?.removeEventListener('abort', onCallerAbort)
      }
    },
  }
}

export const apiClient = createApiClient()
