import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../../api/errors'
import type {
  ConfigEditorResponse,
  ConfigEditorSaveRequest,
  ConfigEditorSaveResponse,
} from '../../../api/config-workspace'
import type { JobResponse } from '../../../api/jobs'
import { useConfigWorkspaceStore } from '../../../stores/config-workspace'
import { useSessionStore } from '../../../stores/session'
import SessionConfigView from '../../../views/SessionConfigView.vue'
import ConfigSaveResult from '../ConfigSaveResult.vue'

function editor(answer = '旧答案', revision = 'a'): ConfigEditorResponse {
  return {
    session_id: 7, configured: true, revision: revision.repeat(64), total_score: 100,
    source: null, issues: [], rows: [{
      row_id: 'row-q12-p1-s1', question_id: 'Q12', part_id: 'P1', step_id: 'S1',
      part_label: '第 1 问', question_type: 'proof', core_goal: '证明', score: 100,
      standard_answer: answer, accepted_answers: [], match_rule: '按要素',
      answer_only_max_score: null, require_final_answer: true, required_elements: [],
      deduction_rules: [], part_deduction_rules: [], final_answer_rule: '',
    }],
  }
}

function saved(answer = '服务器权威答案', mappingStatus: ConfigEditorSaveResponse['save_result']['mapping_status'] = 'refreshed'): ConfigEditorSaveResponse {
  return {
    ...editor(answer, 'b'),
    save_result: { config_saved: true, mapping_status: mappingStatus, mapping_message: 'ignored' },
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

async function settle(): Promise<void> {
  await Promise.resolve(); await nextTick(); await Promise.resolve(); await nextTick()
}

async function mountView(options: {
  saver: (sessionId: number, request: ConfigEditorSaveRequest) => Promise<ConfigEditorSaveResponse>
  loader?: (sessionId: number) => Promise<ConfigEditorResponse>
  refiner?: (sessionId: number, request: { revision: string; commands: unknown[] }) => Promise<JobResponse>
  initialEditor?: ConfigEditorResponse
}) {
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
  workspace.setSource({ session_id: 7, source_id: 'e'.repeat(32), source_revision: 'f'.repeat(64),
    safe_filename: '数学卷.pdf', suffix: '.pdf', size_bytes: 12, sha256_prefix: 'c'.repeat(12),
    parse_state: 'ready', questions: [{ question_id: 'Q12', question_type: 'proof',
      question_preview: '证明题', answer_preview: '', answer_present: false, needs_review: false,
      local_answer_trusted: false, has_question_asset: false, has_answer_asset: false }] })
  workspace.setEditor(options.initialEditor ?? editor())
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(SessionConfigView, { editorSaver: options.saver, editorLoader: options.loader,
    editorRefiner: options.refiner })
  app.use(pinia)
  app.mount(host)
  await nextTick()
  return { host, workspace, unmount: () => app.unmount() }
}

async function mountResult(props: Record<string, unknown>) {
  const host = document.createElement('div')
  document.body.append(host)
  let reloads = 0
  const app = createApp(ConfigSaveResult, { ...props, onReload: () => { reloads += 1 } })
  app.mount(host)
  await nextTick()
  return { host, reloads: () => reloads }
}

describe('ConfigSaveResult', () => {
  it.each([
    ['refreshed', '评分依据已保存，样卷映射已刷新'],
    ['reconfirm_required', '评分依据已保存；样卷映射需要回旧入口重新确认'],
    ['not_present', '评分依据已保存'],
  ] as const)('uses authoritative %s mapping copy', async (mappingStatus, copy) => {
    const mounted = await mountResult({ status: 'success', mappingStatus })
    expect(mounted.host.textContent).toContain(copy)
    expect(mounted.host.querySelector('[role="status"]')).not.toBeNull()
  })

  it('requires two explicit clicks before discarding local edits on conflict', async () => {
    const mounted = await mountResult({ status: 'conflict' })
    expect(mounted.host.querySelector('[role="alert"]')?.textContent).toContain('服务器已有较新版本')
    const first = mounted.host.querySelector<HTMLButtonElement>('button[name="重新加载最新版本"]')!
    first.click()
    await nextTick()
    expect(mounted.reloads()).toBe(0)
    expect(mounted.host.textContent).toContain('本地修改将被丢弃')
    mounted.host.querySelector<HTMLButtonElement>('button[name="确认丢弃并重新加载"]')!.click()
    expect(mounted.reloads()).toBe(1)
  })

  it('states that a failed save retained the teacher edits', async () => {
    const mounted = await mountResult({ status: 'failure' })
    expect(mounted.host.querySelector('[role="alert"]')?.textContent).toContain('未保存')
    expect(mounted.host.textContent).toContain('本地修改已保留')
  })

  it('sends one PUT with the loaded revision and disables a double submit', async () => {
    const pending = deferred<ConfigEditorSaveResponse>()
    const saver = vi.fn((_sessionId: number, _request: ConfigEditorSaveRequest) => {
      void _sessionId
      void _request
      return pending.promise
    })
    const mounted = await mountView({ saver })
    const answer = mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 标准答案"]')!
    answer.value = '本地答案'
    answer.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    const save = mounted.host.querySelector<HTMLButtonElement>('button[name="保存评分依据"]')!
    save.click(); save.click()
    await nextTick()

    expect(saver).toHaveBeenCalledExactlyOnceWith(7, {
      revision: 'a'.repeat(64),
      edits: [{ row_id: 'row-q12-p1-s1', standard_answer: '本地答案' }],
      commands: [],
    })
    expect(save.disabled).toBe(true)
    pending.resolve(saved())
    await settle()
    expect(mounted.workspace.hasDirtyEditor).toBe(false)
    expect(mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 标准答案"]')?.value).toBe('服务器权威答案')
    expect(mounted.host.textContent).toContain('样卷映射已刷新')
  })

  it('retains local edits after a 409 and reloads only after the second confirmation', async () => {
    const conflict = new ApiError({
      kind: 'conflict', status: 409, code: 'config_revision_conflict', message: 'conflict',
      details: {}, requestId: 'safe', retryable: false,
    })
    const loader = vi.fn(async () => editor('最新服务器答案', 'c'))
    const mounted = await mountView({ saver: vi.fn(async () => { throw conflict }), loader })
    const answer = mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 标准答案"]')!
    answer.value = '本地未保存答案'
    answer.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    mounted.host.querySelector<HTMLButtonElement>('button[name="保存评分依据"]')!.click()
    await settle()

    expect(mounted.workspace.saveStatus).toBe('conflict')
    expect(mounted.host.querySelector('[role="alert"]')?.textContent).toContain('服务器已有较新版本')
    expect(mounted.workspace.hasDirtyEditor).toBe(true)
    expect(mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 标准答案"]')?.value).toBe('本地未保存答案')
    mounted.host.querySelector<HTMLButtonElement>('button[name="重新加载最新版本"]')!.click()
    await nextTick()
    expect(loader).not.toHaveBeenCalled()
    mounted.host.querySelector<HTMLButtonElement>('button[name="确认丢弃并重新加载"]')!.click()
    await settle()
    expect(loader).toHaveBeenCalledExactlyOnceWith(7)
    expect(mounted.workspace.hasDirtyEditor).toBe(false)
    expect(mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 标准答案"]')?.value).toBe('最新服务器答案')
  })

  it('keeps edits and shows retained-work copy after an ordinary save failure', async () => {
    const mounted = await mountView({ saver: vi.fn(async () => { throw new Error('private') }) })
    const answer = mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 标准答案"]')!
    answer.value = '不丢失'
    answer.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    mounted.host.querySelector<HTMLButtonElement>('button[name="保存评分依据"]')!.click()
    await settle()
    expect(mounted.workspace.hasDirtyEditor).toBe(true)
    expect(mounted.workspace.saveStatus).toBe('failure')
    expect(mounted.host.textContent).toContain('本地修改已保留')
    expect(mounted.host.textContent).not.toContain('private')
  })

  it('reconciles a lost save response against the authoritative editor before retry', async () => {
    const timeout = new ApiError({ kind: 'timeout', status: null, code: 'request_timeout',
      message: 'timeout', details: {}, requestId: 'safe', retryable: false })
    const loader = vi.fn(async () => editor('本地答案', 'b'))
    const mounted = await mountView({ saver: vi.fn(async () => { throw timeout }), loader })
    const answer = mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 标准答案"]')!
    answer.value = '本地答案'
    answer.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    mounted.host.querySelector<HTMLButtonElement>('button[name="保存评分依据"]')!.click()
    await settle()

    expect(loader).toHaveBeenCalledExactlyOnceWith(7)
    expect(mounted.workspace.hasDirtyEditor).toBe(false)
    expect(mounted.workspace.saveStatus).toBe('success')
    expect(mounted.host.textContent).not.toContain('本次修改未保存')
  })

  it('does not let an old save replace edits made after that request started', async () => {
    const pending = deferred<ConfigEditorSaveResponse>()
    const mounted = await mountView({ saver: vi.fn(() => pending.promise) })
    mounted.workspace.updateEditor({ row_id: 'row-q12-p1-s1', standard_answer: 'first draft' })
    await nextTick()
    mounted.host.querySelector<HTMLButtonElement>('button[name="保存评分依据"]')!.click()
    await nextTick()

    mounted.workspace.updateEditor({ row_id: 'row-q12-p1-s1', standard_answer: 'newer draft' })
    pending.resolve(saved('stale server answer'))
    await settle()

    expect(mounted.workspace.hasDirtyEditor).toBe(true)
    expect(mounted.workspace.editorEdits).toEqual([
      { row_id: 'row-q12-p1-s1', standard_answer: 'newer draft' },
    ])
    expect(mounted.workspace.editor?.rows[0]?.standard_answer).toBe('旧答案')
  })

  it('disables rubric edits after a delayed refine request becomes a running Job', async () => {
    const pending = deferred<JobResponse>()
    const refiner = vi.fn(() => pending.promise)
    const mounted = await mountView({ saver: vi.fn(), refiner })
    mounted.host.querySelector<HTMLButtonElement>('button[name="AI 完善评分单元"]')!.click()
    await nextTick()
    pending.resolve({ id: 55, job_type: 'config_generation', payload: { session_id: 7, mode: 'refine' },
      result: {}, status: 'running', progress: 0.2, stage: 'refining', detail: '', error: null,
      cancel_requested: false, created_at: '2026-07-15T00:00:00Z', started_at: null,
      updated_at: '2026-07-15T00:00:01Z', finished_at: null })
    await settle()

    expect(refiner).toHaveBeenCalledOnce()
    expect(mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 标准答案"]')?.disabled).toBe(true)
  })

  it('does not let an old conflict reload clear edits made while it was loading', async () => {
    const conflict = new ApiError({
      kind: 'conflict', status: 409, code: 'config_revision_conflict', message: 'conflict',
      details: {}, requestId: 'safe', retryable: false,
    })
    const pending = deferred<ConfigEditorResponse>()
    const mounted = await mountView({
      saver: vi.fn(async () => { throw conflict }),
      loader: vi.fn(() => pending.promise),
    })
    mounted.workspace.updateEditor({ row_id: 'row-q12-p1-s1', standard_answer: 'first draft' })
    await nextTick()
    mounted.host.querySelector<HTMLButtonElement>('button[name="保存评分依据"]')!.click()
    await settle()
    mounted.host.querySelector<HTMLButtonElement>('button[name="重新加载最新版本"]')!.click()
    await nextTick()
    mounted.host.querySelector<HTMLButtonElement>('button[name="确认丢弃并重新加载"]')!.click()
    await nextTick()

    mounted.workspace.updateEditor({ row_id: 'row-q12-p1-s1', standard_answer: 'newer draft' })
    pending.resolve(editor('stale reload answer', 'c'))
    await settle()

    expect(mounted.workspace.hasDirtyEditor).toBe(true)
    expect(mounted.workspace.editorEdits).toEqual([
      { row_id: 'row-q12-p1-s1', standard_answer: 'newer draft' },
    ])
    expect(mounted.workspace.editor?.rows[0]?.standard_answer).toBe('旧答案')
  })

  it('shows only allowlisted stable 422 issues and clears the related issue on edit', async () => {
    const invalid = new ApiError({
      kind: 'validation', status: 422, code: 'invalid_config_editor', message: 'invalid',
      details: { issues: [{
        code: 'invalid_score', severity: 'error', row_id: 'row-q12-p1-s1',
        field: 'score', message: '分值必须在 0 至 100 之间',
      }] }, requestId: 'safe', retryable: false,
    })
    const mounted = await mountView({ saver: vi.fn(async () => { throw invalid }) })
    mounted.workspace.updateEditor({ row_id: 'row-q12-p1-s1', standard_answer: 'dirty' })
    await nextTick()
    mounted.host.querySelector<HTMLButtonElement>('button[name="保存评分依据"]')!.click()
    await settle()

    expect(mounted.workspace.serverIssues).toEqual(invalid.details.issues)
    expect(mounted.host.textContent).toContain('分值必须在 0 至 100 之间')
    mounted.host.querySelector<HTMLButtonElement>('[data-issue-row-id="row-q12-p1-s1"]')!.click()
    await nextTick()
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Q12 P1 S1 分值')

    mounted.workspace.updateEditor({ row_id: 'row-q12-p1-s1', score: 100 })
    await nextTick()
    expect(mounted.workspace.serverIssues).toHaveLength(0)
  })

  it.each([
    { code: 'invalid_score', severity: 'error', row_id: 'row-q12-p1-s1', field: 'score', message: 'safe', raw: 'private' },
    { code: 'invalid_score', severity: 'error', row_id: 'row-q12-p1-s1', field: 'score', message: 'safe', path: 'D:/private' },
    { code: 'invalid_score', severity: 'unknown', row_id: 'row-q12-p1-s1', field: 'score', message: 'unsafe' },
  ])('rejects unsafe or unknown 422 issue details', async (issue) => {
    const invalid = new ApiError({
      kind: 'validation', status: 422, code: 'invalid_config_editor', message: 'invalid',
      details: { issues: [issue] }, requestId: 'safe', retryable: false,
    })
    const mounted = await mountView({ saver: vi.fn(async () => { throw invalid }) })
    mounted.workspace.updateEditor({ row_id: 'row-q12-p1-s1', standard_answer: 'dirty' })
    await nextTick()
    mounted.host.querySelector<HTMLButtonElement>('button[name="保存评分依据"]')!.click()
    await settle()

    expect(mounted.workspace.serverIssues).toEqual([])
    expect(mounted.host.textContent).not.toContain('D:/private')
    expect(mounted.workspace.saveStatus).toBe('failure')
  })
})

beforeEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
})
