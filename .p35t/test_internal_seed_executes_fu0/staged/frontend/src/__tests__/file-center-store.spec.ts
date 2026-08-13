import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  JobSummaryList,
  ReportContext,
  TrainingTaskDetail,
  TrainingTaskList,
} from '../api/exports'
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

const tasks: TrainingTaskList = {
  items: [{
    id: 12,
    task_code: 'TRAIN-12',
    created_by: 'teacher',
    scope_snapshot: { mode: 'class' },
    exam_scope: { mode: 'current', session_ids: [7] },
    generation_config: { variant_mode: 'individual' },
    warnings: [],
    status: 'ready',
    created_at: '2026-07-17T09:00:00Z',
    updated_at: '2026-07-17T09:01:00Z',
  }],
  total: 1,
  page: 1,
  page_size: 20,
  total_pages: 1,
}

const noJobs: JobSummaryList = {
  items: [],
  total: 0,
  page: 1,
  page_size: 20,
  total_pages: 0,
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
    listTrainingTasks: vi.fn(async () => tasks),
    getTrainingTask: vi.fn(),
    submitTrainingExport: vi.fn(async () => makeJob({
      id: 51,
      job_type: 'training_export',
      payload: { task_id: 12, format: 'docx' },
    })),
    retryTrainingExport: vi.fn(async () => makeJob({
      id: 52,
      job_type: 'training_export',
      payload: { task_id: 12, format: 'docx', retry_of_job_id: 51 },
    })),
    listTrainingExportJobs: vi.fn(async () => noJobs),
    getJob: vi.fn(),
    downloadJobFile: vi.fn(),
    ...overrides,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('file center store', () => {
  it('loads the selected report ledger and training exports as one snapshot', async () => {
    const api = makeApi()
    const store = useFileCenterStore()

    await store.load(7, api)

    expect(store.sessionId).toBe(7)
    expect(store.reportContext?.has_results).toBe(true)
    expect(store.trainingTasks.map(({ id }) => id)).toEqual([12])
    expect(store.state).toBe('ready')
    expect(api.getReportContext).toHaveBeenCalledWith(
      7,
      1,
      100,
      expect.any(AbortSignal),
    )
    expect(api.listTrainingTasks).toHaveBeenCalledWith(
      1,
      100,
      expect.any(AbortSignal),
    )
    expect(api.listTrainingExportJobs).toHaveBeenCalledWith(
      1,
      100,
      expect.any(AbortSignal),
    )
  })

  it('tracks active jobs discovered from server history after refresh', async () => {
    const reportJob = makeJob({ id: 61 })
    const trainingJob = makeJob({
      id: 62,
      job_type: 'training_export',
      payload: { task_id: 12, format: 'docx' },
      status: 'running',
    })
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
      listTrainingExportJobs: vi.fn(async () => ({
        ...noJobs,
        items: [{
          id: trainingJob.id,
          job_type: trainingJob.job_type,
          status: trainingJob.status,
          progress: trainingJob.progress,
          stage: trainingJob.stage,
          detail: trainingJob.detail,
          cancel_requested: trainingJob.cancel_requested,
          created_at: trainingJob.created_at,
          updated_at: trainingJob.updated_at,
        }],
        total: 1,
      })),
      getJob: vi.fn(async () => trainingJob),
    })
    const store = useFileCenterStore()
    const jobStore = useJobStore()

    await store.load(7, api)

    expect(jobStore.jobs[61]?.status).toBe('queued')
    expect(jobStore.jobs[62]?.status).toBe('running')
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

  it('tracks submitted and retried jobs without persisting their payloads', async () => {
    const api = makeApi()
    const store = useFileCenterStore()
    const jobStore = useJobStore()

    const report = await store.submitReport(7, 'score_excel', false, api)
    const training = await store.submitTrainingBundle(12, api)
    const retry = await store.retryTrainingExport(51, api)

    expect(jobStore.jobs[report.id]?.job_type).toBe('report_export')
    expect(jobStore.jobs[training.id]?.job_type).toBe('training_export')
    expect(jobStore.jobs[retry.id]?.payload.retry_of_job_id).toBe(51)
    expect(localStorage.getItem('ai-grading:tracked-jobs:v1')).not.toContain('payload')
  })

  it('loads a task detail, submits an explicit variant and cancels tracked work', async () => {
    const detail = {
      ...tasks.items[0]!,
      diagnosis_snapshot: {},
      variants: [{
        id: 3,
        variant_key: 'group-a',
        variant_type: 'individual',
        students: [],
        items: [],
      }],
      exports: [],
    }
    const api = makeApi({
      getTrainingTask: vi.fn(async () => detail),
    })
    const store = useFileCenterStore()
    const canceller = vi.fn(async () => {})

    await expect(store.loadTrainingTask(12, api)).resolves.toEqual(detail)
    await store.submitTraining(12, {
      variant_id: 3,
      format: 'markdown',
      audience: 'teacher',
    }, api)
    await store.cancelTrackedJob(51, canceller)

    expect(api.submitTrainingExport).toHaveBeenCalledWith(12, {
      variant_id: 3,
      format: 'markdown',
      audience: 'teacher',
    })
    expect(canceller).toHaveBeenCalledWith(51)
  })

  it('does not expose a slow training detail after the exam changes', async () => {
    const slowDetail = deferred<TrainingTaskDetail>()
    const detail = {
      ...tasks.items[0]!,
      diagnosis_snapshot: {},
      variants: [],
      exports: [],
    }
    const api = makeApi({
      getTrainingTask: vi.fn(() => slowDetail.promise),
    })
    const store = useFileCenterStore()
    await store.load(7, api)

    const loading = store.loadTrainingTask(12, api)
    await store.load(9, api)
    slowDetail.resolve(detail)

    await expect(loading).resolves.toBeNull()
    expect(store.sessionId).toBe(9)
    expect(store.selectedTrainingTask).toBeNull()
    expect(store.trainingDetailState).toBe('idle')
  })
})
