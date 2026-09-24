import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../client'
import {
  confirmReviewItem,
  confirmReviewItems,
  fetchReviewItems,
  fetchReviewQuestions,
  fetchReviewRubric,
  isReviewConfirmResponse,
  isReviewItemListResponse,
  isReviewQuestionListResponse,
  isReviewRubricSection,
} from '../review'

const media = {
  crop_url: '/api/sessions/7/results/11/details/21/crop',
  original_front_url: '/api/sessions/7/results/11/pages/front',
  original_back_url: '/api/sessions/7/results/11/pages/back',
  annotated_front_url: '/api/sessions/7/results/11/pages/front?variant=annotated',
  annotated_back_url: '/api/sessions/7/results/11/pages/back?variant=annotated',
}

const rubricSection = {
  question_id: 'Q11(P3)',
  parent_question_id: 'Q11',
  question_type: 'comprehensive',
  max_score: 7,
  knowledge_labels: ['三角形'],
  points: [{
    part_id: 'Q11(P3)',
    part_label: '第 3 问',
    step_id: 'S3',
    core_goal: '求出∠DCE的度数',
    score: 7,
    standard_answer: '42°',
    accepted_answers: ['42 度'],
    match_rule: '按关键步骤评分',
    required_elements: ['推导∠BCE的表达式', '求出∠DCE的度数'],
    deduction_rules: ['漏写结论扣 1 分'],
    answer_only_max_score: 1,
    require_final_answer: true,
    final_answer_rule: '未写最终结论最多得 1 分',
  }],
}

afterEach(() => vi.restoreAllMocks())

describe('review API contract', () => {
  it('accepts the current question and item contracts', () => {
    expect(isReviewQuestionListResponse({ items: [{ question_id: 'Q1', question_type: null, total_count: 2, needs_review_count: 1, max_score: 5 }], total: 1 })).toBe(true)
    expect(isReviewItemListResponse({ items: [{ session_id: 7, result_id: 11, detail_id: 21, question_id: 'Q1', student_code: 'S001', student_name: '测试学生', class_name: '七年级一班', score_awarded: 3, max_score: 5, deduction_reason: '步骤不完整', error_category: '需复核', error_summary: null, confidence_score: 72, needs_review: true, candidate_scores: [], metadata: {}, media }], total: 1 })).toBe(true)
  })

  it.each([
    { items: [], total: -1 },
    { items: [{ question_id: 'Q1', total_count: 1, needs_review_count: 2, max_score: 5 }], total: 1 },
    { items: [{ session_id: 7, result_id: 11, detail_id: 21, question_id: 'Q1', student_name: '学生', score_awarded: 3, max_score: 5, confidence_score: Number.NaN, needs_review: true, candidate_scores: [], metadata: {}, media }], total: 1 },
    { items: [{ session_id: 7, result_id: 11, detail_id: 21, question_id: 'Q1', student_name: '学生', score_awarded: 3, max_score: 5, confidence_score: null, needs_review: true, candidate_scores: [], metadata: {}, media: { ...media, crop_url: 'https://outside.invalid/private' } }], total: 1 },
  ])('rejects malformed or unsafe payload %#', (payload) => {
    expect(isReviewQuestionListResponse(payload) || isReviewItemListResponse(payload)).toBe(false)
  })

  it('uses the shared client, encodes the question, and defaults to pending items', async () => {
    const request = vi.spyOn(apiClient, 'request')
      .mockResolvedValueOnce({ items: [], total: 0 })
      .mockResolvedValueOnce({ items: [], total: 0 })
      .mockResolvedValueOnce({ items: [], total: 0 })
    await fetchReviewQuestions(7)
    await fetchReviewItems(7, 'Q 1/甲')
    await fetchReviewItems(7, 'Q 1/甲', { needsReviewOnly: false })
    expect(request.mock.calls[0]?.[0]).toBe('/api/sessions/7/review/questions')
    expect(request.mock.calls[1]?.[0]).toBe('/api/sessions/7/review/questions/Q%201%2F%E7%94%B2/items?needs_review_only=true')
    expect(request.mock.calls[2]?.[0]).toBe('/api/sessions/7/review/questions/Q%201%2F%E7%94%B2/items?needs_review_only=false')
  })

  it('accepts only the exact read-only rubric contract', () => {
    expect(isReviewRubricSection(rubricSection)).toBe(true)
    expect(isReviewRubricSection({
      ...rubricSection,
      points: [{ ...rubricSection.points[0], score: -1 }],
    })).toBe(false)
    expect(isReviewRubricSection({
      ...rubricSection,
      points: [{ ...rubricSection.points[0], unexpected: true }],
    })).toBe(false)
    expect(isReviewRubricSection({
      ...rubricSection,
      parent_question_id: '',
    })).toBe(false)
  })

  it('uses the encoded review rubric GET and supports one atomic multi-item confirm POST', async () => {
    const request = vi.spyOn(apiClient, 'request')
      .mockResolvedValueOnce(rubricSection)
      .mockResolvedValueOnce({ updated_details: 2, updated_results: 2, annotation_outcomes: [] })

    await fetchReviewRubric(7, 'Q 1/甲')
    await confirmReviewItems(7, 'Q 1/甲', [
      {
        result_id: 11,
        detail_id: 21,
        score_awarded: 8.5,
        deduction_reason: '步骤二符号错误',
      },
      { result_id: 12, detail_id: 22, score_awarded: 7 },
    ])

    expect(request.mock.calls[0]?.[0]).toBe(
      '/api/sessions/7/review/questions/Q%201%2F%E7%94%B2/rubric',
    )
    expect(request.mock.calls[1]?.[0]).toBe('/api/sessions/7/review/questions/Q%201%2F%E7%94%B2/confirm')
    expect(request.mock.calls[1]?.[1]).toMatchObject({
      method: 'POST',
      body: {
        items: [{
          result_id: 11,
          detail_id: 21,
          score_awarded: 8.5,
          deduction_reason: '步骤二符号错误',
        }, {
          result_id: 12,
          detail_id: 22,
          score_awarded: 7,
        }],
      },
    })
    expect(request.mock.calls[1]?.[1]?.body).not.toHaveProperty('items.0.error_category')
    expect(request.mock.calls[1]?.[1]?.body).not.toHaveProperty('items.0.error_summary')
  })

  it('accepts a null rubric and rejects malformed rubric responses', async () => {
    const request = vi.spyOn(apiClient, 'request')
    request.mockImplementationOnce(async (_path, options) => options.decode(null))

    await expect(fetchReviewRubric(7, 'Q1')).resolves.toBeNull()

    request.mockImplementationOnce(async (_path, options) => options.decode({
      ...rubricSection,
      points: [{ ...rubricSection.points[0], score: Number.NaN }],
    }))
    await expect(fetchReviewRubric(7, 'Q1')).rejects.toThrow('invalid review rubric')
  })

  it('keeps the single-item wrapper and rejects an empty batch before requesting', async () => {
    const request = vi.spyOn(apiClient, 'request')
      .mockResolvedValueOnce({ updated_details: 1, updated_results: 1, annotation_outcomes: [] })

    await confirmReviewItem(7, 'Q1', { result_id: 11, detail_id: 21, score_awarded: 3 })
    await expect(confirmReviewItems(7, 'Q1', [])).rejects.toThrow('review confirmation items are required')

    expect(request).toHaveBeenCalledTimes(1)
    expect(request.mock.calls[0]?.[1]?.body).toEqual({
      annotation_mode: 'on_demand',
      items: [{ result_id: 11, detail_id: 21, score_awarded: 3 }],
    })
  })

  it.each([
    { updated_details: 1, updated_results: 1, annotation_outcomes: [{ result_id: 11, status: 'unknown' }] },
    { updated_details: Number.NaN, updated_results: 1, annotation_outcomes: [] },
    { updated_details: 1, updated_results: 1, annotation_outcomes: 'invalid' },
  ])('rejects malformed confirm response %#', (payload) => {
    expect(isReviewConfirmResponse(payload)).toBe(false)
  })
})
