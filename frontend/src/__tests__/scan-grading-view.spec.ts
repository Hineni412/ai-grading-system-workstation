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
  mode: 'ai' | 'manual',
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
    expect(host.querySelectorAll('[data-grading-mode]')).toHaveLength(2)
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
    await confirmGradingPlan(host, 'ai')

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

    await confirmGradingPlan(host, 'ai')
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
    expect(host.textContent).toContain('已处理（1）')
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

  it('allows accepted fuzzy matches to proceed without confirmation and still be rebound', async () => {
    vi.mocked(api.fetchPreflight).mockResolvedValue({
      revision: 2, summary: {
        auto_matched: 1, ready_to_grade: 1, issues: 0, absent_candidates: 0, total_pages: 2,
      },
      groups: [{ id: 'g1', student_name: '学生乙', detected_name: '学生一', source_label: '001',
        front_media_url: '/api/synthetic/g1/front', back_media_url: '/api/synthetic/g1/back',
        match_method: 'fuzzy', match_score: 0.7 }], issues: [], absent_students: [], warnings: [],
      decisions: [], pending_issue_count: 0,
    })
    vi.mocked(api.saveScanDecisions).mockResolvedValue({
      revision: 3, decisions: [{ target_type: 'group', target_id: 'g1', action: 'match', student_id: 11 }],
      pending_issue_count: 0, ready_to_grade: 1,
    })
    const { app, host } = await mountView()
    expect(host.textContent).toContain('已自动匹配，可直接批改')
    expect(host.querySelector('.scan-match-conflict')).toBeNull()
    expect(host.querySelector('[data-confirm-pending]')).toBeNull()
    host.querySelector<HTMLButtonElement>('[data-grading-mode="ai"]')!.click()
    await vi.waitFor(() => expect(host.querySelector<HTMLButtonElement>('[data-confirm-grading-plan]')?.disabled).toBe(false))
    expect(api.saveScanDecisions).not.toHaveBeenCalled()
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

  it('submits only selected complete papers once and shows the saved unresolved count', async () => {
    vi.mocked(api.saveScanDecisions).mockImplementation(async (_id, _revision, decisions) => ({
      revision: 3, decisions, pending_issue_count: 1, ready_to_grade: 29,
      summary: { scanned_papers: 30, matched_papers: 29, unique_students: 29, ready_to_grade: 29 },
      absent_students: [], match_conflicts: [],
    }))
    const { app, host } = await mountView()
    const batch = host.querySelector<HTMLButtonElement>('[data-match-selected]')!
    expect(batch.disabled).toBe(true)
    await chooseStudent(host, '选择学生')
    expect(batch.textContent).toContain('1 项')
    batch.click()
    await vi.waitFor(() => expect(host.textContent).toContain('已保存 1 项；仍有 1 份待处理'))
    expect(api.saveScanDecisions).toHaveBeenCalledTimes(1)
    expect(api.saveScanDecisions).toHaveBeenCalledWith(7, 2, [
      { target_type: 'issue', target_id: 'i1', action: 'match', student_id: 11 },
    ], true)
    expect(batch.disabled).toBe(true)
    expect(host.querySelector('[data-scan-reconciliation]')?.textContent).toContain('对应 29 名学生')
    app.unmount()
  })

  it('retains selections and shows no success when a bulk match is rejected', async () => {
    vi.mocked(api.saveScanDecisions).mockRejectedValue(new Error('同一学生被分配了多份答卷，本次匹配未保存'))
    const { app, host } = await mountView()
    await chooseStudent(host, '选择学生')
    host.querySelector<HTMLButtonElement>('[data-match-selected]')!.click()
    await vi.waitFor(() => expect(host.textContent).toContain('本次匹配未保存'))
    expect(host.querySelector('[data-match-result][role="alert"]')?.textContent).toContain('同一学生被分配了多份答卷')
    expect(host.querySelector('[data-match-result]')?.textContent).toContain('当前选择已保留')
    expect(host.querySelector('[data-match-selected]')?.textContent).toContain('1 项')
    expect(host.textContent).not.toContain('已保存 1 项；')
    app.unmount()
  })

  it('confirms 32 selected papers in one request and disables every confirmed row', async () => {
    const students = Array.from({ length: 32 }, (_, i) => ({ id: i + 1, name: `合成学生${i + 1}`,
      student_code: `S${String(i + 1).padStart(3, '0')}`, class_name: '合成班', pinyin_initials: '', pinyin_full: '' }))
    vi.mocked(api.fetchScanStudentOptions).mockResolvedValue(students)
    vi.mocked(api.fetchPreflight).mockResolvedValue({ revision: 0,
      summary: { auto_matched: 0, ready_to_grade: 0, issues: 32, absent_candidates: 32, total_pages: 64 },
      groups: [], issues: students.map((student) => ({ id: `i${student.id}`, detected_name: student.name,
        front_media_url: `/api/synthetic/${student.id}/front`, back_media_url: `/api/synthetic/${student.id}/back` })),
      absent_students: [], warnings: [], decisions: [], pending_issue_count: 32, match_conflicts: [] })
    vi.mocked(api.saveScanDecisions).mockImplementation(async (_id, _revision, decisions) => ({
      revision: 1, decisions, pending_issue_count: 0, ready_to_grade: 32,
      summary: { auto_matched: 0, ready_to_grade: 32, issues: 32, absent_candidates: 0, total_pages: 64 },
      absent_students: [], match_conflicts: [],
    }))
    const { app, host } = await mountView()
    const inputs = [...host.querySelectorAll<HTMLInputElement>('input[aria-label="选择学生"]')]
    for (const [index, input] of inputs.entries()) {
      input.dispatchEvent(new FocusEvent('focus'))
      input.value = students[index]!.student_code
      input.dispatchEvent(new Event('input', { bubbles: true }))
      await nextTick()
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
      await nextTick()
    }
    const batch = host.querySelector<HTMLButtonElement>('[data-match-selected]')!
    expect(batch.textContent).toContain('32 项')
    batch.click()
    await vi.waitFor(() => expect(host.querySelector('[data-match-result]')?.textContent).toContain('已保存 32 项；仍有 0 份待处理'))
    expect(api.saveScanDecisions).toHaveBeenCalledTimes(1)
    expect(vi.mocked(api.saveScanDecisions).mock.calls[0]![2]).toHaveLength(32)
    expect(host.querySelectorAll('[data-saved-decision]')).toHaveLength(32)
    expect(batch.disabled).toBe(true)
    expect([...host.querySelectorAll<HTMLButtonElement>('.scan-issue-controls__actions button.secondary')].every((button) => button.disabled)).toBe(true)
    app.unmount()
  })

  it('saves the valid selection and reveals the hidden exact match that blocks the other selection', async () => {
    const students = [1, 2].map((id) => ({ id, name: `合成学生${id}`, student_code: `S${id}`, class_name: '合成班', pinyin_initials: '', pinyin_full: '' }))
    vi.mocked(api.fetchScanStudentOptions).mockResolvedValue(students)
    vi.mocked(api.fetchPreflight).mockResolvedValue({ revision: 0,
      summary: { auto_matched: 1, ready_to_grade: 1, issues: 2, absent_candidates: 1, total_pages: 6 },
      groups: [{ id: 'exact-1', student_id: 1, student_name: '合成学生1', source_label: '原已匹配卷 第2-1页',
        match_method: 'exact', match_score: 1, front_media_url: '/api/synthetic/exact/front', back_media_url: '/api/synthetic/exact/back' }],
      issues: ['good', 'bad'].map((id) => ({ id, detected_name: id, front_media_url: `/api/synthetic/${id}/front`, back_media_url: `/api/synthetic/${id}/back` })),
      absent_students: [], warnings: [], decisions: [], pending_issue_count: 2, match_conflicts: [] })
    const good: api.ScanDecision = { target_type: 'issue', target_id: 'good', action: 'match', student_id: 2 }
    vi.mocked(api.saveScanDecisions).mockResolvedValue({ revision: 1, decisions: [good], pending_issue_count: 1, ready_to_grade: 2,
      match_conflicts: [], rejected_conflicts: [{ code: 'scan_student_multiple_papers', student_id: 1, message: '同一学生对应 2 份答卷，请更正归属或将重复扫描标为无效。',
        targets: [{ target_type: 'group', target_id: 'exact-1' }, { target_type: 'issue', target_id: 'bad' }] }] })
    const { app, host } = await mountView()
    expect(host.textContent).not.toContain('原已匹配卷 第2-1页')
    // Rows are sorted by detected name now, so pick each row's input by its text.
    for (const [detected, code] of [['good', 'S2'], ['bad', 'S1']] as const) {
      const row = [...host.querySelectorAll<HTMLElement>('.scan-issue-row')]
        .find((candidate) => candidate.textContent?.includes(detected))!
      const input = row.querySelector<HTMLInputElement>('input[aria-label="选择学生"]')!
      input.dispatchEvent(new FocusEvent('focus'))
      input.value = code
      input.dispatchEvent(new Event('input', { bubbles: true }))
      await nextTick()
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
      await nextTick()
    }
    host.querySelector<HTMLButtonElement>('[data-match-selected]')!.click()
    await vi.waitFor(() => expect(host.querySelector('[data-match-result]')?.textContent).toContain('已保存 1 项；1 项因重复归属未保存'))
    expect(host.textContent).toContain('原已匹配卷 第2-1页')
    expect(host.querySelector('img[src="/api/synthetic/exact/front"]')).not.toBeNull()
    expect(host.querySelectorAll('.scan-match-conflict')).toHaveLength(3)
    expect(host.textContent).toContain('合成学生1 · S1 · 合成班')
    expect(host.querySelector('[data-saved-decision="issue:good"]')).not.toBeNull()
    expect(host.querySelector('[data-saved-decision="issue:bad"]')).toBeNull()
    expect(host.querySelector('[data-match-selected]')?.textContent).toContain('1 项')
    app.unmount()
  })

  it('exposes exact-name duplicate papers and prevents starting despite pending confirmation', async () => {
    const fixture = await api.fetchPreflight(7)
    fixture.groups = [1, 2].map((id) => ({ id: `g${id}`, student_id: 11, student_name: '学生甲',
      source_label: `合成卷${id}`, match_method: 'exact', match_score: 1,
      front_media_url: `/api/synthetic/${id}/front`, back_media_url: `/api/synthetic/${id}/back` }))
    fixture.match_conflicts = [{ code: 'scan_student_multiple_papers', message: '同一学生对应 2 份答卷', student_id: 11,
      targets: [{ target_type: 'group', target_id: 'g1' }, { target_type: 'group', target_id: 'g2' }] }]
    const { app, host } = await mountView()
    expect(host.textContent).toContain('合成卷1')
    expect(host.textContent).toContain('合成卷2')
    const confirmation = host.querySelector<HTMLInputElement>('[data-confirm-pending]')!
    confirmation.checked = true
    confirmation.dispatchEvent(new Event('change', { bubbles: true }))
    host.querySelector<HTMLButtonElement>('[data-grading-mode="ai"]')!.click()
    await vi.waitFor(() => expect(host.querySelector<HTMLButtonElement>('[data-confirm-grading-plan]')?.disabled).toBe(true))
    expect(api.startGrading).not.toHaveBeenCalled()
    app.unmount()
  })

  it('marks already-assigned students and offers an inline takeover that frees the old paper', async () => {
    vi.mocked(api.fetchScanStudentOptions).mockResolvedValue([
      { id: 11, student_code: 'S011', name: '学生甲', class_name: '一班', pinyin_initials: 'xsj', pinyin_full: '' },
      { id: 12, student_code: 'S012', name: '学生乙', class_name: '一班', pinyin_initials: 'xsy', pinyin_full: '' },
    ])
    vi.mocked(api.fetchPreflight).mockResolvedValue({ revision: 0,
      summary: { auto_matched: 1, ready_to_grade: 1, issues: 1, absent_candidates: 0, total_pages: 4 },
      groups: [{ id: 'g1', student_id: 11, student_name: '学生甲', source_label: '第3-4页',
        match_method: 'exact', match_score: 1, front_media_url: '/api/g1/front', back_media_url: '/api/g1/back' }],
      issues: [{ id: 'i1', detected_name: '学生甲', source_label: '005',
        front_media_url: '/api/i1/front', back_media_url: '/api/i1/back' }],
      absent_students: [], warnings: [], decisions: [], pending_issue_count: 1, match_conflicts: [] })
    vi.mocked(api.saveScanDecisions).mockResolvedValue({
      revision: 1,
      decisions: [
        { target_type: 'issue', target_id: 'i1', action: 'match', student_id: 11 },
        { target_type: 'group', target_id: 'g1', action: 'pending' },
      ],
      pending_issue_count: 1, ready_to_grade: 2,
    })
    const { app, host } = await mountView()

    // The exact-matched group is not listed in the review rows, but still owns the student.
    expect(host.textContent).not.toContain('第3-4页')
    const input = host.querySelector<HTMLInputElement>('input[aria-label="选择学生"]')!
    input.dispatchEvent(new FocusEvent('focus'))
    await nextTick()
    const options = [...host.querySelectorAll<HTMLElement>('[role="option"]')]
    expect(options).toHaveLength(2)
    expect(options[1]!.textContent).toContain('学生甲')
    expect(options[1]!.textContent).toContain('已归属：第3-4页')

    input.value = 'xsj'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    await nextTick()

    const panel = host.querySelector<HTMLElement>('[data-transfer-panel]')!
    expect(panel.textContent).toContain('已归属「第3-4页」')
    // The plain match button is hidden while a takeover is pending.
    expect(panel.closest('.scan-issue-row')!
      .querySelector('.scan-issue-controls__actions button.secondary')).toBeNull()

    ;[...panel.querySelectorAll('button')]
      .find((button) => button.textContent?.includes('改用当前卷'))!.click()
    await vi.waitFor(() => expect(api.saveScanDecisions).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.saveScanDecisions).mock.calls[0]![2]).toEqual([
      { target_type: 'issue', target_id: 'i1', action: 'match', student_id: 11 },
      { target_type: 'group', target_id: 'g1', action: 'pending' },
    ])
    app.unmount()
  })

  it('filters the review list down to unresolved items only', async () => {
    const fixture = await api.fetchPreflight(7)
    fixture.decisions = [{ target_type: 'issue', target_id: 'i1', action: 'invalid' }]
    vi.mocked(api.fetchPreflight).mockResolvedValue(fixture)
    const { app, host } = await mountView()

    expect(host.textContent).toContain('已处理（1）')
    expect(host.textContent).toContain('待匹配（1）')

    const filter = host.querySelector<HTMLInputElement>('[data-filter-pending]')!
    filter.checked = true
    filter.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()

    expect(host.textContent).not.toContain('已处理（')
    expect(host.querySelector('[data-bucket="done"]')).toBeNull()
    expect(host.querySelector('[data-bucket="unmatched"]')).not.toBeNull()
    expect(host.textContent).toContain('待核对乙')
    app.unmount()
  })

  it('groups conflicted papers in one card and returns the unkept one to pending', async () => {
    const fixture = await api.fetchPreflight(7)
    fixture.groups = [1, 2].map((id) => ({ id: `g${id}`, student_id: 11, student_name: '学生甲',
      source_label: `合成卷${id}`, match_method: 'exact', match_score: 1,
      front_media_url: `/api/synthetic/${id}/front`, back_media_url: `/api/synthetic/${id}/back` }))
    fixture.match_conflicts = [{ code: 'scan_student_multiple_papers', message: '同一学生对应 2 份答卷', student_id: 11,
      targets: [{ target_type: 'group', target_id: 'g1' }, { target_type: 'group', target_id: 'g2' }] }]
    vi.mocked(api.saveScanDecisions).mockResolvedValue({
      revision: 3,
      decisions: [{ target_type: 'group', target_id: 'g2', action: 'pending' }],
      pending_issue_count: 3, ready_to_grade: 28, match_conflicts: [],
    })
    const { app, host } = await mountView()

    const card = host.querySelector<HTMLElement>('.scan-conflict-card')!
    expect(card.textContent).toContain('学生甲')
    expect(card.textContent).toContain('合成卷1')
    expect(card.textContent).toContain('合成卷2')
    expect(host.textContent).toContain('归属冲突（2）')
    expect(host.textContent).toContain('精确匹配')

    const keepButtons = [...card.querySelectorAll<HTMLButtonElement>('.scan-conflict-card__target')]
    const first = keepButtons.find((target) => target.textContent?.includes('合成卷1'))!
    ;[...first.querySelectorAll('button')].find((button) => button.textContent === '保留这份')!.click()
    await vi.waitFor(() => expect(api.saveScanDecisions).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.saveScanDecisions).mock.calls[0]![2]).toEqual([
      { target_type: 'group', target_id: 'g2', action: 'pending' },
    ])
    app.unmount()
  })
})
