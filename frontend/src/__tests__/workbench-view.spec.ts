
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createMemoryHistory, type Router } from 'vue-router'

import type { WorkbenchOverview } from '../api/workbench'
import { createAppRouter } from '../router'
import { useSessionStore } from '../stores/session'
import { useWorkbenchStore, type ResourceState } from '../stores/workbench'
import WorkbenchView from '../views/WorkbenchView.vue'

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

  it('routes focus, pulse and workflow actions to the existing destinations', async () => {
    const { host, router } = await mountView()
    expect(host.querySelectorAll('.workbench-workflow__steps > li')).toHaveLength(4)

    clickButton(host, '继续批改')
    await expectPath(router, '/sessions/7/grading-run')

    expect(host.textContent).not.toContain('查看班务')

    clickButton(host, '查看完整学情证据')
    await expectPath(router, '/knowledge-graph')

    clickButton(host, '考试')
    await expectPath(router, '/sessions')

    clickButton(host, '训练')
    await expectPath(router, '/question-assembly')
  })

  it('distinguishes no session and unknown overview values', async () => {
    const noSession = await mountView({ sessionId: null, overviewValue: null, overviewState: 'empty' })
    expect(noSession.host.textContent).toContain('先选择或创建一场考试')
    expect(noSession.host.textContent).toContain('尚未选择考试')
    expect(noSession.host.textContent).toContain('尚无批改进度')
    expect(noSession.host.textContent).toContain('待选择')
    clickButton(noSession.host, '配置考试')
    await expectPath(noSession.router, '/sessions')
    noSession.host.remove()

    const unknown = await mountView({
      overviewValue: { ...overview, progress: null, review: null, anomalies: null, recent_jobs: [] },
    })
    const pulse = unknown.host.querySelector('.workbench-pulse')!
    expect(pulse.textContent).toContain('尚无批改进度')
    expect(pulse.textContent).toContain('—')
  })

})
