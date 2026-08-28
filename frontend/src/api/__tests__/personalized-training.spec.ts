import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  decodePersonalizedPaperInstance,
  decodePersonalizedRecommendationDraft,
  trainingApi,
} from '../training'

const draftPayload = {
  draft_id: 'd'.repeat(64),
  status: 'draft',
  revision: 1,
  result_version: 'a'.repeat(64),
  engine_version: 'personalized-recommendation-v1',
  source_version: 'b'.repeat(64),
  config: {
    question_count: 8,
    expected_minutes: 45,
  },
  students: [{
    student_id: 'SYN-S01',
    student_code: 'S01',
    student_name: '合成学生',
    class_id: 'SYN-C01',
    selection_mode: 'mastery_targeted',
    targets: [{
      stable_key: 'kp_alg_linear_equation',
      display_name: '一元一次方程',
    }],
    items: [{
      item_id: 'item-1',
      item_order: 1,
      slot: 1,
      question_id: 31,
      question_number: '3',
      question_text: '解方程 3x + 1 = 7。',
      stage: 'direct',
      target: {
        stable_key: 'kp_alg_linear_equation',
      },
      matched_key: 'kp_alg_linear_equation',
      matched_name: '一元一次方程',
      relation: null,
      criterion_version_id: 'c'.repeat(64),
      criterion_point_count: 3,
      difficulty: 5,
      estimated_minutes: 6,
      source_paper: '合成题源',
      reason: '直接巩固一元一次方程。',
      locked: false,
      replacement_history: [],
    }],
    shortages: [],
    warnings: [],
    estimated_minutes: 6,
  }],
  warnings: [],
  history: [{
    action: 'created',
    revision: 1,
  }],
} as const

const paperPayload = {
  paper_instance_id: 'e'.repeat(64),
  paper_batch_id: 'f'.repeat(64),
  draft_id: draftPayload.draft_id,
  draft_revision: 1,
  student_id: 'SYN-S01',
  student_code: 'S01',
  student_name: '合成学生',
  class_id: 'SYN-C01',
  series_version: 1,
  status: 'review_pending',
  revision: 1,
  layout_version: 'personalized-paper-school-a4-v1',
  budget: {
    version: 'whole-paper-context-budget-v1',
    status: 'ready',
    context_window_tokens: 32768,
    question_count: 1,
    criterion_point_count: 3,
    image_count: 0,
    page_count: 1,
    page_count_is_estimate: true,
    estimated_input_tokens: 3200,
    estimated_output_tokens: 1340,
    estimated_total_tokens: 4540,
    limits: { questions: 12, criterion_points: 120, images: 48, pages: 20 },
    blockers: [],
  },
  question_count: 1,
  criterion_point_count: 3,
  items: [],
  pages: [],
  review_docx_sha256: '1'.repeat(64),
  reviewed_docx_sha256: null,
  frozen_pdf_sha256: null,
  downloads: {
    review_docx: `/api/training/paper-instances/${'e'.repeat(64)}/files/review-docx`,
    reviewed_docx: null,
    frozen_pdf: null,
  },
  error_code: null,
  created_at: '2026-07-30 08:00:00',
  frozen_at: null,
} as const

const scanBatchPayload = {
  batch_id: '9'.repeat(64),
  paper_batch_id: 'f'.repeat(64),
  status: 'manual_review',
  revision: 1,
  duplicate_upload: false,
  submissions: [{
    submission_id: '8'.repeat(64),
    paper_instance_id: 'e'.repeat(64),
    student_id: 'SYN-S01',
    student_code: 'S01',
    student_name: '合成学生',
    class_id: 'SYN-C01',
    series_version: 1,
    status: 'manual_review',
    revision: 1,
    expected_total_pages: 2,
    missing_pages: [2],
    issue_codes: [],
    assessment_started: false,
  }],
  pages: [{
    scan_page_id: '7'.repeat(64),
    upload_id: '6'.repeat(64),
    upload_page_number: 1,
    submission_id: '8'.repeat(64),
    paper_instance_id: 'e'.repeat(64),
    page_number: 1,
    total_pages: 2,
    issue_code: null,
    state: 'assigned',
    rotation_degrees: 0,
    preview_url: `/api/training/scan-batches/${'9'.repeat(64)}/pages/${'7'.repeat(64)}/preview`,
  }],
  candidates: [{
    paper_instance_id: 'e'.repeat(64),
    student_id: 'SYN-S01',
    student_code: 'S01',
    student_name: '合成学生',
    series_version: 1,
    total_pages: 2,
  }],
  history: [],
  created_at: '2026-07-30T08:00:00+00:00',
  updated_at: '2026-07-30T08:00:00+00:00',
} as const

afterEach(() => {
  vi.restoreAllMocks()
})

describe('personalized training API', () => {
  it('decodes auditable recommendation items and rejects storage fields', () => {
    const decoded = decodePersonalizedRecommendationDraft(draftPayload)

    expect(decoded.students[0]?.items[0]).toMatchObject({
      question_id: 31,
      question_text: '解方程 3x + 1 = 7。',
      criterion_point_count: 3,
      matched_key: 'kp_alg_linear_equation',
    })
    // 旧草稿没有 question_text，仍按可选字段解码。
    const legacyItem = { ...draftPayload.students[0]!.items[0]! } as Record<string, unknown>
    delete legacyItem.question_text
    expect(decodePersonalizedRecommendationDraft({
      ...draftPayload,
      students: [{ ...draftPayload.students[0]!, items: [legacyItem] }],
    }).students[0]?.items[0]?.question_text).toBeUndefined()
    expect(() => decodePersonalizedRecommendationDraft({
      ...draftPayload,
      output_path: 'C:\\private\\draft.json',
    })).toThrow('Path-like response key')
  })

  it('posts create and teacher edit commands to isolated draft endpoints', async () => {
    const updated = {
      ...draftPayload,
      revision: 2,
      students: [{
        ...draftPayload.students[0],
        items: [{
          ...draftPayload.students[0].items[0],
          locked: true,
        }],
      }],
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify(draftPayload), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(updated), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }))

    await trainingApi.createPersonalizedDraft({
      request_token: '1'.repeat(32),
      scope: { mode: 'student', student_ids: ['SYN-S01'] },
      exam_scope: { mode: 'current', session_ids: [7] },
      question_count: 8,
      expected_minutes: 45,
      difficulty_min: 2,
      difficulty_max: 8,
      stage_ratios: {
        direct: 0.6,
        prerequisite: 0.3,
        transfer: 0.1,
      },
      target_names: ['一元一次方程'],
      exclude_current_exam_originals: true,
    })
    await trainingApi.editPersonalizedDraft(draftPayload.draft_id, {
      request_token: '2'.repeat(32),
      expected_revision: 1,
      action: 'lock',
      student_id: 'SYN-S01',
      item_id: 'item-1',
      reason: '保留课堂讲过的题型',
    })

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/training/personalized-drafts',
      `/api/training/personalized-drafts/${draftPayload.draft_id}/edits`,
    ])
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toMatchObject({
      target_names: ['一元一次方程'],
      expected_minutes: 45,
    })
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toMatchObject({
      expected_revision: 1,
      action: 'lock',
      reason: '保留课堂讲过的题型',
    })
  })

  it('decodes paper versions and sends an immutable create command', async () => {
    expect(decodePersonalizedPaperInstance(paperPayload)).toMatchObject({
      status: 'review_pending',
      series_version: 1,
      question_count: 1,
    })
    expect(() => decodePersonalizedPaperInstance({
      ...paperPayload,
      output_path: 'C:\\private\\paper.docx',
    })).toThrow('Path-like response key')

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(paperPayload), {
        status: 201,
        headers: { 'content-type': 'application/json' },
      }),
    )
    await trainingApi.createPaperInstance(draftPayload.draft_id, {
      operation_token: '3'.repeat(32),
      expected_draft_revision: 1,
      student_id: 'SYN-S01',
      context_window_tokens: 32768,
    })

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `/api/training/personalized-drafts/${draftPayload.draft_id}/paper-instances`,
    )
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      operation_token: '3'.repeat(32),
      expected_draft_revision: 1,
      student_id: 'SYN-S01',
      context_window_tokens: 32768,
    })
  })

  it('uses isolated scan batch and revision-protected page endpoints', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify(scanBatchPayload), {
        status: 201,
        headers: { 'content-type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        ...scanBatchPayload,
        revision: 2,
      }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }))

    const batch = await trainingApi.createTrainingScanBatch(
      [paperPayload.paper_instance_id],
      '4'.repeat(32),
    )
    await trainingApi.resolveTrainingScanPage(
      batch,
      batch.pages[0]!,
      {
        operation_token: '5'.repeat(32),
        action: 'replace',
        paper_instance_id: paperPayload.paper_instance_id,
        page_number: 1,
      },
    )

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/training/scan-batches',
      `/api/training/scan-batches/${scanBatchPayload.batch_id}`
      + `/pages/${scanBatchPayload.pages[0].scan_page_id}/resolve`,
    ])
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      operation_token: '5'.repeat(32),
      action: 'replace',
      paper_instance_id: paperPayload.paper_instance_id,
      page_number: 1,
      expected_revision: 1,
    })
  })
})
