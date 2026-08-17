import { afterEach, describe, expect, it, vi } from 'vitest'

import type { QuestionBankTag } from '../api/question-bank'

const revision = 'a'.repeat(64)

const question = {
  id: 17,
  revision,
  paper_id: 4,
  question_number: '12',
  question_type: '解答题',
  question_text: '已知 x + y = 3，求证……',
  answer_text: '证明过程',
  difficulty: '6',
  typicality: null,
  reason: null,
  needs_review: false,
  criteria_needs_review: false,
  has_images: true,
  needs_image_review: false,
  created_at: '2026-07-18T08:00:00Z',
  updated_at: '2026-07-18T09:00:00Z',
  paper_title: '匿名期末试卷',
  year: '2025',
  province: '广东省',
  city: '深圳市',
  district: '南山区',
  exam_type: '期末',
  grade: '九年级',
  semester: '下学期',
  textbook_version: null,
  tags: [
    { tag_type: 'knowledge_point', tag_value: '二次函数', confidence: 0.92 },
  ],
  asset_urls: ['/api/question-bank/questions/17/assets/0'],
  rich_content: {
    available: true,
    question_block_count: 0,
    answer_block_count: 0,
    question_blocks: [],
    answer_blocks: [],
  },
}

afterEach(() => vi.restoreAllMocks())

describe('question bank API contracts', () => {
  it('accepts the exact public paged-question contract and rejects path fields', async () => {
    const { decodeQuestionListResponse } = await import('../api/question-bank')
    const payload = {
      items: [question],
      total: 1,
      page: 1,
      page_size: 20,
      total_pages: 1,
    }

    expect(decodeQuestionListResponse(payload)).toEqual(payload)
    expect(() => decodeQuestionListResponse({
      ...payload,
      items: [{ ...question, source_file: 'private.docx' }],
    })).toThrow('Invalid question bank list')
  })

  it('keeps rich text and controlled media in the exact detail contract', async () => {
    const { decodeQuestionDetailResponse } = await import('../api/question-bank')
    const payload = {
      ...question,
      page_range: '2-3',
      assets: [{ index: 0, url: '/api/question-bank/questions/17/assets/0' }],
      rich_content: {
        available: true,
        question_block_count: 1,
        answer_block_count: 1,
        question_blocks: [{
          kind: 'paragraph',
          segments: [],
          rows: [],
          text: '题干公式块',
          asset_indexes: [0],
          asset_urls: ['/api/question-bank/questions/17/assets/0'],
        }],
        answer_blocks: [{
          kind: 'paragraph',
          segments: [],
          rows: [],
          text: '答案公式块',
          asset_indexes: [],
          asset_urls: [],
        }],
      },
      previews: [{
        preview_type: 'question',
        status: 'ready',
        page_number: 2,
        bbox: { x0: 1, y0: 2, x1: 300, y1: 400 },
        updated_at: '2026-07-18T09:00:00Z',
        url: '/api/question-bank/questions/17/previews/question',
      }],
    }

    expect(decodeQuestionDetailResponse(payload)).toEqual(payload)
    expect(() => decodeQuestionDetailResponse({
      ...payload,
      rich_content: { ...payload.rich_content, internal_path: 'private.json' },
    })).toThrow('Invalid question bank detail')
  })

  it('serializes the applied server filters without sending empty draft values', async () => {
    const { questionBankApi } = await import('../api/question-bank')
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify({
        items: [question],
        total: 21,
        page: 2,
        page_size: 20,
        total_pages: 2,
      }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ))

    await questionBankApi.listQuestions({
      page: 2,
      pageSize: 20,
      keyword: '  二次函数  ',
      questionTypes: ['解答题', '填空题'],
      paperIds: [4, 9],
      difficultyMin: 4,
      difficultyMax: 7,
      tagStatus: 'untagged',
      sort: 'difficulty_desc',
      years: [''],
    })

    const requested = String(fetchSpy.mock.calls[0]?.[0])
    expect(requested).toContain('/api/question-bank/questions?')
    expect(requested).toContain('page=2')
    expect(requested).toContain('keyword=%E4%BA%8C%E6%AC%A1%E5%87%BD%E6%95%B0')
    expect(requested).toContain('question_types=%E8%A7%A3%E7%AD%94%E9%A2%98')
    expect(requested).toContain('question_types=%E5%A1%AB%E7%A9%BA%E9%A2%98')
    expect(requested).toContain('paper_ids=4')
    expect(requested).toContain('paper_ids=9')
    expect(requested).toContain('difficulty_min=4')
    expect(requested).toContain('difficulty_max=7')
    expect(requested).toContain('tag_status=untagged')
    expect(requested).toContain('sort=difficulty')
    expect(requested).not.toContain('years=')
    expect(requested).not.toContain('criteria_needs_review=')
  })

  it('asks the server for questions whose criteria still need review', async () => {
    const { questionBankApi } = await import('../api/question-bank')
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify({
        items: [question],
        total: 1,
        page: 1,
        page_size: 100,
        total_pages: 1,
      }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ))

    await questionBankApi.listQuestions({
      page: 1,
      pageSize: 100,
      criteriaNeedsReview: true,
    })

    const requested = String(fetchSpy.mock.calls[0]?.[0])
    expect(requested).toContain('criteria_needs_review=true')
  })

  it('accepts only the safe paper summary fields', async () => {
    const { decodeQuestionPaperListResponse } = await import('../api/question-bank')
    const paper = {
      id: 4,
      title: '匿名期末试卷',
      year: '2025',
      province: '广东省',
      city: '深圳市',
      district: '南山区',
      exam_type: '期末',
      grade: '九年级',
      semester: '下学期',
      folder_name: null,
      textbook_version: null,
      curriculum_volume_id: null,
      import_status: 'imported',
      created_at: '2026-07-18T08:00:00Z',
      updated_at: '2026-07-18T09:00:00Z',
      question_count: 20,
      tagged_question_count: 12,
      tagged_any_question_count: 15,
      evidence_question_count: 14,
      criteria_question_count: 13,
      criteria_needs_review_count: 0,
      complete_analysis_count: 10,
      source_type: 'docx',
    }

    expect(decodeQuestionPaperListResponse({ items: [paper], total: 1 })).toEqual({
      items: [paper],
      total: 1,
    })
    expect(() => decodeQuestionPaperListResponse({
      items: [{ ...paper, source_file: 'private.docx' }],
      total: 1,
    })).toThrow('Invalid question bank papers')
  })

  it('sends one version-protected paper metadata update', async () => {
    const { questionBankApi } = await import('../api/question-bank')
    const updated = {
      id: 4,
      title: '0526test2',
      year: '2026',
      province: null,
      city: null,
      district: null,
      exam_type: '阶段练习',
      grade: '七年级',
      semester: '下学期',
      folder_name: '期末复习',
      textbook_version: '北师大版',
      updated_at: '2026-07-29 10:30:00.123456',
    }
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify(updated),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ))

    await expect(questionBankApi.updatePaperMetadata(
      4,
      '2026-07-29 10:00:00',
      {
        title: '  0526test2  ',
        year: '2026',
        province: '',
        city: null,
        district: null,
        exam_type: '阶段练习',
        grade: '七年级',
        semester: '下学期',
        folder_name: '期末复习',
        textbook_version: '北师大版',
      },
    )).resolves.toEqual(updated)

    const [url, init] = fetchSpy.mock.calls[0]!
    expect(String(url)).toBe('/api/question-bank/papers/4')
    expect(init?.method).toBe('PATCH')
    expect(JSON.parse(String(init?.body))).toEqual({
      expected_updated_at: '2026-07-29 10:00:00',
      metadata: {
        title: '0526test2',
        year: '2026',
        province: null,
        city: null,
        district: null,
        exam_type: '阶段练习',
        grade: '七年级',
        semester: '下学期',
        folder_name: '期末复习',
        textbook_version: '北师大版',
      },
    })
  })

  it('lists active papers without exposing retired paper trash mutations', async () => {
    const { questionBankApi } = await import('../api/question-bank')
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({
        items: [],
        total: 0,
      }), { status: 200, headers: { 'content-type': 'application/json' } }),
    )

    await questionBankApi.listPapers()

    expect(String(fetchSpy.mock.calls[0]?.[0])).toBe(
      '/api/question-bank/papers',
    )
    expect('trashPaper' in questionBankApi).toBe(false)
    expect('restorePaper' in questionBankApi).toBe(false)
  })

  it('sends one revision-protected full tag replacement', async () => {
    const { questionBankApi } = await import('../api/question-bank')
    const nextRevision = 'b'.repeat(64)
    const response = {
      question_id: 17,
      revision: nextRevision,
      deleted: false,
      tags: [
        { tag_type: 'knowledge_point', tag_value: '二次函数', confidence: null },
        { tag_type: 'ability', tag_value: '运算能力', confidence: 0.8 },
      ] satisfies QuestionBankTag[],
    }
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify(response),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ))

    await expect(questionBankApi.replaceTags(17, revision, response.tags)).resolves.toEqual(response)

    expect(fetchSpy).toHaveBeenCalledTimes(1)
    const [url, init] = fetchSpy.mock.calls[0]!
    expect(String(url)).toBe('/api/question-bank/questions/17/tags')
    expect(init?.method).toBe('PUT')
    expect(JSON.parse(String(init?.body))).toEqual({
      expected_revision: revision,
      tags: response.tags,
    })
  })

  it('uses the returned delete revision for an explicit restore request', async () => {
    const { questionBankApi } = await import('../api/question-bank')
    const deletedRevision = 'c'.repeat(64)
    const restoredRevision = 'd'.repeat(64)
    const tags = [{ tag_type: 'knowledge_point', tag_value: '二次函数', confidence: null }] satisfies QuestionBankTag[]
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({
        question_id: 17,
        revision: deletedRevision,
        deleted: true,
        tags,
      }), { status: 200, headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        question_id: 17,
        revision: restoredRevision,
        deleted: false,
        tags,
      }), { status: 200, headers: { 'content-type': 'application/json' } }))

    const deleted = await questionBankApi.softDelete(17, revision)
    await questionBankApi.restore(17, deleted.revision)

    expect(String(fetchSpy.mock.calls[0]?.[0])).toBe('/api/question-bank/questions/17')
    expect(fetchSpy.mock.calls[0]?.[1]?.method).toBe('DELETE')
    expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toEqual({
      expected_revision: revision,
    })
    expect(String(fetchSpy.mock.calls[1]?.[0])).toBe('/api/question-bank/questions/17/restore')
    expect(JSON.parse(String(fetchSpy.mock.calls[1]?.[1]?.body))).toEqual({
      expected_revision: deletedRevision,
    })
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

  it('rejects unsupported, empty and oversized import files before any request', async () => {
    const { questionBankApi } = await import('../api/question-bank')
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    const unsupported = new File(['test'], 'anonymous.txt', { type: 'text/plain' })
    const empty = new File([], 'anonymous.docx')
    const oversized = {
      name: 'anonymous.pdf',
      size: 200 * 1024 * 1024 + 1,
      type: 'application/pdf',
    } as File

    expect(() => questionBankApi.stageImport(unsupported)).toThrow(
      'Invalid question import file',
    )
    expect(() => questionBankApi.stageImport(empty)).toThrow(
      'Invalid question import file',
    )
    expect(() => questionBankApi.stageImport(oversized)).toThrow(
      'Invalid question import file',
    )
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('uses dedicated tagging and retry endpoints with deduplicated question ids', async () => {
    const { questionBankApi } = await import('../api/question-bank')
    const queued = (id: number, jobType: string) => ({
      id,
      job_type: jobType,
      payload: {},
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
    })
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify(queued(41, 'tagging_sync')), {
        status: 202,
        headers: { 'content-type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(queued(42, 'tagging_sync')), {
        status: 202,
        headers: { 'content-type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(queued(43, 'question_import')), {
        status: 202,
        headers: { 'content-type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(queued(44, 'tagging_sync')), {
        status: 202,
        headers: { 'content-type': 'application/json' },
      }))

    await questionBankApi.submitTagging([17, 17, 18], 'bnu24-math-g7-lower')
    await questionBankApi.retryTagging(41, [18, 18])
    await questionBankApi.retryImport(31)
    await questionBankApi.submitTagging(
      [17, 18], 'bnu24-math-g7-lower', undefined, undefined, true, 'a'.repeat(32),
    )

    expect(String(fetchSpy.mock.calls[0]?.[0])).toBe('/api/question-bank/tagging-jobs')
    expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toEqual({
      question_ids: [17, 18],
      curriculum_volume_id: 'bnu24-math-g7-lower',
    })
    expect(String(fetchSpy.mock.calls[1]?.[0])).toBe(
      '/api/question-bank/tagging-jobs/41/retry',
    )
    expect(JSON.parse(String(fetchSpy.mock.calls[1]?.[1]?.body))).toEqual({
      question_ids: [18],
    })
    expect(String(fetchSpy.mock.calls[2]?.[0])).toBe(
      '/api/question-bank/question-import-jobs/31/retry',
    )
    expect(JSON.parse(String(fetchSpy.mock.calls[3]?.[1]?.body))).toEqual({
      question_ids: [17, 18],
      curriculum_volume_id: 'bnu24-math-g7-lower',
      force_retag: true,
      client_request_token: 'a'.repeat(32),
    })
  })

  it('projects partial failures into a spreadsheet-safe CSV without internal fields', async () => {
    const { questionJobFailures, questionJobFailuresCsv } = await import('../api/question-bank')
    const result = {
      outcome: 'partial',
      failures: [
        {
          question_id: 17,
          category: 'model_timeout',
          message: '=HYPERLINK("unsafe")',
          internal_path: 'D:\\private\\source.docx',
        },
        {
          question_id: 18,
          category: 'invalid_result',
          message: '结果缺少核心标签',
        },
      ],
    }

    expect(questionJobFailures(result)).toEqual([
      {
        question_id: 17,
        category: 'model_timeout',
        message: '=HYPERLINK("unsafe")',
      },
      {
        question_id: 18,
        category: 'invalid_result',
        message: '结果缺少核心标签',
      },
    ])
    const csv = questionJobFailuresCsv(result)
    expect(csv).toContain('题目ID,失败分类,说明')
    expect(csv).toContain('17,model_timeout,"\t=HYPERLINK(""unsafe"")"')
    expect(csv).not.toContain('internal_path')
    expect(csv).not.toContain('private')
  })

  it('derives the exact retry question ids from either failures or the public source payload', async () => {
    const { questionJobRetryIds } = await import('../api/question-bank')
    const source = {
      id: 41,
      job_type: 'tagging_sync',
      payload: { question_ids: [17, 18, 18] },
      result: {},
      status: 'failed',
      progress: 1,
      stage: 'failed',
      detail: '',
      error: 'failed',
      cancel_requested: false,
      created_at: '2026-07-18T10:00:00Z',
      started_at: '2026-07-18T10:00:01Z',
      updated_at: '2026-07-18T10:00:02Z',
      finished_at: '2026-07-18T10:00:02Z',
    } as const

    expect(questionJobRetryIds(source)).toEqual([17, 18])
    expect(questionJobRetryIds({
      ...source,
      status: 'succeeded',
      result: {
        failures: [{
          question_id: 18,
          category: 'model_timeout',
          message: 'timeout',
        }],
      },
    })).toEqual([18])
  })
})
