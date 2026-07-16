import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../api/scan-grading'
import { fetchStudents } from '../api/students'
import type { JobResponse } from '../api/jobs'
import { useScanGradingStore } from '../stores/scan-grading'

vi.mock('../api/scan-grading', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/scan-grading')>(),
  fetchGradingWorkspace: vi.fn(), uploadScan: vi.fn(), removeScan: vi.fn(), clearScans: vi.fn(),
  freezeScans: vi.fn(), startPreflight: vi.fn(), fetchPreflight: vi.fn(),
  saveScanDecisions: vi.fn(), startGrading: vi.fn(), controlGrading: vi.fn(), cancelGrading: vi.fn(),
  startNewScanBatch: vi.fn(),
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
})
