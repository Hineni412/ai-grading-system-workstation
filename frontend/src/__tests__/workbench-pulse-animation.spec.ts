import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { WorkbenchOverview } from '../api/workbench'
import { createAppRouter } from '../router'
import { useAnalysisStore } from '../stores/analysis'
import { useSessionStore } from '../stores/session'
import { useWorkbenchStore } from '../stores/workbench'
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
  recent_jobs: [],
  recent_sessions: [],
  updated_at: '2026-07-15T09:35:00Z',
}

const mountedApps: App[] = []

async function settleUi(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountView(overviewValue: WorkbenchOverview) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createAppRouter(createMemoryHistory())
  await router.push('/design-system')
  await router.isReady()

  const sessionStore = useSessionStore(pinia)
  sessionStore.$patch({
    sessions: [overviewValue.current_session!],
    selectedSessionId: 7,
    loadState: 'ready',
  })

  const workbenchStore = useWorkbenchStore(pinia)
  vi.spyOn(workbenchStore, 'loadOverview').mockImplementation(async (id) => {
    workbenchStore.$patch({
      sessionId: id,
      overview: overviewValue,
      overviewState: 'ready',
      overviewError: '',
      overviewUpdatedAt: '2026-07-15T09:35:00Z',
    })
  })
  vi.spyOn(workbenchStore, 'loadAnomalies').mockResolvedValue()
  vi.spyOn(workbenchStore, 'loadMoreAnomalies').mockResolvedValue()

  const analysisStore = useAnalysisStore(pinia)
  vi.spyOn(analysisStore, 'loadQuestions').mockImplementation(async (id, className) => {
    analysisStore.$patch({
      sessionId: id,
      questions: [],
      classes: [],
      questionsScope: { session_id: id, class_name: className ?? null, question_id: null },
      questionsState: 'ready',
      questionsError: '',
      questionsUpdatedAt: '2026-07-15T09:36:00Z',
      questionsTotal: 0,
      questionsPage: 1,
      questionsPageSize: 100,
      questionsTotalPages: 0,
    })
  })
  vi.spyOn(analysisStore, 'loadStudents').mockResolvedValue()
  vi.spyOn(analysisStore, 'loadMoreQuestions').mockResolvedValue()
  vi.spyOn(analysisStore, 'loadMoreStudents').mockResolvedValue()

  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(WorkbenchView)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  mountedApps.push(app)
  await settleUi()

  return { host }
}

afterEach(() => {
  vi.restoreAllMocks()
  while (mountedApps.length) mountedApps.pop()?.unmount()
  document.body.innerHTML = ''
})

describe('workbench pulse animated metrics', () => {
  it('shows the circular progress and ticker counts at their final values', async () => {
    const { host } = await mountView(overview)

    const pulse = host.querySelector('.workbench-pulse')!
    expect(pulse).not.toBeNull()

    const ring = pulse.querySelector('.workbench-pulse__score .progress-circle-base')
    expect(ring).not.toBeNull()
    expect(pulse.querySelector('[data-current-value]')?.textContent?.trim()).toBe('33')
    expect(pulse.querySelector('.workbench-pulse__score')?.textContent).toContain('% 已批改')

    const stats = [...pulse.querySelectorAll('.workbench-pulse__stats > div')]
    expect(stats[0]?.querySelector('dd')?.textContent).toBe('12份')
    expect(stats[1]?.querySelector('dd')?.textContent).toBe('3项')
    expect(stats[2]?.querySelector('dd')?.textContent).toBe('3条')
  })

  it('keeps the placeholder text when the overview values are unknown', async () => {
    const unknown: WorkbenchOverview = {
      ...overview,
      progress: null,
      review: null,
      anomalies: null,
    }
    const { host } = await mountView(unknown)

    const pulse = host.querySelector('.workbench-pulse')!
    expect(pulse.querySelector('.workbench-pulse__score .progress-circle-base')).toBeNull()
    expect(pulse.querySelector('.workbench-pulse__score strong')?.textContent).toBe('—')
    expect(pulse.querySelector('.workbench-pulse__score')?.textContent).toContain('尚无批改进度')

    const stats = [...pulse.querySelectorAll('.workbench-pulse__stats > div')]
    expect(stats[0]?.querySelector('dd')?.textContent).toBe('—份')
    expect(stats[1]?.querySelector('dd')?.textContent).toBe('—项')
    expect(stats[2]?.querySelector('dd')?.textContent).toBe('—条')
  })
})
