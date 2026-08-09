import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/errors'
import type { JobApi, JobResponse } from '../api/jobs'
import {
  jobPollDelay,
  JOB_STORAGE_KEY,
  useJobStore,
} from '../stores/jobs'

function makeJob(overrides: Partial<JobResponse> = {}): JobResponse {
  return {
    id: 41,
    job_type: 'report_export',
    payload: { session_id: 7, api_key: 'must-not-persist' },
    result: { filename: 'report.xlsx' },
    status: 'running',
    progress: 0.5,
    stage: 'rendering',
    detail: '2/4',
    error: null,
    cancel_requested: false,
    created_at: '2026-07-12T10:00:00Z',
    started_at: '2026-07-12T10:00:00Z',
    updated_at: '2026-07-12T10:00:01Z',
    finished_at: null,
    ...overrides,
  }
}

function makeDependencies(apiOverrides: Partial<JobApi> = {}) {
  const api = {
    getJob: vi.fn(async () => makeJob()),
    cancelJob: vi.fn(async () => makeJob({ cancel_requested: true })),
    ...apiOverrides,
  }
  return {
    api,
    now: () => new Date('2026-07-12T10:00:00.000Z'),
    schedule: vi.fn((callback: () => void, delay: number) => {
      void callback
      void delay
      return 17 as unknown as ReturnType<typeof setTimeout>
    }),
    cancelScheduled: vi.fn(),
    pollIntervalMs: 1_000,
    maxBackoffMs: 8_000,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('Job Store persistence and recovery', () => {
  it('shares a two-second polling cadence capped at thirty seconds', () => {
    expect([0, 1, 2, 3, 4, 5, 6].map(retryCount => jobPollDelay(retryCount)))
      .toEqual([2_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000])
  })

  it('persists only the current browser Job reference', () => {
    const dependencies = makeDependencies()
    const store = useJobStore()

    store.track(makeJob(), dependencies)

    const raw = localStorage.getItem(JOB_STORAGE_KEY)!
    expect(JSON.parse(raw)).toEqual([
      { id: 41, jobType: 'report_export', trackedAt: '2026-07-12T10:00:00.000Z' },
    ])
    expect(raw).not.toContain('payload')
    expect(raw).not.toContain('result')
    expect(raw).not.toContain('api_key')
  })

  it('persists a terminal marker when a newly tracked Job has finished', () => {
    const dependencies = makeDependencies()
    const store = useJobStore()

    store.track(makeJob({
      status: 'succeeded',
      progress: 1,
      finished_at: '2026-07-12T10:00:02Z',
    }), dependencies)

    expect(JSON.parse(localStorage.getItem(JOB_STORAGE_KEY)!)).toEqual([
      {
        id: 41,
        jobType: 'report_export',
        trackedAt: '2026-07-12T10:00:00.000Z',
        terminal: true,
      },
    ])
    expect(dependencies.schedule).not.toHaveBeenCalled()
  })

  it('coalesces concurrent initialization and ignores later repeats', async () => {
    localStorage.setItem(
      JOB_STORAGE_KEY,
      JSON.stringify([{ id: 41, jobType: 'report_export', trackedAt: '2026-07-12T09:00:00Z' }]),
    )
    const dependencies = makeDependencies()
    const store = useJobStore()

    await Promise.all([
      store.initialize(dependencies),
      store.initialize(dependencies),
    ])
    await store.initialize(dependencies)

    expect(dependencies.api.getJob).toHaveBeenCalledTimes(1)
    expect(dependencies.schedule).toHaveBeenCalledTimes(1)
  })

  it('allows initialization to retry after the initialization process fails', async () => {
    localStorage.setItem(
      JOB_STORAGE_KEY,
      JSON.stringify([{ id: 41, jobType: 'report_export', trackedAt: '2026-07-12T09:00:00Z' }]),
    )
    vi.spyOn(Storage.prototype, 'getItem')
      .mockImplementationOnce(() => { throw new Error('storage temporarily unavailable') })
    const dependencies = makeDependencies()
    const store = useJobStore()

    await expect(store.initialize(dependencies)).rejects.toThrow('storage temporarily unavailable')
    await expect(store.initialize(dependencies)).resolves.toBeUndefined()

    expect(dependencies.api.getJob).toHaveBeenCalledTimes(1)
  })

  it('restores active Jobs before every completed Job only once per lifecycle', async () => {
    const completed = Array.from({ length: 24 }, (_value, index) => ({
      id: index + 1,
      jobType: 'report_export',
      trackedAt: new Date(Date.UTC(2026, 6, index + 1)).toISOString(),
      terminal: true,
    }))
    localStorage.setItem(JOB_STORAGE_KEY, JSON.stringify([
      ...completed,
      { id: 101, jobType: 'report_export', trackedAt: '2026-07-25T00:00:00Z' },
      { id: 102, jobType: 'report_export', trackedAt: '2026-07-26T00:00:00Z' },
    ]))
    const getJob = vi.fn(async (id: number) => makeJob({
      id,
      status: id >= 100 ? 'running' : 'succeeded',
      progress: id >= 100 ? 0.5 : 1,
      finished_at: id >= 100 ? null : '2026-07-26T10:00:00Z',
    }))
    const dependencies = makeDependencies({ getJob })
    const store = useJobStore()

    await store.initialize(dependencies)
    await store.initialize(dependencies)

    const restoredIds = getJob.mock.calls.map(([id]) => id)
    expect(restoredIds.slice(0, 2)).toEqual([101, 102])
    expect(restoredIds).toHaveLength(26)
    expect(restoredIds.slice(2).sort((left, right) => left - right))
      .toEqual(Array.from({ length: 24 }, (_value, index) => index + 1))
    expect(dependencies.schedule).toHaveBeenCalledTimes(2)
    expect(JSON.parse(localStorage.getItem(JOB_STORAGE_KEY)!)).toHaveLength(26)
    expect(JSON.parse(localStorage.getItem(JOB_STORAGE_KEY)!).map(
      (reference: { id: number }) => reference.id,
    )).toEqual([
      ...Array.from({ length: 24 }, (_value, index) => index + 1),
      101,
      102,
    ])
  })

  it('retries a completed history item when its first startup refresh is temporarily offline', async () => {
    localStorage.setItem(JOB_STORAGE_KEY, JSON.stringify([{
      id: 41,
      jobType: 'report_export',
      trackedAt: '2026-07-12T09:00:00Z',
      terminal: true,
    }]))
    const callbacks: Array<() => void> = []
    const getJob = vi.fn()
      .mockRejectedValueOnce(new ApiError({
        kind: 'network',
        status: null,
        code: 'network_error',
        message: 'offline',
        details: {},
        requestId: 'req-offline',
        retryable: true,
      }))
      .mockResolvedValueOnce(makeJob({
        status: 'succeeded',
        progress: 1,
        finished_at: '2026-07-12T10:00:02Z',
      }))
    const dependencies = makeDependencies({ getJob })
    dependencies.schedule.mockImplementation((callback, delay) => {
      void delay
      callbacks.push(callback)
      return callbacks.length as unknown as ReturnType<typeof setTimeout>
    })
    const store = useJobStore()

    await store.initialize(dependencies)

    expect(store.jobs[41]).toBeUndefined()
    expect(callbacks).toHaveLength(1)
    callbacks.shift()?.()
    await vi.waitFor(() => {
      expect(store.jobs[41]?.status).toBe('succeeded')
    })
    expect(getJob).toHaveBeenCalledTimes(2)
    expect(dependencies.schedule).toHaveBeenCalledTimes(1)
    expect(store.syncErrors[41]).toBeUndefined()
  })

  it('clears every completed Job reference so none returns', async () => {
    localStorage.setItem(JOB_STORAGE_KEY, JSON.stringify([
      { id: 1, jobType: 'report_export', trackedAt: '2026-07-01T00:00:00Z', terminal: true },
      { id: 2, jobType: 'report_export', trackedAt: '2026-07-02T00:00:00Z', terminal: true },
      { id: 101, jobType: 'report_export', trackedAt: '2026-07-03T00:00:00Z' },
    ]))
    const firstDependencies = makeDependencies({
      getJob: vi.fn(async (id: number) => makeJob({
        id,
        status: id === 101 ? 'running' : 'succeeded',
      })),
    })
    const firstStore = useJobStore()
    await firstStore.initialize(firstDependencies)

    firstStore.clearCompleted()

    expect(JSON.parse(localStorage.getItem(JOB_STORAGE_KEY)!)).toEqual([
      { id: 101, jobType: 'report_export', trackedAt: '2026-07-03T00:00:00Z' },
    ])
    firstStore.stopAllPolling()
    setActivePinia(createPinia())
    const secondDependencies = makeDependencies({
      getJob: vi.fn(async (id: number) => makeJob({ id, status: 'running' })),
    })
    const secondStore = useJobStore()

    await secondStore.initialize(secondDependencies)

    expect(secondDependencies.api.getJob).toHaveBeenCalledExactlyOnceWith(
      101,
      expect.any(AbortSignal),
    )
    expect(secondStore.jobs[1]).toBeUndefined()
    expect(secondStore.jobs[2]).toBeUndefined()
  })

  it('restores only valid persisted ids and polls only active jobs', async () => {
    localStorage.setItem(
      JOB_STORAGE_KEY,
      JSON.stringify([
        { id: 41, jobType: 'report_export', trackedAt: '2026-07-12T09:00:00Z' },
        { id: 'bad', jobType: 'private', trackedAt: 'yesterday' },
      ]),
    )
    const dependencies = makeDependencies()
    const store = useJobStore()

    await store.initialize(dependencies)

    expect(dependencies.api.getJob).toHaveBeenCalledExactlyOnceWith(41, expect.any(AbortSignal))
    expect(store.jobs[41]).toMatchObject({ id: 41, status: 'running' })
    expect(dependencies.schedule).toHaveBeenCalledTimes(1)
    expect(JSON.parse(localStorage.getItem(JOB_STORAGE_KEY)!)).toEqual([
      { id: 41, jobType: 'report_export', trackedAt: '2026-07-12T09:00:00Z' },
    ])
  })

  it('keeps a recovered terminal job without polling', async () => {
    localStorage.setItem(
      JOB_STORAGE_KEY,
      JSON.stringify([{ id: 41, jobType: 'report_export', trackedAt: '2026-07-12T09:00:00Z' }]),
    )
    const dependencies = makeDependencies({
      getJob: vi.fn(async () => makeJob({ status: 'succeeded', progress: 1 })),
    })
    const store = useJobStore()

    await store.initialize(dependencies)

    expect(store.jobs[41]?.status).toBe('succeeded')
    expect(dependencies.schedule).not.toHaveBeenCalled()
    expect(JSON.parse(localStorage.getItem(JOB_STORAGE_KEY)!)).toEqual([
      {
        id: 41,
        jobType: 'report_export',
        trackedAt: '2026-07-12T09:00:00Z',
        terminal: true,
      },
    ])
  })

  it('clears an entirely malformed local index', async () => {
    localStorage.setItem(JOB_STORAGE_KEY, '{broken')
    const dependencies = makeDependencies()
    const store = useJobStore()

    await store.initialize(dependencies)

    expect(localStorage.getItem(JOB_STORAGE_KEY)).toBeNull()
    expect(dependencies.api.getJob).not.toHaveBeenCalled()
  })

  it('strips extra fields from an existing local reference', async () => {
    localStorage.setItem(
      JOB_STORAGE_KEY,
      JSON.stringify([
        {
          id: 41,
          jobType: 'report_export',
          trackedAt: '2026-07-12T09:00:00Z',
          payload: { api_key: 'secret' },
          result: { filename: 'private' },
        },
      ]),
    )
    const dependencies = makeDependencies({
      getJob: vi.fn(async () => makeJob({ status: 'succeeded' })),
    })
    const store = useJobStore()

    await store.initialize(dependencies)

    expect(JSON.parse(localStorage.getItem(JOB_STORAGE_KEY)!)).toEqual([
      {
        id: 41,
        jobType: 'report_export',
        trackedAt: '2026-07-12T09:00:00Z',
        terminal: true,
      },
    ])
  })

  it('removes a missing Job reference and stops retrying it', async () => {
    localStorage.setItem(
      JOB_STORAGE_KEY,
      JSON.stringify([{ id: 41, jobType: 'report_export', trackedAt: '2026-07-12T09:00:00Z' }]),
    )
    const dependencies = makeDependencies({
      getJob: vi.fn(async () => {
        throw new ApiError({ kind: 'not_found', status: 404, code: 'job_not_found', message: 'Job not found', details: { job_id: 41 }, requestId: 'req-41', retryable: false })
      }),
    })
    const store = useJobStore()

    await store.initialize(dependencies)

    expect(localStorage.getItem(JOB_STORAGE_KEY)).toBeNull()
    expect(store.jobs[41]).toBeUndefined()
    expect(store.syncErrors[41]).toMatchObject({ kind: 'not_found', requestId: 'req-41' })
    expect(dependencies.schedule).not.toHaveBeenCalled()
  })

  it('preserves the last successful snapshot during a network failure', async () => {
    const dependencies = makeDependencies({
      getJob: vi.fn(async () => {
        throw new ApiError({ kind: 'network', status: null, code: 'network_error', message: 'offline', details: {}, requestId: 'req-offline', retryable: true })
      }),
    })
    const store = useJobStore()
    store.track(makeJob(), dependencies)

    await store.refresh(41)

    expect(store.jobs[41]).toMatchObject({ id: 41, progress: 0.5 })
    expect(store.syncErrors[41]).toMatchObject({ kind: 'network', retryable: true })
    expect(dependencies.schedule).toHaveBeenCalled()
  })

  it('does not keep polling after a non-retryable contract failure', async () => {
    const dependencies = makeDependencies({
      getJob: vi.fn(async () => {
        throw new ApiError({ kind: 'contract', status: 500, code: 'invalid_success_contract', message: 'invalid', details: {}, requestId: 'contract-req', retryable: false })
      }),
    })
    const store = useJobStore()
    store.track(makeJob(), dependencies)
    store.stopPolling(41)
    dependencies.schedule.mockClear()

    await store.refresh(41)

    expect(store.syncErrors[41]).toMatchObject({ kind: 'other', retryable: false })
    expect(dependencies.schedule).not.toHaveBeenCalled()
  })
})

describe('Job Store polling and cancellation', () => {
  it('keeps exactly one polling timer for repeated tracking', () => {
    const dependencies = makeDependencies()
    const store = useJobStore()

    store.track(makeJob(), dependencies)
    store.track(makeJob({ progress: 0.6 }), dependencies)

    expect(dependencies.schedule).toHaveBeenCalledTimes(1)
    expect(store.jobs[41]?.progress).toBe(0.6)
  })

  it('stops polling and ignores an old in-flight response', async () => {
    const pending = deferred<JobResponse>()
    const dependencies = makeDependencies({ getJob: vi.fn(() => pending.promise) })
    const store = useJobStore()
    store.track(makeJob(), dependencies)

    const refresh = store.refresh(41)
    store.stopPolling(41)
    pending.resolve(makeJob({ progress: 0.9, updated_at: '2026-07-12T10:00:09Z' }))
    await refresh

    expect(store.jobs[41]?.progress).toBe(0.5)
    expect(dependencies.cancelScheduled).toHaveBeenCalledTimes(1)
  })

  it('keeps polling after the server acknowledges a cancellation request', async () => {
    const dependencies = makeDependencies({
      cancelJob: vi.fn(async () =>
        makeJob({ cancel_requested: true, updated_at: '2026-07-12T10:00:03Z' }),
      ),
    })
    const store = useJobStore()
    store.track(makeJob(), dependencies)

    await store.cancel(41)

    expect(store.jobs[41]).toMatchObject({ status: 'running', cancel_requested: true })
    expect(dependencies.cancelScheduled).not.toHaveBeenCalled()
    expect(dependencies.schedule).toHaveBeenCalledTimes(1)
  })

  it('does not let an older poll overwrite a newer cancellation response', async () => {
    const pendingPoll = deferred<JobResponse>()
    const dependencies = makeDependencies({
      getJob: vi.fn(() => pendingPoll.promise),
      cancelJob: vi.fn(async () =>
        makeJob({ cancel_requested: true, progress: 0.7, updated_at: '2026-07-12T10:00:03Z' }),
      ),
    })
    const store = useJobStore()
    store.track(makeJob(), dependencies)
    const refresh = store.refresh(41)

    await store.cancel(41)
    pendingPoll.resolve(makeJob({ progress: 0.6, updated_at: '2026-07-12T10:00:02Z' }))
    await refresh

    expect(store.jobs[41]).toMatchObject({
      progress: 0.7,
      cancel_requested: true,
      updated_at: '2026-07-12T10:00:03Z',
    })
  })

  it('preserves the Job snapshot when cancellation fails', async () => {
    const dependencies = makeDependencies({
      cancelJob: vi.fn(async () => {
        throw new ApiError({ kind: 'network', status: null, code: 'network_error', message: 'offline', details: {}, requestId: 'cancel-req', retryable: true })
      }),
    })
    const store = useJobStore()
    store.track(makeJob(), dependencies)

    await store.cancel(41)

    expect(store.jobs[41]).toMatchObject({ progress: 0.5, cancel_requested: false })
    expect(store.syncErrors[41]).toMatchObject({ retryable: true, requestId: 'cancel-req' })
  })

  it('cleans all timers when the Store is disposed', () => {
    const dependencies = makeDependencies()
    const store = useJobStore()
    store.track(makeJob(), dependencies)

    store.$dispose()

    expect(dependencies.cancelScheduled).toHaveBeenCalledTimes(1)
  })

  it('aborts an in-flight cancellation and ignores its late response on stop', async () => {
    const pendingCancel = deferred<JobResponse>()
    let cancelSignal: AbortSignal | undefined
    const dependencies = makeDependencies({
      cancelJob: vi.fn((_id, signal) => {
        cancelSignal = signal
        return pendingCancel.promise
      }),
    })
    const store = useJobStore()
    store.track(makeJob(), dependencies)
    const cancellation = store.cancel(41)
    await vi.waitFor(() => expect(dependencies.api.cancelJob).toHaveBeenCalledTimes(1))

    store.stopAllPolling()
    expect(cancelSignal?.aborted).toBe(true)
    pendingCancel.resolve(
      makeJob({ cancel_requested: true, progress: 0.9, updated_at: '2026-07-12T10:00:09Z' }),
    )
    await cancellation

    expect(store.jobs[41]).toMatchObject({ progress: 0.5, cancel_requested: false })
  })
})
