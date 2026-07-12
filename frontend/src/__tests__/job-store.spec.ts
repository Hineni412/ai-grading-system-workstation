import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/errors'
import type { JobApi, JobResponse } from '../api/jobs'
import {
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
    schedule: vi.fn(() => 17 as unknown as ReturnType<typeof setTimeout>),
    cancelScheduled: vi.fn(),
    pollIntervalMs: 1_000,
    maxBackoffMs: 8_000,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('Job Store persistence and recovery', () => {
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
  })

  it('clears an entirely malformed local index', async () => {
    localStorage.setItem(JOB_STORAGE_KEY, '{broken')
    const dependencies = makeDependencies()
    const store = useJobStore()

    await store.initialize(dependencies)

    expect(localStorage.getItem(JOB_STORAGE_KEY)).toBeNull()
    expect(dependencies.api.getJob).not.toHaveBeenCalled()
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
})
