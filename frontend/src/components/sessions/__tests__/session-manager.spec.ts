import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick } from 'vue';
import { createMemoryHistory, createRouter } from 'vue-router';

import { ApiError } from '../../../api/errors';
import type { SessionDeletionImpact, SessionPermanentDeletionResponse, SessionSummary } from '../../../api/sessions';

import { useCurriculumScopeStore } from '../../../stores/curriculum-scope';
import { useSessionStore } from '../../../stores/session';

const api=vi.hoisted(() => ({
  fetchPendingSessionCleanups: vi.fn(),
  fetchSessionDeletionImpact: vi.fn(),
  permanentlyDeleteSession: vi.fn(),
  renameSession: vi.fn(),
}))

vi.mock('../../../api/sessions', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../api/sessions')>(),
  ...api,
}))

import SessionManager from '../SessionManager.vue'
import ConfirmDialogHost from '../../design-system/ConfirmDialogHost.vue'
import { useConfirm } from '../../../composables/useConfirm'

function session(overrides: Partial<SessionSummary> & { id: number; name: string }): SessionSummary {
  return {
    status: 'created',
    curriculum_volume_id: null,
    is_deleted: false,
    deleted_at: null,
    created_at: null,
    updated_at: null,
    ...overrides,
  }
}

const older = session({ id: 7, name: '三月月考', created_at: '2026-03-20T08:00:00' })
const newer = session({ id: 9, name: '第五周单元测验', created_at: '2026-09-20T08:00:00' })

function impactFor(target: SessionSummary, overrides: Partial<SessionDeletionImpact> = {}): SessionDeletionImpact {
  return {
    session: target,
    revision: 'a'.repeat(64),
    active_jobs: 0,
    active_grading_runs: 0,
    can_archive: false,
    can_permanently_delete: true,
    permanent_delete_phrase: `永久删除 ${target.name}`,
    permanent_counts: { answer_sheets: 88, grading_results: 88, grading_details: 340 },
    storage_counts: { owned_files: 12 },
    question_bank_counts: {},
    blocking_training_tasks: [],
    can_delete: true,
    ...overrides,
  }
}

function deletionResult(
  sessionId: number,
  storageCleanupPending: boolean,
): SessionPermanentDeletionResponse {
  return {
    session_id: sessionId,
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

function button(host: ParentNode, label: string): HTMLButtonElement {
  const found = [...host.querySelectorAll('button')]
    .find(item => item.textContent?.trim() === label || item.getAttribute('aria-label') === label)
  if (!(found instanceof HTMLButtonElement)) {
    throw new Error(`button not found: ${label}`)
  }
  return found
}

function row(host: ParentNode, name: string): HTMLElement {
  const found = [...host.querySelectorAll<HTMLElement>('.session-manager__row')]
    .find(item => item.textContent?.includes(name))
  if (!found) throw new Error(`row not found: ${name}`)
  return found
}

function mountConfirmHost(): ReturnType<typeof createApp> {
  const el = document.createElement('div')
  document.body.append(el)
  const confirmApp = createApp(ConfirmDialogHost)
  confirmApp.mount(el)
  return confirmApp
}

function confirmDialog(): HTMLElement {
  const found = document.body.querySelector<HTMLElement>('[data-testid="app-confirm-dialog"]')
  if (!found) throw new Error('confirm dialog not open')
  return found
}

async function mountManager(
  sessions: SessionSummary[] = [older, newer],
): Promise<{ app: ReturnType<typeof createApp>; host: HTMLElement }> {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useSessionStore()
  await store.initialize(async () => sessions)
  /* 学期目录置为就绪，避免 onMounted 的 initialize 发出真实请求 */
  useCurriculumScopeStore().loadState = 'ready'
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/sessions', component: { template: '<div />' } },
      { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
    ],
  })
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(SessionManager)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  await settle()
  return { app, host }
}

beforeEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
  setActivePinia(createPinia())
  api.fetchPendingSessionCleanups.mockResolvedValue([])
  api.fetchSessionDeletionImpact.mockImplementation(async (id: number) =>
    impactFor(id === 7 ? older : newer))
  api.permanentlyDeleteSession.mockResolvedValue(deletionResult(7, false))
  api.renameSession.mockImplementation(async (id: number, name: string) =>
    ({ ...(id === 7 ? older : newer), name }))
})

describe('SessionManager', () => {

  it('expands a row with read-only impact, then one irreversible confirm', async () => {
    const confirmApp = mountConfirmHost()
    const { app, host } = await mountManager()

    button(row(host, '三月月考'), `删除 三月月考`).click()
    await settle()

    const detail = row(host, '三月月考').querySelector('.session-manager__detail')!
    expect(detail.textContent).toContain('88 份答卷')
    expect(detail.textContent).toContain('88 份批改结果')
    expect(detail.textContent).toContain('12 个文件')
    expect(detail.textContent).toContain('516 条记录')
    expect(detail.textContent).toContain('永久删除，无法恢复；学生名单和题库试题保留。')

    button(detail, '彻底删除').click()
    await settle()
    const dialog = confirmDialog()
    expect(dialog.textContent).toContain('彻底删除“三月月考”？')
    expect(dialog.textContent).toContain('此操作无法恢复。')
    button(dialog, '取消').click()
    await settle()
    expect(api.permanentlyDeleteSession).not.toHaveBeenCalled()
    app.unmount()
    confirmApp.unmount()
  })

  it.each<[string, Partial<SessionDeletionImpact>, string]>([
    ['training snapshots', { blocking_training_tasks: ['个性化训练 #3'], active_jobs: 0 }, '训练任务仍引用这场考试，当前不能删除。'],
    ['active work', { blocking_training_tasks: [], active_jobs: 2 }, '这场考试仍有活动任务，当前不能删除。'],
  ])('shows the blocking reason instead of the delete button when blocked by %s', async (_case, overrides, message) => {
    api.fetchSessionDeletionImpact.mockResolvedValue(impactFor(older, {
      can_permanently_delete: false,
      ...overrides,
    }))
    const { app, host } = await mountManager()

    button(row(host, '三月月考'), `删除 三月月考`).click()
    await settle()

    const detail = row(host, '三月月考').querySelector('.session-manager__detail')!
    expect(detail.textContent).toContain(message)
    expect(detail.querySelector('.session-manager__delete')).toBeNull()
    app.unmount()
  })

  it('reconciles an ambiguous delete that actually landed', async () => {
    const confirmApp = mountConfirmHost()
    api.permanentlyDeleteSession.mockRejectedValue(new ApiError({
      kind: 'timeout', status: null, code: 'request_timeout',
      message: 'timeout', details: {}, requestId: 'req-2', retryable: true,
    }))
    const { app, host } = await mountManager()
    const store = useSessionStore()
    vi.spyOn(store, 'initialize').mockImplementation(async () => {
      store.$patch({ sessions: [newer], loadState: 'ready' })
    })
    await settle()

    const target = row(host, '三月月考')
    button(target, `删除 三月月考`).click()
    await settle()
    button(target.querySelector('.session-manager__detail')!, '彻底删除').click()
    await settle()
    button(confirmDialog(), '彻底删除').click()
    await settle()

    expect(host.textContent).toContain('已重新核对')
    expect(host.textContent).toContain('已彻底删除')
    expect(host.textContent).not.toContain('结果暂时无法确认')
    expect(api.permanentlyDeleteSession).toHaveBeenCalledOnce()
    app.unmount()
    confirmApp.unmount()
  })

  it('surfaces pending storage cleanup and retries it without resubmitting the delete', async () => {
    const confirmApp = mountConfirmHost()
    api.fetchPendingSessionCleanups
      .mockReset()
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ session_id: 7, deleted_files: 3, deleted_dirs: 1, skipped_shared: 0 }])
      .mockResolvedValueOnce([])
    api.permanentlyDeleteSession
      .mockReset()
      .mockResolvedValueOnce(deletionResult(7, true))
      .mockResolvedValueOnce(deletionResult(7, false))
    const { app, host } = await mountManager()
    const store = useSessionStore()
    vi.spyOn(store, 'initialize').mockResolvedValue()

    const target = row(host, '三月月考')
    button(target, `删除 三月月考`).click()
    await settle()
    button(target.querySelector('.session-manager__detail')!, '彻底删除').click()
    await settle()
    button(confirmDialog(), '彻底删除').click()
    await vi.waitFor(() => expect(host.textContent).toContain('主要数据已永久删除'))

    expect(host.textContent).toContain('文件清理尚未完成')
    expect(host.textContent).toContain('1 场已删除考试的文件待清理')
    expect(api.permanentlyDeleteSession).toHaveBeenCalledOnce()

    button(host.querySelector('.session-manager__cleanup')!, '重试清理').click()
    await vi.waitFor(() => expect(host.textContent).toContain('遗留文件清理已完成'))

    expect(api.permanentlyDeleteSession).toHaveBeenCalledTimes(2)
    expect(api.permanentlyDeleteSession).toHaveBeenLastCalledWith(7, '0'.repeat(64), '恢复文件清理')
    expect(host.querySelector('.session-manager__cleanup')).toBeNull()
    app.unmount()
    confirmApp.unmount()
  })

  it('keeps a queued confirm pending after the first resolves', async () => {
    const confirmApp = mountConfirmHost()
    const { confirm } = useConfirm()
    const first = confirm({ title: '第一个确认？' })
    const second = confirm({ title: '第二个确认？', danger: true })
    await settle()

    let dialog = confirmDialog()
    expect(dialog.textContent).toContain('第一个确认？')
    button(dialog, '确认').click()
    await settle()

    dialog = confirmDialog()
    expect(dialog.textContent).toContain('第二个确认？')
    expect(dialog.textContent).not.toContain('第一个确认？')
    await expect(first).resolves.toBe(true)
    await vi.waitFor(() => expect(document.activeElement).toBe(button(dialog, '取消')))

    let secondSettled = false
    void second.then(() => { secondSettled = true })
    await settle()
    expect(secondSettled).toBe(false)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await settle()
    await expect(second).resolves.toBe(false)
    expect(document.body.querySelector('[data-testid="app-confirm-dialog"]')).toBeNull()
    confirmApp.unmount()
  })

})
