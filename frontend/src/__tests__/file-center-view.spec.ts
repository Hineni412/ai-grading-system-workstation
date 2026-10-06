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
const personalMock = vi.hoisted(() => ({ states: vi.fn(), exams: vi.fn(), bundle: vi.fn() }))
vi.mock('../api/personal-reports', async original => ({
  ...await original<typeof import('../api/personal-reports')>(),
  personalReportsApi: { ...(await original<typeof import('../api/personal-reports')>()).personalReportsApi, ...personalMock },
}))
const storageMock = vi.hoisted(() => ({ getOriginals: vi.fn() }))

vi.mock('../api/ops', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/ops')>(),
  storageApi: storageMock,
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

async function mountView(
  selectedSessionId: number | null = 7,
  props: { embedded?: boolean; variant?: 'page' | 'popover' } = {},
) {
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
  const app = createApp(FileCenterView, props)
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
  personalMock.states.mockResolvedValue(Array.from({length: 10}, (_, index) => ({student_id: index + 1, status: 'missing', generated_at: null, reason: null})))
  personalMock.bundle.mockResolvedValue(makeJob({job_type: 'personal_report_bundle', status: 'queued', result: {}}))
  storageMock.getOriginals.mockResolvedValue({ originals_state: 'complete' })
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

  it.each(['page', 'popover'] as const)('disables original regeneration after cleanup in %s mode', async (variant) => {
    storageMock.getOriginals.mockResolvedValue({ originals_state: 'cleared' })
    const context = await apiMock.getReportContext()
    const oldOriginal = context.jobs.find((job: JobResponse) => job.payload.report_type === 'annotated_original_pdf')!
    apiMock.getReportContext.mockResolvedValue({ ...context, jobs: [...context.jobs, { ...oldOriginal, id: 39 }] })
    const { host } = await mountView(7, { variant })
    await vi.waitFor(() => expect(host.textContent).toContain('原卷已清理，不能再导出批注原卷。'))
    const generate = host.querySelector<HTMLButtonElement>('[data-testid="generate-annotated_original_pdf"]')!
    expect(generate.disabled).toBe(true)
    const row = generate.closest(variant === 'popover' ? '.file-center__row' : 'tr')!
    expect(row.textContent!.split('原卷已清理，不能再导出批注原卷。')).toHaveLength(2)
    const history = [...row.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent!.trim().startsWith('历史'))!
    history.click()
    await settle()
    const historyScope = variant === 'popover' ? row : row.nextElementSibling!
    const regenerate = [...row.querySelectorAll<HTMLButtonElement>('button'), ...historyScope.querySelectorAll<HTMLButtonElement>('button')].filter(button => button.textContent!.trim() === '重新生成')
    expect(regenerate.length).toBeGreaterThanOrEqual(2)
    for (const button of regenerate) {
      expect(button.disabled).toBe(true)
      button.click()
    }
    expect(apiMock.submitReport).not.toHaveBeenCalled()
  })

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

  it('offers only the export entry on the personal report card', async () => {
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('学生个人分析报告'))

    const cardTitle = [...host.querySelectorAll('th[title]')]
      .find((cell) => cell.textContent?.includes('学生个人分析报告'))
    expect(cardTitle?.getAttribute('title')).toContain('AI 部分由成绩中心「AI 整理」生成')
    expect(host.querySelector('[data-testid="generate-personal_analysis_html"]')).toBeNull()
    expect(host.querySelector('[data-testid="regenerate-personal_analysis_html"]')).toBeNull()
    expect(host.querySelector('[data-testid="export-personal-reports"]')).not.toBeNull()
    expect(apiMock.getAnalysisPreflight).not.toHaveBeenCalled()
  })

  it('links a finished personal report job to the review notes in results center', async () => {
    apiMock.getReportContext.mockResolvedValueOnce({
      score_revision: 'a'.repeat(64),
      has_results: true,
      jobs: [{
        ...makeJob({
          id: 55,
          payload: {
            session_id: 7,
            report_type: 'personal_analysis_html',
            score_revision: 'a'.repeat(64),
          },
          result: {
            session_id: 7,
            report_type: 'personal_analysis_html',
            score_revision: 'a'.repeat(64),
            filename: '个人分析报告.zip',
            download_url: '/api/jobs/55/download',
            review_note_count: 2,
          },
        }),
        is_current_revision: true,
        file_status: 'available',
      }],
      total: 1,
      page: 1,
      page_size: 100,
      total_pages: 1,
    })

    const { host, router } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('发现 2 条建议核对'))
    host.querySelector<HTMLButtonElement>('[data-testid="open-review-notes"]')!.click()
    await vi.waitFor(() => expect(
      router.currentRoute.value.query.tab,
    ).toBe('overview'))
    expect(router.currentRoute.value.query).toMatchObject({
      tab: 'overview',
      session: '7',
    })
  })

})

describe('personal report export selection', () => {
  it('loads the current student list when exporting from the standalone file center', async () => {
    const { host } = await mountView()
    const store = useResultsCenterStore()
    const snapshot = store.results
    store.reset()
    const load = vi.spyOn(store, 'load').mockImplementation(async sid => {
      store.$patch({ sessionId: sid, results: snapshot, state: 'ready' })
    })
    await vi.waitFor(() => expect(host.querySelector('[data-testid="export-personal-reports"]')).not.toBeNull())
    const trigger = host.querySelector<HTMLButtonElement>('[data-testid="export-personal-reports"]')!
    trigger.focus(); trigger.click()
    await vi.waitFor(() => expect(document.querySelector('.pe-summary')?.textContent).toContain('10 人 × 1 场'))
    expect(load).toHaveBeenCalledWith(7)
    document.querySelector<HTMLInputElement>('input[type="radio"][value="pick"]')!.click()
    await nextTick()
    document.querySelector<HTMLInputElement>('.pe-list input')!.click()
    await nextTick()
    expect(document.querySelector('.pe-filename')?.textContent).toContain('指定1人.zip')
    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))
    await vi.waitFor(() => expect(document.querySelector('.personal-export-dialog')).toBeNull())
    expect(document.activeElement).toBe(trigger)
  })
  it('summarizes three ranges, searches students and submits a selected multi-exam bundle', async () => {
    const { host } = await mountView(7, {variant: 'popover'})
    useSessionStore().sessions[0]!.curriculum_volume_id = 'test-volume'
    useSessionStore().sessions.push({id: 8, name: '第二场合成考试', curriculum_volume_id: 'test-volume', status: 'completed', is_deleted: false, deleted_at: null, created_at: null, updated_at: null})
    personalMock.states.mockResolvedValue(Array.from({length: 10}, (_, i) => ({student_id: i + 1, status: i < 3 ? 'current' : 'missing', generated_at: null, reason: null})))
    await vi.waitFor(() => expect(host.querySelector('[data-testid="export-personal-reports"]')).not.toBeNull())
    host.querySelector<HTMLButtonElement>('[data-testid="export-personal-reports"]')!.click()
    await vi.waitFor(() => expect(document.querySelector('.pe-summary')?.textContent).toContain('10 人 × 1 场'))
    expect(document.querySelector('.pe-filename')?.textContent).toContain('全部_10人.zip')
    const radio = document.querySelector<HTMLInputElement>('input[type="radio"][value="class"]')!
    radio.click()
    await nextTick()
    document.querySelector<HTMLInputElement>('.pe-classes input')!.click()
    await nextTick()
    expect(document.querySelector('.pe-filename')?.textContent).toContain('一班_10人.zip')
    document.querySelector<HTMLInputElement>('input[type="radio"][value="pick"]')!.click()
    await nextTick()
    const search = document.querySelector<HTMLInputElement>('input[aria-label="搜索学生"]')!
    search.value = 'S001'; search.dispatchEvent(new Event('input', {bubbles: true}))
    await nextTick()
    expect(document.querySelectorAll('.pe-list label')).toHaveLength(1)
    document.querySelector<HTMLInputElement>('.pe-list input')!.click()
    await nextTick()
    expect(document.querySelector('.pe-filename')?.textContent).toContain('S001_学生01_七年级期末_个人报告.html')
    document.querySelectorAll<HTMLInputElement>('.pe-exams input')[1]!.click()
    await nextTick()
    expect(document.querySelector('.pe-summary')?.textContent).toContain('1 人 × 2 场')
    expect(document.querySelector('.pe-filename')?.textContent).toContain('本学期_个人报告_2场_1人.zip')
    expect(document.querySelector('.personal-export-dialog')?.textContent).not.toContain('PDF')
    document.querySelector<HTMLButtonElement>('.pe-primary')!.click()
    await vi.waitFor(() => expect(personalMock.bundle).toHaveBeenCalledWith([7, 8], [1], '指定1人'))
    expect(apiMock.submitReport).not.toHaveBeenCalled()
    await vi.waitFor(() => expect(host.textContent).toContain('完成后在任务中心下载'))
  })
})
