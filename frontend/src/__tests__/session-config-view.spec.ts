import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  uploadConfigSource,
  type ConfigGenerationRequest,
  type ConfigSource,
} from '../api/config-workspace'
import type { JobResponse } from '../api/jobs'
import { ApiError } from '../api/errors'
import type { RegionReadiness } from '../api/template-regions'
import { useConfigWorkspaceStore } from '../stores/config-workspace'
import { useJobStore } from '../stores/jobs'
import { useSessionStore } from '../stores/session'
import SessionConfigView from '../views/SessionConfigView.vue'

vi.mock('../api/config-workspace', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/config-workspace')>(),
  uploadConfigSource: vi.fn(),
}))

function source(id = 'a', revision = 'b'): ConfigSource {
  return {
    session_id: 7, source_id: id.repeat(32), source_revision: revision.repeat(64),
    safe_filename: `${id}.pdf`, suffix: '.pdf', size_bytes: 10,
    sha256_prefix: 'c'.repeat(12), parse_state: 'ready', questions: [],
  }
}

function activeJob(): JobResponse {
  return {
    id: 31, job_type: 'config_generation',
    payload: { session_id: 7, mode: 'generate' }, result: {}, status: 'running',
    progress: 0.4, stage: 'config_generation', detail: '', error: null,
    cancel_requested: false, created_at: '2026-07-15T00:00:00Z',
    started_at: '2026-07-15T00:00:01Z', updated_at: '2026-07-15T00:00:02Z',
    finished_at: null,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

async function settle(): Promise<void> {
  await Promise.resolve(); await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 240))
  await Promise.resolve(); await nextTick()
}

async function mountDirtyView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const sessions = useSessionStore(pinia)
  sessions.$patch({
    sessions: [{ id: 7, name: '七年级数学', status: 'created', is_deleted: false,
      deleted_at: null, created_at: null, updated_at: null }],
    selectedSessionId: 7, loadState: 'ready',
  })
  const workspace = useConfigWorkspaceStore(pinia)
  workspace.selectSession(7)
  workspace.setSource(source())
  workspace.setEditor({
    session_id: 7, configured: true, revision: 'd'.repeat(64), rows: [], total_score: 0,
    issues: [], source: null,
  })
  workspace.updateEditor({ row_id: 'row-1', standard_answer: '教师草稿' })
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(SessionConfigView, {
    templateReadinessLoader: vi.fn(async (): Promise<RegionReadiness> => ({
      session_id: 7, scoring_configured: true, template_present: false,
      template_ready: false,
    })),
  })
  app.use(pinia)
  app.mount(host)
  await nextTick()
  return { host, workspace, unmount: () => app.unmount() }
}

async function chooseAndSubmit(host: HTMLElement): Promise<void> {
  const sourceStage = [...host.querySelectorAll<HTMLButtonElement>('.config-stage-rail button')]
    .find((button) => button.textContent?.includes('上传与拆题'))
  sourceStage?.click()
  await settle()
  const input = host.querySelector<HTMLInputElement>('input[type="file"]')!
  Object.defineProperty(input, 'files', {
    configurable: true, value: [new File(['%PDF'], '替换.pdf')],
  })
  input.dispatchEvent(new Event('change', { bubbles: true }))
  await nextTick()
  host.querySelector<HTMLButtonElement>('.config-source button[type="submit"]')!.click()
  await settle()
}

beforeEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
  window.history.replaceState({}, '', '/')
  vi.clearAllMocks()
})

describe('SessionConfigView source replacement guard', () => {
  it.each([
    ['分批重新生成', 'batched'],
    ['整卷重新生成', 'whole_document'],
  ] as const)('starts %s beside blocking validation without leaving the editor', async (
    buttonName,
    generationMode,
  ) => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const sessions = useSessionStore(pinia)
    sessions.$patch({
      sessions: [{ id: 7, name: '七年级数学', status: 'created', is_deleted: false,
        deleted_at: null, created_at: null, updated_at: null }],
      selectedSessionId: 7,
      loadState: 'ready',
    })
    const workspace = useConfigWorkspaceStore(pinia)
    workspace.selectSession(7)
    workspace.setSource({
      ...source(),
      questions: [{
        question_id: 'Q1',
        question_type: 'calculation',
        question_preview: '计算',
        answer_preview: '答案',
        answer_present: true,
        needs_review: false,
        local_answer_trusted: true,
        has_question_asset: false,
        has_answer_asset: false,
      }],
    })
    workspace.setEditor({
      session_id: 7,
      configured: true,
      revision: 'd'.repeat(64),
      total_score: 100,
      issues: [{
        code: 'quality_blocking',
        severity: 'error',
        row_id: 'row-q1-p1-s1',
        field: 'standard_answer',
        message: '[质量检查-阻断] Q1 缺少可评分的文本标准答案',
      }],
      source: null,
      rows: [{
        row_id: 'row-q1-p1-s1',
        question_id: 'Q1',
        part_id: 'P1',
        step_id: 'S1',
        part_label: '第 1 问',
        question_type: 'calculation',
        core_goal: '计算',
        score: 100,
        standard_answer: '模型原结果',
        accepted_answers: [],
        match_rule: 'exact',
        answer_only_max_score: null,
        require_final_answer: true,
        required_elements: [],
        deduction_rules: [],
        part_deduction_rules: [],
        final_answer_rule: '',
      }],
    })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    if (generationMode === 'batched') {
      workspace.updateEditor({
        row_id: 'row-q1-p1-s1',
        standard_answer: '尚未保存的教师修改',
      })
    }
    const pending = deferred<JobResponse>()
    const generationSubmitter = vi.fn((
      sessionId: number,
      request: ConfigGenerationRequest,
    ) => {
      void sessionId
      void request
      return pending.promise
    })
    const editorLoader = vi.fn(async () => ({
      session_id: 7,
      configured: true,
      revision: 'e'.repeat(64),
      total_score: 100,
      issues: [],
      source: null,
      rows: workspace.editor?.rows ?? [],
    }))
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(SessionConfigView, {
      generationSubmitter,
      editorLoader,
      templateReadinessLoader: vi.fn(async (): Promise<RegionReadiness> => ({
        session_id: 7,
        scoring_configured: true,
        template_present: false,
        template_ready: false,
      })),
    })
    app.use(pinia)
    app.mount(host)
    await settle()

    const button = host.querySelector<HTMLButtonElement>(`button[name="${buttonName}"]`)!
    expect(button).not.toBeNull()
    expect(host.querySelectorAll('.rubric-unit-card')).toHaveLength(1)
    button.click()
    button.click()
    await nextTick()

    expect(confirm).toHaveBeenCalledTimes(generationMode === 'batched' ? 1 : 0)
    expect(generationSubmitter).toHaveBeenCalledExactlyOnceWith(7, expect.objectContaining({
      source_id: 'a'.repeat(32),
      source_revision: 'b'.repeat(64),
      generation_mode: generationMode,
      client_request_token: expect.stringMatching(/^[0-9a-f]{32}$/),
      ...(generationMode === 'batched' ? {
        regenerate_question_ids: ['Q1'],
        base_revision: 'd'.repeat(64),
      } : {}),
    }))
    expect(host.querySelectorAll('.rubric-unit-card')).toHaveLength(1)
    expect(host.querySelector<HTMLButtonElement>('button[name="分批重新生成"]')?.disabled)
      .toBe(true)
    expect(host.querySelector<HTMLButtonElement>('button[name="整卷重新生成"]')?.disabled)
      .toBe(true)
    expect(button.textContent).toContain('正在提交')
    const inactiveButtonName = generationMode === 'batched' ? '整卷重新生成' : '分批重新生成'
    expect(host.querySelector<HTMLButtonElement>(
      `button[name="${inactiveButtonName}"]`,
    )?.textContent).toContain(inactiveButtonName)

    pending.resolve({
      ...activeJob(),
      payload: {
        session_id: 7,
        mode: 'generate',
        generation_mode: generationMode,
        source_id: 'a'.repeat(32),
        source_revision: 'b'.repeat(64),
      },
    })
    await settle()
    expect(useJobStore().jobs[31]?.status).toBe('running')
    useJobStore().track({
      ...activeJob(),
      status: 'succeeded',
      progress: 1,
      result: { outcome: 'complete' },
      updated_at: '2026-07-15T00:00:03Z',
      finished_at: '2026-07-15T00:00:03Z',
    })
    await settle()

    expect(editorLoader).toHaveBeenCalledWith(7)
    expect(workspace.editor?.revision).toBe('e'.repeat(64))
    expect(host.textContent).toContain('新评分依据已更新')
    expect(host.querySelector('button[name="分批重新生成"]')).toBeNull()
    expect(host.querySelector('button[name="整卷重新生成"]')).toBeNull()
    app.unmount()
  })

  it.each([
    [{ template_present: false, template_ready: false }, '准备样卷', '可开始'],
    [{ template_present: true, template_ready: false }, '继续标定', '标定中'],
    [{ template_present: true, template_ready: true }, '查看已确认版本', '已确认'],
  ])('shows the real template stage state %#', async (state, action, fact) => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const sessions = useSessionStore(pinia)
    sessions.$patch({
      sessions: [{ id: 7, name: '七年级数学', status: 'created', is_deleted: false,
        deleted_at: null, created_at: null, updated_at: null }],
      selectedSessionId: 7, loadState: 'ready',
    })
    const workspace = useConfigWorkspaceStore(pinia)
    workspace.selectSession(7)
    workspace.setEditor({ session_id: 7, configured: true, revision: 'd'.repeat(64),
      rows: [], total_score: 100, issues: [], source: null })
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(SessionConfigView, {
      templateReadinessLoader: vi.fn(async () => ({ session_id: 7,
        scoring_configured: true, ...state })),
    })
    app.use(pinia)
    app.mount(host)
    await settle()

    expect(host.querySelector<HTMLAnchorElement>('a[href="/sessions/7/regions"]')?.textContent)
      .toContain(action)
    expect(host.textContent).toContain(fact)
    app.unmount()
  })

  it('keeps refine reconciliation visible for a legacy editor without a P2 source', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const sessions = useSessionStore(pinia)
    sessions.$patch({
      sessions: [{ id: 7, name: '旧考试', status: 'created', is_deleted: false,
        deleted_at: null, created_at: null, updated_at: null }],
      selectedSessionId: 7,
      loadState: 'ready',
    })
    const workspace = useConfigWorkspaceStore(pinia)
    workspace.selectSession(7)
    workspace.setEditor({
      session_id: 7, configured: true, revision: 'd'.repeat(64), rows: [], total_score: 0,
      issues: [], source: null,
    })
    workspace.markJobSubmissionPending('3'.repeat(32), 'refine')
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(SessionConfigView)
    app.use(pinia)
    app.mount(host)
    await nextTick()

    const generationStage = [...host.querySelectorAll<HTMLButtonElement>('.config-stage-rail button')]
      .find((button) => button.textContent?.includes('AI 生成'))
    generationStage?.click()
    await settle()
    expect(host.querySelector('button[name="重新核对生成任务"]')).not.toBeNull()
    expect(host.textContent).toContain('结果尚未确认')
    app.unmount()
  })

  it('unlocks an ambiguous refine request when its exact lookup confirms 404', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const sessions = useSessionStore(pinia)
    sessions.$patch({
      sessions: [{ id: 7, name: '旧考试', status: 'created', is_deleted: false,
        deleted_at: null, created_at: null, updated_at: null }],
      selectedSessionId: 7,
      loadState: 'ready',
    })
    const workspace = useConfigWorkspaceStore(pinia)
    workspace.selectSession(7)
    workspace.setEditor({
      session_id: 7, configured: true, revision: 'd'.repeat(64), total_score: 100,
      issues: [], source: null,
      rows: [{ row_id: 'row-q1-p1-s1', question_id: 'Q1', part_id: 'P1', step_id: 'S1',
        part_label: '第 1 问', question_type: 'calculation', core_goal: '计算', score: 100,
        standard_answer: '1', accepted_answers: ['1'], match_rule: 'exact',
        answer_only_max_score: null, require_final_answer: true, required_elements: [],
        deduction_rules: [], part_deduction_rules: [], final_answer_rule: '' }],
    })
    const timeout = new ApiError({ kind: 'network', status: null, code: 'network_error',
      message: 'offline', details: {}, requestId: 'safe', retryable: true })
    const notFound = new ApiError({ kind: 'not_found', status: 404,
      code: 'config_generation_job_not_found', message: 'missing', details: {},
      requestId: 'safe-404', retryable: false })
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(SessionConfigView, {
      editorRefiner: vi.fn(async () => { throw timeout }),
      generationLoader: vi.fn(async () => { throw notFound }),
      requestAbandoner: vi.fn(async () => undefined),
    })
    app.use(pinia)
    app.mount(host)
    await nextTick()

    expect(host.querySelectorAll('.rubric-unit-card')).toHaveLength(1)
    expect(host.querySelector('.rubric-ledger table')).toBeNull()
    host.querySelector<HTMLButtonElement>('button[name="AI 完善评分单元"]')!.click()
    await settle()

    expect(workspace.pendingJobRequestToken).toBeNull()
    expect(host.textContent).toContain('服务器确认未收到这次 AI 完善请求')
    app.unmount()
  })

  it('cancels before the upload request and retains the dirty editor', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const mounted = await mountDirtyView()
    await chooseAndSubmit(mounted.host)

    expect(confirm).toHaveBeenCalledOnce()
    expect(uploadConfigSource).not.toHaveBeenCalled()
    expect(mounted.workspace.sourceId).toBe('a'.repeat(32))
    expect(mounted.workspace.hasDirtyEditor).toBe(true)
  })

  it('uploads only after confirmation and resets context only after acceptance', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(uploadConfigSource).mockResolvedValue(source('e', 'f'))
    const mounted = await mountDirtyView()
    await chooseAndSubmit(mounted.host)

    expect(confirm.mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(uploadConfigSource).mock.invocationCallOrder[0]!,
    )
    expect(mounted.workspace.sourceId).toBe('e'.repeat(32))
    expect(mounted.workspace.editor).toBeNull()
    expect(mounted.workspace.hasDirtyEditor).toBe(false)
  })

  it('retains the dirty editor when a confirmed replacement fails', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(uploadConfigSource).mockRejectedValue(new Error('private path'))
    const mounted = await mountDirtyView()
    await chooseAndSubmit(mounted.host)

    expect(uploadConfigSource).toHaveBeenCalledOnce()
    expect(mounted.workspace.sourceId).toBe('a'.repeat(32))
    expect(mounted.workspace.editor).not.toBeNull()
    expect(mounted.workspace.hasDirtyEditor).toBe(true)
    expect(mounted.host.textContent).not.toContain('private path')
  })

  it('disables upload and editor save while a configuration job is active', async () => {
    const mounted = await mountDirtyView()
    mounted.workspace.jobId = 31
    useJobStore().track(activeJob())
    await nextTick()

    expect(mounted.host.querySelector<HTMLButtonElement>('button[name="保存评分依据"]')?.disabled).toBe(true)
    const sourceStage = [...mounted.host.querySelectorAll<HTMLButtonElement>('.config-stage-rail button')]
      .find((button) => button.textContent?.includes('上传与拆题'))
    sourceStage?.click()
    await settle()
    expect(mounted.host.querySelector<HTMLInputElement>('.config-source input[type="file"]')?.disabled).toBe(true)
    expect(mounted.host.textContent).toContain('样卷题框')
  })

  it('consumes a returned stage once and still advances after later generation', async () => {
    window.history.replaceState({}, '', '/sessions?stage=source')
    const mounted = await mountDirtyView()
    await settle()

    expect(mounted.host.querySelector('.config-source')).not.toBeNull()
    mounted.workspace.phase = 'generation'
    await nextTick()
    mounted.workspace.phase = 'editor'
    await settle()

    expect(mounted.host.querySelector('.rubric-ledger')).not.toBeNull()
    mounted.unmount()
  })

  it('focuses the selected preparation stage after the slide finishes', async () => {
    const scrollIntoView = vi.fn()
    vi.stubGlobal('scrollTo', vi.fn())
    HTMLElement.prototype.scrollIntoView = scrollIntoView
    const mounted = await mountDirtyView()
    const sourceStage = [...mounted.host.querySelectorAll<HTMLButtonElement>('.config-stage-rail button')]
      .find((button) => button.textContent?.includes('上传与拆题'))
    sourceStage?.click()
    await settle()

    expect(document.activeElement?.id).toBe('config-source-stage')
    expect(scrollIntoView).toHaveBeenCalled()
    mounted.unmount()
  })
})
