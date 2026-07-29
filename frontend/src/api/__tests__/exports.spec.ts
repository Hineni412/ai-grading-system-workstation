import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../errors'
import {
  decodeJobSummaryList,
  decodeReportContext,
  decodeTrainingTaskDetail,
  decodeTrainingTaskList,
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

  it('accepts the public training task list and rejects storage fields', () => {
    const payload = {
      items: [
        {
          id: 12,
          task_code: 'TRAIN-12',
          created_by: 'teacher',
          scope_snapshot: { mode: 'class', class_id: '七年级一班' },
          exam_scope: { mode: 'current', session_ids: [7] },
          generation_config: { variant_mode: 'individual' },
          warnings: [],
          status: 'ready',
          created_at: '2026-07-17T09:00:00Z',
          updated_at: '2026-07-17T09:01:00Z',
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
      total_pages: 1,
    }

    expect(decodeTrainingTaskList(payload)).toEqual(payload)
    expect(() => decodeTrainingTaskList({
      ...payload,
      items: [{ ...payload.items[0], output_path: 'C:/private/training.docx' }],
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

  it('loads training export history and resolves a selected job safely', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({
        items: [{
          id: 91,
          job_type: 'training_export',
          status: 'succeeded',
          progress: 1,
          stage: 'training_export',
          detail: 'complete',
          created_at: '2026-07-17T10:00:00Z',
          started_at: '2026-07-17T10:00:00Z',
          updated_at: '2026-07-17T10:00:01Z',
          finished_at: '2026-07-17T10:00:01Z',
        }],
        total: 1,
        page: 1,
        page_size: 20,
        total_pages: 1,
      }), { status: 200, headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        ...job,
        id: 91,
        job_type: 'training_export',
        payload: { task_id: 12, format: 'docx' },
        result: {
          task_id: 12,
          filename: '训练材料.zip',
          download_url: '/api/jobs/91/download',
        },
      }), { status: 200, headers: { 'content-type': 'application/json' } }))

    const listing = await exportsApi.listTrainingExportJobs()
    const detail = await exportsApi.getJob(91)

    expect(listing.total).toBe(1)
    expect(detail.result.filename).toBe('训练材料.zip')
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/jobs?job_type=training_export&page=1&page_size=20',
    )
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/jobs/91')
  })

  it('submits a task bundle and retries only through dedicated training endpoints', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockImplementation(async () => new Response(JSON.stringify({
        ...job,
        job_type: 'training_export',
        payload: { task_id: 12, format: 'docx' },
      }), { status: 202, headers: { 'content-type': 'application/json' } }))

    await exportsApi.submitTrainingExport(12, { format: 'docx' })
    await exportsApi.retryTrainingExport(81)

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/training/tasks/12/exports')
    expect(fetchMock.mock.calls[0]?.[1]).toEqual(expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ format: 'docx' }),
    }))
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/training/exports/jobs/81/retry')
  })

  it('decodes training details and job pages without accepting storage fields', () => {
    const task = {
      id: 12,
      task_code: 'TRAIN-12',
      created_by: 'teacher',
      scope_snapshot: { mode: 'class', class_id: '七年级一班' },
      exam_scope: { mode: 'current', session_ids: [7] },
      generation_config: { variant_mode: 'individual' },
      warnings: [],
      status: 'completed',
      created_at: '2026-07-17T09:00:00Z',
      updated_at: '2026-07-17T09:01:00Z',
      diagnosis_snapshot: {},
      variants: [{
        id: 3,
        variant_key: 'group-a',
        variant_type: 'individual',
        students: [],
        items: [],
      }],
      exports: [{ id: 4, status: 'succeeded', export_format: 'docx' }],
    }
    const page = {
      items: [{
        id: 91,
        job_type: 'training_export',
        status: 'succeeded',
        progress: 1,
        stage: 'training_export',
        detail: 'complete',
        created_at: '2026-07-17T10:00:00Z',
        started_at: null,
        updated_at: '2026-07-17T10:00:01Z',
        finished_at: '2026-07-17T10:00:01Z',
      }],
      total: 1,
      page: 1,
      page_size: 20,
      total_pages: 1,
    }

    expect(decodeTrainingTaskDetail(task).variants).toHaveLength(1)
    expect(decodeJobSummaryList(page).items[0]?.id).toBe(91)
    expect(() => decodeTrainingTaskDetail({
      ...task,
      exports: [{ output_path: 'C:/private/file.docx' }],
    })).toThrow()
  })

  it('enforces bundle and variant export choices before writing', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockImplementation(async () => new Response(JSON.stringify({
        ...job,
        job_type: 'training_export',
        payload: {
          task_id: 12,
          variant_id: 3,
          format: 'markdown',
          audience: 'teacher',
        },
      }), { status: 202, headers: { 'content-type': 'application/json' } }))

    await exportsApi.submitTrainingExport(12, {
      variant_id: 3,
      format: 'markdown',
      audience: 'teacher',
    })
    await expect(exportsApi.submitTrainingExport(12, {
      format: 'docx',
      audience: 'teacher',
    })).rejects.toThrow()
    await expect(exportsApi.submitTrainingExport(12, {
      variant_id: 3,
      format: 'docx',
    })).rejects.toThrow()

    expect(fetchMock).toHaveBeenCalledTimes(1)
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
})
