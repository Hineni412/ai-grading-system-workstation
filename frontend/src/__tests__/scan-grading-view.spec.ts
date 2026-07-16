import { createApp, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../api/scan-grading'
import { createAppRouter } from '../router'
import ScanGradingView from '../views/ScanGradingView.vue'

vi.mock('../api/scan-grading', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/scan-grading')>(),
  fetchGradingWorkspace: vi.fn(), uploadScan: vi.fn(), removeScan: vi.fn(), clearScans: vi.fn(),
  freezeScans: vi.fn(), startPreflight: vi.fn(), fetchPreflight: vi.fn(),
  saveScanDecisions: vi.fn(), startGrading: vi.fn(), controlGrading: vi.fn(), cancelGrading: vi.fn(),
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

async function mountView() {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/sessions/7/grading-run')
  await router.isReady()
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(ScanGradingView)
  app.use(createPinia()); app.use(router); app.mount(host)
  await Promise.resolve(); await nextTick(); await Promise.resolve(); await nextTick()
  return { app, host }
}

beforeEach(() => {
  document.body.innerHTML = ''; localStorage.clear(); vi.clearAllMocks()
  vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(workspace())
  vi.mocked(api.fetchPreflight).mockResolvedValue({
    revision: 2, summary: { auto_matched: 28, issues: 2, absent_candidates: 1, total_pages: 60 },
    groups: [], issues: [{ issue_id: 'i1' }, { issue_id: 'i2' }], absent_students: [], warnings: [],
    decisions: [], pending_issue_count: 2,
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
})
