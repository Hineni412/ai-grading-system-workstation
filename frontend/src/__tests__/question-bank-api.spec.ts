import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => vi.restoreAllMocks())

describe('question bank API contracts', () => {

  it('stages one controlled file before creating and submitting its import request', async () => {
    const { questionBankApi } = await import('../api/question-bank')
    const uploadId = 'e'.repeat(32)
    const requestId = 'f'.repeat(32)
    const job = {
      id: 31,
      job_type: 'question_import',
      payload: { request_id: requestId },
      result: {},
      status: 'queued',
      progress: 0,
      stage: '',
      detail: '',
      error: null,
      cancel_requested: false,
      created_at: '2026-07-18T10:00:00Z',
      started_at: null,
      updated_at: '2026-07-18T10:00:00Z',
      finished_at: null,
    }
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({
        upload_id: uploadId,
        filename: '匿名 期末卷.docx',
        suffix: '.docx',
        size: 4,
        sha256: '1'.repeat(64),
      }), { status: 201, headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        request_id: requestId,
        upload_id: uploadId,
        filename: '匿名 期末卷.docx',
        size: 4,
        sha256: '1'.repeat(64),
        status: 'pending',
      }), { status: 201, headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(job), {
        status: 202,
        headers: { 'content-type': 'application/json' },
      }))
    const file = new File(['test'], '匿名 期末卷.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })

    const upload = await questionBankApi.stageImport(file)
    const request = await questionBankApi.createImportRequest(upload.upload_id)
    await expect(questionBankApi.submitImportJob(request.request_id)).resolves.toEqual(job)

    expect(String(fetchSpy.mock.calls[0]?.[0])).toContain(
      '/api/question-bank/import-uploads?filename=%E5%8C%BF%E5%90%8D+%E6%9C%9F%E6%9C%AB%E5%8D%B7.docx',
    )
    expect(fetchSpy.mock.calls[0]?.[1]?.body).toBe(file)
    expect(JSON.parse(String(fetchSpy.mock.calls[1]?.[1]?.body))).toEqual({
      upload_id: uploadId,
    })
    expect(String(fetchSpy.mock.calls[2]?.[0])).toBe(
      `/api/question-bank/import-requests/${requestId}/jobs`,
    )
  })

})
