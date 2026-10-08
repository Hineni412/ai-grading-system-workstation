import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { JobApi, JobResponse } from '../api/jobs';
import { JOB_STORAGE_KEY, useJobStore } from '../stores/jobs';

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
    getJobStatusBatch: vi.fn(async (ids: number[]) =>
      ids.map((id) => ({ id, found: true, job: makeJob() })),
    ),
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

  it.each(['question_import', 'tagging_sync', 'question_bank_sync'])('tracks a completed %s chapter child and retains it after recovery', async (jobType) => {
    const child = makeJob({ id: 42, job_type: 'chapter_type_organize', payload: {}, result: {}, status: 'queued' })
    const dependencies = makeDependencies({ getJob: vi.fn(async id => id === 41
      ? makeJob({ job_type: jobType, status: 'succeeded', result: { chapter_type_job_id: 42, chapter_type_state: 'queued' } }) : child) })
    const store = useJobStore()
    store.track(makeJob({ job_type: jobType, result: {} }), dependencies)
    await store.refresh(41)
    await vi.waitFor(() => expect(store.jobs[42]?.status).toBe('queued'))
    expect(vi.mocked(dependencies.api.getJob).mock.calls.filter(([id]) => id === 42)).toHaveLength(1)
    expect(JSON.parse(localStorage.getItem(JOB_STORAGE_KEY)!)).toContainEqual({ id: 42, jobType: 'chapter_type_organize', trackedAt: dependencies.now().toISOString() })
    store.$dispose()
    setActivePinia(createPinia())
    const recovered = useJobStore()
    const restored = makeDependencies({ getJob: vi.fn(async id => id === 42 ? child : makeJob({ status: 'succeeded', result: { chapter_type_job_id: 42 } })) })
    await recovered.initialize(restored)
    expect(vi.mocked(restored.api.getJob).mock.calls.filter(([id]) => id === 42)).toHaveLength(1)
    expect(recovered.jobs[42]?.status).toBe('queued')
    recovered.$dispose()
  })

  it('tracks the config intake and its chapter child without changing the submitted task notice', async () => {
    const dependencies = makeDependencies({ getJob: vi.fn(async id => id === 42
      ? makeJob({ id: 42, job_type: 'question_bank_sync', status: 'succeeded', result: { chapter_type_job_id: 43 } })
      : makeJob({ id: 43, job_type: 'chapter_type_organize', status: 'succeeded', result: { outcome: 'published', chapter_summaries: [] } })) })
    const store = useJobStore()
    store.track(makeJob({ job_type: 'config_generation', status: 'succeeded', result: { question_bank_sync_job_id: 42 } }), dependencies)
    await vi.waitFor(() => expect(store.jobs[43]?.status).toBe('succeeded'))
    expect(vi.mocked(dependencies.api.getJob).mock.calls.map(([id]) => id)).toEqual([42, 43])
    expect(store.latestTrackedJobId).toBe(41)
    expect(store.jobNoticeRevision).toBe(1)
    expect(JSON.parse(localStorage.getItem(JOB_STORAGE_KEY)!)).toHaveLength(3)
    store.$dispose()
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

})

describe('Job Store polling and cancellation', () => {

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

  it('cleans all timers when the Store is disposed', () => {
    const dependencies = makeDependencies()
    const store = useJobStore()
    store.track(makeJob(), dependencies)

    store.$dispose()

    expect(dependencies.cancelScheduled).toHaveBeenCalledTimes(1)
  })

})
