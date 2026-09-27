import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'

import type {
  ConfigEditorResponse,
  ConfigEditorSaveRequest,
  ConfigEditorSaveResponse,
} from '../../../api/config-workspace'
import { ApiError } from '../../../api/errors'
import type { JobResponse } from '../../../api/jobs'
import { useConfigWorkspaceStore } from '../../../stores/config-workspace'
import { useSessionStore } from '../../../stores/session'
import SessionConfigView from '../../../views/SessionConfigView.vue'

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
  generator?: (sessionId: number, request: unknown) => Promise<JobResponse>
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
    generationSubmitter: options.generator })
  app.use(pinia)
  app.mount(host)
  await nextTick()
  return { host, workspace, unmount: () => app.unmount() }
}

describe('ConfigSaveResult', () => {
  it('saves a teacher total above 100 and can retry a stale global rejection without losing edits', async () => {
    const saver = vi.fn().mockRejectedValueOnce(new ApiError({ kind: 'validation', status: 422,
      requestId: 'manual-score-test', retryable: false,
      code: 'invalid_config_editor', message: 'invalid', details: { issues: [{
        code: 'invalid_generated_config', severity: 'error', row_id: null,
        field: 'config', message: '旧校验未通过',
      }] },
    })).mockResolvedValueOnce({ ...saved(), total_score: 148,
      rows: [{ ...editor().rows[0]!, score: 148 }] })
    const mounted = await mountView({ saver })
    mounted.workspace.updateEditor({ row_id: 'row-q12-p1-s1', score: 148 })
    await nextTick()
    const save = mounted.host.querySelector<HTMLButtonElement>('button[name="保存评分依据"]')!
    expect(save.disabled).toBe(false)
    expect(mounted.host.querySelector('.rubric-ledger__issues--warning')?.textContent).toContain('当前总分为 148')
    save.click()
    await settle()
    expect(mounted.workspace.editorEdits[0]?.score).toBe(148)
    expect(save.disabled).toBe(false)
    save.click()
    await settle()
    expect(saver).toHaveBeenLastCalledWith(7, expect.objectContaining({ edits: [{ row_id: 'row-q12-p1-s1', score: 148 }] }))
    expect(mounted.workspace.effectiveTotalScore).toBe(148)
    expect(mounted.workspace.hasDirtyEditor).toBe(false)
    mounted.unmount()
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

})

beforeEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
})
