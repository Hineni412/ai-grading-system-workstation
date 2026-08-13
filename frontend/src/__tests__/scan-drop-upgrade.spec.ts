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

describe('scan drop zone upgrade', () => {
  it('keeps the native multi-file input and adds the upload icon trigger visual', async () => {
    const { app, host } = await mountView()

    expect(host.textContent).toContain('上传答卷')
    const drop = host.querySelector<HTMLElement>('label.scan-drop')!
    expect(drop).not.toBeNull()
    expect(drop.getAttribute('data-disabled')).toBe('false')
    expect(drop.querySelector('.scan-drop__icon svg')).not.toBeNull()
    const input = drop.querySelector<HTMLInputElement>('input[type="file"]')!
    expect(input.hasAttribute('multiple')).toBe(true)
    expect(input.getAttribute('accept')).toContain('application/pdf')
    expect(drop.textContent).toContain('重新上传答卷')
    expect(drop.textContent).toContain('支持 PDF、JPG、PNG')
    expect(host.querySelector('.scan-file-list')).not.toBeNull()

    app.unmount()
  })
})
