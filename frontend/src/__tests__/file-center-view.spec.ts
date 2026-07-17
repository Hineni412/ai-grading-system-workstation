import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { JobResponse } from '../api/jobs'
import { createAppRouter } from '../router'
import { useJobStore } from '../stores/jobs'
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
  const router = createAppRouter(createMemoryHistory())
  await router.push('/files')
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
  vi.stubGlobal('URL', {
    createObjectURL: vi.fn(() => 'blob:download'),
    revokeObjectURL: vi.fn(),
  })
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
