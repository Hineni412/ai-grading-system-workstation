import { createApp, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../api/scan-grading'
import { fetchStudents } from '../api/students'
import { createAppRouter } from '../router'
import ScanGradingView from '../views/ScanGradingView.vue'

vi.mock('../api/scan-grading', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/scan-grading')>(),
  fetchGradingWorkspace: vi.fn(), uploadScan: vi.fn(), removeScan: vi.fn(), clearScans: vi.fn(),
  freezeScans: vi.fn(), startPreflight: vi.fn(), fetchPreflight: vi.fn(),
  saveScanDecisions: vi.fn(), startGrading: vi.fn(), controlGrading: vi.fn(), cancelGrading: vi.fn(),
  supplementGrading: vi.fn(), startNewScanBatch: vi.fn(),
}))
vi.mock('../api/students', () => ({ fetchStudents: vi.fn() }))

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

async function mountView() {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/sessions/7/grading-run')
  await router.isReady()
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(ScanGradingView)
  app.use(createPinia()); app.use(router); app.mount(host)
  for (let index = 0; index < 6; index += 1) {
    await Promise.resolve(); await nextTick()
  }
  return { app, host }
}

beforeEach(() => {
  document.body.innerHTML = ''; localStorage.clear(); vi.clearAllMocks()
  vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(workspace())
  vi.mocked(api.fetchPreflight).mockResolvedValue({
    revision: 2, summary: { auto_matched: 28, issues: 2, absent_candidates: 1, total_pages: 60 },
    groups: [], issues: [{ id: 'i1' }, { id: 'i2' }], absent_students: [], warnings: [],
    decisions: [], pending_issue_count: 2,
  })
  vi.mocked(fetchStudents).mockResolvedValue([
    { id: 11, student_code: 'S011', name: '学生甲', class_name: '一班', created_at: null },
  ])
  vi.mocked(api.saveScanDecisions).mockResolvedValue({
    revision: 3, decisions: [{ target_type: 'issue', target_id: 'i1', action: 'invalid' }],
    pending_issue_count: 1,
  })
})

describe('scan grading workspace', () => {
  it('shows one four-stage run rail and keeps both grading modes equally available', async () => {
    const { app, host } = await mountView()

    for (const label of ['上传答卷', '扫描预检', '开始批改', '运行与补批']) {
      expect(host.textContent).toContain(label)
    }
    expect(host.querySelector('input[type="file"]')?.hasAttribute('multiple')).toBe(true)
    expect(host.querySelectorAll('[data-grading-mode]')).toHaveLength(2)
    expect(host.textContent).toContain('仍有 2 份异常答卷待处理')
    expect(host.querySelector<HTMLInputElement>('[data-confirm-pending]')?.checked).toBe(false)
    app.unmount()
  })

  it('offers pause and cancel together for a running grading run', async () => {
    const value = workspace()
    value.grading_run = {
      run_id: 19, mode: 'hybrid_batch', state: 'running',
      counts: { graded: 12, grading: 1, pending: 15, skipped: 2, failed: 1, conflict: 0, total: 31 },
      allowed_actions: ['pause', 'cancel'],
    }
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(value)
    const { app, host } = await mountView()

    expect(host.querySelector('[data-action="pause"]')).not.toBeNull()
    expect(host.querySelector('[data-action="cancel"]')).not.toBeNull()
    expect(host.textContent).toContain('已完成 12')
    app.unmount()
  })

  it('submits the public issue id for an invalid decision', async () => {
    const { app, host } = await mountView()

    host.querySelector<HTMLButtonElement>('.scan-issue-list .text-button')!.click()
    await Promise.resolve(); await nextTick()

    expect(api.saveScanDecisions).toHaveBeenCalledWith(7, 2, [
      { target_type: 'issue', target_id: 'i1', action: 'invalid' },
    ])
    app.unmount()
  })

  it('allows a low-confidence automatic group to be rebound', async () => {
    vi.mocked(api.fetchPreflight).mockResolvedValue({
      revision: 2, summary: { auto_matched: 1, issues: 0, absent_candidates: 0, total_pages: 2 },
      groups: [{ id: 'g1', student_name: '学生乙', detected_name: '学生一', source_label: '001',
        match_method: 'fuzzy', match_score: 0.7 }], issues: [], absent_students: [], warnings: [],
      decisions: [], pending_issue_count: 0,
    })
    vi.mocked(api.saveScanDecisions).mockResolvedValue({
      revision: 3, decisions: [{ target_type: 'group', target_id: 'g1', action: 'match', student_id: 11 }],
      pending_issue_count: 0,
    })
    const { app, host } = await mountView()
    const select = host.querySelector<HTMLSelectElement>('select[aria-label="重新选择学生"]')!
    select.value = '11'; select.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    host.querySelector<HTMLButtonElement>('.scan-issue-list button.secondary')!.click()
    await Promise.resolve(); await nextTick()

    expect(api.saveScanDecisions).toHaveBeenCalledWith(7, 2, [
      { target_type: 'group', target_id: 'g1', action: 'match', student_id: 11 },
    ])
    app.unmount()
  })

  it('invalidates pending-issue confirmation when a decision revision changes', async () => {
    const { app, host } = await mountView()
    const confirmation = host.querySelector<HTMLInputElement>('[data-confirm-pending]')!
    confirmation.checked = true
    confirmation.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    expect(confirmation.checked).toBe(true)

    host.querySelector<HTMLButtonElement>('.scan-issue-list .text-button')!.click()
    for (let index = 0; index < 4; index += 1) {
      await Promise.resolve(); await nextTick()
    }

    expect(confirmation.checked).toBe(false)
    app.unmount()
  })

  it('keeps supplementation separate from failed-item retry', async () => {
    const value = workspace()
    value.grading_run = {
      run_id: 19, mode: 'hybrid_batch', state: 'completed',
      counts: { graded: 28, grading: 0, pending: 0, skipped: 2, failed: 1, conflict: 0, total: 31 },
      allowed_actions: ['retry_failed', 'supplement_new_matches'],
    }
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(value)
    vi.mocked(api.supplementGrading).mockResolvedValue({
      id: 92, job_type: 'grading_run', payload: { session_id: 7 }, result: {}, status: 'queued',
      progress: 0, stage: 'queued', detail: '', error: null, cancel_requested: false,
      created_at: '2026-07-17T00:00:00Z', started_at: null,
      updated_at: '2026-07-17T00:00:00Z', finished_at: null,
    })
    const { app, host } = await mountView()

    expect(host.querySelector('[data-action="retry-failed"]')).not.toBeNull()
    const supplement = host.querySelector<HTMLButtonElement>('[data-action="supplement"]')!
    expect(supplement).not.toBeNull()
    supplement.click()
    for (let index = 0; index < 4; index += 1) {
      await Promise.resolve(); await nextTick()
    }

    expect(api.supplementGrading).toHaveBeenCalledWith(7, 19)
    app.unmount()
  })

  it('offers a server-backed preflight retry after restart failure', async () => {
    const value = workspace()
    value.scan_analysis_job = {
      id: 31, status: 'failed', progress: 0.4, updated_at: '2026-07-17T00:00:01Z',
      cancel_requested: true, scan_batch_id: 'batch-1',
    }
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(value)
    vi.mocked(api.fetchPreflight).mockRejectedValue(new Error('not ready'))
    vi.mocked(api.startPreflight).mockResolvedValue({
      id: 32, job_type: 'scan_analysis', payload: { session_id: 7 }, result: {}, status: 'queued',
      progress: 0, stage: 'queued', detail: '', error: null, cancel_requested: false,
      created_at: '2026-07-17T00:00:02Z', started_at: null,
      updated_at: '2026-07-17T00:00:02Z', finished_at: null,
    })
    const { app, host } = await mountView()
    const retry = host.querySelector<HTMLButtonElement>('[data-action="retry-preflight"]')!
    expect(retry).not.toBeNull()

    retry.click()
    for (let index = 0; index < 4; index += 1) {
      await Promise.resolve(); await nextTick()
    }

    expect(api.startPreflight).toHaveBeenCalledWith(7)
    app.unmount()
  })
})
