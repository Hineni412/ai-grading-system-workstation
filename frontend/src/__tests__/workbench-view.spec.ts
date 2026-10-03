
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createMemoryHistory, type Router } from 'vue-router'

import type { WorkbenchOverview } from '../api/workbench'
import { createAppRouter } from '../router'
import { useSessionStore } from '../stores/session'
import { useWorkbenchStore, type ResourceState } from '../stores/workbench'
import WorkbenchView from '../views/WorkbenchView.vue'
import { fetchRegionReadiness } from '../api/template-regions'
import { getSessionQuestionBankAnalysisStatus } from '../api/session-question-bank-sync'
import { buildTodoRows, buildExamSteps, type HomeInputs } from '../components/workbench/workbench-home'
import { useJobStore } from '../stores/jobs'
import { useConfigWorkspaceStore } from '../stores/config-workspace'
import type { JobResponse } from '../api/jobs'
vi.mock('../api/template-regions', () => ({ fetchRegionReadiness: vi.fn() }))
vi.mock('../api/session-question-bank-sync', () => ({ getSessionQuestionBankAnalysisStatus: vi.fn() }))
const ready = { session_id: 7, scoring_configured: true, template_present: true, template_ready: true }
const bank = { question_count: 2, tagged_count: 1, evidence_count: 1, criteria_count: 1, complete_count: 1,
  pending_taxonomy_count: 0, incomplete_question_ids: [1], incomplete_source_refs: ['Q1'] }
beforeEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(fetchRegionReadiness).mockResolvedValue(ready)
  vi.mocked(getSessionQuestionBankAnalysisStatus).mockResolvedValue(bank)
})

const overview: WorkbenchOverview = {
  current_session: {
    id: 7,
    name: '七年级数学期末质量监测',
    status: 'grading',
    is_deleted: false,
    deleted_at: null,
    created_at: '2026-07-14T08:00:00Z',
    updated_at: '2026-07-15T09:30:00Z',
  },
  progress: {
    total_papers: 36,
    matched_papers: 35,
    unmatched_papers: 1,
    graded_papers: 12,
    failed_papers: 1,
    grading_papers: 2,
    needs_human_review: 3,
    absent_students: 0,
    scan_issue_students: 1,
    progress_percent: 33.33,
  },
  review: { question_count: 2, item_count: 3 },
  anomalies: { unmatched_papers: 1, scan_issue_students: 1, failed_papers: 1 },
  recent_jobs: [{
    id: 41,
    job_type: 'grading',
    status: 'running',
    progress: 0.58,
    stage: '评分中',
    detail: '正在读取已匹配答卷',
    created_at: '2026-07-15T09:00:00Z',
    started_at: '2026-07-15T09:01:00Z',
    updated_at: '2026-07-15T09:32:00Z',
    finished_at: null,
  }],
  recent_sessions: [{
    session: {
      id: 8,
      name: '八年级物理单元检测（长名称用于验证稳定布局）',
      status: 'completed',
      is_deleted: false,
      deleted_at: null,
      created_at: '2026-07-12T08:00:00Z',
      updated_at: '2026-07-13T10:00:00Z',
    },
    progress: {
      total_papers: 40,
      matched_papers: 40,
      unmatched_papers: 0,
      graded_papers: 40,
      failed_papers: 0,
      grading_papers: 0,
      needs_human_review: 0,
      absent_students: 0,
      scan_issue_students: 0,
      progress_percent: 100,
    },
  }],
  updated_at: '2026-07-15T09:35:00Z',
}

interface MountOptions {
  sessionId?: number | null
  overviewState?: ResourceState
  overviewValue?: WorkbenchOverview | null
}

const mountedApps: App[] = []

async function settleUi(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountView({
  sessionId = 7,
  overviewState = 'ready',
  overviewValue = overview,
}: MountOptions = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createAppRouter(createMemoryHistory())
  await router.push('/workbench')
  await router.isReady()

  const sessionStore = useSessionStore(pinia)
  sessionStore.$patch({
    sessions: [overview.current_session!, overview.recent_sessions[0]!.session],
    selectedSessionId: sessionId,
    loadState: 'ready',
  })

  const workbenchStore = useWorkbenchStore(pinia)
  const loadOverview = vi.spyOn(workbenchStore, 'loadOverview').mockImplementation(async (id) => {
    workbenchStore.$patch({
      sessionId: id,
      overview: overviewValue,
      overviewState,
      overviewError: overviewState === 'stale-error' || overviewState === 'error'
        ? '工作台数据暂时无法更新'
        : '',
      overviewUpdatedAt: overviewValue === null ? null : '2026-07-15T09:35:00Z',
    })
  })

  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(WorkbenchView)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  mountedApps.push(app)
  await settleUi()

  return {
    host,
    router,
    sessionStore,
    workbenchStore,
    loadOverview,
  }
}

function clickButton(host: HTMLElement, label: string): void {
  const button = [...host.querySelectorAll<HTMLButtonElement>('button')]
    .find((candidate) => candidate.textContent?.trim().includes(label))
  expect(button, `button ${label}`).toBeDefined()
  button!.click()
}

async function expectPath(router: Router, path: string): Promise<void> {
  await vi.waitFor(
    () => expect(router.currentRoute.value.fullPath).toBe(path),
    { timeout: 5000, interval: 20 },
  )
}

afterEach(() => {
  while (mountedApps.length) mountedApps.pop()?.unmount()
  document.body.innerHTML = ''
})


describe('workbench view', () => {
  it('shows ordered actions, one primary button, separate anomaly units and vertical hints', async () => {
    const { host, router } = await mountView()
    expect([...host.querySelectorAll('[data-todo]')].map(el => el.getAttribute('data-todo')))
      .toEqual(['anomalies', 'grade', 'review', 'bank', 'walkthrough'])
    const todo = host.querySelector('.workbench-todo')!
    expect(todo.querySelectorAll('[data-variant="primary"]')).toHaveLength(1)
    expect(todo.textContent).toContain('5 项')
    expect(todo.textContent).toContain('1 份答卷未匹配学生，1 名学生扫描页有问题，1 份批改失败')
    expect(todo.textContent).not.toContain('异常 3')
    expect(host.querySelector('.step-progress--vertical')?.textContent).toContain('35/36 份已匹配')
    clickButton(host, '去看卷')
    await expectPath(router, '/results?tab=overview&open=walkthrough')
    clickButton(host, '去补齐')
    await expectPath(router, '/question-bank?tab=todo')
    clickButton(host, '开始复核')
    await expectPath(router, '/grading')
    clickButton(host, '继续批改')
    await expectPath(router, '/sessions/7/grading-run')
  })
  it('shows review and completed states without promoting continue actions', async () => {
    const completed = { ...overview, progress: { ...overview.progress!, matched_papers: 36, graded_papers: 36, failed_papers: 0,
      unmatched_papers: 0, scan_issue_students: 0 }, anomalies: { unmatched_papers: 0, scan_issue_students: 0, failed_papers: 0 } }
    vi.mocked(getSessionQuestionBankAnalysisStatus).mockResolvedValue({ ...bank, incomplete_question_ids: [] })
    const { host, router, workbenchStore } = await mountView({ overviewValue: completed })
    expect(host.querySelector('.workbench-todo')?.textContent).toContain('复核 3 项评分')
    workbenchStore.overview = { ...completed, review: { item_count: 0, question_count: 0 } }
    await settleUi()
    expect(host.querySelector('.workbench-todo')?.textContent).toContain('按失分题组讲义')
    expect(host.querySelector('.workbench-todo [data-variant="primary"]')).toBeNull()
    expect(host.querySelectorAll('.step-progress .is-done')).toHaveLength(9)
    clickButton(host, '去组卷')
    await expectPath(router, '/question-assembly?mode=assistant')
  })
  it('handles no exam and failed overview without inventing exam actions', async () => {
    const noSession = await mountView({ sessionId: null, overviewValue: null, overviewState: 'empty' })
    expect(noSession.host.textContent).toContain('尚未选择考试')
    expect(noSession.host.textContent).toContain('今天没有待处理的事项')
    expect(fetchRegionReadiness).not.toHaveBeenCalled()
    clickButton(noSession.host, '新建考试')
    await expectPath(noSession.router, '/sessions')
    const failed = await mountView({ overviewValue: null, overviewState: 'error' })
    expect(failed.host.textContent).toContain('考试概况暂时无法读取')
    expect(failed.host.querySelector('[data-todo]')).toBeNull()
  })
  it('retries only failed question-bank data and cancels old exam reads', async () => {
    vi.mocked(getSessionQuestionBankAnalysisStatus).mockRejectedValueOnce(new Error('unavailable'))
    const { host, sessionStore } = await mountView()
    expect(host.textContent).toContain('题库资料状态暂时无法读取')
    expect(host.querySelector('[data-todo="bank"]')).toBeNull()
    clickButton(host, '重试')
    await settleUi()
    expect(host.querySelector('[data-todo="bank"]')).not.toBeNull()
    let finish!: (value: typeof ready) => void
    vi.mocked(fetchRegionReadiness).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    sessionStore.selectedSessionId = 8
    await settleUi()
    const oldSignal = vi.mocked(fetchRegionReadiness).mock.calls.slice(-1)[0]![1]!
    sessionStore.selectedSessionId = 7
    await settleUi()
    expect(oldSignal.aborted).toBe(true)
    finish({ ...ready, session_id: 8, scoring_configured: false })
    await settleUi()
    expect(host.querySelector('[data-todo="setup"]')).toBeNull()
  })
  it('shows tracked running jobs and honors the shared exam-switch guard', async () => {
    const { host, sessionStore } = await mountView()
    useJobStore().jobs = { 41: { ...overview.recent_jobs[0]!, payload: { session_id: 7 }, result: {}, error: null, cancel_requested: false } }
    await settleUi()
    expect(host.querySelector('[data-todo="grade"]')).toBeNull()
    expect(host.querySelector('.workbench-running-row')?.textContent).toContain('58%')
    expect(host.querySelector('.workbench-running-row button')).toBeNull()
    const guard = vi.spyOn(useConfigWorkspaceStore(), 'selectSession').mockReturnValue(false)
    host.querySelector<HTMLButtonElement>('.workbench-exams-name button')!.click()
    await settleUi()
    expect(guard).toHaveBeenCalledWith(8)
    expect(sessionStore.selectedSessionId).toBe(7)
    guard.mockReturnValue(true)
    host.querySelector<HTMLButtonElement>('.workbench-exams-name button')!.click()
    await settleUi()
    expect(sessionStore.selectedSessionId).toBe(8)
  })
})
describe('home rule boundaries', () => {
  it('drops superseded overview requests and retains data only within the same semester', async () => {
    setActivePinia(createPinia())
    const store = useWorkbenchStore()
    let finish!: (value: WorkbenchOverview) => void
    const oldLoad = store.loadOverview(7, 'term-a', () => new Promise(resolve => { finish = resolve }))
    const next = { ...overview, current_session: { ...overview.current_session!, id: 8 } }
    await store.loadOverview(8, 'term-b', async (_id, volume) => {
      expect(volume).toBe('term-b')
      return next
    })
    finish(overview)
    await oldLoad
    expect(store.overview?.current_session?.id).toBe(8)
    const fail = async (): Promise<WorkbenchOverview> => { throw new Error('TEST-unavailable') }
    await store.loadOverview(8, 'term-b', fail)
    expect(store.overviewState).toBe('stale-error')
    expect(store.overview?.current_session?.id).toBe(8)
    await store.loadOverview(8, 'term-a', fail)
    expect(store.overviewState).toBe('error')
    expect(store.overview).toBeNull()
  })
  const input = (): HomeInputs => ({ sessionId: 7, overview: structuredClone(overview), readiness: ready, analysis: null, jobs: [] })
  it('routes configuration before upload and never completes unknown review or failed grading', () => {
    const data = input()
    data.overview!.progress = { ...overview.progress!, total_papers: 0, matched_papers: 0, graded_papers: 0, failed_papers: 0 }
    data.readiness = { ...ready, scoring_configured: false, template_ready: false }
    expect(buildTodoRows(data).need[0]).toMatchObject({ id: 'setup', path: '/sessions' })
    data.readiness.scoring_configured = true
    expect(buildTodoRows(data).need[0]?.path).toBe('/sessions/7/regions')
    data.readiness.template_ready = true
    expect(buildTodoRows(data).need[0]?.id).toBe('upload')
    expect(buildExamSteps(data)[2]).toMatchObject({ status: 'todo', hint: '—' })
    data.overview!.progress = { ...overview.progress!, graded_papers: 34, failed_papers: 1 }
    expect(buildExamSteps(data)[2]).toMatchObject({ status: 'in_progress', hint: '34/35 · 失败 1' })
    data.overview!.progress = { ...overview.progress!, graded_papers: 35, failed_papers: 0 }
    data.overview!.review = null
    expect(buildExamSteps(data)[3]).toMatchObject({ status: 'todo', hint: '状态未读取' })
    expect(buildTodoRows(data).continued.map(row => row.id)).toEqual(['walkthrough'])
  })
  it('suppresses continuing grading only for this exam and a nonterminal grading job', () => {
    const data = input()
    const job: JobResponse = { ...overview.recent_jobs[0]!, payload: { session_id: 8 }, result: {}, error: null, cancel_requested: false }
    data.jobs = [job]
    expect(buildTodoRows(data).need.some(row => row.id === 'grade')).toBe(true)
    job.payload.session_id = 7
    expect(buildTodoRows(data).need.some(row => row.id === 'grade')).toBe(false)
    job.status = 'succeeded'
    expect(buildTodoRows(data).need.some(row => row.id === 'grade')).toBe(true)
  })
})
