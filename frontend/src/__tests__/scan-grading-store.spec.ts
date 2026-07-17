import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../api/scan-grading'
import { fetchStudents } from '../api/students'
import type { JobResponse } from '../api/jobs'
import { useJobStore } from '../stores/jobs'
import { useScanGradingStore } from '../stores/scan-grading'

vi.mock('../api/scan-grading', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/scan-grading')>(),
  fetchGradingWorkspace: vi.fn(), uploadScan: vi.fn(), removeScan: vi.fn(), clearScans: vi.fn(),
  freezeScans: vi.fn(), startPreflight: vi.fn(), fetchPreflight: vi.fn(),
  saveScanDecisions: vi.fn(), startGrading: vi.fn(), controlGrading: vi.fn(), cancelGrading: vi.fn(),
  supplementGrading: vi.fn(), startNewScanBatch: vi.fn(),
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

function succeededScanJob(): JobResponse {
  return {
    id: 51, job_type: 'scan_analysis', payload: { session_id: 1 }, result: {}, status: 'succeeded',
    progress: 1, stage: 'scan_analysis', detail: '', error: null, cancel_requested: false,
    created_at: '2026-07-16T00:00:00Z', started_at: '2026-07-16T00:00:01Z',
    updated_at: '2026-07-16T00:00:02Z', finished_at: '2026-07-16T00:00:02Z',
  }
}

beforeEach(() => {
  setActivePinia(createPinia()); localStorage.clear(); vi.clearAllMocks()
  vi.mocked(fetchStudents).mockResolvedValue([])
})

describe('scan grading store isolation and recovery', () => {
  it('does not let an old remove response overwrite a newly selected session', async () => {
    let resolveRemove!: (value: api.ScanUploadBatch) => void
    vi.mocked(api.fetchGradingWorkspace)
      .mockResolvedValueOnce(workspace(1))
      .mockResolvedValueOnce(workspace(2))
    vi.mocked(api.removeScan).mockReturnValue(new Promise((resolve) => { resolveRemove = resolve }))
    const store = useScanGradingStore()
    await store.load(1)
    store.workspace!.upload_batch.files = [{ id: 'old', name: 'old.jpg', media_type: 'image/jpeg',
      size_bytes: 4, sha256_prefix: 'a'.repeat(12), added_at: 'now' }]
    store.workspace!.upload_batch.file_count = 1

    const oldAction = store.remove('old')
    await store.load(2)
    resolveRemove(workspace(1).upload_batch)
    await oldAction

    expect(store.workspace?.session_id).toBe(2)
    expect(store.workspace?.upload_batch.batch_id).toBe('batch-2')
  })

  it('loads the preflight automatically when its tracked job succeeds', async () => {
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(workspace(1, 'frozen'))
    vi.mocked(api.fetchPreflight)
      .mockRejectedValueOnce(new Error('not ready'))
      .mockResolvedValueOnce({ revision: 0, summary: {}, groups: [], issues: [], absent_students: [],
        warnings: [], decisions: [], pending_issue_count: 0 })
    vi.mocked(api.startPreflight).mockResolvedValue(succeededScanJob())
    const store = useScanGradingStore()
    await store.load(1)

    await store.analyze()
    await Promise.resolve(); await Promise.resolve()

    expect(api.fetchPreflight).toHaveBeenCalledTimes(2)
    expect(store.preflight?.pending_issue_count).toBe(0)
  })

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

  it('refreshes server counts and actions when a grading job poll changes', async () => {
    const running = workspace(1, 'frozen')
    running.grading_run = {
      run_id: 19, job_id: 71, mode: 'full_paper', state: 'running',
      counts: { graded: 1, grading: 1, pending: 3, skipped: 0, failed: 0, conflict: 0, total: 5 },
      allowed_actions: ['pause', 'cancel'],
    }
    const completed = workspace(1, 'frozen')
    completed.grading_run = {
      run_id: 19, job_id: 71, job_status: 'succeeded', mode: 'full_paper', state: 'completed',
      counts: { graded: 5, grading: 0, pending: 0, skipped: 0, failed: 0, conflict: 0, total: 5 },
      allowed_actions: ['supplement_new_matches'],
    }
    vi.mocked(api.fetchGradingWorkspace)
      .mockResolvedValueOnce(running)
      .mockResolvedValue(completed)
    vi.mocked(api.fetchPreflight).mockResolvedValue({
      revision: 0, summary: {}, groups: [], issues: [], absent_students: [], warnings: [],
      decisions: [], pending_issue_count: 0,
    })
    const store = useScanGradingStore()
    await store.load(1)
    const jobs = useJobStore()
    jobs.jobs[71] = {
      id: 71, job_type: 'grading_run', payload: { session_id: 1 }, result: {}, status: 'succeeded',
      progress: 1, stage: 'grading_completed', detail: '', error: null, cancel_requested: false,
      created_at: '2026-07-17T00:00:00Z', started_at: '2026-07-17T00:00:01Z',
      updated_at: '2026-07-17T00:00:03Z', finished_at: '2026-07-17T00:00:03Z',
    }
    for (let index = 0; index < 6; index += 1) await Promise.resolve()

    expect(api.fetchGradingWorkspace).toHaveBeenCalledTimes(2)
    expect(store.gradingRun?.state).toBe('completed')
    expect(store.gradingRun?.counts.graded).toBe(5)
    expect(store.gradingRun?.allowed_actions).toContain('supplement_new_matches')
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

  it('recovers an active preflight from the server without browser storage', async () => {
    const value = workspace(1, 'frozen')
    value.scan_analysis_job = {
      id: 81, status: 'running', progress: 0.4, updated_at: '2026-07-17T00:00:02Z',
      cancel_requested: false, scan_batch_id: 'batch-1',
    }
    vi.mocked(api.fetchGradingWorkspace).mockResolvedValue(value)
    vi.mocked(api.fetchPreflight).mockRejectedValue(new Error('not ready'))
    const store = useScanGradingStore()
    await store.load(1)

    expect(store.preflightJobId).toBe(81)
    expect(useJobStore().jobs[81]?.status).toBe('running')
  })
})
