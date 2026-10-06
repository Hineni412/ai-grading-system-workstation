import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../api/scan-grading'
import { ApiError } from '../api/errors';
import { fetchStudents } from '../api/students';

import { useJobStore } from '../stores/jobs';
import { useScanGradingStore } from '../stores/scan-grading';

vi.mock('../api/scan-grading', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/scan-grading')>(),
  fetchGradingWorkspace: vi.fn(), uploadScan: vi.fn(), removeScan: vi.fn(), clearScans: vi.fn(),
  freezeScans: vi.fn(), startPreflight: vi.fn(), fetchPreflight: vi.fn(),
  saveScanDecisions: vi.fn(), startGrading: vi.fn(), controlGrading: vi.fn(), cancelGrading: vi.fn(),
  supplementGrading: vi.fn(), startNewScanBatch: vi.fn(), commitScanReplacement: vi.fn(),
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
})
