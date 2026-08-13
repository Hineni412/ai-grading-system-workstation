import { afterEach, describe, expect, it, vi } from 'vitest'

import { decodeJobResponse, jobApi } from '../jobs'

const job = {
  id: 41,
  job_type: 'report_export',
  payload: {},
  result: {},
  status: 'running',
  progress: 0.5,
  stage: 'rendering',
  detail: '2/4',
  error: null,
  cancel_requested: false,
  created_at: '2026-07-12T10:00:00Z',
  started_at: null,
  updated_at: '2026-07-12T10:00:01Z',
  finished_at: null,
} as const

afterEach(() => vi.restoreAllMocks())

describe('Job API contract', () => {
  it('accepts the exact public Job response', () => {
    expect(decodeJobResponse(job)).toEqual(job)
  })

  it('accepts the backend-approved file count aggregate in an Ops Job result', () => {
    const opsJob = {
      ...job,
      job_type: 'ops_backup',
      status: 'succeeded',
      progress: 1,
      result: {
        operation: 'backup',
        filename: 'backup_20260719_120000_manual.zip',
        file_count: 4,
        download_url: '/api/jobs/41/download',
      },
      finished_at: '2026-07-19T12:01:00Z',
    } as const

    expect(decodeJobResponse(opsJob)).toEqual(opsJob)
  })

  it.each([
    { ...job, status: 'cancelling' },
    { ...job, id: 0 },
    { ...job, progress: Number.NaN },
    { ...job, payload: [] },
    { ...job, updated_at: null },
  ])('rejects a malformed public Job response', (payload) => {
    expect(() => decodeJobResponse(payload)).toThrow('Invalid Job response')
  })

  it('queries and cancels through the exact Job endpoints', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (path, init) =>
      new Response(JSON.stringify(job), {
        status: 200,
        headers: {
          'content-type': 'application/json',
          'x-request-id': String((init?.headers as Record<string, string>)['x-request-id']),
          'x-test-path': String(path),
        },
      }),
    )

    await expect(jobApi.getJob(41)).resolves.toEqual(job)
    await expect(jobApi.cancelJob(41)).resolves.toEqual(job)
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/jobs/41',
      expect.objectContaining({ method: 'GET' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/jobs/41/cancel',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it.each([0, -1, Number.NaN, Number.MAX_SAFE_INTEGER + 1])(
    'rejects unsafe Job id %s before fetch',
    async (id) => {
      const fetchMock = vi.spyOn(globalThis, 'fetch')
      await expect(jobApi.getJob(id)).rejects.toThrow('Invalid Job id')
      expect(fetchMock).not.toHaveBeenCalled()
    },
  )
})
