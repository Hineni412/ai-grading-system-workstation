import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick } from 'vue';

import type { ConfigSource, ConfigSourceSubmission } from '../../../api/config-workspace';
import { ApiError } from '../../../api/errors';

import { useConfigWorkspaceStore } from '../../../stores/config-workspace';

import ConfigSourceUpload from '../ConfigSourceUpload.vue'

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
  submissionAbandoner?: (sessionId: number, requestToken: string) => Promise<void>
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
    submissionAbandoner: options.submissionAbandoner,
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

  it('keeps upload locked when the missing token cannot be atomically abandoned', async () => {
    const timeout = new ApiError({ kind: 'timeout', status: null, code: 'request_timeout',
      message: 'timeout', details: {}, requestId: 'safe', retryable: false })
    const notFound = new ApiError({ kind: 'not_found', status: 404,
      code: 'config_source_not_found', message: 'missing', details: {},
      requestId: 'safe-404', retryable: false })
    const conflict = new ApiError({ kind: 'conflict', status: 409,
      code: 'config_source_submission_conflict', message: 'late request', details: {},
      requestId: 'safe-409', retryable: false })
    const mounted = await mountUpload({
      uploader: vi.fn(async () => { throw timeout }),
      submissionLoader: vi.fn(async () => { throw notFound }),
      submissionAbandoner: vi.fn(async () => { throw conflict }),
    })
    await choose(mounted.host, new File(['%PDF'], 'retry.pdf'))

    mounted.host.querySelector<HTMLButtonElement>('button[type="submit"]')!.click()
    await settle()

    expect(useConfigWorkspaceStore().pendingUploadRequestToken).not.toBeNull()
    expect(mounted.host.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true)
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
