import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ConfigSource, ConfigSourceSubmission } from '../../../api/config-workspace'
import type { JobResponse } from '../../../api/jobs'
import { ApiError } from '../../../api/errors'
import { useConfigWorkspaceStore } from '../../../stores/config-workspace'
import { useJobStore } from '../../../stores/jobs'
import ConfigSourceUpload from '../ConfigSourceUpload.vue'

const MiB = 1024 * 1024

function source(overrides: Partial<ConfigSource> = {}): ConfigSource {
  return {
    session_id: 7,
    source_id: 'a'.repeat(32),
    source_revision: 'b'.repeat(64),
    safe_filename: '七年级数学.pdf',
    suffix: '.pdf',
    size_bytes: 4096,
    sha256_prefix: 'c'.repeat(12),
    parse_state: 'ready',
    questions: [{
      question_id: 'Q1', question_type: 'calculation', question_preview: '计算 1 + 1',
      answer_preview: '2', answer_present: true, needs_review: false,
      local_answer_trusted: true, has_question_asset: false, has_answer_asset: false,
    }],
    ...overrides,
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
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountUpload(options: {
  accepted?: ConfigSource | null
  uploader?: (sessionId: number, file: File, requestToken: string) => Promise<ConfigSource>
  onUploaded?: (value: ConfigSource) => void
  beforeUpload?: () => boolean
  submissionLoader?: (sessionId: number, requestToken: string) => Promise<ConfigSourceSubmission>
  activeSourceLoader?: (sessionId: number) => Promise<ConfigSource>
} = {}) {
  const host = document.createElement('div')
  document.body.append(host)
  const uploader = options.uploader ?? vi.fn(async () => source())
  const onUploaded = options.onUploaded ?? vi.fn()
  const app = createApp(ConfigSourceUpload, {
    sessionId: 7,
    source: options.accepted ?? null,
    uploader,
    beforeUpload: options.beforeUpload,
    submissionLoader: options.submissionLoader,
    activeSourceLoader: options.activeSourceLoader,
    onUploaded,
  })
  app.mount(host)
  await nextTick()
  return { host, uploader, onUploaded, unmount: () => app.unmount() }
}

async function choose(host: HTMLElement, file: File): Promise<void> {
  const input = host.querySelector<HTMLInputElement>('input[type="file"]')!
  Object.defineProperty(input, 'files', { configurable: true, value: [file] })
  input.dispatchEvent(new Event('change', { bubbles: true }))
  await nextTick()
}

beforeEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
  setActivePinia(createPinia())
})

describe('ConfigSourceUpload', () => {
  it('keeps the accepted source visible and hides raw details when replacement fails', async () => {
    const uploader = vi.fn(async () => { throw new Error('D:/private/parser.py failed') })
    const mounted = await mountUpload({ accepted: source(), uploader })
    await choose(mounted.host, new File(['bad'], 'replacement.pdf', { type: 'application/pdf' }))
    mounted.host.querySelector<HTMLButtonElement>('button[type="submit"]')!.click()
    await settle()

    expect(mounted.host.textContent).toContain('七年级数学.pdf')
    expect(mounted.host.querySelector('[role="alert"]')?.textContent).toContain('新文件未接收成功')
    expect(mounted.host.textContent).not.toContain('D:/private')
  })

  it('reconciles an ambiguous replacement before offering another upload', async () => {
    const timeout = new ApiError({ kind: 'timeout', status: null, code: 'request_timeout',
      message: 'timeout', details: {}, requestId: 'safe', retryable: false })
    const submissionLoader = vi.fn(async () => ({ status: 'processing', source: null } as const))
    const mounted = await mountUpload({ accepted: source(),
      uploader: vi.fn(async () => { throw timeout }), submissionLoader })
    await choose(mounted.host, new File(['%PDF'], 'replacement.pdf'))
    mounted.host.querySelector<HTMLButtonElement>('button[type="submit"]')!.click()
    await settle()

    expect(submissionLoader).toHaveBeenCalledExactlyOnceWith(7, expect.stringMatching(/^[0-9a-f]{32}$/))
    expect(mounted.host.textContent).toContain('核对')
    expect(mounted.host.textContent).not.toContain('新文件未接收成功')
  })

  it('unlocks upload when the authoritative token lookup confirms 404', async () => {
    const timeout = new ApiError({ kind: 'timeout', status: null, code: 'request_timeout',
      message: 'timeout', details: {}, requestId: 'safe', retryable: false })
    const notFound = new ApiError({ kind: 'not_found', status: 404,
      code: 'config_source_not_found', message: 'missing', details: {},
      requestId: 'safe-404', retryable: false })
    const mounted = await mountUpload({
      uploader: vi.fn(async () => { throw timeout }),
      submissionLoader: vi.fn(async () => { throw notFound }),
    })
    await choose(mounted.host, new File(['%PDF'], 'retry.pdf'))

    mounted.host.querySelector<HTMLButtonElement>('button[type="submit"]')!.click()
    await settle()

    expect(useConfigWorkspaceStore().pendingUploadRequestToken).toBeNull()
    expect(mounted.host.textContent).toContain('服务器确认未收到这次上传')
    expect(mounted.host.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(false)
  })

  it('discovers a newly active source after the first upload response is lost', async () => {
    const timeout = new ApiError({ kind: 'timeout', status: null, code: 'request_timeout',
      message: 'timeout', details: {}, requestId: 'safe', retryable: false })
    const accepted = source({ source_id: 'e'.repeat(32), source_revision: 'f'.repeat(64) })
    const submissionLoader = vi.fn(async () => ({ status: 'succeeded', source: accepted } as const))
    const mounted = await mountUpload({ uploader: vi.fn(async () => { throw timeout }),
      submissionLoader })
    await choose(mounted.host, new File(['%PDF'], 'first.pdf'))
    mounted.host.querySelector<HTMLButtonElement>('button[type="submit"]')!.click()
    await settle()

    expect(submissionLoader).toHaveBeenCalledExactlyOnceWith(7, expect.stringMatching(/^[0-9a-f]{32}$/))
    expect(mounted.onUploaded).toHaveBeenCalledWith(accepted)
    expect(mounted.host.querySelector('[role="alert"]')).toBeNull()
  })

  it('offers exact upload reconciliation when a pending token was restored', async () => {
    const token = '1'.repeat(32)
    useConfigWorkspaceStore().markUploadSubmissionPending(token)
    const submissionLoader = vi.fn(async () => ({ status: 'processing', source: null } as const))
    const mounted = await mountUpload({ submissionLoader })

    mounted.host.querySelector<HTMLButtonElement>('button:not([type="submit"])')!.click()
    await settle()

    expect(submissionLoader).toHaveBeenCalledExactlyOnceWith(7, token)
    expect(mounted.host.textContent).toContain('重新核对上传结果')
  })

  it('loads the current active source when the submitted upload was replaced', async () => {
    const token = '1'.repeat(32)
    const active = source({ source_id: 'e'.repeat(32), source_revision: 'f'.repeat(64) })
    useConfigWorkspaceStore().markUploadSubmissionPending(token)
    const activeSourceLoader = vi.fn(async () => active)
    const mounted = await mountUpload({
      submissionLoader: vi.fn(async () => ({ status: 'replaced', source: null } as const)),
      activeSourceLoader,
    })

    mounted.host.querySelector<HTMLButtonElement>('button:not([type="submit"])')!.click()
    await settle()

    expect(activeSourceLoader).toHaveBeenCalledExactlyOnceWith(7)
    expect(mounted.onUploaded).toHaveBeenCalledWith(active)
    expect(useConfigWorkspaceStore().pendingUploadRequestToken).toBeNull()
  })

  it.each([
    [new File(['x'], '试卷.txt'), '只支持 DOCX 或 PDF'],
    [Object.assign(new File(['x'], '试卷.pdf'), {}), '文件不能超过 200 MiB'],
  ])('rejects an invalid file before upload', async (file, message) => {
    if (message.includes('200')) Object.defineProperty(file, 'size', { value: 200 * MiB + 1 })
    const mounted = await mountUpload()
    await choose(mounted.host, file)

    expect(mounted.host.querySelector('[role="alert"]')?.textContent).toContain(message)
    expect(mounted.host.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true)
    expect(mounted.uploader).not.toHaveBeenCalled()
  })

  it('shows indeterminate upload status until the accepted source arrives', async () => {
    const pending = deferred<ConfigSource>()
    const mounted = await mountUpload({ uploader: vi.fn(() => pending.promise) })
    await choose(mounted.host, new File(['%PDF'], '试卷.pdf'))
    mounted.host.querySelector<HTMLButtonElement>('button[type="submit"]')!.click()
    await nextTick()

    expect(mounted.host.querySelector('[role="status"]')?.textContent).toContain('正在上传并拆题')
    expect(mounted.host.querySelector('progress')?.hasAttribute('value')).toBe(false)
    expect(mounted.host.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true)
    pending.resolve(source())
    await settle()
    expect(mounted.onUploaded).toHaveBeenCalledWith(source())
  })

  it('disables source replacement while configuration generation is active', async () => {
    const configStore = useConfigWorkspaceStore()
    configStore.jobId = 31
    useJobStore().track(activeJob())
    const mounted = await mountUpload({ accepted: source() })

    expect(mounted.host.querySelector<HTMLInputElement>('input[type="file"]')?.disabled).toBe(true)
    expect(mounted.host.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true)
    expect(mounted.uploader).not.toHaveBeenCalled()
  })

  it('does not start an upload when the request-time guard is cancelled', async () => {
    const beforeUpload = vi.fn(() => false)
    const mounted = await mountUpload({ beforeUpload })
    await choose(mounted.host, new File(['%PDF'], '试卷.pdf'))
    mounted.host.querySelector<HTMLButtonElement>('button[type="submit"]')!.click()
    await settle()

    expect(beforeUpload).toHaveBeenCalledOnce()
    expect(mounted.uploader).not.toHaveBeenCalled()
  })

  it('clears the native file input after success so the same file can be selected again', async () => {
    const uploader = vi.fn(async () => source())
    const mounted = await mountUpload({ uploader })
    const file = new File(['%PDF'], '同一份试卷.pdf')
    const input = mounted.host.querySelector<HTMLInputElement>('input[type="file"]')!
    Object.defineProperty(input, 'value', { configurable: true, writable: true, value: 'selected.pdf' })
    await choose(mounted.host, file)
    mounted.host.querySelector<HTMLButtonElement>('button[type="submit"]')!.click()
    await settle()
    expect(input.value).toBe('')

    Object.defineProperty(input, 'value', { configurable: true, writable: true, value: 'selected.pdf' })
    await choose(mounted.host, file)
    mounted.host.querySelector<HTMLButtonElement>('button[type="submit"]')!.click()
    await settle()
    expect(uploader).toHaveBeenCalledTimes(2)
  })

  it('shows a parse-safe empty result without inventing questions or paths', async () => {
    const empty = source({ questions: [], safe_filename: '空白卷.docx', suffix: '.docx' })
    const mounted = await mountUpload({ accepted: empty })

    expect(mounted.host.textContent).toContain('未识别到题目')
    expect(mounted.host.textContent).toContain('4 KiB')
    expect(mounted.host.textContent).toContain('c'.repeat(12))
    expect(mounted.host.textContent).toContain('b'.repeat(64))
    expect(mounted.host.textContent).not.toMatch(/[A-Z]:\\|source_path|storage_path/i)
  })

  it('clears generation and editor context only after a replacement is accepted', () => {
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    store.setSource(source())
    store.jobId = 31
    store.decisions = [{ question_id: 'Q1', question_type: 'proof', excluded: false }]
    store.setEditor({
      session_id: 7, configured: true, revision: 'd'.repeat(64), rows: [], total_score: 0,
      issues: [], source: null,
    })

    store.acceptUploadedSource(source({
      source_id: 'e'.repeat(32), source_revision: 'f'.repeat(64), safe_filename: '替换卷.pdf',
    }))

    expect(store.sourceRevision).toBe('f'.repeat(64))
    expect(store.jobId).toBeNull()
    expect(store.editor).toBeNull()
    expect(store.decisions).toEqual([])
    expect(store.phase).toBe('source')
  })
})
