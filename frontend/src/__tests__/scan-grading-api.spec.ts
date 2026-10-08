import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchPreflight, removeScan, supplementGrading, uploadScan } from '../api/scan-grading';
import { assertNoPathLikeKeys } from '../api/validation';

function response(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'content-type': 'application/json', 'x-request-id': 'rid' },
  })
}

afterEach(() => vi.unstubAllGlobals())

describe('scan grading API contract', () => {

  it('submits supplementation through its run-specific endpoint', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => response({
      id: 9, job_type: 'grading_run', payload: { session_id: 7 }, result: {}, status: 'queued',
      progress: 0, stage: 'queued', detail: '', error: null, cancel_requested: false,
      created_at: '2026-07-17T00:00:00Z', started_at: null,
      updated_at: '2026-07-17T00:00:00Z', finished_at: null,
    }))
    vi.stubGlobal('fetch', fetchMock)

    await supplementGrading(7, 19)

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/sessions/7/grading/runs/19/supplement-new-matches',
    )
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: 'POST' })
  })

  function preflightPayload(identity: unknown): unknown {
    return {
      revision: 0,
      summary: { auto_matched: 0, ready_to_grade: 0, issues: 0,
        absent_candidates: 0, total_pages: 0 },
      groups: [], issues: [], absent_students: [], warnings: [],
      decisions: [], pending_issue_count: 0,
      input_changed: false, appended_file_count: 2,
      identity,
    }
  }

  it('uploads appended files through the append channel', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => response({
      duplicate: false,
      file: { id: 'f1', name: 'late.jpg', media_type: 'image/jpeg', size_bytes: 3,
        sha256_prefix: 'a'.repeat(12), added_at: 'now', appended: true },
    }))
    vi.stubGlobal('fetch', fetchMock)

    const file = new File(['late'], 'late.jpg', { type: 'image/jpeg' })
    const result = await uploadScan(7, file, 'append')

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/sessions/7/scan-uploads?append=true')
    expect(result.file.appended).toBe(true)
  })

  it('removes an appended file through the append channel', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => response({
      batch_id: 'batch-1', revision: 3, state: 'frozen', files: [],
      file_count: 0, total_bytes: 0, frozen_at: '2026-07-17T00:00:00Z',
    }))
    vi.stubGlobal('fetch', fetchMock)

    await removeScan(7, 'f1', 3, 'append')

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/sessions/7/scan-uploads/f1?expected_revision=3&append=true',
    )
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: 'DELETE' })
  })

  it('accepts a null preflight identity emitted by the backend', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => response(preflightPayload(null))))

    const check = await fetchPreflight(7)

    expect(check.identity).toBeNull()
    expect(check.appended_file_count).toBe(2)
  })

  it('keeps rejecting file paths beside appended file counts', () => {
    expect(() => assertNoPathLikeKeys({
      appended_file_count: 2, detail: { file_path: '/test/scan.pdf' },
    })).toThrow('Path-like response key')
  })

  it('rejects a malformed preflight identity', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => response(preflightPayload('broken'))))

    await expect(fetchPreflight(7)).rejects.toThrow('无法识别')
  })
})
