import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { JobResponse } from '../api/jobs'
import { createAppRouter } from '../router'
import { useJobStore } from '../stores/jobs'
import { useResultsCenterStore } from '../stores/results-center'
import { useSessionStore } from '../stores/session'
import FileCenterView from '../views/FileCenterView.vue'

const apiMock = vi.hoisted(() => ({
  getReportContext: vi.fn(),
  submitReport: vi.fn(),
  listTrainingTasks: vi.fn(),
  getTrainingTask: vi.fn(),
  submitTrainingExport: vi.fn(),
  retryTrainingExport: vi.fn(),
  listTrainingExportJobs: vi.fn(),
  getJob: vi.fn(),
  downloadJobFile: vi.fn(),
}))

vi.mock('../api/exports', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/exports')>(),
  exportsApi: apiMock,
}))

function makeJob(overrides: Partial<JobResponse> = {}): JobResponse {
  return {
    id: 41,
    job_type: 'report_export',
    payload: {
      session_id: 7,
      report_type: 'score_excel',
      score_revision: 'a'.repeat(64),
    },
    result: {
      session_id: 7,
      report_type: 'score_excel',
      score_revision: 'a'.repeat(64),
      filename: '七年级成绩.xlsx',
      download_url: '/api/jobs/41/download',
    },
    status: 'succeeded',
    progress: 1,
    stage: 'report_export',
    detail: 'complete',
    error: null,
    cancel_requested: false,
    created_at: '2026-07-17T10:00:00Z',
    started_at: '2026-07-17T10:00:00Z',
    updated_at: '2026-07-17T10:00:01Z',
    finished_at: '2026-07-17T10:00:01Z',
    ...overrides,
  }
}

const mounted: App[] = []

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((done, fail) => {
    resolve = done
    reject = fail
  })
  return { promise, resolve, reject }
}

async function settle() {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountView(selectedSessionId: number | null = 7) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const sessionStore = useSessionStore(pinia)
  sessionStore.$patch({
    sessions: [{
      id: 7,
      name: '七年级期末',
      status: 'completed',
      is_deleted: false,
      deleted_at: null,
      created_at: null,
      updated_at: null,
    }],
    selectedSessionId,
    loadState: 'ready',
  })
  const resultsStore = useResultsCenterStore(pinia)
  resultsStore.$patch({
    sessionId: selectedSessionId,
    state: selectedSessionId === null ? 'idle' : 'ready',
    results: selectedSessionId === null
      ? null
      : {
        session_id: 7,
        session_name: '七年级期末',
        summary: {
          student_count: 10,
          complete_student_count: 10,
          average_sample_count: 10,
          average_score: 84.5,
          highest_score: 89,
          lowest_score: 80,
          max_score: 100,
          ungraded_item_count: 0,
          failed_item_count: 0,
          needs_review_item_count: 0,
          ai_ready_item_count: 10,
          teacher_final_item_count: 0,
        },
        questions: [],
        students: Array.from({ length: 10 }, (_, index) => ({
          student_id: index + 1,
          student_code: `S${String(index + 1).padStart(3, '0')}`,
          student_name: `学生${String(index + 1).padStart(2, '0')}`,
          class_name: '一班',
          current_score: 89 - index,
          max_score: 100,
          ungraded_count: 0,
          failed_count: 0,
          needs_review_count: 0,
          status: 'complete' as const,
          items: [],
        })),
      },
  })
  const router = createAppRouter(createMemoryHistory())
  await router.push('/results?tab=exports')
  await router.isReady()
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(FileCenterView)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  mounted.push(app)
  await settle()
  return { host, router }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  apiMock.getReportContext.mockResolvedValue({
    score_revision: 'a'.repeat(64),
    has_results: true,
    jobs: [
      { ...makeJob(), is_current_revision: true, file_status: 'available' },
      {
        ...makeJob({
          id: 40,
          payload: {
            session_id: 7,
            report_type: 'annotated_original_pdf',
            score_revision: 'a'.repeat(64),
          },
          result: {
            session_id: 7,
            report_type: 'annotated_original_pdf',
            score_revision: 'b'.repeat(64),
            filename: '旧批注原卷.pdf',
            download_url: '/api/jobs/40/download',
          },
        }),
        is_current_revision: true,
        file_status: 'expired',
      },
    ],
    total: 2,
    page: 1,
    page_size: 100,
    total_pages: 1,
  })
  apiMock.listTrainingTasks.mockResolvedValue({
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
  })
  apiMock.listTrainingExportJobs.mockResolvedValue({
    items: [{
      id: 51,
      job_type: 'training_export',
      status: 'failed',
      progress: 0.4,
      stage: 'training_export',
      detail: 'failed',
      created_at: '2026-07-17T09:05:00Z',
      started_at: '2026-07-17T09:05:00Z',
      updated_at: '2026-07-17T09:05:01Z',
      finished_at: '2026-07-17T09:05:01Z',
    }],
    total: 1,
    page: 1,
    page_size: 20,
    total_pages: 1,
  })
  apiMock.getJob.mockResolvedValue(makeJob({
    id: 51,
    job_type: 'training_export',
    payload: { task_id: 12, format: 'docx' },
    result: { task_id: 12 },
    status: 'failed',
    error: 'Job failed; see local logs for details.',
  }))
  apiMock.getTrainingTask.mockResolvedValue({
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
    diagnosis_snapshot: {},
    variants: [{
      id: 3,
      variant_key: 'group-a',
      variant_type: 'individual',
      students: [],
      items: [],
    }],
    exports: [],
  })
  apiMock.submitReport.mockResolvedValue(makeJob({
    id: 61,
    status: 'queued',
    result: {},
  }))
  apiMock.submitTrainingExport.mockResolvedValue(makeJob({
    id: 62,
    job_type: 'training_export',
    payload: { task_id: 12, format: 'docx' },
    result: {},
    status: 'queued',
  }))
  apiMock.retryTrainingExport.mockResolvedValue(makeJob({
    id: 63,
    job_type: 'training_export',
    payload: { task_id: 12, format: 'docx', retry_of_job_id: 51 },
    result: {},
    status: 'queued',
  }))
  apiMock.downloadJobFile.mockResolvedValue({
    blob: new Blob(['report']),
    filename: '七年级成绩.xlsx',
  })
  const NativeURL = globalThis.URL
  class TestURL extends NativeURL {}
  Object.defineProperties(TestURL, {
    createObjectURL: {
      configurable: true,
      value: vi.fn(() => 'blob:download'),
    },
    revokeObjectURL: {
      configurable: true,
      value: vi.fn(),
    },
  })
  vi.stubGlobal('URL', TestURL)
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('file center view', () => {
  it('presents reports, expiry recovery and training materials in one ledger', async () => {
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('七年级成绩.xlsx'))

    expect(host.querySelector('h1')?.textContent).toBe('文件中心')
    expect(host.textContent).toContain('成绩表')
    expect(host.textContent).toContain('批注原卷')
    expect(host.textContent).toContain('文件已过期，可重新生成')
    expect(host.textContent).toContain('TRAIN-12')
    expect(host.textContent).toContain('生成失败')
    expect(host.querySelector('[data-testid="download-report-41"]')).not.toBeNull()
    expect(host.querySelector('[data-testid="retry-training-51"]')).not.toBeNull()
  })

  it('submits, retries and downloads through explicit actions', async () => {
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('七年级成绩.xlsx'))

    host.querySelector<HTMLButtonElement>('[data-testid="generate-annotated_original_pdf"]')!.click()
    await vi.waitFor(() => expect(apiMock.submitReport).toHaveBeenCalledWith(
      7,
      'annotated_original_pdf',
      false,
    ))

    host.querySelector<HTMLButtonElement>('[data-testid="export-training-12"]')!.click()
    await vi.waitFor(() => expect(apiMock.submitTrainingExport).toHaveBeenCalled())

    host.querySelector<HTMLButtonElement>('[data-testid="retry-training-51"]')!.click()
    await vi.waitFor(() => expect(apiMock.retryTrainingExport).toHaveBeenCalledWith(51))

    host.querySelector<HTMLButtonElement>('[data-testid="download-report-41"]')!.click()
    await vi.waitFor(() => expect(apiMock.downloadJobFile).toHaveBeenCalledWith(41))
    await vi.waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:download'))
  })

  it('shows a report download as soon as its tracked job completes without a manual refresh', async () => {
    const queuedPdf = makeJob({
      id: 61,
      payload: {
        session_id: 7,
        report_type: 'annotated_original_pdf',
        score_revision: 'a'.repeat(64),
      },
      result: {},
      status: 'queued',
      progress: 0,
      updated_at: '2026-07-17T10:01:00Z',
      finished_at: null,
    })
    const pendingContext = {
      score_revision: 'a'.repeat(64),
      has_results: true,
      jobs: [{
        ...queuedPdf,
        is_current_revision: true,
        file_status: 'pending' as const,
      }],
      total: 1,
      page: 1,
      page_size: 100,
      total_pages: 1,
    }
    const availablePdf = makeJob({
      ...queuedPdf,
      status: 'succeeded',
      progress: 1,
      result: {
        session_id: 7,
        report_type: 'annotated_original_pdf',
        score_revision: 'a'.repeat(64),
        filename: '七年级批注原卷.pdf',
        download_url: '/api/jobs/61/download',
      },
      updated_at: '2026-07-17T10:01:02Z',
      finished_at: '2026-07-17T10:01:02Z',
    })
    apiMock.submitReport.mockResolvedValue(queuedPdf)

    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('七年级成绩.xlsx'))

    apiMock.getReportContext.mockResolvedValue(pendingContext)
    host.querySelector<HTMLButtonElement>(
      '[data-testid="generate-annotated_original_pdf"]',
    )!.click()
    await vi.waitFor(() => expect(apiMock.getReportContext).toHaveBeenCalledTimes(2))

    apiMock.getReportContext.mockResolvedValue({
      ...pendingContext,
      jobs: [{
        ...availablePdf,
        is_current_revision: true,
        file_status: 'available' as const,
      }],
    })
    useJobStore().track(availablePdf)

    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="download-report-61"]'),
    ).not.toBeNull())
    expect(apiMock.getReportContext).toHaveBeenCalledTimes(3)
  })

  it('does not let an old report submission refresh a session the user has left', async () => {
    const pending = deferred<JobResponse>()
    apiMock.submitReport.mockImplementation(() => pending.promise)
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('七年级成绩.xlsx'))

    host.querySelector<HTMLButtonElement>(
      '[data-testid="generate-annotated_original_pdf"]',
    )!.click()
    useSessionStore().selectedSessionId = null
    await settle()
    pending.resolve(makeJob({
      id: 61,
      payload: {
        session_id: 7,
        report_type: 'annotated_original_pdf',
        score_revision: 'a'.repeat(64),
      },
      result: {},
      status: 'queued',
      progress: 0,
    }))
    await settle()

    expect(apiMock.getReportContext).toHaveBeenCalledOnce()
    expect(host.textContent).toContain('请先在顶部选择考试')
  })

  it('does not show an old report submission error after the user changes sessions', async () => {
    const pending = deferred<JobResponse>()
    apiMock.submitReport.mockImplementation(() => pending.promise)
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('七年级成绩.xlsx'))

    host.querySelector<HTMLButtonElement>(
      '[data-testid="generate-annotated_original_pdf"]',
    )!.click()
    useSessionStore().selectedSessionId = 8
    await settle()
    pending.reject(new Error('old request failed'))
    await settle()

    expect(host.textContent).not.toContain('文件生成请求未能提交')
  })

  it('opens visible Excel settings beside export actions and previews hidden names', async () => {
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('七年级成绩.xlsx'))

    host.querySelector<HTMLButtonElement>('[data-testid="configure-score-excel"]')!.click()
    await settle()

    expect(host.querySelector('[data-testid="excel-settings-dialog"]')).not.toBeNull()
    expect(
      host.querySelector<HTMLInputElement>('[data-testid="excel-hide-bottom-n"]')?.value,
    ).toBe('8')
    expect(
      host.querySelector('[data-testid="excel-preview-statistical"]')?.textContent,
    ).toContain('10')
    expect(
      host.querySelector('[data-testid="excel-preview-hidden"]')?.textContent,
    ).toContain('8')

    host.querySelector<HTMLInputElement>('[data-testid="excel-manual-enabled"]')!.click()
    await settle()
    host.querySelector<HTMLInputElement>('[data-testid="excel-student-1"]')!.click()
    await settle()
    expect(
      host.querySelector('[data-testid="excel-preview-hidden"]')?.textContent,
    ).toContain('9')
    expect(
      host.querySelector('[data-testid="excel-preview-visible"]')?.textContent,
    ).toContain('1')

    host.querySelector<HTMLButtonElement>('[data-testid="submit-score-excel"]')!.click()
    await vi.waitFor(() => expect(apiMock.submitReport).toHaveBeenCalledWith(
      7,
      'score_excel',
      false,
      {
        hide_bottom_enabled: true,
        hide_bottom_n: 8,
        manual_hidden_student_ids: [1],
      },
    ))
  })

  it('exports a selected training variant and cancels a queued job', async () => {
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('TRAIN-12'))

    host.querySelector<HTMLButtonElement>('[data-testid="configure-training-12"]')!.click()
    await vi.waitFor(() => expect(apiMock.getTrainingTask).toHaveBeenCalledWith(
      12,
      expect.any(AbortSignal),
    ))
    await vi.waitFor(() => expect(host.querySelector('[data-testid="training-export-form"]')).not.toBeNull())

    const mode = host.querySelector<HTMLSelectElement>('[data-testid="training-mode"]')!
    mode.value = 'variant'
    mode.dispatchEvent(new Event('change', { bubbles: true }))
    await settle()
    const format = host.querySelector<HTMLSelectElement>('[data-testid="training-format"]')!
    format.value = 'markdown'
    format.dispatchEvent(new Event('change', { bubbles: true }))
    const audience = host.querySelector<HTMLSelectElement>('[data-testid="training-audience"]')!
    audience.value = 'teacher'
    audience.dispatchEvent(new Event('change', { bubbles: true }))
    host.querySelector<HTMLButtonElement>('[data-testid="submit-training-choice"]')!.click()

    await vi.waitFor(() => expect(apiMock.submitTrainingExport).toHaveBeenCalledWith(12, {
      variant_id: 3,
      format: 'markdown',
      audience: 'teacher',
    }))

    host.querySelector<HTMLButtonElement>('[data-testid="generate-annotated_original_pdf"]')!.click()
    await vi.waitFor(() => expect(host.querySelector('[data-testid="cancel-job-61"]')).not.toBeNull())
    const cancelSpy = vi.spyOn(useJobStore(), 'cancel').mockResolvedValue()
    host.querySelector<HTMLButtonElement>('[data-testid="cancel-job-61"]')!.click()
    await vi.waitFor(() => expect(cancelSpy).toHaveBeenCalledWith(61))
  })

  it('does not guess an exam when none is selected', async () => {
    const { host } = await mountView(null)

    expect(host.textContent).toContain('请先在顶部选择考试')
    expect(apiMock.getReportContext).not.toHaveBeenCalled()
  })

  it('disables report generation when the exam has no saved results', async () => {
    apiMock.getReportContext.mockResolvedValueOnce({
      score_revision: '0'.repeat(64),
      has_results: false,
      jobs: [],
      total: 0,
      page: 1,
      page_size: 100,
      total_pages: 1,
    })

    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain(
      '当前考试还没有已保存成绩',
    ))

    expect(
      host.querySelector<HTMLButtonElement>('[data-testid="generate-score_excel"]')
        ?.disabled,
    ).toBe(true)
    expect(
      host.querySelector<HTMLButtonElement>(
        '[data-testid="generate-annotated_original_pdf"]',
      )?.disabled,
    ).toBe(true)
  })

  it('never presents an old score revision as the current download', async () => {
    apiMock.getReportContext.mockResolvedValueOnce({
      score_revision: 'a'.repeat(64),
      has_results: true,
      jobs: [{
        ...makeJob({
          payload: {
            session_id: 7,
            report_type: 'score_excel',
            score_revision: 'b'.repeat(64),
          },
        }),
        is_current_revision: false,
        file_status: 'available',
      }],
      total: 1,
      page: 1,
      page_size: 100,
      total_pages: 1,
    })

    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('成绩已变化，需重新生成'))

    expect(host.querySelector('[data-testid="download-report-41"]')).toBeNull()
    expect(host.querySelector('[data-testid="generate-score_excel"]')).not.toBeNull()
  })
})
