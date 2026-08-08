import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fetchSessions,
  SessionReadError,
  type SessionSummary,
} from '../api/sessions'
import { ApiError } from '../api/errors'
import { SESSION_STORAGE_KEY, SessionDraftOutcomeUnknownError, useSessionStore } from '../stores/session'

const sessions: SessionSummary[] = [
  {
    id: 7,
    name: '七年级数学期末质量监测',
    status: 'configured',
    curriculum_volume_id: null,
    is_deleted: false,
    deleted_at: null,
    created_at: null,
    updated_at: null,
  },
  {
    id: 9,
    name: '超长考试名称用于验证顶部栏不会遮挡主要操作与检查器入口',
    status: 'grading',
    curriculum_volume_id: null,
    is_deleted: false,
    deleted_at: null,
    created_at: null,
    updated_at: null,
  },
]

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('session Store', () => {
  it('selects a created draft only after reloading it from the server list', async () => {
    const store = useSessionStore()
    await store.initialize(async () => sessions)
    const created = { ...sessions[0]!, id: 12, name: '新考试草稿', status: 'created' }
    const creator = vi.fn(async () => created)
    const loader = vi.fn(async () => [...sessions, created])

    await expect(store.createDraft(' 新考试草稿 ', creator, loader)).resolves.toEqual(created)
    expect(creator).toHaveBeenCalledWith('新考试草稿')
    expect(loader).toHaveBeenCalledTimes(1)
    expect(store.currentSession?.id).toBe(12)
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBe('12')
  })

  it('reconciles a lost create response from the authoritative session list', async () => {
    const store = useSessionStore()
    await store.initialize(async () => sessions)
    const created = { ...sessions[0]!, id: 12, name: '新考试草稿', status: 'created' }
    const creator = vi.fn(async () => {
      throw new ApiError({ kind: 'timeout', status: null, code: 'request_timeout',
        message: 'timeout', details: {}, requestId: 'safe', retryable: false })
    })
    const loader = vi.fn(async () => [...sessions, created])

    await expect(store.createDraft('新考试草稿', creator, loader)).resolves.toEqual(created)
    expect(store.currentSession?.id).toBe(12)
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBe('12')
  })

  it('reports an unknown create outcome when the authoritative list cannot be read', async () => {
    const store = useSessionStore()
    const timeout = new ApiError({ kind: 'network', status: null, code: 'network_error',
      message: 'offline', details: {}, requestId: 'safe', retryable: true })
    await expect(store.createDraft('新考试草稿', vi.fn(async () => { throw timeout }),
      vi.fn(async () => { throw new Error('offline') })))
      .rejects.toBeInstanceOf(SessionDraftOutcomeUnknownError)
  })

  it('does not let an old list response overwrite a later reload', async () => {
    const store = useSessionStore()
    let resolveOld!: (value: SessionSummary[]) => void
    const oldLoad = store.initialize(() => new Promise((resolve) => { resolveOld = resolve }))
    await store.initialize(async () => [sessions[1]!])
    store.selectSession(9)
    resolveOld([sessions[0]!])
    await oldLoad

    expect(store.sessions.map(({ id }) => id)).toEqual([9])
    expect(store.selectedSessionId).toBe(9)
  })

  it('restores only an API-validated persisted id', async () => {
    localStorage.setItem(SESSION_STORAGE_KEY, '9')
    const store = useSessionStore()
    await store.initialize(async () => sessions)
    expect(store.currentSession?.id).toBe(9)
  })

  it('clears a stale id and never selects the first session', async () => {
    localStorage.setItem(SESSION_STORAGE_KEY, '99')
    const store = useSessionStore()
    await store.initialize(async () => sessions)
    expect(store.selectedSessionId).toBeNull()
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBeNull()
  })

  it('keeps the candidate for retry without exposing it after failure', async () => {
    localStorage.setItem(SESSION_STORAGE_KEY, '7')
    const store = useSessionStore()
    await store.initialize(async () => {
      throw new Error('network detail')
    })
    expect(store.currentSession).toBeNull()
    expect(store.loadState).toBe('error')
    expect(store.errorMessage).not.toContain('network detail')
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBe('7')
    await store.initialize(async () => sessions)
    expect(store.currentSession?.id).toBe(7)
  })

  it('persists only loaded session ids and clears a valid selection', async () => {
    const store = useSessionStore()
    await store.initialize(async () => sessions)

    expect(() => store.selectSession(99)).toThrow()
    expect(store.selectedSessionId).toBeNull()

    store.selectSession(7)
    expect(store.currentSession?.id).toBe(7)
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBe('7')

    store.clearSelection()
    expect(store.selectedSessionId).toBeNull()
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBeNull()
  })
})

describe('sessions adapter', () => {
  it('requests only the sessions endpoint and accepts the exact public response', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify({ items: sessions, total: sessions.length })))

    await expect(fetchSessions()).resolves.toEqual(sessions)
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      '/api/sessions',
      expect.objectContaining({
        method: 'GET',
        headers: expect.objectContaining({
          accept: 'application/json',
          'x-request-id': expect.any(String),
        }),
      }),
    )
  })

  it('returns only active sessions', async () => {
    const deletedSession = { ...sessions[0]!, id: 11, is_deleted: true }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ items: [...sessions, deletedSession], total: 3 })),
    )

    await expect(fetchSessions()).resolves.toEqual(sessions)
  })

  it.each([
    ['a non-success response', () => new Response('private server detail', { status: 500 })],
    [
      'a malformed session item',
      () =>
        new Response(
          JSON.stringify({ items: [{ ...sessions[0]!, id: '7' }], total: 1 }),
        ),
    ],
  ])('throws the stable SessionReadError for %s', async (_case, responseFactory) => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(responseFactory())

    const request = fetchSessions()
    await expect(request).rejects.toBeInstanceOf(SessionReadError)
    await expect(request).rejects.toThrow('无法读取考试列表')
  })
})
