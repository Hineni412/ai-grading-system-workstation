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
import ConfigSourceUpload from '../ConfigSourceUpload.vue'
import type { ConfigSource } from '../../../api/config-workspace'

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

  it('keeps an answer typed during conflict reload before the field loses focus', async () => {
    const pending = deferred<ConfigEditorResponse>()
    const loader = vi.fn(() => pending.promise)
    const conflict = new ApiError({ kind: 'conflict', status: 409,
      code: 'config_revision_conflict', message: 'conflict', details: {}, requestId: 'safe', retryable: false })
    const saver = vi.fn(async () => { throw conflict })
    const mounted = await mountView({ saver, loader })
    mounted.workspace.updateEditor({ row_id: 'row-q12-p1-s1', score: 101 })
    await nextTick()
    mounted.host.querySelector<HTMLButtonElement>('button[name="保存评分依据"]')!.click()
    await settle()
    mounted.host.querySelector<HTMLButtonElement>('button[name="重新加载最新版本"]')!.click()
    await nextTick()
    mounted.host.querySelector<HTMLButtonElement>('button[name="确认丢弃并重新加载"]')!.click()
    await settle()
    expect(loader).toHaveBeenCalledExactlyOnceWith(7)

    mounted.host.querySelector<HTMLButtonElement>('.rubric-unit-card__preview')!.click()
    await nextTick()
    const answer = mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 标准答案"]')!
    answer.focus()
    answer.value = '请求之后的新草稿'
    answer.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(document.activeElement).toBe(answer)
    pending.resolve(editor('旧响应答案', 'c'))
    await settle()

    expect(saver).toHaveBeenCalledOnce()
    expect(loader).toHaveBeenCalledExactlyOnceWith(7)
    expect(mounted.workspace.hasDirtyEditor).toBe(true)
    expect(mounted.workspace.effectiveEditorRows[0]?.standard_answer).toBe('请求之后的新草稿')
    expect(answer.value).toBe('请求之后的新草稿')
    expect(mounted.workspace.editor?.rows[0]?.standard_answer).toBe('旧答案')
    mounted.unmount()
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
    mounted.unmount()

    // 同一考试配置流程：分文件模式下试卷和答案两个 PDF 都选好才能提交。
    const uploader = vi.fn(
      async (sessionId: number, file: File, answerFile?: File): Promise<ConfigSource> => ({
        session_id: sessionId, source_id: 'a'.repeat(32), source_revision: 'b'.repeat(64),
        safe_filename: file.name, suffix: '.pdf', size_bytes: file.size,
        sha256_prefix: 'c'.repeat(12), parse_state: 'ready',
        answer_filename: answerFile?.name ?? null, questions: [],
      }),
    )
    const uploadHost = document.createElement('div')
    document.body.append(uploadHost)
    const uploadApp = createApp(ConfigSourceUpload, {
      sessionId: 7, source: null, uploader,
    })
    uploadApp.mount(uploadHost)
    await nextTick()
    const modeButtons = uploadHost.querySelectorAll<HTMLButtonElement>('.config-source__mode button')
    modeButtons[1]!.click()
    await nextTick()
    const submitButton = () => uploadHost.querySelector<HTMLButtonElement>('.config-source__submit')!
    const inputs = uploadHost.querySelectorAll<HTMLInputElement>('input[type=file]')
    const paper = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], 'paper.pdf')
    const answer = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], 'answer.pdf')
    Object.defineProperty(inputs[0]!, 'files', { value: [paper], configurable: true })
    inputs[0]!.dispatchEvent(new Event('change'))
    await nextTick()
    expect(submitButton().disabled).toBe(true)
    Object.defineProperty(inputs[1]!, 'files', { value: [answer], configurable: true })
    inputs[1]!.dispatchEvent(new Event('change'))
    await nextTick()
    expect(submitButton().disabled).toBe(false)
    uploadHost.querySelector<HTMLFormElement>('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await settle()
    expect(uploader).toHaveBeenCalledExactlyOnceWith(7, paper, answer)

    // 已知的拆题失败（details.reason 存在）直接展示服务端的中文提示。
    uploader.mockRejectedValueOnce(new ApiError({
      kind: 'validation', status: 422, code: 'config_source_invalid',
      message: '答案文件里没有找到和试卷题号对应的答案，请确认上传的是这份试卷的答案。',
      details: { reason: 'answer_unmatched' }, requestId: 'r1', retryable: false,
    }))
    const paper2 = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], 'paper2.pdf')
    const answer2 = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], 'answer2.pdf')
    Object.defineProperty(inputs[0]!, 'files', { value: [paper2], configurable: true })
    inputs[0]!.dispatchEvent(new Event('change'))
    Object.defineProperty(inputs[1]!, 'files', { value: [answer2], configurable: true })
    inputs[1]!.dispatchEvent(new Event('change'))
    await nextTick()
    uploadHost.querySelector<HTMLFormElement>('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await settle()
    const alert = uploadHost.querySelector<HTMLElement>('.config-source__error')
    expect(alert?.textContent).toContain('答案文件里没有找到和试卷题号对应的答案')

    // 非拆题原因的失败仍用通用文案，不泄露内部错误原文。
    uploader.mockRejectedValueOnce(new ApiError({
      kind: 'server', status: 500, code: 'internal_error',
      message: 'private database failure', details: {}, requestId: 'r2', retryable: true,
    }))
    Object.defineProperty(inputs[0]!, 'files', { value: [paper2], configurable: true })
    inputs[0]!.dispatchEvent(new Event('change'))
    Object.defineProperty(inputs[1]!, 'files', { value: [answer2], configurable: true })
    inputs[1]!.dispatchEvent(new Event('change'))
    await nextTick()
    uploadHost.querySelector<HTMLFormElement>('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await settle()
    const alert2 = uploadHost.querySelector<HTMLElement>('.config-source__error')
    expect(alert2?.textContent).toContain('文件未接收成功')
    expect(alert2?.textContent).not.toContain('private database failure')
    uploadApp.unmount()
  })

})

beforeEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
})
