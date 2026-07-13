import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../client'
import {
  fetchReviewItems,
  fetchReviewQuestions,
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
})
