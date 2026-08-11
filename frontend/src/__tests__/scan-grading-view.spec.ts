import { createApp, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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

async function chooseStudent(host: HTMLElement, ariaLabel: string): Promise<void> {
  const input = host.querySelector<HTMLInputElement>(`input[aria-label="${ariaLabel}"]`)!
  input.dispatchEvent(new FocusEvent('focus'))
  input.value = 'xsj'
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
  input.dispatchEvent(new KeyboardEvent('keydown', {
    key: 'Enter',
    bubbles: true,
    cancelable: true,
  }))
  await nextTick()
}

async function confirmGradingPlan(
  host: HTMLElement,
  mode: 'full_paper' | 'hybrid_batch',
): Promise<void> {
  host.querySelector<HTMLButtonElement>(`[data-grading-mode="${mode}"]`)!.click()
  await vi.waitFor(() => {
    expect(host.querySelector<HTMLButtonElement>('[data-confirm-grading-plan]')?.disabled)
      .toBe(false)
  })
  host.querySelector<HTMLButtonElement>('[data-confirm-grading-plan]')!.click()
  await nextTick()
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
      auto_matched: 28, ready_to_grade: 28, issues: 2, absent_candidates: 1, total_pages: 60,
    },
    groups: [], issues: [
      { id: 'i1', detected_name: '待核对甲', source_label: '001',
        front_media_url: '/api/sessions/7/scan/preflight/media/issue:i1:front',
        back_media_url: '/api/sessions/7/scan/preflight/media/issue:i1:back' },
      { id: 'i2', detected_name: '待核对乙', source_label: '002',
        front_media_url: '/api/sessions/7/scan/preflight/media/issue:i2:front',
        back_media_url: null },
    ], absent_students: [], warnings: [],
    decisions: [], pending_issue_count: 2,
  })
  vi.mocked(api.fetchScanStudentOptions).mockResolvedValue([
    {
      id: 11,
      student_code: 'S011',
      name: '学生甲',
      class_name: '一班',
      pinyin_initials: 'xsj',
      pinyin_full: 'xueshengjia',
    },
  ])
  vi.mocked(api.fetchGradingPlan).mockImplementation(async (_sessionId, mode) => ({
    mode,
    status: 'ready',
    counts: { ready_to_grade: 28, pending_items: 28, teacher_final_items: 0 },
    requests: { full_paper: 28, objective_sheet: 28, subjective_batches: 10 },
    batching: { subjective_group_min: 2, subjective_group_max: 3 },
    warnings: [],
    blockers: [],
  }))
  vi.mocked(api.saveScanDecisions).mockResolvedValue({
    revision: 3, decisions: [{ target_type: 'issue', target_id: 'i1', action: 'invalid' }],
    pending_issue_count: 1, ready_to_grade: 28,
  })
})

describe('scan grading workspace', () => {
  it('shows one five-stage run rail and keeps all grading modes equally available', async () => {
    const { app, host } = await mountView()

    for (const label of ['上传答卷', '扫描预检', '开始批改', '运行与补批']) {
      expect(host.textContent).toContain(label)
    }
    expect(host.querySelector('input[type="file"]')?.hasAttribute('multiple')).toBe(true)
    expect(host.querySelectorAll('[data-grading-mode]')).toHaveLength(3)
    expect(host.textContent).toContain('仍有 2 份异常答卷待处理')
    expect(host.querySelector<HTMLInputElement>('[data-confirm-pending]')?.checked).toBe(false)
    app.unmount()
  })

  it('acknowledges a submitted grading job immediately without requiring a reload', async () => {
    let resolveStart!: (job: JobResponse) => void
    vi.mocked(api.startGrading).mockReturnValue(new Promise((resolve) => {
      resolveStart = resolve
    }))
    const { app, host } = await mountView()
    const confirmation = host.querySelector<HTMLInputElement>('[data-confirm-pending]')!
    confirmation.checked = true
    confirmation.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    await confirmGradingPlan(host, 'full_paper')

    expect(host.querySelector('[data-grading-submit-status]')?.textContent).toContain(
      '批改任务已提交，正在后台启动',
    )
    expect(host.querySelector('[data-grading-starting]')).not.toBeNull()
    expect([...host.querySelectorAll<HTMLButtonElement>('[data-grading-mode]')]
      .every((button) => button.disabled)).toBe(true)
    host.querySelector<HTMLButtonElement>('[data-confirm-grading-plan]')?.click()
    expect(api.startGrading).toHaveBeenCalledTimes(1)
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalledTimes(1)
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalledWith({ block: 'start' })

    resolveStart({
      id: 91, job_type: 'grading_run', payload: { session_id: 7 }, result: {}, status: 'queued',
      progress: 0, stage: 'queued', detail: '', error: null, cancel_requested: false,
      created_at: '2026-07-17T00:00:00Z', started_at: null,
      updated_at: '2026-07-17T00:00:00Z', finished_at: null,
    })
    const activeWorkspace = workspace()
    activeWorkspace.grading_job = {
      id: 91, status: 'queued', progress: 0, updated_at: '2026-07-17T00:00:00Z',
      cancel_requested: false, scan_batch_id: 'batch-1',
    }
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(activeWorkspace)
    for (let index = 0; index < 8; index += 1) {
      await Promise.resolve(); await nextTick()
    }

    expect(host.querySelector('[data-grading-starting]')).not.toBeNull()
    expect(api.startGrading).toHaveBeenCalledTimes(1)
    app.unmount()

    localStorage.clear()
    const reopened = await mountView()
    expect(reopened.host.querySelector('[data-grading-starting]')).not.toBeNull()
    expect([...reopened.host.querySelectorAll<HTMLButtonElement>('[data-grading-mode]')]
      .every((button) => button.disabled)).toBe(true)
    reopened.app.unmount()
  })

  it('releases the start lock when a job fails before creating its run ledger', async () => {
    const failedWorkspace = workspace()
    failedWorkspace.grading_job = {
      id: 92, status: 'failed', progress: 0, updated_at: '2026-07-17T00:00:02Z',
      cancel_requested: false, scan_batch_id: 'batch-1',
    }
    vi.mocked(api.fetchGradingWorkspace)
      .mockResolvedValueOnce(workspace())
      .mockResolvedValue(failedWorkspace)
    vi.mocked(api.startGrading).mockResolvedValue({
      id: 92, job_type: 'grading_run', payload: { session_id: 7 }, result: {}, status: 'queued',
      progress: 0, stage: 'queued', detail: '', error: null, cancel_requested: false,
      created_at: '2026-07-17T00:00:00Z', started_at: null,
      updated_at: '2026-07-17T00:00:00Z', finished_at: null,
    })
    const { app, host } = await mountView()
    const confirmation = host.querySelector<HTMLInputElement>('[data-confirm-pending]')!
    confirmation.checked = true
    confirmation.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()

    await confirmGradingPlan(host, 'full_paper')
    for (let index = 0; index < 10; index += 1) {
      await Promise.resolve(); await nextTick()
    }

    expect(host.querySelector('[data-grading-starting]')).toBeNull()
    expect(host.textContent).toContain('批改任务未能建立运行记录，可以重新提交')
    const retryConfirmation = host.querySelector<HTMLInputElement>('[data-confirm-pending]')!
    retryConfirmation.checked = true
    retryConfirmation.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    expect([...host.querySelectorAll<HTMLButtonElement>('[data-grading-mode]')]
      .every((button) => !button.disabled)).toBe(true)
    app.unmount()
  })

  it('keeps a succeeded job without a run ledger non-retryable and points to saved results', async () => {
    const succeededWorkspace = workspace()
    succeededWorkspace.grading_job = {
      id: 93, status: 'succeeded', progress: 1, updated_at: '2026-07-17T00:00:03Z',
      cancel_requested: false, scan_batch_id: 'batch-1',
    }
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(succeededWorkspace)

    const { app, host } = await mountView()
    const confirmation = host.querySelector<HTMLInputElement>('[data-confirm-pending]')!
    confirmation.checked = true
    confirmation.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()

    expect(host.textContent).toContain('批改处理已结束，但本次运行进度记录没有生成')
    expect(host.textContent).not.toContain('批改任务未能建立运行记录，可以重新提交')
    expect([...host.querySelectorAll<HTMLButtonElement>('[data-grading-mode]')]
      .every((button) => button.disabled)).toBe(true)
    expect(host.querySelector('[data-open-grading-results]')).not.toBeNull()
    expect(host.querySelector('#run-title')?.closest('section')?.textContent)
      .not.toContain('进入人工干预')
    expect(api.startGrading).not.toHaveBeenCalled()
    app.unmount()
  })

  it('does not let a completed job from an older scan batch lock the replacement batch', async () => {
    const replacementWorkspace = workspace()
    replacementWorkspace.upload_batch.batch_id = 'batch-2'
    replacementWorkspace.grading_job = {
      id: 93, status: 'succeeded', progress: 1, updated_at: '2026-07-17T00:00:03Z',
      cancel_requested: false, scan_batch_id: 'batch-1',
    }
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(replacementWorkspace)

    const { app, host } = await mountView()

    expect(host.textContent).not.toContain('批改处理已结束，但本次运行进度记录没有生成')
    expect([...host.querySelectorAll<HTMLButtonElement>('[data-grading-mode]')]
      .every((button) => !button.disabled)).toBe(true)
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
    expect(host.querySelector('#run-title')?.closest('section')?.textContent)
      .not.toContain('进入人工干预')
    expect(host.querySelector('#intervention-title')?.closest('section')?.textContent)
      .toContain('进入人工干预工作台')
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

  it('shows controlled front and back evidence plus saved-decision status', async () => {
    const { app, host } = await mountView()

    const evidence = [...host.querySelectorAll<HTMLImageElement>('.scan-evidence img')]
    expect(evidence.map((image) => image.getAttribute('src'))).toEqual([
      '/api/sessions/7/scan/preflight/media/issue:i1:front',
      '/api/sessions/7/scan/preflight/media/issue:i1:back',
      '/api/sessions/7/scan/preflight/media/issue:i2:front',
    ])

    host.querySelector<HTMLButtonElement>('.scan-issue-list .text-button')!.click()
    for (let index = 0; index < 4; index += 1) {
      await Promise.resolve(); await nextTick()
    }

    expect(host.querySelector('[data-saved-decision="issue:i1"]')?.textContent).toContain('已保存：标记无效')
    expect(host.textContent).toContain('已确认 1 项')
    app.unmount()
  })

  it('updates the true gradable count after an issue is matched', async () => {
    vi.mocked(api.saveScanDecisions).mockResolvedValue({
      revision: 3,
      decisions: [{ target_type: 'issue', target_id: 'i1', action: 'match', student_id: 11 }],
      pending_issue_count: 1,
      ready_to_grade: 29,
    })
    const { app, host } = await mountView()
    await chooseStudent(host, '选择学生')
    host.querySelector<HTMLButtonElement>('.scan-issue-list button.secondary')!.click()
    for (let index = 0; index < 4; index += 1) {
      await Promise.resolve(); await nextTick()
    }

    expect(host.textContent).toContain('本轮可批改 29 份')
    app.unmount()
  })

  it('allows a low-confidence automatic group to be rebound', async () => {
    vi.mocked(api.fetchPreflight).mockResolvedValue({
      revision: 2, summary: {
        auto_matched: 1, ready_to_grade: 1, issues: 0, absent_candidates: 0, total_pages: 2,
      },
      groups: [{ id: 'g1', student_name: '学生乙', detected_name: '学生一', source_label: '001',
        match_method: 'fuzzy', match_score: 0.7 }], issues: [], absent_students: [], warnings: [],
      decisions: [], pending_issue_count: 0,
    })
    vi.mocked(api.saveScanDecisions).mockResolvedValue({
      revision: 3, decisions: [{ target_type: 'group', target_id: 'g1', action: 'match', student_id: 11 }],
      pending_issue_count: 0, ready_to_grade: 1,
    })
    const { app, host } = await mountView()
    await chooseStudent(host, '重新选择学生')
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

  it('warns when a finished run left questions without AI results', async () => {
    const value = workspace()
    value.grading_run = {
      run_id: 21, mode: 'hybrid_batch', state: 'completed',
      counts: { graded: 4, grading: 0, pending: 0, skipped: 0, failed: 0, conflict: 0, total: 4 },
      allowed_actions: ['retry_failed', 'supplement_new_matches'],
      incomplete_result_count: 4,
      incomplete_item_count: 10,
    }
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(value)
    const { app, host } = await mountView()

    const warning = host.querySelector('[data-incomplete-warning]')
    expect(warning).not.toBeNull()
    expect(warning!.textContent).toContain('10 个小题没有 AI 评分结果')
    expect(host.querySelector('[data-action="retry-failed"]')).not.toBeNull()
    app.unmount()
  })

  it('refreshes only the workspace snapshot after failed-item retry', async () => {
    const value = workspace()
    value.grading_run = {
      run_id: 21, mode: 'hybrid_batch', state: 'completed',
      counts: { graded: 4, grading: 0, pending: 0, skipped: 0, failed: 0, conflict: 0, total: 4 },
      allowed_actions: ['retry_failed'],
      incomplete_result_count: 4,
      incomplete_item_count: 10,
    }
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(value)
    vi.mocked(api.controlGrading).mockResolvedValue({
      id: 92, job_type: 'grading_run', payload: { session_id: 7 }, result: {}, status: 'queued',
      progress: 0, stage: 'queued', detail: '', error: null, cancel_requested: false,
      created_at: '2026-07-17T00:00:00Z', started_at: null,
      updated_at: '2026-07-17T00:00:00Z', finished_at: null,
    })
    const { app, host } = await mountView()

    host.querySelector<HTMLButtonElement>('[data-action="retry-failed"]')!.click()
    for (let index = 0; index < 6; index += 1) {
      await Promise.resolve(); await nextTick()
    }

    expect(api.controlGrading).toHaveBeenCalledWith(7, 21, 'retry-failed')
    // 局部刷新：不触发整页 load（学生选项只在进入页面时拉取一次）
    expect(api.fetchScanStudentOptions).toHaveBeenCalledTimes(1)
    // 局部刷新：不进入整页加载态，运行面板保持挂载
    expect(host.textContent).not.toContain('正在恢复本次批改工作区')
    expect(host.querySelector('[data-incomplete-warning]')).not.toBeNull()
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
