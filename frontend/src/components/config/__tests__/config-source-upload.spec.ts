import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ConfigSource } from '../../../api/config-workspace'
import { ApiError } from '../../../api/errors'
import { useConfigWorkspaceStore } from '../../../stores/config-workspace'
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
  uploader?: (sessionId: number, file: File) => Promise<ConfigSource>
  onUploaded?: (value: ConfigSource) => void
  beforeUpload?: () => boolean
  sourceLoader?: (sessionId: number, sourceId: string) => Promise<ConfigSource>
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
    sourceLoader: options.sourceLoader,
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
    const sourceLoader = vi.fn(async () => source())
    const mounted = await mountUpload({ accepted: source(),
      uploader: vi.fn(async () => { throw timeout }), sourceLoader })
    await choose(mounted.host, new File(['%PDF'], 'replacement.pdf'))
    mounted.host.querySelector<HTMLButtonElement>('button[type="submit"]')!.click()
    await settle()

    expect(sourceLoader).toHaveBeenCalledExactlyOnceWith(7, 'a'.repeat(32))
    expect(mounted.host.textContent).toContain('核对')
    expect(mounted.host.textContent).not.toContain('新文件未接收成功')
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
