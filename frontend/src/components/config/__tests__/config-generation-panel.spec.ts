import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ConfigEditorResponse,
  ConfigGenerationRequest,
  ConfigSource,
} from '../../../api/config-workspace'
import type { JobResponse } from '../../../api/jobs'
import { ApiError } from '../../../api/errors'
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
  retryer?: (sessionId: number, jobId: number, questionIds: string[], requestToken: string) => Promise<JobResponse>
  editorLoader?: (sessionId: number) => Promise<ConfigEditorResponse>
  generationLoader?: (sessionId: number, requestToken: string) => Promise<JobResponse>
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
    const submitter = vi.fn((_sessionId: number, _request: ConfigGenerationRequest) => {
      void _sessionId
      void _request
      return pending.promise
    })
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

  it('reconciles a lost generation response from the authoritative server record', async () => {
    const timeout = new ApiError({ kind: 'network', status: null, code: 'network_error',
      message: 'offline', details: {}, requestId: 'safe', retryable: true })
    const submitter = vi.fn(async () => { throw timeout })
    const recovered = job({ id: 44, status: 'queued', progress: 0 })
    const generationLoader = vi.fn(async () => recovered)
    const mounted = await mountPanel({ submitter, generationLoader })
    mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')!.click()
    await settle()

    expect(generationLoader).toHaveBeenCalledWith(7, expect.stringMatching(/^[0-9a-f]{32}$/))
    expect(useConfigWorkspaceStore().jobId).toBe(44)
    expect(useConfigWorkspaceStore().pendingGenerationMode).toBeNull()
    expect(mounted.host.textContent).not.toContain('未提交成功')
  })

  it('persists an unknown generation outcome and blocks duplicate submission', async () => {
    const timeout = new ApiError({ kind: 'network', status: null, code: 'network_error',
      message: 'offline', details: {}, requestId: 'safe', retryable: true })
    const mounted = await mountPanel({
      submitter: vi.fn(async () => { throw timeout }),
      generationLoader: vi.fn(async () => { throw timeout }),
    })
    mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')!.click()
    await settle()

    expect(useConfigWorkspaceStore().pendingGenerationMode).toBe('per_question')
    expect(localStorage.getItem('ai-grading:config-workspace:v1')).toContain('pendingGenerationMode')
    expect(mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')?.disabled).toBe(true)
    expect(mounted.host.querySelector('button[name="重新核对生成任务"]')).not.toBeNull()
  })

  it('unlocks generation when the authoritative token lookup confirms 404', async () => {
    const timeout = new ApiError({ kind: 'network', status: null, code: 'network_error',
      message: 'offline', details: {}, requestId: 'safe', retryable: true })
    const notFound = new ApiError({ kind: 'not_found', status: 404,
      code: 'config_generation_job_not_found', message: 'missing', details: {},
      requestId: 'safe-404', retryable: false })
    const mounted = await mountPanel({
      submitter: vi.fn(async () => { throw timeout }),
      generationLoader: vi.fn(async () => { throw notFound }),
    })

    mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')!.click()
    await settle()

    expect(useConfigWorkspaceStore().pendingJobRequestToken).toBeNull()
    expect(mounted.host.textContent).toContain('服务器确认未收到这次请求')
    expect(mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')?.disabled).toBe(false)
  })

  it('reconciles only the exact pending token restored after a refresh', async () => {
    const token = '3'.repeat(32)
    const configStore = useConfigWorkspaceStore()
    configStore.markJobSubmissionPending(token, 'refine')
    const generationLoader = vi.fn(async () => job({ id: 45, status: 'queued', progress: 0 }))
    const mounted = await mountPanel({ generationLoader })

    mounted.host.querySelector<HTMLButtonElement>('button[name="重新核对生成任务"]')!.click()
    await settle()

    expect(generationLoader).toHaveBeenCalledExactlyOnceWith(7, token)
    expect(configStore.jobId).toBe(45)
    expect(configStore.pendingJobRequestToken).toBeNull()
  })

  it('shows and reconciles a refine recovery without requiring a P2 source', async () => {
    const token = '4'.repeat(32)
    const configStore = useConfigWorkspaceStore()
    configStore.selectSource(null)
    configStore.setEditor(editor())
    configStore.markJobSubmissionPending(token, 'refine')
    const generationLoader = vi.fn(async () => job({
      id: 46,
      status: 'queued',
      progress: 0,
      payload: { session_id: 7, mode: 'refine' },
    }))
    const mounted = await mountPanel({ generationLoader })

    expect(configStore.canGenerate).toBe(false)
    const reconcile = mounted.host.querySelector<HTMLButtonElement>(
      'button[name="重新核对生成任务"]',
    )!
    expect(reconcile).not.toBeNull()
    reconcile.click()
    await settle()

    expect(generationLoader).toHaveBeenCalledExactlyOnceWith(7, token)
    expect(configStore.jobId).toBe(46)
    expect(configStore.pendingJobRequestToken).toBeNull()
  })

  it('unlocks a restored refine request after an authoritative 404', async () => {
    const token = '6'.repeat(32)
    const configStore = useConfigWorkspaceStore()
    configStore.selectSource(null)
    configStore.setEditor(editor())
    configStore.markJobSubmissionPending(token, 'refine')
    const notFound = new ApiError({ kind: 'not_found', status: 404,
      code: 'config_generation_job_not_found', message: 'missing', details: {},
      requestId: 'safe-404', retryable: false })
    const mounted = await mountPanel({
      generationLoader: vi.fn(async () => { throw notFound }),
    })

    mounted.host.querySelector<HTMLButtonElement>('button[name="重新核对生成任务"]')!.click()
    await settle()

    expect(configStore.pendingJobRequestToken).toBeNull()
    expect(mounted.host.textContent).toContain('服务器确认未收到这次请求')
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

    expect(retryer).toHaveBeenCalledExactlyOnceWith(
      7, 31, ['Q5'], expect.stringMatching(/^[0-9a-f]{32}$/),
    )
    expect(configStore.jobId).toBe(32)
    expect(mounted.host.textContent).toContain('已成功 3 题')
    expect(mounted.host.textContent).toContain('当前恢复任务')
  })

  it('unlocks an ambiguous retry when its exact lookup confirms 404', async () => {
    const timeout = new ApiError({ kind: 'timeout', status: null, code: 'request_timeout',
      message: 'timeout', details: {}, requestId: 'safe', retryable: false })
    const notFound = new ApiError({ kind: 'not_found', status: 404,
      code: 'config_generation_job_not_found', message: 'missing', details: {},
      requestId: 'safe-404', retryable: false })
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: 'succeeded', progress: 1, result: {
        outcome: 'partial', total_questions: 5, generated_questions: 3,
        failed_count: 2, failed_question_ids: ['Q2', 'Q5'], retryable: true,
      }, finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({
      retryer: vi.fn(async () => { throw timeout }),
      generationLoader: vi.fn(async () => { throw notFound }),
    })
    const failedInputs = mounted.host.querySelectorAll<HTMLInputElement>(
      '.config-generation__partial input[type="checkbox"]',
    )
    failedInputs[1]!.click()
    await nextTick()

    mounted.host.querySelector<HTMLButtonElement>('.config-generation__partial button')!.click()
    await settle()

    expect(configStore.pendingJobRequestToken).toBeNull()
    expect(mounted.host.textContent).toContain('服务器确认未收到这次请求')
    expect(mounted.host.querySelector<HTMLButtonElement>('.config-generation__partial button')?.disabled).toBe(false)
  })

  it('uses a fresh whole-document generation after whole mode failure', async () => {
    const submitter = vi.fn(async (_sessionId: number, _request: ConfigGenerationRequest) => {
      void _sessionId
      void _request
      return job({ id: 32, status: 'queued', progress: 0 })
    })
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

  it('keeps refine failures in the editor workflow without offering generation retry', async () => {
    const submitter = vi.fn()
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({ status: 'failed', payload: { session_id: 7, mode: 'refine' },
      finished_at: '2026-07-15T00:01:00Z' }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({ submitter })

    expect(mounted.host.textContent).toContain('重新细化')
    expect(mounted.host.textContent).toContain('返回编辑器')
    expect(mounted.host.textContent).not.toContain('重新逐题生成')
    mounted.host.querySelector<HTMLButtonElement>('button[name="返回编辑器"]')!.click()
    await settle()
    expect(submitter).not.toHaveBeenCalled()
    expect(configStore.jobId).toBeNull()
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

  it.each([
    ['refreshed', '样卷映射已刷新'],
    ['reconfirm_required', '样卷映射需要回到旧入口重新确认'],
    ['not_present', '当前考试没有样卷映射'],
  ] as const)('shows the %s mapping outcome after complete generation', async (mappingStatus, copy) => {
    const configStore = useConfigWorkspaceStore()
    useJobStore().track(job({
      status: 'succeeded', progress: 1, result: {
        outcome: 'complete', total_questions: 3, generated_questions: 3,
        failed_count: 0, failed_question_ids: [], retryable: false,
        mapping_status: mappingStatus,
      }, finished_at: '2026-07-15T00:01:00Z',
    }))
    configStore.attachJob(31, configStore.captureGenerationContext())
    const mounted = await mountPanel({ editorLoader: vi.fn(async () => editor()) })
    await settle()

    expect(mounted.host.textContent).toContain(copy)
  })

  it('blocks a session change until an in-flight submission is attached', async () => {
    const pending = deferred<JobResponse>()
    const submitter = vi.fn((_sessionId: number, _request: ConfigGenerationRequest) => {
      void _sessionId
      void _request
      return pending.promise
    })
    const configStore = useConfigWorkspaceStore()
    const mounted = await mountPanel({ submitter })
    mounted.host.querySelector<HTMLButtonElement>('button[name="开始生成"]')!.click()
    await nextTick()
    expect(configStore.selectSession(8)).toBe(false)
    pending.resolve(job())
    await settle()

    expect(configStore.sessionId).toBe(7)
    expect(configStore.jobId).toBe(31)
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
