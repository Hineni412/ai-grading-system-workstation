import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchPreflight, supplementGrading } from '../api/scan-grading';

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
      identity,
    }
  }

  it('accepts a null preflight identity emitted by the backend', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => response(preflightPayload(null))))

    const check = await fetchPreflight(7)

    expect(check.identity).toBeNull()
  })

  it('rejects a malformed preflight identity', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => response(preflightPayload('broken'))))

    await expect(fetchPreflight(7)).rejects.toThrow('无法识别')
  })
})
