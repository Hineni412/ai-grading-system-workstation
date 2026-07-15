import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  decodeQuestionAnalysisResponse,
  decodeStudentAnalysisResponse,
  fetchQuestionAnalysis,
  fetchStudentAnalysis,
} from '../analysis'
import {
  decodeGraphEvidenceResponse,
  decodeGraphRowsResponse,
  fetchGraphEvidence,
  fetchGraphRows,
} from '../graph'

const questionResponse = {
  scope: { session_id: 7, class_name: null, question_id: null },
  classes: ['七年级一班'],
  items: [
    {
      class_name: '七年级一班',
      question_id: 'Q2(1)',
      max_score: 10,
      score_rate: 0.8,
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

const scope = { mode: 'class', student_ids: ['13'], class_id: '七年级一班' } as const
const examScope = {
  mode: 'current',
  session_ids: [7],
  sessions: [{ session_id: 7, session_name: '期中考试' }],
} as const
const coverage = { covered_items: 1, total_items: 1, missing_items: {} }
const node = {
  knowledge_key: 'knowledge_point:三角形全等',
  knowledge_label: '三角形全等',
  student_count: 1,
  item_count: 1,
  deduction_count: 1,
  average_mastery: 0.8,
  tag_context: { method: ['构造辅助线'] },
  error_counts: { primary: { '条件遗漏': 1 }, secondary: {} },
} as const
const graphRows = {
  scope,
  exam_scope: examScope,
  rows: [],
  nodes: [node],
  edges: [],
  coverage,
  warnings: [],
  diagnosis_identity: 'question_tag',
} as const
const graphEvidence = {
  scope,
  exam_scope: examScope,
  knowledge_key: node.knowledge_key,
  knowledge_label: node.knowledge_label,
  items: [],
  total: 0,
  page: 1,
  page_size: 20,
  total_pages: 1,
  coverage,
  warnings: [],
  diagnosis_identity: 'question_tag',
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

  it.each([
    { ...questionResponse, items: [{ ...questionResponse.items[0], score_rate: Number.POSITIVE_INFINITY }] },
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

  it('builds query strings and encodes the question path segment', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (path) =>
      new Response(JSON.stringify(String(path).includes('/students') ? studentResponse : questionResponse), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    await fetchQuestionAnalysis(7, '七年级一班')
    await fetchStudentAnalysis(7, 'Q2(1)', '七年级一班')
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/sessions/7/analysis/questions?class_name=%E4%B8%83%E5%B9%B4%E7%BA%A7%E4%B8%80%E7%8F%AD&page=1&page_size=100')
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/sessions/7/analysis/questions/Q2(1)/students?class_name=%E4%B8%83%E5%B9%B4%E7%BA%A7%E4%B8%80%E7%8F%AD&page=1&page_size=100')
  })
})

describe('graph API contract', () => {
  it('accepts question-tag rows and evidence', () => {
    expect(decodeGraphRowsResponse(graphRows)).toEqual(graphRows)
    expect(decodeGraphEvidenceResponse(graphEvidence)).toEqual(graphEvidence)
  })

  it.each([
    { ...graphRows, diagnosis_identity: 'legacy_skill' },
    { ...graphRows, nodes: [{ ...node, average_mastery: Number.NaN }] },
    { ...graphRows, coverage: { ...coverage, covered_items: -1 } },
  ])('rejects malformed graph rows', (payload) => {
    expect(() => decodeGraphRowsResponse(payload)).toThrow('Invalid graph rows')
  })

  it('posts only the selected class and current exam', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (path) =>
      new Response(JSON.stringify(String(path).endsWith('/evidence') ? graphEvidence : graphRows), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    await fetchGraphRows(7, '七年级一班')
    await fetchGraphEvidence(7, '七年级一班', node.knowledge_key)
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      scope: { mode: 'class', class_id: '七年级一班' },
      exam_scope: { mode: 'current', session_ids: [7] },
    })
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toMatchObject({
      knowledge_key: node.knowledge_key,
      page: 1,
      page_size: 20,
    })
  })
})
