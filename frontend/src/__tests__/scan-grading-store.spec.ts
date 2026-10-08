import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../api/scan-grading'
import { ApiError } from '../api/errors';
import { fetchReviewQuestions } from '../api/review';
import { fetchStudents } from '../api/students';

import { useJobStore } from '../stores/jobs';
import { useResultsCenterStore } from '../stores/results-center';
import { useScanGradingStore } from '../stores/scan-grading';

vi.mock('../api/scan-grading', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/scan-grading')>(),
  fetchGradingWorkspace: vi.fn(), uploadScan: vi.fn(), removeScan: vi.fn(), clearScans: vi.fn(),
  freezeScans: vi.fn(), startPreflight: vi.fn(), fetchPreflight: vi.fn(),
  saveScanDecisions: vi.fn(), startGrading: vi.fn(), controlGrading: vi.fn(), cancelGrading: vi.fn(),
  supplementGrading: vi.fn(), startNewScanBatch: vi.fn(), commitScanReplacement: vi.fn(),
  beginScanReplacement: vi.fn(), cancelScanReplacement: vi.fn(),
}))
vi.mock('../api/review', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/review')>(),
  fetchReviewQuestions: vi.fn(),
}))
vi.mock('../api/students', () => ({ fetchStudents: vi.fn() }))

function workspace(sessionId: number, state: 'draft' | 'frozen' = 'draft'): api.GradingWorkspace {
  return {
    session_id: sessionId,
    upload_batch: { batch_id: `batch-${sessionId}`, revision: 0, state, files: [], file_count: 0,
      total_bytes: 0, frozen_at: state === 'frozen' ? '2026-07-16T00:00:00Z' : null },
    grading_run: null,
  }
}

beforeEach(() => {
  setActivePinia(createPinia()); localStorage.clear(); vi.clearAllMocks()
  vi.mocked(fetchStudents).mockResolvedValue([])
})

describe('scan grading store isolation and recovery', () => {

  it('keeps successful uploads visible when another selected file fails', async () => {
    vi.mocked(api.fetchGradingWorkspace)
      .mockResolvedValueOnce(workspace(1))
      .mockResolvedValueOnce({
        ...workspace(1), upload_batch: { ...workspace(1).upload_batch, revision: 2, file_count: 2,
          files: [
            { id: 'a', name: 'a.jpg', media_type: 'image/jpeg', size_bytes: 1, sha256_prefix: 'a'.repeat(12), added_at: 'now' },
            { id: 'c', name: 'c.jpg', media_type: 'image/jpeg', size_bytes: 1, sha256_prefix: 'c'.repeat(12), added_at: 'now' },
          ] },
      })
    vi.mocked(api.uploadScan)
      .mockResolvedValueOnce({ duplicate: false, file: { id: 'a', name: 'a.jpg', media_type: 'image/jpeg', size_bytes: 1, sha256_prefix: 'a'.repeat(12), added_at: 'now' } })
      .mockRejectedValueOnce(new Error('bad file'))
      .mockResolvedValueOnce({ duplicate: false, file: { id: 'c', name: 'c.jpg', media_type: 'image/jpeg', size_bytes: 1, sha256_prefix: 'c'.repeat(12), added_at: 'now' } })
    const store = useScanGradingStore()
    await store.load(1)

    await store.addFiles([
      new File(['a'], 'a.jpg', { type: 'image/jpeg' }),
      new File(['b'], 'b.txt', { type: 'text/plain' }),
      new File(['c'], 'c.jpg', { type: 'image/jpeg' }),
    ])

    expect(store.uploadBatch?.file_count).toBe(2)
    expect(store.errorMessage).toContain('1 个文件未加入队列')
    expect(api.uploadScan).toHaveBeenCalledTimes(3)
  })

  it('appends files into a frozen batch without starting replacement', async () => {
    vi.mocked(api.fetchGradingWorkspace)
      .mockResolvedValueOnce(workspace(1, 'frozen'))
      .mockResolvedValueOnce({
        ...workspace(1, 'frozen'),
        upload_batch: { ...workspace(1, 'frozen').upload_batch, revision: 4, file_count: 2,
          files: [
            { id: 'a', name: 'a.jpg', media_type: 'image/jpeg', size_bytes: 1, sha256_prefix: 'a'.repeat(12), added_at: 'now' },
            { id: 'b', name: 'late.jpg', media_type: 'image/jpeg', size_bytes: 1, sha256_prefix: 'b'.repeat(12), added_at: 'now', appended: true },
          ] },
      })
    vi.mocked(api.uploadScan).mockResolvedValue({
      duplicate: false,
      file: { id: 'b', name: 'late.jpg', media_type: 'image/jpeg', size_bytes: 1,
        sha256_prefix: 'b'.repeat(12), added_at: 'now', appended: true },
    })
    const store = useScanGradingStore()
    await store.load(1)

    await store.appendFiles([new File(['late'], 'late.jpg', { type: 'image/jpeg' })])

    expect(api.uploadScan).toHaveBeenCalledWith(1, expect.any(File), 'append')
    expect(api.beginScanReplacement).not.toHaveBeenCalled()
    expect(store.uploadBatch?.file_count).toBe(2)
  })

  it('does not auto-start replacement when files are picked on a frozen batch', async () => {
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(workspace(1, 'frozen'))
    const store = useScanGradingStore()
    await store.load(1)

    await store.addFiles([new File(['x'], 'x.jpg', { type: 'image/jpeg' })])

    expect(api.uploadScan).not.toHaveBeenCalled()
    expect(api.beginScanReplacement).not.toHaveBeenCalled()
  })

  it('starts a replacement batch only from the explicit action', async () => {
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(workspace(1, 'frozen'))
    vi.mocked(api.beginScanReplacement).mockResolvedValue({
      batch_id: 'replacement-1', revision: 0, state: 'draft', files: [],
      file_count: 0, total_bytes: 0, frozen_at: null,
    })
    const store = useScanGradingStore()
    await store.load(1)

    await store.beginReplacement()

    expect(api.beginScanReplacement).toHaveBeenCalledWith(1)
    expect(store.replacementBatch?.batch_id).toBe('replacement-1')
  })

  it('removes an appended file against the frozen batch revision', async () => {
    const frozen = workspace(1, 'frozen')
    frozen.upload_batch.revision = 4
    frozen.upload_batch.files = [
      { id: 'a', name: 'a.jpg', media_type: 'image/jpeg', size_bytes: 1, sha256_prefix: 'a'.repeat(12), added_at: 'now' },
      { id: 'b', name: 'late.jpg', media_type: 'image/jpeg', size_bytes: 1, sha256_prefix: 'b'.repeat(12), added_at: 'now', appended: true },
    ]
    frozen.upload_batch.file_count = 2
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(frozen)
    vi.mocked(api.removeScan).mockResolvedValue({
      ...frozen.upload_batch, revision: 5, file_count: 1,
      files: [frozen.upload_batch.files[0]!],
    })
    const store = useScanGradingStore()
    await store.load(1)

    await store.remove('b', true)

    expect(api.removeScan).toHaveBeenCalledWith(1, 'b', 4, 'append')
    expect(store.uploadBatch?.file_count).toBe(1)
  })

  it('does one trailing workspace refresh when a terminal poll arrives mid-refresh', async () => {
    const running = workspace(1, 'frozen')
    running.grading_run = {
      run_id: 21, job_id: 73, mode: 'full_paper', state: 'running',
      counts: { graded: 1, grading: 1, pending: 2, skipped: 0, failed: 0, conflict: 0, total: 4 },
      allowed_actions: ['pause', 'cancel'],
    }
    const completed = workspace(1, 'frozen')
    completed.grading_run = {
      run_id: 21, mode: 'full_paper', state: 'completed',
      counts: { graded: 4, grading: 0, pending: 0, skipped: 0, failed: 0, conflict: 0, total: 4 },
      allowed_actions: ['supplement_new_matches'],
    }
    let resolveStaleRefresh!: (value: api.GradingWorkspace) => void
    vi.mocked(api.fetchGradingWorkspace)
      .mockResolvedValueOnce(running)
      .mockReturnValueOnce(new Promise((resolve) => { resolveStaleRefresh = resolve }))
      .mockResolvedValue(completed)
    vi.mocked(api.fetchPreflight).mockResolvedValue({
      revision: 0, summary: {}, groups: [], issues: [], absent_students: [], warnings: [],
      decisions: [], pending_issue_count: 0,
    })
    const store = useScanGradingStore()
    await store.load(1)
    const jobs = useJobStore()
    jobs.jobs[73] = {
      id: 73, job_type: 'grading_run', payload: { session_id: 1 }, result: {}, status: 'running',
      progress: 0.5, stage: 'grading_run', detail: '', error: null, cancel_requested: false,
      created_at: '2026-07-17T00:00:00Z', started_at: '2026-07-17T00:00:01Z',
      updated_at: '2026-07-17T00:00:02Z', finished_at: null,
    }
    for (let index = 0; index < 4; index += 1) await Promise.resolve()
    jobs.jobs[73] = { ...jobs.jobs[73]!, status: 'succeeded', progress: 1,
      updated_at: '2026-07-17T00:00:03Z', finished_at: '2026-07-17T00:00:03Z' }
    await Promise.resolve()
    resolveStaleRefresh(running)
    for (let index = 0; index < 10; index += 1) await Promise.resolve()

    expect(api.fetchGradingWorkspace).toHaveBeenCalledTimes(3)
    expect(store.gradingRun?.state).toBe('completed')
    expect(store.gradingRun?.counts.graded).toBe(4)
  })

  it('refreshes a changed decision revision without retrying or claiming an unsaved match', async () => {
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(workspace(1, 'frozen'))
    const initial: api.ScanPreflight = { revision: 2, summary: {}, groups: [], issues: [],
      absent_students: [], warnings: [], decisions: [], pending_issue_count: 1 }
    vi.mocked(api.fetchPreflight).mockResolvedValueOnce(initial).mockResolvedValue({ ...initial, revision: 3,
      decisions: [{ target_type: 'issue', target_id: 'i1', action: 'invalid' }], pending_issue_count: 0 })
    vi.mocked(api.saveScanDecisions).mockRejectedValue(new ApiError({ kind: 'conflict', status: 409,
      code: 'scan_decision_revision_conflict', message: 'changed', details: {}, requestId: 'test', retryable: false }))
    const store = useScanGradingStore()
    await store.load(1)
    const saved = await store.saveDecisions([{ target_type: 'issue', target_id: 'i1', action: 'match', student_id: 11 }])
    expect(saved).toBe(false)
    expect(api.saveScanDecisions).toHaveBeenCalledTimes(1)
    expect(store.preflight?.revision).toBe(3)
    expect(store.preflight?.decisions[0]?.action).toBe('invalid')
    expect(store.errorMessage).toContain('本次选择尚未保存')
  })

  it('fetches workspace and preflight in parallel and revalidates without blanking', async () => {
    let resolveFirst!: (value: api.GradingWorkspace) => void
    vi.mocked(api.fetchGradingWorkspace)
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve }))
    const preflight = { revision: 2, summary: {}, groups: [], issues: [],
      absent_students: [], warnings: [], decisions: [], pending_issue_count: 0 }
    vi.mocked(api.fetchPreflight).mockResolvedValueOnce(preflight)
    const store = useScanGradingStore()
    const first = store.load(1)
    // 预检与工作区并行发起：工作区未返回时预检已在进行
    expect(api.fetchPreflight).toHaveBeenCalledTimes(1)
    const frozen = workspace(1, 'frozen')
    resolveFirst(frozen)
    await first
    expect(store.loadState).toBe('ready')
    const shownWorkspace = store.workspace
    const shownPreflight = store.preflight
    expect(shownWorkspace).toStrictEqual(frozen)
    expect(shownPreflight).toStrictEqual(preflight)

    const changed = workspace(1, 'frozen')
    changed.upload_batch.revision = 5
    let resolveSecond!: (value: api.GradingWorkspace) => void
    vi.mocked(api.fetchGradingWorkspace)
      .mockReturnValueOnce(new Promise((resolve) => { resolveSecond = resolve }))
    vi.mocked(api.fetchPreflight).mockResolvedValueOnce({ ...preflight, revision: 3 })
    const second = store.load(1)
    await Promise.resolve()
    // 重查期间旧工作区与预检结果保持展示，loadState 不回到 loading
    expect(store.loadState).toBe('ready')
    expect(store.workspace).toBe(shownWorkspace)
    expect(store.preflight).toBe(shownPreflight)
    resolveSecond(changed)
    await second
    expect(store.workspace).toStrictEqual(changed)
    expect(store.uploadBatch?.revision).toBe(5)
    expect(store.preflight?.revision).toBe(3)
  })

  it('stays silent only for a real missing preflight, not for load failures', async () => {
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(workspace(1, 'frozen'))
    vi.mocked(api.fetchPreflight).mockRejectedValue(new ApiError({ kind: 'not_found', status: 404,
      code: 'scan_preflight_not_found', message: 'not ready', details: {}, requestId: 't', retryable: false }))
    const store = useScanGradingStore()
    await store.load(1)
    expect(store.loadState).toBe('ready')
    expect(store.preflight).toBeNull()
    expect(store.errorMessage).toBe('')

    vi.mocked(api.fetchPreflight).mockRejectedValue(new Error('Invalid scan preflight identity'))
    await store.load(1)
    expect(store.loadState).toBe('ready')
    expect(store.preflight).toBeNull()
    expect(store.errorMessage).toContain('Invalid scan preflight identity')
  })

  it('prefetch warms the workspace and intervention cache before first entry', async () => {
    const completed = workspace(1, 'frozen')
    completed.grading_run = {
      run_id: 21, mode: 'full_paper', state: 'completed',
      counts: { graded: 4, grading: 0, pending: 0, skipped: 0, failed: 0, conflict: 0, total: 4 },
      allowed_actions: ['supplement_new_matches'],
    }
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(completed)
    vi.mocked(api.fetchPreflight).mockResolvedValue({
      revision: 0, summary: {}, groups: [], issues: [], absent_students: [], warnings: [],
      decisions: [], pending_issue_count: 0,
    })
    const summary = [{ question_id: 'Q1', question_type: null, total_count: 4,
      needs_review_count: 1, max_score: 5 }]
    vi.mocked(fetchReviewQuestions).mockResolvedValue(summary)
    // 复核就绪时预取顺带暖起成绩中心；没有运行记录时不暖
    const resultsLoad = vi.spyOn(useResultsCenterStore(), 'load').mockResolvedValue()
    const store = useScanGradingStore()

    await store.prefetch(1)
    expect(api.fetchGradingWorkspace).toHaveBeenCalledTimes(1)
    expect(fetchReviewQuestions).toHaveBeenCalledWith(1, { scope: 'all' })
    expect(store.cachedInterventionSummary(1)).toEqual(summary)
    expect(store.loadState).toBe('ready')
    expect(resultsLoad).toHaveBeenCalledWith(1)

    // 同一场考试已就绪时再次预取不再发请求
    await store.prefetch(1)
    expect(api.fetchGradingWorkspace).toHaveBeenCalledTimes(1)
    expect(fetchReviewQuestions).toHaveBeenCalledTimes(1)

    // 预取后首次 load 走静默重查：旧工作区保持展示
    const shownWorkspace = store.workspace
    let resolveLoad!: (value: api.GradingWorkspace) => void
    vi.mocked(api.fetchGradingWorkspace)
      .mockReturnValueOnce(new Promise((resolve) => { resolveLoad = resolve }))
    const pending = store.load(1)
    await Promise.resolve()
    expect(store.loadState).toBe('ready')
    expect(store.workspace).toBe(shownWorkspace)
    resolveLoad(workspace(1, 'frozen'))
    await pending

    // 没有批改运行记录的考试不触发成绩中心预热
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(workspace(2, 'frozen'))
    vi.mocked(api.fetchPreflight).mockResolvedValue({
      revision: 0, summary: {}, groups: [], issues: [], absent_students: [], warnings: [],
      decisions: [], pending_issue_count: 0,
    })
    await store.prefetch(2)
    expect(store.loadState).toBe('ready')
    expect(resultsLoad).toHaveBeenCalledTimes(1)
  })
})
