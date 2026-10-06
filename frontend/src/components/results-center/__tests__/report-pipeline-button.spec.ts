import { createApp, h, nextTick, ref, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { JobResponse, JobStatus } from '../../../api/jobs'
import type { ReportPipelineStatus } from '../../../api/report-pipeline'
import ReportPipelineButton from '../ReportPipelineButton.vue'
import { cachedPipelineStatus, invalidatePipelineStatus } from '../pipeline-status-cache'

const pipelineMock = vi.hoisted(() => ({ getStatus: vi.fn(), start: vi.fn() }))
vi.mock('../../../api/report-pipeline', async (original) => ({
  ...await original<typeof import('../../../api/report-pipeline')>(),
  reportPipelineApi: pipelineMock,
}))

const classAnalysisMock = vi.hoisted(() => ({ updateSettings: vi.fn() }))
vi.mock('../../../api/class-analysis', async (original) => ({
  ...await original<typeof import('../../../api/class-analysis')>(),
  classAnalysisApi: classAnalysisMock,
}))

const jobsMock = vi.hoisted(() => ({
  getJob: vi.fn(),
  getJobStatusBatch: vi.fn(async () => []),
  cancelJob: vi.fn(),
}))
vi.mock('../../../api/jobs', async (original) => ({
  ...await original<typeof import('../../../api/jobs')>(),
  jobApi: jobsMock,
}))

function status(overrides: Partial<ReportPipelineStatus> = {}): ReportPipelineStatus {
  return {
    auto_generate: true,
    configured: true,
    service_name: '默认内容服务',
    model_name: 'qwen-plus',
    active_job_id: null,
    review_pending: 0,
    causes: { pending_questions: 2, total_questions: 4, call_count: 3, estimated_tokens: 12000 },
    class_reports: { pending: 2, total: 2 },
    personal_reports: { pending: 3, total: 4, call_count: 5, cache_hits: 1, estimated_tokens: 34000 },
    complete: false,
    ...overrides,
  }
}

function job(jobStatus: JobStatus): JobResponse {
  return {
    id: 91,
    job_type: 'class_analysis_generate',
    payload: { session_id: 7, kind: 'pipeline', mode: 'manual' },
    result: {},
    status: jobStatus,
    progress: jobStatus === 'succeeded' ? 1 : 0.4,
    stage: 'report_pipeline',
    detail: '',
    error: null,
    cancel_requested: false,
    created_at: '2026-10-03T10:00:00Z',
    started_at: '2026-10-03T10:00:00Z',
    updated_at: '2026-10-03T10:00:01Z',
    finished_at: null,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

let app: App | null = null

function mountButton(activeJob?: ReturnType<typeof ref<JobResponse | null>>): HTMLElement {
  localStorage.clear()
  const host = document.createElement('div')
  document.body.append(host)
  const pinia = createPinia()
  setActivePinia(pinia)
  app = createApp({
    render: () => h(ReportPipelineButton, {
      sessionId: 7,
      activeJob: activeJob?.value ?? null,
    }),
  })
  app.use(pinia).mount(host)
  return host
}

function openButton(host: HTMLElement): HTMLButtonElement {
  return host.querySelector<HTMLButtonElement>('[data-testid="report-pipeline-open"]')!
}

describe('ReportPipelineButton', () => {
  afterEach(() => {
    app?.unmount()
    app = null
    document.body.innerHTML = ''
    invalidatePipelineStatus()
    vi.clearAllMocks()
  })

  it('prefetches status on mount and shows the model-call badge', async () => {
    pipelineMock.getStatus.mockResolvedValue(status())
    const host = mountButton()
    await vi.waitFor(() => expect(pipelineMock.getStatus).toHaveBeenCalledWith(7))
    await vi.waitFor(() => expect(cachedPipelineStatus(7)).not.toBeNull())
    const button = openButton(host)
    await vi.waitFor(() => expect(button.textContent).toContain('· 10'))
    expect(button.textContent).toContain('AI 整理')
    expect(button.title).toBe('预计调用模型 10 次')
    expect(button.dataset.variant).toBe('secondary')
  })

  it.each([
    ['全部已整理', status({ complete: true })],
    ['未配置模型', status({ configured: false })],
    ['没有待办', status({
      causes: { pending_questions: 0, total_questions: 4, call_count: 0, estimated_tokens: 0 },
      class_reports: { pending: 0, total: 2 },
      personal_reports: { pending: 0, total: 4, call_count: 0, cache_hits: 0, estimated_tokens: 0 },
    })],
  ])('hides the badge when %s', async (_name, value) => {
    pipelineMock.getStatus.mockResolvedValue(value)
    const host = mountButton()
    await vi.waitFor(() => expect(cachedPipelineStatus(7)).not.toBeNull())
    const button = openButton(host)
    expect(button.textContent).toContain('AI 整理')
    expect(button.textContent).not.toContain('·')
    expect(button.title).toBe('')
  })

  it('shows the cached status instantly on open and keeps confirm disabled while revalidating', async () => {
    pipelineMock.getStatus.mockResolvedValue(status())
    const host = mountButton()
    await vi.waitFor(() => expect(cachedPipelineStatus(7)).not.toBeNull())
    // 第二次请求挂起：弹窗必须立即显示缓存内容，并在核对完成前禁用确认。
    const pending = deferred<ReportPipelineStatus>()
    pipelineMock.getStatus.mockImplementation(() => pending.promise)
    openButton(host).click()
    await nextTick()
    expect(document.querySelector('[data-testid="state-panel"][data-kind="loading"]')).toBeNull()
    expect(document.querySelector('[data-testid="pipeline-service"]')?.textContent)
      .toContain('默认内容服务')
    expect(document.querySelector('[data-testid="pipeline-revalidating"]')?.textContent)
      .toContain('正在核对最新状态')
    const confirm = document.querySelector<HTMLButtonElement>('[data-testid="pipeline-confirm"]')!
    expect(confirm.disabled).toBe(true)
    pending.resolve(status({ model_name: 'qwen-max' }))
    await vi.waitFor(() => expect(
      document.querySelector('[data-testid="pipeline-revalidating"]'),
    ).toBeNull())
    expect(confirm.disabled).toBe(false)
    expect(document.querySelector('[data-testid="pipeline-model"]')?.textContent)
      .toContain('qwen-max')

    // 关闭后再次打开：缓存保留，先显示上次核对的模型，再后台复核。
    const pendingAgain = deferred<ReportPipelineStatus>()
    pipelineMock.getStatus.mockImplementation(() => pendingAgain.promise)
    const cancel = [...document.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === '取消')!
    cancel.click()
    await vi.waitFor(() => expect(
      document.querySelector('[data-testid="report-pipeline-dialog"]'),
    ).toBeNull())
    openButton(host).click()
    await nextTick()
    expect(document.querySelector('[data-testid="state-panel"][data-kind="loading"]')).toBeNull()
    expect(document.querySelector('[data-testid="pipeline-model"]')?.textContent)
      .toContain('qwen-max')
    expect(document.querySelector('[data-testid="pipeline-revalidating"]')).not.toBeNull()
    pendingAgain.resolve(status())
  })

  it('revalidates once the tracked job reaches a terminal status', async () => {
    const activeJob = ref<JobResponse | null>(null)
    pipelineMock.getStatus.mockResolvedValue(status())
    mountButton(activeJob)
    await vi.waitFor(() => expect(pipelineMock.getStatus).toHaveBeenCalledTimes(1))
    activeJob.value = job('running')
    await nextTick()
    expect(pipelineMock.getStatus).toHaveBeenCalledTimes(1)
    activeJob.value = job('succeeded')
    await vi.waitFor(() => expect(pipelineMock.getStatus).toHaveBeenCalledTimes(2))
  })
})
