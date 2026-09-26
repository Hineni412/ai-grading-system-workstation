import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  SessionDeletionImpact,
  SessionPermanentDeletionResponse,
  SessionSummary,
} from '../../../api/sessions'
import { ApiError } from '../../../api/errors'
import { SESSION_STORAGE_KEY, useSessionStore } from '../../../stores/session'
import { useConfigWorkspaceStore } from '../../../stores/config-workspace'
import { useCurriculumScopeStore } from '../../../stores/curriculum-scope'

const api = vi.hoisted(() => ({
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
  it('lists sessions newest-first with the current marker and meta columns', async () => {
    const { app, host } = await mountManager()
    const store = useSessionStore()
    store.selectSession(7)
    await settle()

    const names = [...host.querySelectorAll('.session-manager__name')]
      .map(item => item.textContent)
    expect(names).toEqual(['第五周单元测验', '三月月考'])
    expect(row(host, '三月月考').textContent).toContain('当前')
    expect(row(host, '三月月考').textContent).toContain('3 月 20 日')
    expect(row(host, '三月月考').querySelector('.session-manager__state')?.textContent).toContain('待开始')
    expect(row(host, '三月月考').querySelector('button[aria-label="设为当前考试"]')).toBeNull()
    expect(row(host, '第五周单元测验').querySelector('button[aria-label="设为当前考试"]')).not.toBeNull()
    app.unmount()
  })

  it('filters by search text and by a LOCAL semester selection', async () => {
    const { app, host } = await mountManager([
      session({ id: 7, name: '八上期中', curriculum_volume_id: 'vol-8a' }),
      session({ id: 9, name: '七下期末', curriculum_volume_id: 'vol-7b' }),
    ])
    const curriculumScope = useCurriculumScopeStore()
    curriculumScope.volumes = [
      { id: 'vol-8a', label: '八年级上册' },
      { id: 'vol-7b', label: '七年级下册' },
    ] as never
    curriculumScope.loadState = 'ready'
    await settle()

    const search = host.querySelector<HTMLInputElement>('.session-manager__search input')!
    search.value = '七下'
    search.dispatchEvent(new Event('input', { bubbles: true }))
    await settle()
    expect(host.querySelectorAll('.session-manager__row')).toHaveLength(1)
    expect(host.textContent).toContain('七下期末')

    search.value = ''
    search.dispatchEvent(new Event('input', { bubbles: true }))
    const select = host.querySelector<HTMLSelectElement>('.session-manager__toolbar select')!
    select.value = 'vol-8a'
    select.dispatchEvent(new Event('change'))
    await settle()

    expect(host.querySelectorAll('.session-manager__row')).toHaveLength(1)
    expect(host.textContent).toContain('八上期中')
    expect(row(host, '八上期中').querySelector('.session-manager__term')?.textContent).toBe('八上')
    expect(curriculumScope.selectedVolumeId).toBeNull()
    app.unmount()
  })

  it('switches the current exam through the row action and honors a declined guard', async () => {
    const { app, host } = await mountManager()
    const store = useSessionStore()
    const configStore = useConfigWorkspaceStore()
    store.selectSession(7)
    configStore.selectSession(7)
    await settle()

    button(row(host, '第五周单元测验'), '设为当前考试').click()
    await settle()
    expect(store.selectedSessionId).toBe(9)
    expect(configStore.sessionId).toBe(9)

    configStore.updateEditor({ row_id: 'row-1', standard_answer: '未保存答案' })
    vi.stubGlobal('confirm', vi.fn(() => false))
    button(row(host, '三月月考'), '设为当前考试').click()
    await settle()
    expect(store.selectedSessionId).toBe(9)
    expect(configStore.sessionId).toBe(9)
    app.unmount()
  })

  it('renames a non-selected session inline and keeps the selection', async () => {
    const { app, host } = await mountManager()
    const store = useSessionStore()
    store.selectSession(7)
    await settle()

    const target = row(host, '第五周单元测验')
    button(target, '重命名').click()
    await settle()
    const input = target.querySelector<HTMLInputElement>('.session-manager__rename-input')!
    input.value = '第六周单元测验'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await settle()

    expect(api.renameSession).toHaveBeenCalledWith(9, '第六周单元测验')
    expect(store.sessions.find(item => item.id === 9)?.name).toBe('第六周单元测验')
    expect(store.selectedSessionId).toBe(7)
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBe('7')
    expect(host.querySelector('.session-manager__rename-input')).toBeNull()
    app.unmount()
  })

  it('shows an inline error for an empty rename and cancels on Escape', async () => {
    const { app, host } = await mountManager()
    const target = row(host, '第五周单元测验')
    button(target, '重命名').click()
    await settle()
    const input = target.querySelector<HTMLInputElement>('.session-manager__rename-input')!
    input.value = '   '
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await settle()

    expect(target.textContent).toContain('考试名称不能为空')
    expect(api.renameSession).not.toHaveBeenCalled()

    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await settle()
    expect(host.querySelector('.session-manager__rename-input')).toBeNull()
    app.unmount()
  })

  it('expands a row with read-only impact, then one irreversible confirm', async () => {
    vi.stubGlobal('confirm', vi.fn(() => false))
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
    expect(window.confirm).toHaveBeenCalledWith('确认彻底删除“三月月考”吗？此操作无法恢复。')
    expect(api.permanentlyDeleteSession).not.toHaveBeenCalled()
    app.unmount()
  })

  it('deletes the selected exam and clears the selection', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))
    const { app, host } = await mountManager()
    const store = useSessionStore()
    store.selectSession(7)
    vi.spyOn(store, 'initialize').mockImplementation(async () => {
      store.$patch({ sessions: [newer], loadState: 'ready' })
    })
    await settle()

    const target = row(host, '三月月考')
    button(target, `删除 三月月考`).click()
    await settle()
    button(target.querySelector('.session-manager__detail')!, '彻底删除').click()
    await settle()

    expect(api.permanentlyDeleteSession).toHaveBeenCalledWith(7, 'a'.repeat(64), '永久删除 三月月考')
    expect(store.selectedSessionId).toBeNull()
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBeNull()
    expect(host.textContent).toContain('已彻底删除')
    app.unmount()
  })

  it('keeps the current selection when deleting another exam', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))
    const { app, host } = await mountManager()
    const store = useSessionStore()
    store.selectSession(7)
    vi.spyOn(store, 'initialize').mockImplementation(async () => {
      store.$patch({ sessions: [older], loadState: 'ready' })
    })
    await settle()

    const target = row(host, '第五周单元测验')
    button(target, `删除 第五周单元测验`).click()
    await settle()
    button(target.querySelector('.session-manager__detail')!, '彻底删除').click()
    await settle()

    expect(api.permanentlyDeleteSession).toHaveBeenCalledWith(9, expect.any(String), expect.any(String))
    expect(store.selectedSessionId).toBe(7)
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBe('7')
    expect(host.textContent).toContain('已彻底删除')
    app.unmount()
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

  it('reports an impact-read failure without deleting anything', async () => {
    api.fetchSessionDeletionImpact.mockRejectedValue(new Error('offline'))
    const { app, host } = await mountManager()

    button(row(host, '三月月考'), `删除 三月月考`).click()
    await settle()

    expect(row(host, '三月月考').textContent).toContain('暂时无法读取删除范围，没有删除任何内容。')
    expect(api.permanentlyDeleteSession).not.toHaveBeenCalled()
    app.unmount()
  })

  it('maps the revision-conflict error code to a stable message', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))
    api.permanentlyDeleteSession.mockRejectedValue(new ApiError({
      kind: 'conflict', status: 409, code: 'session_delete_revision_conflict',
      message: 'private detail', details: {}, requestId: 'req-1', retryable: false,
    }))
    const { app, host } = await mountManager()

    const target = row(host, '三月月考')
    button(target, `删除 三月月考`).click()
    await settle()
    button(target.querySelector('.session-manager__detail')!, '彻底删除').click()
    await settle()

    expect(target.textContent).toContain('考试内容在确认期间发生变化，请重新查看影响。')
    expect(target.textContent).not.toContain('private detail')
    app.unmount()
  })

  it('reconciles an ambiguous delete that actually landed', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))
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

    expect(host.textContent).toContain('已重新核对')
    expect(host.textContent).toContain('已彻底删除')
    expect(host.textContent).not.toContain('结果暂时无法确认')
    expect(api.permanentlyDeleteSession).toHaveBeenCalledOnce()
    app.unmount()
  })

  it('keeps the row open with a recheck message when the exam was not deleted', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))
    api.permanentlyDeleteSession.mockRejectedValue(new ApiError({
      kind: 'timeout', status: null, code: 'request_timeout',
      message: 'timeout', details: {}, requestId: 'req-3', retryable: true,
    }))
    const { app, host } = await mountManager()
    const store = useSessionStore()
    vi.spyOn(store, 'initialize').mockImplementation(async () => {
      store.$patch({ loadState: 'ready' })
    })
    await settle()

    const target = row(host, '三月月考')
    button(target, `删除 三月月考`).click()
    await settle()
    button(target.querySelector('.session-manager__detail')!, '彻底删除').click()
    await settle()

    expect(row(host, '三月月考').textContent).toContain('已重新核对：考试尚未删除，可以再次查看影响后重试。')
    expect(api.permanentlyDeleteSession).toHaveBeenCalledOnce()
    app.unmount()
  })

  it('reports an unconfirmable outcome when the list cannot be re-read', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))
    api.permanentlyDeleteSession.mockRejectedValue(new ApiError({
      kind: 'network', status: null, code: 'network_error',
      message: 'offline', details: {}, requestId: 'req-4', retryable: true,
    }))
    const { app, host } = await mountManager()
    const store = useSessionStore()
    vi.spyOn(store, 'initialize').mockImplementation(async () => {
      store.$patch({ loadState: 'error' })
    })
    await settle()

    const target = row(host, '三月月考')
    button(target, `删除 三月月考`).click()
    await settle()
    button(target.querySelector('.session-manager__detail')!, '彻底删除').click()
    await settle()

    expect(host.textContent).toContain('删除结果暂时无法确认')
    expect(host.textContent).toContain('不会重复提交')
    expect(api.permanentlyDeleteSession).toHaveBeenCalledOnce()
    app.unmount()
  })

  it('surfaces pending storage cleanup and retries it without resubmitting the delete', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))
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

    expect(host.textContent).toContain('主要数据已永久删除')
    expect(host.textContent).toContain('文件清理尚未完成')
    expect(host.textContent).toContain('1 场已删除考试的文件待清理')
    expect(api.permanentlyDeleteSession).toHaveBeenCalledOnce()

    button(host.querySelector('.session-manager__cleanup')!, '重试清理').click()
    await settle()

    expect(api.permanentlyDeleteSession).toHaveBeenCalledTimes(2)
    expect(api.permanentlyDeleteSession).toHaveBeenLastCalledWith(7, '0'.repeat(64), '恢复文件清理')
    expect(host.textContent).toContain('遗留文件清理已完成')
    expect(host.querySelector('.session-manager__cleanup')).toBeNull()
    app.unmount()
  })

  it('rediscovers a pending cleanup on mount', async () => {
    api.fetchPendingSessionCleanups.mockReset().mockResolvedValue([{
      session_id: 9, deleted_files: 2, deleted_dirs: 1, skipped_shared: 0,
    }])
    api.permanentlyDeleteSession.mockReset().mockResolvedValue(deletionResult(9, false))
    const { app, host } = await mountManager()

    expect(host.textContent).toContain('1 场已删除考试的文件待清理')
    button(host.querySelector('.session-manager__cleanup')!, '重试清理').click()
    await settle()

    expect(api.permanentlyDeleteSession).toHaveBeenCalledWith(9, '0'.repeat(64), '恢复文件清理')
    expect(host.querySelector('.session-manager__cleanup')).toBeNull()
    app.unmount()
  })

  it('shows the empty state with a new-exam entry', async () => {
    const { app, host } = await mountManager([])
    expect(host.textContent).toContain('还没有考试')
    expect(host.querySelector('.session-manager__empty a')?.textContent).toContain('新建考试')
    app.unmount()
  })
})
