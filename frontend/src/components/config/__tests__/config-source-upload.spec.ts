import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick } from 'vue';

import type { ConfigSource } from '../../../api/config-workspace';
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
  uploader?: (sessionId: number, file: File) => Promise<ConfigSource>
  onUploaded?: (value: ConfigSource) => void
  beforeUpload?: () => boolean
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

  it('accepts the refreshed active source after an ambiguous upload result', async () => {
    const timeout = new ApiError({ kind: 'timeout', status: null, code: 'request_timeout',
      message: 'timeout', details: {}, requestId: 'safe', retryable: false })
    const uploaded = source({ source_id: 'd'.repeat(32), source_revision: 'e'.repeat(64) })
    const activeSourceLoader = vi.fn(async () => uploaded)
    const onUploaded = vi.fn()
    const mounted = await mountUpload({ accepted: source(), onUploaded,
      uploader: vi.fn(async () => { throw timeout }), activeSourceLoader })
    await choose(mounted.host, new File(['%PDF'], 'replacement.pdf'))
    mounted.host.querySelector<HTMLButtonElement>('button[type="submit"]')!.click()
    await settle()

    expect(activeSourceLoader).toHaveBeenCalledExactlyOnceWith(7)
    expect(onUploaded).toHaveBeenCalledExactlyOnceWith(uploaded)
    expect(mounted.host.textContent).not.toContain('尚未确认')
  })

  it('reports an unconfirmed result when the active source did not change', async () => {
    const timeout = new ApiError({ kind: 'timeout', status: null, code: 'request_timeout',
      message: 'timeout', details: {}, requestId: 'safe', retryable: false })
    const onUploaded = vi.fn()
    const mounted = await mountUpload({ accepted: source(), onUploaded,
      uploader: vi.fn(async () => { throw timeout }),
      activeSourceLoader: vi.fn(async () => source()) })
    await choose(mounted.host, new File(['%PDF'], 'retry.pdf'))

    mounted.host.querySelector<HTMLButtonElement>('button[type="submit"]')!.click()
    await settle()

    expect(onUploaded).not.toHaveBeenCalled()
    expect(mounted.host.textContent).toContain('尚未确认')
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
