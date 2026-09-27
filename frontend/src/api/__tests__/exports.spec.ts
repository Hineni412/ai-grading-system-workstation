import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../errors'
import {
  decodeAnalysisPreflight,
  decodeReportContext,
  exportsApi,
} from '../exports'

const job = {
  id: 81,
  job_type: 'report_export',
  payload: {
    session_id: 7,
    report_type: 'annotated_original_pdf',
    score_revision: 'a'.repeat(64),
  },
  result: {
    session_id: 7,
    report_type: 'annotated_original_pdf',
    score_revision: 'a'.repeat(64),
    filename: '七年级期中_批注原卷.pdf',
    download_url: '/api/jobs/81/download',
  },
  status: 'succeeded',
  progress: 1,
  stage: 'report_export',
  detail: 'complete',
  error: null,
  cancel_requested: false,
  created_at: '2026-07-17T10:00:00Z',
  started_at: '2026-07-17T10:00:00Z',
  updated_at: '2026-07-17T10:00:01Z',
  finished_at: '2026-07-17T10:00:01Z',
} as const

afterEach(() => vi.restoreAllMocks())

describe('file center API contract', () => {
  it('accepts a path-free report context with current and expired history', () => {
    const payload = {
      score_revision: 'a'.repeat(64),
      has_results: true,
      jobs: [
        {
          ...job,
          is_current_revision: true,
          file_status: 'available',
        },
      ],
      total: 1,
      page: 1,
      page_size: 100,
      total_pages: 1,
    }

    expect(decodeReportContext(payload)).toEqual(payload)
    expect(() => decodeReportContext({
      ...payload,
      jobs: [{ ...payload.jobs[0], file_path: 'C:/private/report.pdf' }],
    })).toThrow()
  })

  it('submits both report types through the dedicated endpoint', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(job), {
        status: 202,
        headers: { 'content-type': 'application/json' },
      }),
    )

    await exportsApi.submitReport(7, 'annotated_original_pdf', true)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sessions/7/reports/export',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          report_type: 'annotated_original_pdf',
          force_regenerate: true,
        }),
      }),
    )
  })

  it.each(['personal_analysis_html'] as const)(
    'submits the analysis report type %s without Excel options',
    async (reportType) => {
      const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
        new Response(JSON.stringify(job), {
          status: 202,
          headers: { 'content-type': 'application/json' },
        }),
      )

      await exportsApi.submitReport(7, reportType, false)

      expect(fetchMock).toHaveBeenCalledWith(
        '/api/sessions/7/reports/export',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            report_type: reportType,
            force_regenerate: false,
          }),
        }),
      )
    },
  )

  it('requests the analysis preflight with the report type query', async () => {
    const preflight = {
      report_type: 'personal_analysis_html',
      configured: true,
      service_name: '默认内容服务',
      model_name: 'qwen-plus',
      call_count: 10,
      estimated_total_tokens: 120000,
      cache_hits: 3,
      cause_call_count: 0,
      cause_total_questions: 0,
      cause_estimated_tokens: 0,
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(preflight), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )

    const result = await exportsApi.getAnalysisPreflight(7, 'personal_analysis_html')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sessions/7/reports/analysis-preflight?report_type=personal_analysis_html',
      expect.anything(),
    )
    expect(result).toEqual(preflight)
  })

  it('rejects malformed or path-leaking analysis preflight payloads', () => {
    const preflight = {
      report_type: 'personal_analysis_html',
      configured: false,
      service_name: null,
      model_name: null,
      call_count: 1,
      estimated_total_tokens: 50000,
      cache_hits: 0,
      cause_call_count: 0,
      cause_total_questions: 0,
      cause_estimated_tokens: 0,
    }

    expect(decodeAnalysisPreflight(preflight)).toEqual(preflight)
    expect(() => decodeAnalysisPreflight({
      ...preflight,
      call_count: -1,
    })).toThrow()
    expect(() => decodeAnalysisPreflight({
      ...preflight,
      cache_dir: 'C:/private/cache',
    })).toThrow()
  })

  it('normalizes visible Excel name-list options in the export request', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        ...job,
        payload: {
          session_id: 7,
          report_type: 'score_excel',
          score_revision: 'a'.repeat(64),
        },
      }), {
        status: 202,
        headers: { 'content-type': 'application/json' },
      }),
    )

    await exportsApi.submitReport(7, 'score_excel', false, {
      hide_bottom_enabled: false,
      hide_bottom_n: 8,
      manual_hidden_student_ids: [9, 2, 9],
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sessions/7/reports/export',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          report_type: 'score_excel',
          force_regenerate: false,
          excel_options: {
            hide_bottom_enabled: false,
            hide_bottom_n: 0,
            manual_hidden_student_ids: [2, 9],
          },
        }),
      }),
    )
  })

  it('downloads a controlled file and decodes its Chinese filename', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(new Blob(['%PDF']), {
        status: 200,
        headers: {
          'content-type': 'application/pdf',
          'content-disposition': "attachment; filename*=utf-8''%E4%B8%83%E5%B9%B4%E7%BA%A7.pdf",
        },
      }),
    )

    const downloaded = await exportsApi.downloadJobFile(81)

    expect(downloaded.filename).toBe('七年级.pdf')
    expect(downloaded.blob.type).toBe('application/pdf')
  })

  it('preserves the stable expired-file error for recovery messaging', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      const headers = init?.headers as Record<string, string>
      const requestId = headers['x-request-id'] ?? 'missing-request-id'
      return new Response(JSON.stringify({
        error: {
          code: 'job_file_expired',
          message: 'Job file is no longer available',
          details: { job_id: 81 },
          request_id: requestId,
        },
      }), {
        status: 410,
        headers: {
          'content-type': 'application/json',
          'x-request-id': requestId,
        },
      })
    })

    await expect(exportsApi.downloadJobFile(81)).rejects.toMatchObject({
      name: 'ApiError',
      kind: 'gone',
      code: 'job_file_expired',
    } satisfies Partial<ApiError>)
  })

  it.each([0, -1, Number.NaN, Number.MAX_SAFE_INTEGER + 1])(
    'rejects unsafe identifiers before fetch: %s',
    async (id) => {
      const fetchMock = vi.spyOn(globalThis, 'fetch')
      await expect(exportsApi.getReportContext(id)).rejects.toThrow()
      await expect(exportsApi.downloadJobFile(id)).rejects.toThrow()
      expect(fetchMock).not.toHaveBeenCalled()
    },
  )

  it('deletes a retained report file through the dedicated endpoint', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ job_id: 81, deleted: true, freed_bytes: 2048 }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )

    const result = await exportsApi.deleteReportFile(7, 81)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sessions/7/reports/81/file',
      expect.objectContaining({ method: 'DELETE' }),
    )
    expect(result).toEqual({ job_id: 81, deleted: true, freed_bytes: 2048 })
  })

  it('rejects malformed delete results and path-like keys', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ job_id: 81, deleted: 'yes', freed_bytes: -1 }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    await expect(exportsApi.deleteReportFile(7, 81)).rejects.toThrow()

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ job_id: 81, deleted: false, freed_bytes: 0, file_path: 'C:/x' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    await expect(exportsApi.deleteReportFile(7, 81)).rejects.toThrow()
  })

  it.each([0, -1])('rejects unsafe delete identifiers before fetch: %s', async (id) => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    await expect(exportsApi.deleteReportFile(id, 81)).rejects.toThrow()
    await expect(exportsApi.deleteReportFile(7, id)).rejects.toThrow()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
