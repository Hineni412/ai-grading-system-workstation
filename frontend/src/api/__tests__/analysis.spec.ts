
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  decodeQuestionAnalysisResponse,
  decodeStudentAnalysisResponse,
  fetchQuestionAnalysis,
  fetchStudentAnalysis,
} from '../analysis'

const questionResponse = {
  scope: { session_id: 7, class_name: null, question_id: null },
  classes: ['七年级一班'],
  items: [
    {
      class_name: '七年级一班',
      question_id: 'Q2(1)',
      max_score: 10,
      score_rate: 80,
      average_score: 8,
      deduction_count: 1,
      attempt_count: 2,
      metric_status: 'ready',
    },
  ],
  total: 1,
  page: 1,
  page_size: 100,
  total_pages: 1,
} as const

const studentResponse = {
  scope: { session_id: 7, class_name: '七年级一班', question_id: 'Q2(1)' },
  items: [
    {
      result_id: 11,
      detail_id: 12,
      student_id: 13,
      student_code: 'S-13',
      student_name: '学生甲',
      class_name: '七年级一班',
      question_id: 'Q2(1)',
      score_awarded: 8,
      max_score: 10,
      deduction_amount: 2,
      deduction_reason: '步骤不完整',
      needs_review: false,
      evidence_url: '/api/sessions/7/results/11/details/12/crop',
    },
  ],
  total: 1,
  page: 1,
  page_size: 100,
  total_pages: 1,
} as const

afterEach(() => vi.restoreAllMocks())

describe('analysis API contract', () => {
  it('accepts explicit unknown metrics without coercing them to zero', () => {
    const payload = {
      ...questionResponse,
      items: [{
        ...questionResponse.items[0],
        max_score: null,
        score_rate: null,
        average_score: null,
        metric_status: 'missing_max_score',
      }],
    }
    expect(decodeQuestionAnalysisResponse(payload).items[0]).toMatchObject({
      max_score: null,
      score_rate: null,
      average_score: null,
    })
    expect(decodeStudentAnalysisResponse(studentResponse)).toEqual(studentResponse)
  })

  it('accepts percentage score rates and legal empty pages', () => {
    expect(decodeQuestionAnalysisResponse(questionResponse).items[0]?.score_rate).toBe(80)
    expect(decodeQuestionAnalysisResponse({
      ...questionResponse, items: [], total: 0, total_pages: 0,
    }).items).toEqual([])
    expect(decodeStudentAnalysisResponse({
      ...studentResponse, items: [], total: 0, total_pages: 0,
    }).items).toEqual([])
    expect(decodeQuestionAnalysisResponse({
      ...questionResponse, items: [], page: 2, total: 1, total_pages: 1,
    }).items).toEqual([])
  })

  it.each([
    { ...questionResponse, items: [{ ...questionResponse.items[0], score_rate: Number.POSITIVE_INFINITY }] },
    { ...questionResponse, items: [{ ...questionResponse.items[0], score_rate: -0.1 }] },
    { ...questionResponse, items: [{ ...questionResponse.items[0], score_rate: 100.1 }] },
    { ...questionResponse, items: [{ ...questionResponse.items[0], attempt_count: -1 }] },
    { ...questionResponse, total: 0 },
  ])('rejects malformed question analysis', (payload) => {
    expect(() => decodeQuestionAnalysisResponse(payload)).toThrow('Invalid question analysis')
  })

  it.each([
    { ...studentResponse, items: [{ ...studentResponse.items[0], evidence_url: 'https://example.test/private' }] },
    { ...studentResponse, items: [{ ...studentResponse.items[0], deduction_amount: Number.NaN }] },
    { ...studentResponse, total: 0 },
  ])('rejects malformed student analysis', (payload) => {
    expect(() => decodeStudentAnalysisResponse(payload)).toThrow('Invalid student analysis')
  })

  it('rejects extra private analysis fields', () => {
    expect(() => decodeQuestionAnalysisResponse({ ...questionResponse, private_path: 'C:/private' })).toThrow()
    expect(() => decodeQuestionAnalysisResponse({
      ...questionResponse, scope: { ...questionResponse.scope, raw: true },
    })).toThrow()
    expect(() => decodeQuestionAnalysisResponse({
      ...questionResponse, items: [{ ...questionResponse.items[0], payload: {} }],
    })).toThrow()
    expect(() => decodeStudentAnalysisResponse({
      ...studentResponse, items: [{ ...studentResponse.items[0], private_path: 'C:/private' }],
    })).toThrow()
  })

  it.each([
    {
      ...studentResponse,
      items: [{ ...studentResponse.items[0], question_id: 'Q9' }],
    },
    {
      ...studentResponse,
      items: [{ ...studentResponse.items[0], class_name: 'other-class' }],
    },
    {
      ...studentResponse,
      items: [{
        ...studentResponse.items[0],
        evidence_url: '/api/sessions/8/results/11/details/12/crop',
      }],
    },
    {
      ...studentResponse,
      items: [{
        ...studentResponse.items[0],
        evidence_url: '/api/sessions/7/results/99/details/12/crop',
      }],
    },
    {
      ...studentResponse,
      items: [{
        ...studentResponse.items[0],
        evidence_url: '/api/sessions/7/results/11/details/99/crop',
      }],
    },
  ])('rejects cross-scope student items and evidence URLs', (payload) => {
    expect(() => decodeStudentAnalysisResponse(payload)).toThrow('Invalid student analysis')
  })

  it('rejects a student response whose scope differs from the request', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
      ...studentResponse,
      scope: { ...studentResponse.scope, session_id: 8 },
      items: [{
        ...studentResponse.items[0],
        evidence_url: '/api/sessions/8/results/11/details/12/crop',
      }],
    }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }))

    await expect(fetchStudentAnalysis(7, 'Q2(1)', '七年级一班')).rejects.toMatchObject({
      code: 'invalid_success_contract',
    })
  })

  it('builds query strings and encodes the question path segment', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (path) => {
      const isStudent = String(path).includes('/students')
      const response = isStudent ? studentResponse : questionResponse
      return new Response(JSON.stringify(String(path).includes('page=2')
        ? { ...response, items: [], page: 2 }
        : response), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    })
    await fetchQuestionAnalysis(7, '七年级一班')
    await fetchStudentAnalysis(7, 'Q2(1)', '七年级一班')
    await fetchQuestionAnalysis(7, '七年级一班', undefined, 2)
    await fetchStudentAnalysis(7, 'Q2(1)', '七年级一班', undefined, 2)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/sessions/7/analysis/questions?class_name=%E4%B8%83%E5%B9%B4%E7%BA%A7%E4%B8%80%E7%8F%AD&page=1&page_size=100')
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/sessions/7/analysis/questions/Q2(1)/students?class_name=%E4%B8%83%E5%B9%B4%E7%BA%A7%E4%B8%80%E7%8F%AD&page=1&page_size=100')
    expect(fetchMock.mock.calls[2]?.[0]).toContain('page=2&page_size=100')
    expect(fetchMock.mock.calls[3]?.[0]).toContain('page=2&page_size=100')
  })
})
