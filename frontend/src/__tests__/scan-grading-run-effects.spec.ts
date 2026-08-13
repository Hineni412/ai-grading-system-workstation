import { createApp, nextTick, type App } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../api/scan-grading'
import type { JobResponse } from '../api/jobs'
import { createAppRouter } from '../router'
import ScanGradingView from '../views/ScanGradingView.vue'

vi.mock('../api/scan-grading', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/scan-grading')>(),
  fetchGradingWorkspace: vi.fn(), uploadScan: vi.fn(), removeScan: vi.fn(), clearScans: vi.fn(),
  freezeScans: vi.fn(), startPreflight: vi.fn(), fetchPreflight: vi.fn(),
  fetchScanStudentOptions: vi.fn(), fetchGradingPlan: vi.fn(),
  saveScanDecisions: vi.fn(), startGrading: vi.fn(), controlGrading: vi.fn(), cancelGrading: vi.fn(),
  supplementGrading: vi.fn(), startNewScanBatch: vi.fn(),
}))

function workspace(): api.GradingWorkspace {
  return {
    session_id: 7,
    upload_batch: {
      batch_id: 'batch-1', revision: 3, state: 'frozen', files: [
        { id: 'one', name: '一班答卷.pdf', media_type: 'application/pdf', size_bytes: 2048,
          sha256_prefix: 'a'.repeat(12), added_at: '2026-07-16T12:00:00Z' },
      ], file_count: 1, total_bytes: 2048, frozen_at: '2026-07-16T12:01:00Z',
    },
    grading_run: null,
  }
}

const mountedApps: App[] = []

async function mountView() {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/sessions/7/grading-run')
  await router.isReady()
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(ScanGradingView)
  app.use(createPinia()); app.use(router); app.mount(host)
  mountedApps.push(app)
  for (let index = 0; index < 6; index += 1) {
    await Promise.resolve(); await nextTick()
  }
  return { host }
}

beforeEach(() => {
  document.body.innerHTML = ''; localStorage.clear(); vi.clearAllMocks()
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: vi.fn(),
  })
  vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(workspace())
  vi.mocked(api.fetchPreflight).mockResolvedValue({
    revision: 2, summary: {
      auto_matched: 28, ready_to_grade: 28, issues: 0, absent_candidates: 0, total_pages: 60,
    },
    groups: [], issues: [], absent_students: [], warnings: [],
    decisions: [], pending_issue_count: 0,
  })
  vi.mocked(api.fetchScanStudentOptions).mockResolvedValue([])
  vi.mocked(api.fetchGradingPlan).mockImplementation(async (_sessionId, mode) => ({
    mode,
    status: 'ready',
    counts: { ready_to_grade: 28, pending_items: 28, teacher_final_items: 0 },
    requests: { full_paper: 28, objective_sheet: 28, subjective_batches: 10 },
    batching: { subjective_group_min: 2, subjective_group_max: 3 },
    warnings: [],
    blockers: [],
  }))
})

afterEach(() => {
  while (mountedApps.length) mountedApps.pop()?.unmount()
  document.body.innerHTML = ''
})

describe('scan grading run effects', () => {
  it('shows the border beam on the run progress card only while the run is active', async () => {
    const active = workspace()
    active.grading_run = {
      run_id: 19, mode: 'hybrid_batch', state: 'running',
      counts: { graded: 12, grading: 1, pending: 15, skipped: 2, failed: 1, conflict: 0, total: 31 },
      allowed_actions: ['pause', 'cancel'],
    }
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(active)
    const { host } = await mountView()

    const progress = host.querySelector('.scan-progress--run')!
    expect(progress.getAttribute('role')).toBe('status')
    expect(progress.getAttribute('aria-live')).toBe('polite')
    expect(progress.textContent).toContain('31')
    const beam = progress.querySelector('.border-beam')
    expect(beam).not.toBeNull()
    expect(beam?.getAttribute('aria-hidden')).toBe('true')
  })

  it('removes the border beam once the run reaches a terminal state', async () => {
    const finished = workspace()
    finished.grading_run = {
      run_id: 19, mode: 'hybrid_batch', state: 'completed',
      counts: { graded: 31, grading: 0, pending: 0, skipped: 0, failed: 0, conflict: 0, total: 31 },
      allowed_actions: [],
    }
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(finished)
    const { host } = await mountView()

    const progress = host.querySelector('.scan-progress--run')!
    expect(progress).not.toBeNull()
    expect(progress.querySelector('.border-beam')).toBeNull()
  })

  it('keeps the confirm-plan hooks on the AppButton and starts grading on click', async () => {
    vi.mocked(api.startGrading).mockResolvedValue({
      id: 91, job_type: 'grading_run', payload: { session_id: 7 }, result: {}, status: 'queued',
      progress: 0, stage: 'queued', detail: '', error: null, cancel_requested: false,
      created_at: '2026-07-17T00:00:00Z', started_at: null,
      updated_at: '2026-07-17T00:00:00Z', finished_at: null,
    } satisfies JobResponse)
    const { host } = await mountView()

    host.querySelector<HTMLButtonElement>('[data-grading-mode="full_paper"]')!.click()
    let button: HTMLButtonElement | null = null
    await vi.waitFor(() => {
      button = host.querySelector<HTMLButtonElement>('[data-confirm-grading-plan]')
      expect(button).not.toBeNull()
      expect(button!.disabled).toBe(false)
    })
    button = button!

    expect(button.classList.contains('app-button')).toBe(true)
    expect(button.getAttribute('data-variant')).toBe('primary')
    expect(button.getAttribute('type')).toBe('button')
    expect(button.querySelector('.app-button__ripples')).not.toBeNull()
    expect(button.textContent).toContain('确认并开始整卷批改')

    button.click()
    await nextTick()
    expect(api.startGrading).toHaveBeenCalledTimes(1)
  })
})
