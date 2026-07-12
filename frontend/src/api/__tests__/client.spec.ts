import { describe, expect, it, vi } from 'vitest'

import { createApiClient } from '../client'
import { ApiError } from '../errors'

function jsonResponse(payload: unknown, init: ResponseInit = {}): Response {
  const headers = new Headers(init.headers)
  headers.set('content-type', 'application/json')
  if (!headers.has('x-request-id')) headers.set('x-request-id', 'server-req')
  return new Response(JSON.stringify(payload), { ...init, headers })
}

function createTestClient(fetchMock: ReturnType<typeof vi.fn>) {
  return createApiClient({
    fetch: fetchMock as typeof globalThis.fetch,
    createRequestId: () => 'client-req-1',
    delay: vi.fn(async () => undefined),
  })
}

describe('API client', () => {
  it('sends a same-origin request id and decodes a successful response', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ ok: true }, { headers: { 'x-request-id': 'client-req-1' } }),
    )
    const client = createTestClient(fetchMock)

    await expect(
      client.request('/api/sessions', {
        decode: (value) => value as { ok: boolean },
      }),
    ).resolves.toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      '/api/sessions',
      expect.objectContaining({
        method: 'GET',
        headers: expect.objectContaining({
          accept: 'application/json',
          'x-request-id': 'client-req-1',
        }),
      }),
    )
  })

  it.each([
    'https://example.com/api/jobs/1',
    '/healthz',
    'api/jobs/1',
    '/api/../healthz',
    '/api/%2e%2e/healthz',
  ])(
    'rejects the unsafe path %s before fetch',
    async (path) => {
      const fetchMock = vi.fn()
      const client = createTestClient(fetchMock)

      await expect(client.request(path, { decode: (value) => value })).rejects.toMatchObject({
        kind: 'contract',
        code: 'unsafe_api_path',
      })
      expect(fetchMock).not.toHaveBeenCalled()
    },
  )

  it('retries a GET network failure and a server failure up to three attempts', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('offline detail'))
      .mockResolvedValueOnce(
        jsonResponse(
          {
            error: {
              code: 'server_error',
              message: 'Request failed',
              details: {},
              request_id: 'client-req-1',
            },
          },
          { status: 500, headers: { 'x-request-id': 'client-req-1' } },
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse({ ok: true }, { headers: { 'x-request-id': 'client-req-1' } }),
      )
    const client = createTestClient(fetchMock)

    await expect(
      client.request('/api/jobs/1', { decode: (value) => value }),
    ).resolves.toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('never automatically replays a write request', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('offline detail'))
    const client = createTestClient(fetchMock)

    await expect(
      client.request('/api/jobs/1/cancel', {
        method: 'POST',
        decode: (value) => value,
      }),
    ).rejects.toMatchObject({ kind: 'network', retryable: true })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('maps a standard 404 without retrying', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(
        {
          error: {
            code: 'job_not_found',
            message: 'Job not found',
            details: { job_id: 41 },
            request_id: 'client-req-1',
          },
        },
        { status: 404, headers: { 'x-request-id': 'client-req-1' } },
      ),
    )
    const client = createTestClient(fetchMock)

    await expect(
      client.request('/api/jobs/41', { decode: (value) => value }),
    ).rejects.toMatchObject({ kind: 'not_found', code: 'job_not_found' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('turns malformed success data into a contract error without leaking it', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ private_path: 'D:/secret' }, { headers: { 'x-request-id': 'client-req-1' } }),
    )
    const client = createTestClient(fetchMock)

    const request = client.request('/api/sessions', {
      decode: () => {
        throw new Error('private decoder detail')
      },
    })
    await expect(request).rejects.toBeInstanceOf(ApiError)
    await expect(request).rejects.toMatchObject({
      kind: 'contract',
      code: 'invalid_success_contract',
      details: {},
    })
  })

  it('distinguishes caller cancellation from timeout and does not retry either', async () => {
    const caller = new AbortController()
    const cancelledFetch = vi.fn((_path: string, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      }),
    )
    const cancelledClient = createTestClient(cancelledFetch)
    const cancelled = cancelledClient.request('/api/jobs/1', {
      decode: (value) => value,
      signal: caller.signal,
    })
    caller.abort()
    await expect(cancelled).rejects.toMatchObject({ kind: 'cancelled' })
    expect(cancelledFetch).toHaveBeenCalledTimes(1)

    vi.useFakeTimers()
    try {
      const timeoutFetch = vi.fn((_path: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
        }),
      )
      const timeoutClient = createTestClient(timeoutFetch)
      const timedOut = timeoutClient.request('/api/jobs/1', {
        decode: (value) => value,
        timeoutMs: 5,
      })
      const capturedTimeout = timedOut.catch((error: unknown) => error)
      await vi.advanceTimersByTimeAsync(5)
      await expect(capturedTimeout).resolves.toMatchObject({ kind: 'timeout' })
      expect(timeoutFetch).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('normalizes caller cancellation while waiting for a retry', async () => {
    const caller = new AbortController()
    const delay = vi.fn(
      (_milliseconds: number, signal: AbortSignal) =>
        new Promise<void>((_resolve, reject) => {
          signal.addEventListener(
            'abort',
            () => reject(new DOMException('aborted', 'AbortError')),
            { once: true },
          )
        }),
    )
    const client = createApiClient({
      fetch: vi.fn().mockRejectedValue(new TypeError('offline')) as typeof globalThis.fetch,
      createRequestId: () => 'retry-request',
      delay,
    })

    const request = client.request('/api/jobs/1', {
      decode: (value) => value,
      signal: caller.signal,
    })
    const captured = request.catch((error: unknown) => error)
    await vi.waitFor(() => expect(delay).toHaveBeenCalledTimes(1))
    caller.abort()

    await expect(captured).resolves.toMatchObject({
      kind: 'cancelled',
      code: 'request_cancelled',
    })
  })
})
