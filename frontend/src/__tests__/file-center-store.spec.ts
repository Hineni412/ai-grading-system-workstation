import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ReportContext } from '../api/exports'
import type { JobResponse } from '../api/jobs'
import { useFileCenterStore } from '../stores/file-center'
import { useJobStore } from '../stores/jobs'

function makeJob(overrides: Partial<JobResponse> = {}): JobResponse {
  return {
    id: 41,
    job_type: 'report_export',
    payload: {
      session_id: 7,
      report_type: 'score_excel',
      score_revision: 'a'.repeat(64),
    },
    result: {},
    status: 'queued',
    progress: 0,
    stage: 'queued',
    detail: '',
    error: null,
    cancel_requested: false,
    created_at: '2026-07-17T10:00:00Z',
    started_at: null,
    updated_at: '2026-07-17T10:00:00Z',
    finished_at: null,
    ...overrides,
  }
}

function reportContext(sessionId: number): ReportContext {
  return {
    score_revision: String(sessionId).padStart(64, '0'),
    has_results: true,
    jobs: [],
    total: 0,
    page: 1,
    page_size: 100,
    total_pages: 1,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise })
  return { promise, resolve }
}

function makeApi(overrides: Record<string, unknown> = {}) {
  return {
    getReportContext: vi.fn(async (sessionId: number) => reportContext(sessionId)),
    submitReport: vi.fn(async () => makeJob()),
    downloadJobFile: vi.fn(),
    deleteReportFile: vi.fn(),
    ...overrides,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('file center store', () => {
  it('loads the selected report ledger as one snapshot', async () => {
    const api = makeApi()
    const store = useFileCenterStore()

    await store.load(7, api)

    expect(store.sessionId).toBe(7)
    expect(store.reportContext?.has_results).toBe(true)
    expect(store.state).toBe('empty')
    expect(api.getReportContext).toHaveBeenCalledWith(
      7,
      1,
      100,
      expect.any(AbortSignal),
    )
  })

  it('tracks active jobs discovered from server history after refresh', async () => {
    const reportJob = makeJob({ id: 61 })
    const api = makeApi({
      getReportContext: vi.fn(async () => ({
        ...reportContext(7),
        jobs: [{
          ...reportJob,
          is_current_revision: true,
          file_status: 'pending',
        }],
        total: 1,
      })),
    })
    const store = useFileCenterStore()
    const jobStore = useJobStore()

    await store.load(7, api)

    expect(store.state).toBe('ready')
    expect(jobStore.jobs[61]?.status).toBe('queued')
  })

  it('ignores an old session response after the user switches exams', async () => {
    const old = deferred<ReportContext>()
    const api = makeApi({
      getReportContext: vi.fn((sessionId: number) => (
        sessionId === 7 ? old.promise : Promise.resolve(reportContext(9))
      )),
    })
    const store = useFileCenterStore()

    const firstLoad = store.load(7, api)
    await store.load(9, api)
    old.resolve(reportContext(7))
    await firstLoad

    expect(store.sessionId).toBe(9)
    expect(store.reportContext?.score_revision).toBe(reportContext(9).score_revision)
  })

  it('keeps the last good ledger visible when refresh fails', async () => {
    const api = makeApi()
    const store = useFileCenterStore()
    await store.load(7, api)
    api.getReportContext.mockRejectedValueOnce(new Error('private network detail'))

    await store.load(7, api)

    expect(store.state).toBe('stale-error')
    expect(store.reportContext?.has_results).toBe(true)
    expect(store.errorMessage).not.toContain('private network detail')
  })

  it('tracks submitted jobs and cancels tracked work without persisting payloads', async () => {
    const api = makeApi()
    const store = useFileCenterStore()
    const jobStore = useJobStore()
    const canceller = vi.fn(async () => {})

    const report = await store.submitReport(
      7,
      'score_excel',
      false,
      undefined,
      api,
    )
    await store.cancelTrackedJob(report.id, canceller)

    expect(jobStore.jobs[report.id]?.job_type).toBe('report_export')
    expect(localStorage.getItem('ai-grading:tracked-jobs:v1')).not.toContain('payload')
    expect(canceller).toHaveBeenCalledWith(report.id)
  })
})
