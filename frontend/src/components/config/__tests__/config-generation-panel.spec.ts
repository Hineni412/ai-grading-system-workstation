import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ConfigEditorResponse,
  ConfigGenerationRequest,
  ConfigSource,
} from '../../../api/config-workspace'
import type { JobResponse } from '../../../api/jobs'
import { useConfigWorkspaceStore } from '../../../stores/config-workspace'
import { useJobStore } from '../../../stores/jobs'
import ConfigGenerationPanel from '../ConfigGenerationPanel.vue'

function source(): ConfigSource {
  return {
    session_id: 7, source_id: 'a'.repeat(32), source_revision: 'b'.repeat(64),
    safe_filename: '数学卷.pdf', suffix: '.pdf', size_bytes: 4096,
    sha256_prefix: 'c'.repeat(12), parse_state: 'ready',
    questions: [
      { question_id: 'Q1', question_type: 'calculation', question_preview: '1',
        answer_preview: '1', answer_present: true, needs_review: false,
        local_answer_trusted: true, has_question_asset: false, has_answer_asset: false },
      { question_id: 'Q2', question_type: 'proof', question_preview: '2',
        answer_preview: '', answer_present: false, needs_review: false,
        local_answer_trusted: false, has_question_asset: false, has_answer_asset: false },
      { question_id: 'Q5', question_type: 'comprehensive', question_preview: '5',
        answer_preview: '', answer_present: false, needs_review: false,
        local_answer_trusted: false, has_question_asset: false, has_answer_asset: false },
    ],
  }
}

function job(overrides: Partial<JobResponse> = {}): JobResponse {
  return {
    id: 31, job_type: 'config_generation',
    payload: { session_id: 7, mode: 'generate', generation_mode: 'per_question',
      source_id: 'a'.repeat(32), source_revision: 'b'.repeat(64) },
    result: {}, status: 'running', progress: 0.4, stage: 'generating', detail: '',
    error: null, cancel_requested: false, created_at: '2026-07-15T00:00:00Z',
    started_at: '2026-07-15T00:00:01Z', updated_at: '2026-07-15T00:00:02Z',
    finished_at: null, ...overrides,
  }
}

function editor(): ConfigEditorResponse {
  return {
    session_id: 7, configured: true, revision: 'd'.repeat(64), rows: [],
    total_score: 100, issues: [], source: null,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

async function settle(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountPanel(options: {
  submitter?: (sessionId: number, request: ConfigGenerationRequest) => Promise<JobResponse>
  retryer?: (sessionId: number, jobId: number, questionIds: string[]) => Promise<JobResponse>
  editorLoader?: (sessionId: number) => Promise<ConfigEditorResponse>
} = {}) {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(ConfigGenerationPanel, options)
  app.mount(host)
  await nextTick()
  return { host, unmount: () => app.unmount() }
}

beforeEach(async () => {
  document.body.innerHTML = ''
  localStorage.clear()
  setActivePinia(createPinia())
  const configStore = useConfigWorkspaceStore()
  configStore.selectSession(7)
  configStore.setSource(source())
  await useJobStore().initialize({
    api: { getJob: vi.fn(), cancelJob: vi.fn() }, now: () => new Date(0),
    schedule: vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>), cancelScheduled: vi.fn(),
    pollIntervalMs: 2_000, maxBackoffMs: 30_000,
  })
})

describe('ConfigGenerationPanel', () => {
  it('explains both modes and submits a write only once while disabled', async () => {
    const pending = deferred<JobResponse>()
    const submitter = vi.fn((_sessionId: number, _request: ConfigGenerationRequest) => pending.promise)
    const mounted = await mountPanel({ submitter })

    expect(mounted.host.textContent).toContain('逐题生成')
    expect(mounted.host.textContent).toContain('整卷单次生成')
    const whole = mounted.host.querySelector<HTMLInputElement>('[aria-label="整卷单次生成"]')!
    whole.click()
    const submit = mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')!
    submit.click()
    submit.click()
    await nextTick()

    expect(submitter).toHaveBeenCalledOnce()
    expect(submitter.mock.calls[0]?.[1]).toMatchObject({ generation_mode: 'whole_document' })
    expect(submit.disabled).toBe(true)
    pending.resolve(job({ payload: { ...job().payload, generation_mode: 'whole_document' } }))
    await settle()
    expect(useConfigWorkspaceStore().jobId).toBe(31)
  })

  it('retries only checked failed questions and preserves successful counts', async () => {
    const retryer = vi.fn(async () => job({ id: 32, status: 'queued', progress: 0 }))
    const configStore = useConfigWorkspaceStore()
    const jobStore = useJobStore()
    jobStore.track(job({
      status: 'succeeded', progress: 1, result: {
        outcome: 'partial', total_questions: 5, generated_questions: 3,
        failed_count: 2, failed_question_ids: ['Q2', 'Q5'], retryable: true,
      }, finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({ retryer })

    expect(mounted.host.textContent).toContain('已成功 3 题')
    expect(mounted.host.textContent).toContain('失败 2 题')
    expect(mounted.host.querySelectorAll('input[type="checkbox"]')).toHaveLength(2)
    mounted.host.querySelector<HTMLInputElement>('[aria-label="选择失败题 Q5"]')!.click()
    await nextTick()
    mounted.host.querySelector<HTMLButtonElement>('button[name="重试所选题"]')!.click()
    await settle()

    expect(retryer).toHaveBeenCalledExactlyOnceWith(7, 31, ['Q5'])
    expect(configStore.jobId).toBe(32)
    expect(mounted.host.textContent).toContain('已成功 3 题')
    expect(mounted.host.textContent).toContain('当前恢复任务')
  })

  it('uses a fresh whole-document generation after whole mode failure', async () => {
    const submitter = vi.fn(async (_sessionId: number, _request: ConfigGenerationRequest) =>
      job({ id: 32, status: 'queued', progress: 0 }))
    const retryer = vi.fn()
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: 'failed', payload: { ...job().payload, generation_mode: 'whole_document' },
      detail: '应用重启后，本次生成已停止。', error: 'private failure',
      finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({ submitter, retryer })

    expect(mounted.host.textContent).toContain('应用重启后，本次生成已停止。')
    expect(mounted.host.textContent).not.toContain('private failure')
    mounted.host.querySelector<HTMLButtonElement>('button[name="重新整卷生成"]')!.click()
    await settle()
    expect(submitter.mock.calls[0]?.[1]).toMatchObject({ generation_mode: 'whole_document' })
    expect(retryer).not.toHaveBeenCalled()
  })

  it('does not relabel cancel-requested as cancelled before the terminal state', async () => {
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({ cancel_requested: true }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel()

    expect(mounted.host.textContent).toContain('正在等待当前模型请求返回')
    expect(mounted.host.textContent).not.toContain('已取消')
  })

  it.each([
    ['failed', {}, '生成失败'],
    ['succeeded', { outcome: 'complete' }, '生成完成'],
    ['succeeded', {
      outcome: 'partial', total_questions: 5, generated_questions: 3,
      failed_count: 2, failed_question_ids: ['Q2', 'Q5'], retryable: true,
    }, '部分完成'],
  ] as const)('uses terminal %s status even when cancel_requested remains true', async (
    status, result, expected,
  ) => {
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status, result, cancel_requested: true, progress: 1,
      finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({ editorLoader: vi.fn(async () => editor()) })

    expect(mounted.host.textContent).toContain(expected)
    expect(mounted.host.textContent).not.toContain('正在等待当前模型请求返回')
  })

  it('keeps the last Job visible on sync error and offers explicit refresh', async () => {
    const configStore = useConfigWorkspaceStore()
    const jobStore = useJobStore()
    jobStore.track(job())
    configStore.attachJob(31, configStore.captureGenerationContext())
    jobStore.syncErrors[31] = {
      kind: 'network', message: '任务状态暂时无法更新', retryable: true, requestId: 'safe-id',
    }
    const refresh = vi.spyOn(jobStore, 'refresh').mockResolvedValue()
    const mounted = await mountPanel()

    expect(mounted.host.textContent).toContain('已保留上次进度')
    expect(mounted.host.querySelector<HTMLProgressElement>('progress')?.value).toBe(0.4)
    mounted.host.querySelector<HTMLButtonElement>('button[name="重新同步"]')!.click()
    await settle()
    expect(refresh).toHaveBeenCalledWith(31)
  })

  it('reloads the authoritative editor after complete success and isolates an old session', async () => {
    const editorPending = deferred<ConfigEditorResponse>()
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: 'succeeded', progress: 1, result: {
        outcome: 'complete', total_questions: 3, generated_questions: 3,
        failed_count: 0, failed_question_ids: [], retryable: false,
      }, finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    await mountPanel({ editorLoader: vi.fn(() => editorPending.promise) })
    await nextTick()
    configStore.selectSession(8)
    editorPending.resolve(editor())
    await settle()

    expect(configStore.sessionId).toBe(8)
    expect(configStore.editor).toBeNull()
    expect(useJobStore().jobs[31]?.status).toBe('succeeded')
  })

  it('replaces the workspace with the authoritative editor after complete success', async () => {
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: 'succeeded', progress: 1, result: {
        outcome: 'complete', total_questions: 3, generated_questions: 3,
        failed_count: 0, failed_question_ids: [], retryable: false,
      }, finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    await mountPanel({ editorLoader: vi.fn(async () => editor()) })
    await settle()

    expect(configStore.editor).toEqual(editor())
    expect(configStore.phase).toBe('editor')
  })

  it('keeps an old submitted Job tracked without attaching it to a new session', async () => {
    const pending = deferred<JobResponse>()
    const submitter = vi.fn((_sessionId: number, _request: ConfigGenerationRequest) => pending.promise)
    const configStore = useConfigWorkspaceStore()
    const mounted = await mountPanel({ submitter })
    mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')!.click()
    await nextTick()
    configStore.selectSession(8)
    pending.resolve(job())
    await settle()

    expect(configStore.sessionId).toBe(8)
    expect(configStore.jobId).toBeNull()
    expect(useJobStore().jobs[31]).toEqual(job())
  })

  it.each([
    ['queued', '等待开始'],
    ['running', '正在生成'],
    ['paused', '生成已暂停'],
    ['succeeded', '生成完成'],
    ['failed', '生成失败'],
    ['cancelled', '已取消'],
  ] as const)('shows the %s terminal state truthfully', async (status, copy) => {
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({ status, finished_at: '2026-07-15T00:01:00Z' }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({ editorLoader: vi.fn(async () => editor()) })
    expect(mounted.host.textContent).toContain(copy)
  })
})
