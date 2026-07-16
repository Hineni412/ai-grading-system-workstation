import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { uploadConfigSource, type ConfigSource } from '../api/config-workspace'
import { useConfigWorkspaceStore } from '../stores/config-workspace'
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
})
