import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  decodeSessionAnomalyResponse,
  decodeWorkbenchOverview,
  fetchSessionAnomalies,
  fetchWorkbenchOverview,
} from '../workbench'

const session = {
  id: 7,
  name: '期中考试',
  status: 'completed',
  curriculum_volume_id: 'bnu24-math-g7-upper',
  is_deleted: false,
  deleted_at: null,
  created_at: '2026-07-15T08:00:00Z',
  updated_at: '2026-07-15T09:00:00Z',
} as const

const progress = {
  total_papers: 10,
  matched_papers: 9,
  unmatched_papers: 1,
  graded_papers: 8,
  failed_papers: 1,
  grading_papers: 0,
  needs_human_review: 2,
  absent_students: 1,
  scan_issue_students: 1,
  progress_percent: 80,
} as const

const job = {
  id: 41,
  job_type: 'grading',
  status: 'running',
  progress: 0.5,
  stage: 'grading',
  detail: '5/10',
  created_at: '2026-07-15T08:00:00Z',
  started_at: '2026-07-15T08:00:01Z',
  updated_at: '2026-07-15T08:01:00Z',
  finished_at: null,
} as const

const overview = {
  current_session: session,
  progress,
  review: { question_count: 2, item_count: 3 },
  anomalies: { unmatched_papers: 1, scan_issue_students: 1, failed_papers: 1 },
  recent_jobs: [job],
  recent_sessions: [{ session, progress }],
  updated_at: '2026-07-15T09:00:00Z',
} as const

const anomalies = {
  items: [
    {
      anomaly_id: 'unmatched:10',
      anomaly_type: 'unmatched_paper',
      display_name: '答卷 10',
      student_code: null,
      class_name: null,
      status: 'unmatched',
      detail: null,
      created_at: '2026-07-15T08:00:00Z',
    },
  ],
  total: 1,
  page: 1,
  page_size: 20,
  total_pages: 1,
} as const

afterEach(() => vi.restoreAllMocks())

describe('workbench API contract', () => {
  it('accepts the path-free overview and anomaly contracts', () => {
    expect(decodeWorkbenchOverview(overview)).toEqual(overview)
    expect(decodeSessionAnomalyResponse(anomalies)).toEqual(anomalies)
    expect(JSON.stringify(overview)).not.toMatch(/(?:rubric|answer_key|payload|result|path)/)
  })

  it('accepts an unassigned curriculum volume in a session summary', () => {
    const unassignedSession = { ...session, curriculum_volume_id: null }
    const payload = {
      ...overview,
      current_session: unassignedSession,
      recent_sessions: [{ session: unassignedSession, progress }],
    }

    expect(decodeWorkbenchOverview(payload)).toEqual(payload)
  })

  it('accepts empty and out-of-range anomaly pages', () => {
    expect(decodeSessionAnomalyResponse({
      items: [], total: 0, page: 1, page_size: 20, total_pages: 0,
    }).items).toEqual([])
    expect(decodeSessionAnomalyResponse({
      items: [], total: 1, page: 2, page_size: 20, total_pages: 1,
    }).items).toEqual([])
  })

  it.each([
    { ...overview, recent_jobs: [{ ...job, status: 'cancelling' }] },
    { ...overview, progress: { ...progress, progress_percent: Number.NaN } },
    { ...overview, progress: { ...progress, failed_papers: -1 } },
    { ...overview, review: { question_count: -1, item_count: 3 } },
  ])('rejects malformed overview data', (payload) => {
    expect(() => decodeWorkbenchOverview(payload)).toThrow('Invalid workbench overview')
  })

  it.each([
    { ...anomalies, total: 0 },
    { ...anomalies, page: 0 },
    { ...anomalies, items: [{ ...anomalies.items[0], anomaly_type: 'private' }] },
  ])('rejects malformed anomaly data', (payload) => {
    expect(() => decodeSessionAnomalyResponse(payload)).toThrow('Invalid session anomalies')
  })

  it('rejects extra private workbench fields', () => {
    expect(() => decodeWorkbenchOverview({ ...overview, private_path: 'C:/private' })).toThrow()
    expect(() => decodeWorkbenchOverview({
      ...overview, progress: { ...progress, raw: { source: 'private' } },
    })).toThrow()
    expect(() => decodeWorkbenchOverview({
      ...overview, recent_jobs: [{ ...job, payload: { secret: true } }],
    })).toThrow()
    expect(() => decodeSessionAnomalyResponse({
      ...anomalies, items: [{ ...anomalies.items[0], private_path: 'C:/private' }],
    })).toThrow()
  })

  it('uses known query values and safe session paths', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (path) =>
      new Response(JSON.stringify(String(path).includes('/anomalies')
        ? String(path).includes('page=2')
          ? { ...anomalies, items: [], page: 2 }
          : anomalies
        : overview), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )

    await expect(fetchWorkbenchOverview(7)).resolves.toEqual(overview)
    await expect(fetchSessionAnomalies(7)).resolves.toEqual(anomalies)
    await expect(fetchSessionAnomalies(7, undefined, 2)).resolves.toEqual({
      ...anomalies, items: [], page: 2,
    })
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/workbench/overview?session_id=7&recent_limit=5')
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/sessions/7/anomalies?page=1&page_size=100')
    expect(fetchMock.mock.calls[2]?.[0]).toBe('/api/sessions/7/anomalies?page=2&page_size=100')
  })
})
