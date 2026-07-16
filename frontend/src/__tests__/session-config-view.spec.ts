import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { uploadConfigSource, type ConfigSource } from '../api/config-workspace'
import type { JobResponse } from '../api/jobs'
import { ApiError } from '../api/errors'
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

async function settle(): Promise<void> {
  await Promise.resolve(); await nextTick(); await Promise.resolve(); await nextTick()
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
  const app = createApp(SessionConfigView)
  app.use(pinia)
  app.mount(host)
  await nextTick()
  return { host, workspace, unmount: () => app.unmount() }
}

async function chooseAndSubmit(host: HTMLElement): Promise<void> {
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
  vi.clearAllMocks()
})

describe('SessionConfigView source replacement guard', () => {
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
        standard_answer: '1', accepted_answers: ['1'], match_rule: 'exact', knowledge: '',
        answer_only_max_score: null, require_final_answer: true, required_elements: [],
        deduction_rules: [], final_answer_rule: '' }],
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

    expect(mounted.host.querySelector<HTMLInputElement>('.config-source input[type="file"]')?.disabled).toBe(true)
    expect(mounted.host.querySelector<HTMLButtonElement>('button[name="保存评分依据"]')?.disabled).toBe(true)
  })
})
