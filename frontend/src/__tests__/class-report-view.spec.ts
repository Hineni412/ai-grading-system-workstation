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

  it('renders the data report with a pending banner when no AI narrative exists', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      narrative: null,
      generated_at: null,
    }))
    const { host } = await mountView()
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="class-report-frame"]'),
    ).not.toBeNull())
    expect(
      host.querySelector('[data-testid="class-report-frame"]')!.getAttribute('src'),
    ).toContain('/api/sessions/7/class-analysis/report')
    expect(host.querySelector('[data-testid="class-report-banner"]')?.textContent)
      .toContain('AI 讲评部分尚未整理')
    expect(host.querySelector('[data-testid="class-report-generate-empty"]')).toBeNull()
  })

  it('warns when the saved report is stale relative to current scores', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({ stale: true }))
    const { host } = await mountView()
    await vi.waitFor(() => expect(
      host.querySelector('.class-analysis__banner'),
    ).not.toBeNull())
    expect(host.textContent).toContain('AI 分析可能与当前成绩不一致')
    expect(host.textContent).toContain('「AI 整理」')
  })

})
