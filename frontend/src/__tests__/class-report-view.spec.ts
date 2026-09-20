import { createApp, h, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ClassAnalysisResponse } from '../api/class-analysis'
import type { JobResponse } from '../api/jobs'
import { useJobStore } from '../stores/jobs'
import { useSessionStore } from '../stores/session'
import ClassReportView from '../views/ClassReportView.vue'

const apiMock = vi.hoisted(() => ({
  getClassAnalysis: vi.fn(),
  updateSettings: vi.fn(),
  regenerate: vi.fn(),
  getQuestionPreview: vi.fn(),
}))

const modelProfilesMock = vi.hoisted(() => ({
  getState: vi.fn(),
}))

const jobsMock = vi.hoisted(() => ({
  getJob: vi.fn(),
  cancelJob: vi.fn(),
  getJobStatusBatch: vi.fn(),
}))

vi.mock('../api/class-analysis', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/class-analysis')>(),
  classAnalysisApi: apiMock,
}))

vi.mock('../api/model-profiles', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/model-profiles')>(),
  modelProfilesApi: modelProfilesMock,
}))

vi.mock('../api/jobs', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/jobs')>(),
  jobApi: jobsMock,
}))

function makeJob(overrides: Partial<JobResponse> = {}): JobResponse {
  return {
    id: 91,
    job_type: 'class_analysis',
    payload: { session_id: 7 },
    result: {},
    status: 'running',
    progress: 0.4,
    stage: 'class_analysis',
    detail: 'generating',
    error: null,
    cancel_requested: false,
    created_at: '2026-08-30T10:00:00Z',
    started_at: '2026-08-30T10:00:01Z',
    updated_at: '2026-08-30T10:00:01Z',
    finished_at: null,
    ...overrides,
  }
}

function makeAnalysis(overrides: Partial<ClassAnalysisResponse> = {}): ClassAnalysisResponse {
  return {
    status: 'ready',
    auto_generate: true,
    small_sample: false,
    class_names: ['一班', '二班'],
    selected_class: '一班',
    data: null,
    narrative: {
      key_findings: [{ title: '两极分化严重', detail: '出现 47 分断层', severity: 'high' }],
      common_issues: [],
      student_notes: [],
      grouping_advice: '高分组布置压轴变式。',
    },
    narrative_failed: false,
    generated_at: '2026-08-30T10:00:00Z',
    stale: false,
    active_job_id: null,
    ...overrides,
  }
}

const mounted: App[] = []

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountView(query = 'session=7') {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().$patch({
    selectedSessionId: 7,
    loadState: 'ready',
    sessions: [{
      id: 7,
      name: '合成班级报告验证',
      status: 'grading',
      is_deleted: false,
      deleted_at: null,
      created_at: null,
      updated_at: null,
    }],
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/class-report', component: ClassReportView },
      { path: '/results', component: { render: () => h('div', '合成成绩中心') } },
    ],
  })
  await router.push(`/class-report?${query}`)
  await router.isReady()
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp({ render: () => h(RouterView) })
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
  apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis())
  apiMock.updateSettings.mockImplementation(
    async (_id: number, autoGenerate: boolean) => ({ auto_generate: autoGenerate }),
  )
  apiMock.regenerate.mockResolvedValue(makeJob({ id: 92, status: 'queued', progress: 0 }))
  jobsMock.getJob.mockResolvedValue(makeJob())
  jobsMock.getJobStatusBatch.mockResolvedValue([
    { id: 91, found: true, job: makeJob() },
  ])
  modelProfilesMock.getState.mockResolvedValue({
    profiles: [],
    active_profile_name: null,
    active_profile: null,
    task_bindings: {
      content_generation: { profile_name: '默认内容服务', model: 'qwen-plus' },
      grading: { profile_name: '默认内容服务', model: 'qwen-plus' },
      class_teacher: { profile_name: '默认内容服务', model: 'qwen-plus' },
    },
  })
})

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  try {
    useJobStore().stopAllPolling()
  } catch {
    // 无活动 pinia 时无需清理。
  }
  document.body.innerHTML = ''
})

describe('class report view', () => {
  it('embeds the generated class report for the selected class', async () => {
    const { host } = await mountView()
    await vi.waitFor(() => expect(
      host.querySelector<HTMLIFrameElement>('[data-testid="class-report-frame"]'),
    ).not.toBeNull())
    const frame = host.querySelector<HTMLIFrameElement>('[data-testid="class-report-frame"]')!
    expect(frame.src).toContain('/api/sessions/7/class-analysis/report')
    expect(frame.src).toContain('class_name=')
    expect(apiMock.getClassAnalysis).toHaveBeenCalledWith(
      7, expect.any(AbortSignal), undefined, 'narrative',
    )
    expect(host.textContent).toContain('AI 分析 · 仅供参考')
  })

  it('switches the report class through the selector', async () => {
    const { host } = await mountView()
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="class-report-frame"]'),
    ).not.toBeNull())
    const select = host.querySelector<HTMLSelectElement>('select[aria-label="报告班级"]')!
    select.value = '二班'
    select.dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect(apiMock.getClassAnalysis).toHaveBeenLastCalledWith(
      7, expect.any(AbortSignal), '二班', 'narrative',
    ))
  })

  it('offers generation when no narrative exists and submits only after confirmation', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      narrative: null,
      generated_at: null,
    }))
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('尚未生成班级报告'))
    expect(host.querySelector('[data-testid="class-report-frame"]')).toBeNull()

    host.querySelector<HTMLButtonElement>('[data-testid="class-report-generate-empty"]')!.click()
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="regenerate-confirm-dialog"]'),
    ).not.toBeNull())
    expect(host.textContent).toContain('默认内容服务')
    expect(apiMock.regenerate).not.toHaveBeenCalled()

    host.querySelector<HTMLButtonElement>('[data-testid="regenerate-confirm"]')!.click()
    await vi.waitFor(() => expect(apiMock.regenerate).toHaveBeenCalledWith(7))
    expect(useJobStore().jobs[92]).toBeDefined()
  })

  it('shows the failed state when narrative generation failed', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      narrative: null,
      narrative_failed: true,
      generated_at: null,
    }))
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('班级报告生成失败'))
    expect(host.textContent).toContain('重新生成分析')
  })

  it('shows the generating state and reloads when the tracked job finishes', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      status: 'generating',
      narrative: null,
      generated_at: null,
      active_job_id: 91,
    }))
    const { host } = await mountView()
    await vi.waitFor(() => expect(jobsMock.getJob).toHaveBeenCalledWith(91))
    expect(host.textContent).toContain('班级报告生成中')

    useJobStore().track(makeJob({
      status: 'succeeded',
      progress: 1,
      updated_at: '2026-08-30T10:02:00Z',
      finished_at: '2026-08-30T10:02:00Z',
    }))
    await vi.waitFor(() => expect(apiMock.getClassAnalysis).toHaveBeenCalledTimes(2))
  })

  it('warns when the saved report is stale relative to current scores', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({ stale: true }))
    const { host } = await mountView()
    await vi.waitFor(() => expect(
      host.querySelector('.class-analysis__banner'),
    ).not.toBeNull())
    expect(host.textContent).toContain('建议重新生成')
  })

  it('persists the auto-generate toggle and rolls back on failure', async () => {
    const { host } = await mountView()
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="class-report-frame"]'),
    ).not.toBeNull())
    const toggle = host.querySelector<HTMLInputElement>('input[type="checkbox"]')!
    expect(toggle.checked).toBe(true)

    toggle.click()
    await vi.waitFor(() => expect(apiMock.updateSettings).toHaveBeenCalledWith(7, false))

    apiMock.updateSettings.mockRejectedValueOnce(new Error('save failed'))
    toggle.click()
    await settle()
    expect(toggle.checked).toBe(false)
  })

  it('returns to the results analysis tab', async () => {
    const { host, router } = await mountView()
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="class-report-frame"]'),
    ).not.toBeNull())
    host.querySelector<HTMLButtonElement>('.class-report__hero button')!.click()
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/results'))
    expect(router.currentRoute.value.query).toMatchObject({ tab: 'analysis', session: '7' })
  })
})
