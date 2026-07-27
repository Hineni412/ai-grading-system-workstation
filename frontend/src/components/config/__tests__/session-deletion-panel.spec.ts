import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  SessionDeletionImpact,
  SessionPermanentDeletionResponse,
  SessionSummary,
} from '../../../api/sessions'
import { useSessionStore } from '../../../stores/session'

const api = vi.hoisted(() => ({
  archiveSession: vi.fn(),
  fetchArchivedSessions: vi.fn(),
  fetchSessionDeletionImpact: vi.fn(),
  permanentlyDeleteSession: vi.fn(),
  restoreArchivedSession: vi.fn(),
}))

vi.mock('../../../api/sessions', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../api/sessions')>(),
  ...api,
}))

import SessionDeletionPanel from '../SessionDeletionPanel.vue'

const archived: SessionSummary = {
  id: 7,
  name: '测试考试',
  status: 'archived',
  is_deleted: true,
  deleted_at: '2026-07-27T00:00:00',
  created_at: '2026-07-26T00:00:00',
  updated_at: '2026-07-27T00:00:00',
}

const impact: SessionDeletionImpact = {
  session: archived,
  revision: 'a'.repeat(64),
  active_jobs: 0,
  active_grading_runs: 0,
  can_archive: false,
  can_permanently_delete: true,
  permanent_delete_phrase: '永久删除 测试考试',
  permanent_counts: { answer_sheets: 2, grading_results: 2, grading_details: 4 },
  storage_counts: { owned_files: 3 },
  question_bank_counts: {},
  blocking_training_tasks: [],
  can_delete: true,
}

function deletionResult(
  storageCleanupPending: boolean,
): SessionPermanentDeletionResponse {
  return {
    session_id: 7,
    db_counts: { grading_sessions: 1 },
    question_bank_counts: {},
    deleted_files: 3,
    deleted_dirs: 1,
    skipped_shared: 0,
    storage_cleanup_pending: storageCleanupPending,
    recovered_interrupted_delete: !storageCleanupPending,
  }
}

async function settle(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

function button(host: HTMLElement, label: string): HTMLButtonElement {
  const found = [...host.querySelectorAll('button')]
    .find((item) => item.textContent?.includes(label))
  if (!(found instanceof HTMLButtonElement)) {
    throw new Error(`button not found: ${label}`)
  }
  return found
}

beforeEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
  vi.clearAllMocks()
  setActivePinia(createPinia())
  api.fetchArchivedSessions
    .mockResolvedValueOnce([archived])
    .mockResolvedValue([])
  api.fetchSessionDeletionImpact.mockResolvedValue(impact)
  api.permanentlyDeleteSession
    .mockResolvedValueOnce(deletionResult(true))
    .mockResolvedValueOnce(deletionResult(false))
})

describe('SessionDeletionPanel', () => {
  it('shows partial completion and offers a cleanup-only retry', async () => {
    const store = useSessionStore()
    store.$patch({ sessions: [], selectedSessionId: null, loadState: 'ready' })
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(SessionDeletionPanel)
    app.use(createPinia())
    app.mount(host)
    await settle()

    button(host, '查看永久删除清单').click()
    await settle()
    const confirmation = host.querySelector<HTMLInputElement>(
      '.session-lifecycle__permanent input',
    )
    expect(confirmation).not.toBeNull()
    confirmation!.value = impact.permanent_delete_phrase
    confirmation!.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    button(host, '永久删除这场考试').click()
    await settle()

    expect(host.textContent).toContain('主要数据已永久删除')
    expect(host.textContent).toContain('文件清理尚未完成')
    expect(api.permanentlyDeleteSession).toHaveBeenCalledOnce()

    button(host, '重试文件清理').click()
    await settle()

    expect(api.permanentlyDeleteSession).toHaveBeenCalledTimes(2)
    expect(host.textContent).toContain('遗留文件清理已完成')
    expect(host.textContent).not.toContain('文件清理尚未完成')
    app.unmount()
  })
})
