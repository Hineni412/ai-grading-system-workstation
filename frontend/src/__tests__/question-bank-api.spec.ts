import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => vi.restoreAllMocks())

describe('question bank API contracts', () => {

  it('passes skill filters to lists and facets and validates the index', async () => {
    const { questionBankApi } = await import('../api/question-bank')
    const response = { graph_release_id: 'kgr_TEST', curriculum_volume_id: 'bnu24-math-g8-upper',
      model_calls: 0, question_count: 0, unlinked: { no_usable_evidence: 0, no_skill_link: 0 }, chapters: [] }
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(response), {
      status: 200, headers: { 'content-type': 'application/json' },
    }))
    await expect(questionBankApi.skillIndex('bnu24-math-g8-upper')).resolves.toEqual(response)
    expect(String(fetchSpy.mock.calls[0]?.[0])).toContain('/skill-index?curriculum_volume_id=bnu24-math-g8-upper')
    fetchSpy.mockImplementation(async () => new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } }))
    await expect(questionBankApi.listQuestions({ skillKeys: ['sk_TEST'], skillUnlinked: true, includeSkills: true })).rejects.toThrow()
    expect(String(fetchSpy.mock.calls[1]?.[0])).toContain('skill_keys=sk_TEST')
    expect(String(fetchSpy.mock.calls[1]?.[0])).toContain('skill_unlinked=true')
    expect(String(fetchSpy.mock.calls[1]?.[0])).toContain('include_skills=true')
    await expect(questionBankApi.listFacets({ skillKeys: ['sk_TEST'], includeSkills: true })).rejects.toThrow()
    expect(String(fetchSpy.mock.calls[2]?.[0])).toContain('skill_keys=sk_TEST')
    expect(String(fetchSpy.mock.calls[2]?.[0])).not.toContain('include_skills')
  })

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
