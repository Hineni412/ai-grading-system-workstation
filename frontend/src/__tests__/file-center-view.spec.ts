import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick, type App } from 'vue';
import { createMemoryHistory } from 'vue-router';

import type { JobResponse } from '../api/jobs';
import { createAppRouter } from '../router';

import { useResultsCenterStore } from '../stores/results-center';
import { useSessionStore } from '../stores/session';
import FileCenterView from '../views/FileCenterView.vue'

const apiMock = vi.hoisted(() => ({
  getReportContext: vi.fn(),
  submitReport: vi.fn(),
  getAnalysisPreflight: vi.fn(),
  deleteReportFile: vi.fn(),
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
  apiMock.submitReport.mockResolvedValue(makeJob({
    id: 61,
    status: 'queued',
    result: {},
  }))
  apiMock.getAnalysisPreflight.mockResolvedValue({
    report_type: 'personal_analysis_html',
    configured: true,
    service_name: '默认内容服务',
    model_name: 'qwen-plus',
    call_count: 10,
    estimated_total_tokens: 120000,
    cache_hits: 3,
    cause_call_count: 0,
    cause_total_questions: 0,
    cause_estimated_tokens: 0,
  })
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

  it('submits and downloads through explicit actions', async () => {
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('七年级成绩.xlsx'))

    host.querySelector<HTMLButtonElement>('[data-testid="generate-annotated_original_pdf"]')!.click()
    await vi.waitFor(() => expect(apiMock.submitReport).toHaveBeenCalledWith(
      7,
      'annotated_original_pdf',
      false,
    ))

    host.querySelector<HTMLButtonElement>('[data-testid="download-report-41"]')!.click()
    await vi.waitFor(() => expect(apiMock.downloadJobFile).toHaveBeenCalledWith(41))
    await vi.waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:download'))
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

  it('confirms preflight details before submitting an analysis report', async () => {
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('学生个人分析报告'))

    host.querySelector<HTMLButtonElement>(
      '[data-testid="generate-personal_analysis_html"]',
    )!.click()
    await vi.waitFor(() => expect(apiMock.getAnalysisPreflight).toHaveBeenCalledWith(
      7,
      'personal_analysis_html',
    ))
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="analysis-confirm-dialog"]'),
    ).not.toBeNull())

    expect(
      host.querySelector('[data-testid="analysis-service"]')?.textContent,
    ).toContain('默认内容服务')
    expect(
      host.querySelector('[data-testid="analysis-model"]')?.textContent,
    ).toContain('qwen-plus')
    expect(
      host.querySelector('[data-testid="analysis-call-count"]')?.textContent,
    ).toContain('10')
    expect(
      host.querySelector('[data-testid="analysis-tokens"]')?.textContent,
    ).toContain('120,000')
    expect(host.textContent).toContain('粗略估算')
    expect(host.textContent).toContain('其中 3 份复用已生成内容，不重复计费')
    expect(host.textContent).toContain('实际费用取决于服务商定价')
    expect(host.textContent).toContain('学生答卷图片')
    expect(host.textContent).toContain('图片用量另计')
    expect(host.textContent).toContain('AI 分析内容仅供参考')
    expect(apiMock.submitReport).not.toHaveBeenCalled()

    host.querySelector<HTMLButtonElement>('[data-testid="confirm-analysis"]')!.click()
    await vi.waitFor(() => expect(apiMock.submitReport).toHaveBeenCalledWith(
      7,
      'personal_analysis_html',
      false,
    ))
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="analysis-confirm-dialog"]'),
    ).toBeNull())
  })

})
