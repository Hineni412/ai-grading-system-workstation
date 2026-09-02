import { beforeEach, describe, expect, it, vi } from 'vitest'

import { teachingPrepWorkbenchApi } from './workbench'

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

beforeEach(() => vi.restoreAllMocks())

describe('teaching preparation workbench API', () => {
  it('loads all lesson readiness rows with one semester request', async () => {
    const semesterId = 's'.repeat(32)
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({
      items: [{
        lesson_node_id: 'l'.repeat(32),
        title: '一元一次方程',
        sort_order: 1,
        duration_minutes: 45,
        manual_progress: 'preparing',
        manual_progress_revision: 2,
        preparation_stage: 'plan',
        next_action: '确定课堂方案',
        blockers: [],
        latest: {
          resource_pack_id: 'p'.repeat(32),
          lesson_draft_id: null,
          slide_plan_id: null,
          pptx_version_id: null,
          pptx_revision: null,
          up_class_package_id: null,
        },
      }],
    }))

    await expect(teachingPrepWorkbenchApi.lessonStatuses(semesterId))
      .resolves.toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      `/api/teaching-prep/semesters/${semesterId}/lesson-preparation-statuses`,
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('lists question bank sections for a volume', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({
      volume_id: 'bnu24-math-g8-upper',
      items: [{
        section_id: 'bnu24-math-g8-upper-c04-s03',
        section_name: '八年级上册｜第四章 一次函数｜3 一次函数的应用',
        chapter_id: 'bnu24-math-g8-upper-c04',
        chapter_name: '八年级上册｜第四章 一次函数',
        question_count: 42,
      }],
    }))

    await expect(teachingPrepWorkbenchApi.questionBankSections('bnu24-math-g8-upper'))
      .resolves.toMatchObject([{ section_id: 'bnu24-math-g8-upper-c04-s03', question_count: 42 }])
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      '/api/teaching-prep/question-bank-sections?volume_id=bnu24-math-g8-upper',
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('posts a question selection preview with exclusions and drops server image paths', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({
      request: {
        volume_id: 'bnu24-math-g8-upper',
        section_ids: ['bnu24-math-g8-upper-c04-s03'],
        difficulty_max: 5,
        stem_max_chars: 220,
        limit: 12,
        max_per_method: 2,
        exclude_question_ids: [7],
      },
      stats: {
        candidate_total: 40,
        dropped_stem_length: 3,
        dropped_excluded: 1,
        dropped_duplicate: 2,
        dropped_method_balance: 4,
        selected: 1,
      },
      items: [{
        question_id: 101,
        question_type: '解答题',
        stem: '某水库水位……',
        answer_text: '答：……',
        difficulty: 4,
        frequency_score: 0.873,
        frequency: { midterm: 0.5, final: 0.3, zhongkao: 0.073 },
        method: '函数建模',
        knowledge_points: ['一次函数的应用'],
        has_images: true,
        image_paths: ['C:\\private\\q101.png'],
        source_file: 'C:\\private\\bank.docx',
        answer_needs_review: false,
        selection_reason: {
          frequency: '综合考频 0.873',
          difficulty: '难度 4',
          method: '函数建模',
        },
      }],
      method_distribution: { 函数建模: 1 },
    }))

    const result = await teachingPrepWorkbenchApi.questionSelectionPreview({
      volume_id: 'bnu24-math-g8-upper',
      section_ids: ['bnu24-math-g8-upper-c04-s03'],
      exclude_question_ids: [7],
    })

    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toMatchObject({
      volume_id: 'bnu24-math-g8-upper',
      section_ids: ['bnu24-math-g8-upper-c04-s03'],
      exclude_question_ids: [7],
    })
    expect(result.items).toHaveLength(1)
    expect(result.items[0]).not.toHaveProperty('image_paths')
    expect(result.items[0]).not.toHaveProperty('source_file')
    expect(result.items[0]?.selection_reason.frequency).toBe('综合考频 0.873')
  })

  it('executes a slide plan locally with an explicit confirmation token', async () => {
    const planId = 'p'.repeat(32)
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({
      id: 'o'.repeat(32),
      lesson_node_id: 'l'.repeat(32),
      version_number: 1,
      status: 'completed',
      output_filename: 'adapted.pptx',
      source_file_name: 'source.pptx',
      source_page_count: 24,
      final_page_count: 18,
      lesson_kind: 'review',
      audit: {
        superscript_subscript: { scanned_runs: 300, finding_count: 0, findings: [], passed: true },
        page_budget: { final_page_count: 18, limit: 18, over_limit: false, suggestion: '页数在课时容量红线内。' },
        question_pages: { checked: 2, pages: { 5: 101, 9: 102 }, problem_count: 0, problems: [], passed: true },
        passed: true,
      },
      inserted_question_pages: [{ question_id: 101, final_position: 5 }],
      worksheet_filename: null,
      created_at: '2026-08-03T00:00:00Z',
    }))

    const output = await teachingPrepWorkbenchApi.executeSlidePlanLocal(planId, 'execute-token-0001')

    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      `/api/teaching-prep/slide-plans/${planId}/execute-local`,
      expect.objectContaining({ method: 'POST' }),
    )
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      request_token: 'execute-token-0001',
      confirmed: true,
    })
    expect(output.audit.passed).toBe(true)
    expect(output.audit.question_pages.pages).toEqual({ 5: 101, 9: 102 })
  })

  it('rejects a pptx output row that discloses a server path', async () => {
    const row = {
      id: 'o'.repeat(32),
      lesson_node_id: 'l'.repeat(32),
      version_number: 1,
      status: 'completed',
      output_filename: 'adapted.pptx',
      source_file_name: 'source.pptx',
      source_page_count: 24,
      final_page_count: 18,
      lesson_kind: 'review',
      audit: {},
      inserted_question_pages: [],
      worksheet_filename: null,
      created_at: '2026-08-03T00:00:00Z',
      local_file_path: 'C:\\private\\outputs\\adapted.pptx',
    }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({
      lesson_node_id: 'l'.repeat(32),
      items: [row],
    }))

    await expect(teachingPrepWorkbenchApi.listPptxOutputs('l'.repeat(32)))
      .rejects.toMatchObject({ kind: 'contract', code: 'invalid_success_contract' })
  })

  it('creates a worksheet for an output with explicit confirmation', async () => {
    const outputId = 'o'.repeat(32)
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({
      id: outputId,
      lesson_node_id: 'l'.repeat(32),
      version_number: 1,
      status: 'completed',
      output_filename: 'adapted.pptx',
      source_file_name: 'source.pptx',
      source_page_count: 24,
      final_page_count: 18,
      lesson_kind: 'review',
      audit: { passed: true },
      inserted_question_pages: [],
      worksheet_filename: '学案.docx',
      created_at: '2026-08-03T00:00:00Z',
    }))

    const output = await teachingPrepWorkbenchApi.createWorksheet(outputId, 'worksheet-token-01')

    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      `/api/teaching-prep/pptx-outputs/${outputId}/worksheet`,
      expect.objectContaining({ method: 'POST' }),
    )
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      request_token: 'worksheet-token-01',
      confirmed: true,
    })
    expect(output.worksheet_filename).toBe('学案.docx')
  })
})
