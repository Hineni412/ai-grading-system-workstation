import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../client'
import {
  confirmReviewItem,
  extractReviewRubricSection,
  fetchReviewItems,
  fetchReviewQuestions,
  fetchReviewRubric,
  isReviewConfirmResponse,
  isReviewItemListResponse,
  isReviewQuestionListResponse,
} from '../review'

const media = {
  crop_url: '/api/sessions/7/results/11/details/21/crop',
  original_front_url: '/api/sessions/7/results/11/pages/front',
  original_back_url: '/api/sessions/7/results/11/pages/back',
  annotated_front_url: '/api/sessions/7/results/11/pages/front?variant=annotated',
  annotated_back_url: '/api/sessions/7/results/11/pages/back?variant=annotated',
}

afterEach(() => vi.restoreAllMocks())

describe('review API contract', () => {
  it('accepts the current question and item contracts', () => {
    expect(isReviewQuestionListResponse({ items: [{ question_id: 'Q1', total_count: 2, needs_review_count: 1, max_score: 5 }], total: 1 })).toBe(true)
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

  it('uses the shared client, encodes the question, and requests all items', async () => {
    const request = vi.spyOn(apiClient, 'request')
      .mockResolvedValueOnce({ items: [], total: 0 })
      .mockResolvedValueOnce({ items: [], total: 0 })
    await fetchReviewQuestions(7)
    await fetchReviewItems(7, 'Q 1/甲')
    expect(request.mock.calls[0]?.[0]).toBe('/api/sessions/7/review/questions')
    expect(request.mock.calls[1]?.[0]).toBe('/api/sessions/7/review/questions/Q%201%2F%E7%94%B2/items?needs_review_only=false')
  })

  it('extracts the current question and part rubric without inventing fields', () => {
    const rubric = {
      questions: [{
        question_id: 'Q1',
        question_type: 'comprehensive',
        max_score: 10,
        knowledge_points: [{ knowledge_id: 'K1', knowledge_name: '一次函数' }],
        parts: [{
          part_id: 'Q1-P1',
          part_score: 4,
          steps: [{ step_id: 'S1', step_score: 4, core_goal: '列出正确关系式', required_elements: ['变量定义', '等量关系'] }],
        }],
      }],
    }

    expect(extractReviewRubricSection(rubric, 'Q1-P1')).toEqual({
      questionId: 'Q1-P1',
      parentQuestionId: 'Q1',
      title: 'Q1-P1',
      maxScore: 4,
      questionType: 'comprehensive',
      knowledgeLabels: ['一次函数'],
      lines: [
        { label: '列出正确关系式', score: 4 },
        { label: '变量定义', score: null },
        { label: '等量关系', score: null },
      ],
    })
    expect(extractReviewRubricSection({ questions: 'not-an-array' }, 'Q1')).toBeNull()
  })

  it('uses the config GET and existing single-item confirm POST', async () => {
    const request = vi.spyOn(apiClient, 'request')
      .mockResolvedValueOnce({ questions: [] })
      .mockResolvedValueOnce({ updated_details: 1, updated_results: 1, annotation_outcomes: [] })

    await fetchReviewRubric(7, 'Q 1/甲')
    await confirmReviewItem(7, 'Q 1/甲', {
      result_id: 11,
      detail_id: 21,
      score_awarded: 8.5,
      deduction_reason: '步骤二符号错误',
    })

    expect(request.mock.calls[0]?.[0]).toBe('/api/sessions/7/config')
    expect(request.mock.calls[1]?.[0]).toBe('/api/sessions/7/review/questions/Q%201%2F%E7%94%B2/confirm')
    expect(request.mock.calls[1]?.[1]).toMatchObject({
      method: 'POST',
      body: {
        items: [{
          result_id: 11,
          detail_id: 21,
          score_awarded: 8.5,
          deduction_reason: '步骤二符号错误',
        }],
      },
    })
    expect(request.mock.calls[1]?.[1]?.body).not.toHaveProperty('items.0.error_category')
    expect(request.mock.calls[1]?.[1]?.body).not.toHaveProperty('items.0.error_summary')
  })

  it.each([
    { updated_details: 1, updated_results: 1, annotation_outcomes: [{ result_id: 11, status: 'unknown' }] },
    { updated_details: Number.NaN, updated_results: 1, annotation_outcomes: [] },
    { updated_details: 1, updated_results: 1, annotation_outcomes: 'invalid' },
  ])('rejects malformed confirm response %#', (payload) => {
    expect(isReviewConfirmResponse(payload)).toBe(false)
  })
})
