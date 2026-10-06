import { createApp, nextTick, type App } from 'vue';
import { createPinia, disposePinia, type Pinia } from 'pinia';
import { createMemoryHistory } from 'vue-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../api/scan-grading'
import { fetchResultsCenter } from '../api/results-center'
import type { ResultsCenterResponse, ResultsCenterStudent } from '../api/results-center'
import { fetchReviewQuestions } from '../api/review'

import { createAppRouter } from '../router';
import ScanGradingView from '../views/ScanGradingView.vue'
import { useScanGradingStore } from '../stores/scan-grading'

vi.mock('../api/scan-grading', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/scan-grading')>(),
  fetchGradingWorkspace: vi.fn(), uploadScan: vi.fn(), removeScan: vi.fn(), clearScans: vi.fn(),
  freezeScans: vi.fn(), startPreflight: vi.fn(), fetchPreflight: vi.fn(),
  fetchScanStudentOptions: vi.fn(), fetchGradingPlan: vi.fn(),
  saveScanDecisions: vi.fn(), startGrading: vi.fn(), controlGrading: vi.fn(), cancelGrading: vi.fn(),
  supplementGrading: vi.fn(), startNewScanBatch: vi.fn(),
}))
vi.mock('../api/review', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/review')>(),
  fetchReviewQuestions: vi.fn(),
}))
vi.mock('../api/results-center', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/results-center')>(),
  fetchResultsCenter: vi.fn(),
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

const mountedViews: { app: App; pinia: Pinia; host: HTMLElement }[] = []

afterEach(() => {
  for (const { app, pinia, host } of mountedViews.splice(0)) {
    app.unmount()
    disposePinia(pinia)
    host.remove()
  }
})

async function mountView() {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/sessions/7/grading-run')
  await router.isReady()
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(ScanGradingView)
  const pinia = createPinia()
  mountedViews.push({ app, pinia, host })
  app.use(pinia); app.use(router); app.mount(host)
  await vi.waitFor(() => {
    expect(useScanGradingStore(pinia).loadState).toBe('ready')
    expect(host.querySelector('.scan-stage')).not.toBeNull()
  })
  return { host, router }
}

async function openStage(host: HTMLElement, stepId: string): Promise<void> {
  host.querySelector<HTMLButtonElement>(`[data-step-id="${stepId}"]`)!.click()
  await vi.waitFor(() => {
    expect(host.querySelector('.scan-stage')?.getAttribute('aria-labelledby'))
      .toBe(`${stepId}-title`)
  })
}

async function selectReviewRow(host: HTMLElement, key: string): Promise<void> {
  const item = [...host.querySelectorAll<HTMLElement>('.scan-review-item')]
    .find((el) => el.getAttribute('data-row-key') === key)
  expect(item, `review row ${key}`).toBeTruthy()
  item!.click()
  await nextTick()
}

async function pickStudent(host: HTMLElement, code: string): Promise<void> {
  const input = host.querySelector<HTMLInputElement>('.scan-review-detail input[aria-label="选择学生"]')!
  input.dispatchEvent(new FocusEvent('focus'))
  input.value = code
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
  await nextTick()
}

function resultsStudent(id: number, score: number): ResultsCenterStudent {
  return {
    student_id: id, student_code: `S${id}`, student_name: `学生${id}`, class_name: '一班',
    pinyin_initials: '', pinyin_full: '', current_score: score, max_score: 100,
    ungraded_count: 0, failed_count: 0, needs_review_count: 0, status: 'complete', items: [],
  }
}

function resultsCenter(): ResultsCenterResponse {
  return {
    session_id: 7, session_name: '合成考试',
    summary: {
      student_count: 3, complete_student_count: 3, average_sample_count: 3,
      average_score: 51.7, highest_score: 100, lowest_score: 0, max_score: 10,
      ungraded_item_count: 5, failed_item_count: 3, needs_review_item_count: 4,
      ai_ready_item_count: 43, teacher_final_item_count: 5,
    },
    questions: [
      { question_id: 'Q1', max_score: 4, total_count: 30, ungraded_count: 0, failed_count: 0,
        needs_review_count: 0, ai_ready_count: 28, teacher_final_count: 2, average_score: 3.6 },
      { question_id: 'Q17', max_score: 6, total_count: 30, ungraded_count: 5, failed_count: 3,
        needs_review_count: 4, ai_ready_count: 18, teacher_final_count: 0, average_score: 2.1 },
    ],
    students: [resultsStudent(1, 0), resultsStudent(2, 55), resultsStudent(3, 100)],
  }
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
  document.body.innerHTML = ''; localStorage.clear(); vi.resetAllMocks()
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
  vi.mocked(fetchReviewQuestions).mockResolvedValue([])
  vi.mocked(fetchResultsCenter).mockResolvedValue(resultsCenter())
})

describe('scan grading workspace', () => {

  it('opens the panel that matches real progress and switches panels from the rail', async () => {
    const { host } = await mountView()
    expect(host.querySelector('[data-step-id="prepare"]')?.getAttribute('aria-current')).toBe('step')
    expect(host.querySelector('.scan-review-nav')).not.toBeNull()
    await openStage(host, 'grade')
    expect(host.querySelector('[data-step-id="grade"]')?.getAttribute('aria-current')).toBe('step')
    expect(host.querySelector('[data-confirm-pending]')).not.toBeNull()
    expect(host.querySelector('.scan-review-nav')).toBeNull()

    const done = workspace()
    done.grading_run = {
      run_id: 5, mode: 'hybrid_batch', state: 'completed',
      counts: { graded: 28, grading: 0, pending: 0, skipped: 0, failed: 0, conflict: 0, total: 28 },
      allowed_actions: [],
    }
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(done)
    const second = await mountView()
    expect(second.host.querySelector('[data-step-id="review"]')?.getAttribute('aria-current')).toBe('step')
    expect(second.host.textContent).toContain('复核')

    done.replacement_batch = {
      ...done.upload_batch, batch_id: 'replacement-1', state: 'draft', frozen_at: null,
    }
    const replacing = await mountView()
    expect(replacing.host.querySelector('[data-step-id="prepare"]')?.getAttribute('aria-current')).toBe('step')
    expect(replacing.host.textContent).toContain('旧答卷和已有成果目前仍然保留')
    expect(replacing.host.querySelector('.scan-stage')?.getAttribute('aria-labelledby')).toBe('prepare-title')
  })

  it('shows a finished run as finished instead of an in-progress placeholder', async () => {
    const done = workspace()
    done.grading_run = {
      run_id: 5, mode: 'hybrid_batch', state: 'completed',
      counts: { graded: 28, grading: 0, pending: 0, skipped: 0, failed: 0, conflict: 0, total: 28 },
      allowed_actions: [],
    }
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(done)
    const { host } = await mountView()
    await openStage(host, 'grade')
    expect(host.querySelector('.run-console')).not.toBeNull()
    expect(host.textContent).not.toContain('正在处理本次批改任务')
    expect(host.textContent).toContain('本次批改已完成')
    const phases = host.querySelectorAll('.hybrid-run-board__phases li')
    expect(phases).toHaveLength(4)
    for (const phase of phases) {
      expect(phase.classList.contains('is-done')).toBe(true)
      expect(phase.classList.contains('is-current')).toBe(false)
    }
  })

  it('moves the detail selection to the next row after the handled row leaves the filter', async () => {
    const { host } = await mountView()
    expect(host.querySelector('[data-review-filter="todo"]')?.textContent).toContain('2')
    expect(host.querySelector('.scan-review-detail')?.textContent).toContain('待核对甲')

    const invalid = [...host.querySelectorAll<HTMLButtonElement>('.scan-review-detail__controls button')]
      .find((button) => button.textContent?.includes('标记无效'))!
    invalid.click()
    await vi.waitFor(() => expect(api.saveScanDecisions).toHaveBeenCalledTimes(1))
    for (let index = 0; index < 4; index += 1) {
      await Promise.resolve(); await nextTick()
    }

    expect(host.querySelector('[data-review-filter="todo"]')?.textContent).toContain('1')
    const items = host.querySelectorAll('.scan-review-item')
    expect(items).toHaveLength(1)
    expect(items[0]?.getAttribute('aria-current')).toBe('true')
    expect(host.querySelector('.scan-review-detail')?.textContent).toContain('待核对乙')
  })

  it('keeps an unsaved student pick when switching rows and counts it for batch match', async () => {
    const { host } = await mountView()
    await pickStudent(host, 'xsj')
    await selectReviewRow(host, 'issue:i2')
    expect(host.querySelector('.scan-review-detail')?.textContent).toContain('待核对乙')
    const first = host.querySelector('.scan-review-item[data-row-key="issue:i1"]')!
    expect(first.textContent).toContain('已选：学生甲')
    expect(host.querySelector('[data-match-selected]')?.textContent).toContain('1 项')
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
    const { host } = await mountView()
    await openStage(host, 'grade')
    const confirmation = host.querySelector<HTMLInputElement>('[data-confirm-pending]')!
    confirmation.checked = true
    confirmation.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()

    await confirmGradingPlan(host, 'ai')
    for (let index = 0; index < 10; index += 1) {
      await Promise.resolve(); await nextTick()
    }

    await vi.waitFor(() => expect(host.querySelector('[data-grading-starting]')).toBeNull())
    expect(host.textContent).toContain('批改任务未能建立运行记录，可以重新提交')
    await openStage(host, 'grade')
    const retryConfirmation = host.querySelector<HTMLInputElement>('[data-confirm-pending]')!
    retryConfirmation.checked = true
    retryConfirmation.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    expect([...host.querySelectorAll<HTMLButtonElement>('[data-grading-mode]')]
      .every((button) => !button.disabled)).toBe(true)
  })

  it('invalidates pending-issue confirmation when a decision revision changes', async () => {
    const { host } = await mountView()
    await openStage(host, 'grade')
    const confirmation = host.querySelector<HTMLInputElement>('[data-confirm-pending]')!
    confirmation.checked = true
    confirmation.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    expect(confirmation.checked).toBe(true)

    await openStage(host, 'prepare')
    const pending = [...host.querySelectorAll<HTMLButtonElement>('.scan-review-detail__controls button')]
      .find((button) => button.textContent?.includes('稍后处理'))!
    pending.click()
    for (let index = 0; index < 4; index += 1) {
      await Promise.resolve(); await nextTick()
    }

    await openStage(host, 'grade')
    const again = host.querySelector<HTMLInputElement>('[data-confirm-pending]')!
    expect(again.checked).toBe(false)
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
    const { host } = await mountView()
    await openStage(host, 'grade')

    expect(host.querySelector('[data-action="retry-failed"]')).not.toBeNull()
    const supplement = host.querySelector<HTMLButtonElement>('[data-action="supplement"]')!
    expect(supplement).not.toBeNull()
    supplement.click()
    for (let index = 0; index < 4; index += 1) {
      await Promise.resolve(); await nextTick()
    }

    expect(api.supplementGrading).toHaveBeenCalledWith(7, 19)
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
    const { host } = await mountView()
    expect(host.textContent).not.toContain('原已匹配卷 第2-1页')
    for (const [rowKey, code] of [['issue:good', 'S2'], ['issue:bad', 'S1']] as const) {
      await selectReviewRow(host, rowKey)
      await pickStudent(host, code)
    }
    host.querySelector<HTMLButtonElement>('[data-match-selected]')!.click()
    await vi.waitFor(() => expect(host.querySelector('[data-match-result]')?.textContent).toContain('已保存 1 项；1 项因重复归属未保存'))
    expect(host.textContent).toContain('原已匹配卷 第2-1页')
    expect(host.textContent).toContain('合成学生1 · S1 · 合成班')

    await selectReviewRow(host, 'group:exact-1')
    expect(host.querySelector('.scan-review-detail img[src="/api/synthetic/exact/front"]')).not.toBeNull()
    expect(host.querySelectorAll('.scan-match-conflict')).toHaveLength(2)

    host.querySelector<HTMLButtonElement>('[data-review-filter="done"]')!.click()
    await nextTick()
    await selectReviewRow(host, 'issue:good')
    expect(host.querySelector('[data-saved-decision="issue:good"]')).not.toBeNull()
    expect(host.querySelector('[data-saved-decision="issue:bad"]')).toBeNull()
    expect(host.querySelector('[data-match-selected]')?.textContent).toContain('1 项')
  })

  it('exposes exact-name duplicate papers and prevents starting despite pending confirmation', async () => {
    const fixture = await api.fetchPreflight(7)
    fixture.groups = [1, 2].map((id) => ({ id: `g${id}`, student_id: 11, student_name: '学生甲',
      source_label: `合成卷${id}`, match_method: 'exact', match_score: 1,
      front_media_url: `/api/synthetic/${id}/front`, back_media_url: `/api/synthetic/${id}/back` }))
    fixture.match_conflicts = [{ code: 'scan_student_multiple_papers', message: '同一学生对应 2 份答卷', student_id: 11,
      targets: [{ target_type: 'group', target_id: 'g1' }, { target_type: 'group', target_id: 'g2' }] }]
    const { host } = await mountView()
    expect(host.textContent).toContain('合成卷1')
    expect(host.textContent).toContain('合成卷2')
    await openStage(host, 'grade')
    const confirmation = host.querySelector<HTMLInputElement>('[data-confirm-pending]')!
    confirmation.checked = true
    confirmation.dispatchEvent(new Event('change', { bubbles: true }))
    host.querySelector<HTMLButtonElement>('[data-grading-mode="ai"]')!.click()
    await vi.waitFor(() => expect(host.querySelector<HTMLButtonElement>('[data-confirm-grading-plan]')?.disabled).toBe(true))
    expect(api.startGrading).not.toHaveBeenCalled()
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
    const { host } = await mountView()

    // The exact-matched group is not listed in the review rows, but still owns the student.
    expect(host.textContent).not.toContain('第3-4页')
    const input = host.querySelector<HTMLInputElement>('.scan-review-detail input[aria-label="选择学生"]')!
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

    const panel = host.querySelector<HTMLElement>('.scan-review-detail [data-transfer-panel]')!
    expect(panel.textContent).toContain('已归属「第3-4页」')
    // The plain match button is hidden while a takeover is pending.
    expect(host.querySelector('.scan-review-detail__controls button.secondary')).toBeNull()

    ;[...panel.querySelectorAll('button')]
      .find((button) => button.textContent?.includes('改用当前卷'))!.click()
    await vi.waitFor(() => expect(api.saveScanDecisions).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.saveScanDecisions).mock.calls[0]![2]).toEqual([
      { target_type: 'issue', target_id: 'i1', action: 'match', student_id: 11 },
      { target_type: 'group', target_id: 'g1', action: 'pending' },
    ])
  })

  it('shows roster groups in the auto filter and applies a suggestion chip', async () => {
    vi.mocked(api.fetchPreflight).mockResolvedValue({ revision: 0,
      summary: { auto_matched: 1, ready_to_grade: 1, issues: 1, absent_candidates: 0, total_pages: 4 },
      groups: [{ id: 'g1', student_id: 11, student_name: '学生甲', source_label: '第3-4页',
        match_method: 'roster', match_score: 0.97, front_media_url: '/api/g1/front', back_media_url: '/api/g1/back' }],
      issues: [{ id: 'i1', detected_name: '学生甲', source_label: '005', issue_type: 'needs_confirmation',
        front_media_url: '/api/i1/front', back_media_url: '/api/i1/back',
        suggested_students: [
          { student_id: 12, student_name: '学生乙', class_name: '一班', score: 0.87 },
          { student_id: 11, student_name: '学生甲', class_name: '一班', score: 0.61 },
        ] }],
      absent_students: [], warnings: [], decisions: [], pending_issue_count: 1, match_conflicts: [] })
    const { host } = await mountView()

    // Issue row is selected by default under 需处理; the confirm button starts disabled.
    const matchButton = [...host.querySelectorAll<HTMLButtonElement>('.scan-review-detail__controls button')]
      .find((button) => button.textContent?.trim() === '匹配')!
    expect(matchButton.disabled).toBe(true)
    const chip = host.querySelector<HTMLButtonElement>('[data-suggestion-id="12"]')!
    expect(chip.textContent).toContain('学生乙')
    expect(chip.textContent).toContain('一班')
    expect(chip.textContent).toContain('最可能')
    expect(chip.textContent).not.toContain('%')
    chip.click()
    await nextTick()
    expect(chip.getAttribute('aria-pressed')).toBe('true')
    expect(matchButton.disabled).toBe(false)
    expect(host.querySelector('[data-match-selected]')?.textContent).toContain('1 项')

    host.querySelector<HTMLButtonElement>('[data-review-filter="auto"]')!.click()
    await nextTick()
    const groupRow = host.querySelector('[data-row-key="group:g1"]')
    expect(groupRow?.textContent).toContain('学生甲')
    expect(groupRow?.textContent).toContain('名单比对')
  })

  function reviewQuestions(): Awaited<ReturnType<typeof fetchReviewQuestions>> {
    return [
      { question_id: 'Q1', question_type: 'single_choice', total_count: 30, needs_review_count: 0,
        ungraded_count: 0, failed_count: 0, ai_ready_count: 28, teacher_confirmed_count: 2, max_score: 4 },
      { question_id: 'Q17', question_type: 'subjective', total_count: 30, needs_review_count: 4,
        ungraded_count: 5, failed_count: 3, ai_ready_count: 18, teacher_confirmed_count: 0, max_score: 6 },
    ]
  }

  it('renders question tiles by type with score rates and routes a tile into the workbench', async () => {
    vi.mocked(fetchReviewQuestions).mockResolvedValue(reviewQuestions())
    const { host, router } = await mountView()
    expect(host.querySelector('[data-manual-review]')).toBeNull()

    await openStage(host, 'review')
    await vi.waitFor(() => {
      expect(host.querySelector('[data-question-tile="Q1"]')?.textContent).toContain('90%')
    })
    expect(host.textContent).toContain('还有 12 项需要老师处理')
    const groupTitles = [...host.querySelectorAll('.review-rate-group h4')]
      .map((element) => element.textContent?.trim())
    expect(groupTitles).toEqual(['客观题', '解答题'])

    const choice = host.querySelector<HTMLElement>('[data-question-tile="Q1"]')!
    expect(choice.querySelector('.review-rate-tile__top')?.textContent).not.toContain('确认')
    expect(choice.querySelector('.review-rate-tile__rate')?.textContent).toContain('3.6/4')
    expect(choice.querySelector('.review-rate-tile__meta')?.textContent).toContain('确认 2')
    const subjective = host.querySelector<HTMLElement>('[data-question-tile="Q17"]')!
    expect(subjective.textContent).toContain('35%')
    expect(subjective.textContent).toContain('待 12')
    expect(subjective.querySelector('.review-rate-tile__meta')?.textContent).toContain('待处理 12')

    expect(host.querySelector('[data-score-bin="0"]')?.textContent).toContain('1')
    expect(host.querySelector('[data-score-bin="5"]')?.textContent).toContain('1')
    expect(host.querySelector('[data-score-bin="9"]')?.textContent).toContain('1')
    expect(host.querySelector('[data-score-bin="1"]')?.textContent).toContain('0')

    const navigation = vi.spyOn(router, 'push')
    choice.click()
    expect(navigation).toHaveBeenCalledTimes(1)
    await navigation.mock.results[0]!.value
    expect(router.currentRoute.value.path).toBe('/grading')
    expect(router.currentRoute.value.query).toMatchObject({
      session: '7', scope: 'all', entry: 'intervention', question: 'Q1',
    })
  })

  it('routes the pending-work button to the first question needing review', async () => {
    vi.mocked(fetchReviewQuestions).mockResolvedValue(reviewQuestions())
    const { host, router } = await mountView()
    await openStage(host, 'review')
    await vi.waitFor(() => {
      expect(host.querySelector('[data-question-tile="Q1"]')).not.toBeNull()
    })

    const button = host.querySelector<HTMLButtonElement>('[data-pending-review]')!
    expect(button.textContent).toContain('处理待复核 12 项')
    const navigation = vi.spyOn(router, 'push')
    button.click()
    expect(navigation).toHaveBeenCalledTimes(1)
    await navigation.mock.results[0]!.value
    expect(router.currentRoute.value.path).toBe('/grading')
    expect(router.currentRoute.value.query).toMatchObject({
      session: '7', scope: 'teacher_pending', entry: 'intervention', question: 'Q17',
    })
  })

  it('shows the all-ready headline and hides the pending button when nothing is left', async () => {
    vi.mocked(fetchReviewQuestions).mockResolvedValue([
      { question_id: 'Q1', question_type: 'single_choice', total_count: 30, needs_review_count: 0,
        ungraded_count: 0, failed_count: 0, ai_ready_count: 25, teacher_confirmed_count: 5, max_score: 4 },
    ])
    const { host } = await mountView()
    await openStage(host, 'review')
    await vi.waitFor(() => {
      expect(host.querySelector('[data-question-tile="Q1"]')).not.toBeNull()
    })
    expect(host.textContent).toContain('AI 批改结果已全部就绪')
    expect(host.querySelector('[data-pending-review]')).toBeNull()
  })

  it('keeps tiles and offers a retry when the results center cannot be read', async () => {
    vi.mocked(fetchReviewQuestions).mockResolvedValue(reviewQuestions())
    vi.mocked(fetchResultsCenter).mockRejectedValue(new Error('offline'))
    const { host } = await mountView()
    await openStage(host, 'review')
    await vi.waitFor(() => {
      expect(host.textContent).toContain('成绩暂时无法读取')
    })
    expect(host.querySelector('[data-question-tile="Q1"]')?.textContent).toContain('—')
  })

})
