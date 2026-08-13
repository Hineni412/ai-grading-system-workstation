import { afterEach, describe, expect, it, vi } from 'vitest'

import { decodeStudentExamResults, fetchStudentExamResults } from '../api/students'

const payload = {
  student: {
    id: 12,
    student_code: 'S012',
    name: '匿名学生甲',
    class_name: '七年级一班',
    created_at: null,
  },
  sessions: [{
    session_id: 7,
    session_name: '匿名阶段测验',
    graded_at: '2026-07-18T09:00:00Z',
    exam_created_at: '2026-07-15T09:00:00Z',
    result_id: 31,
    student_score: 78,
    total_score: 100,
    items: [{
      detail_id: 501,
      question_id: 'Q1',
      bank_question_id: 101,
      score_awarded: 6,
      max_score: 10,
      deduction_amount: 4,
      deduction_reason: '证明步骤缺少依据',
      error_category: '逻辑断裂',
      error_summary: null,
      evidence_url: '/api/sessions/7/results/31/details/501/crop',
    }],
  }],
  total_sessions: 21,
  page: 1,
  page_size: 10,
  total_pages: 3,
}

function stubResponse(value: unknown): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => new Response(
    JSON.stringify(value),
    { status: 200, headers: { 'content-type': 'application/json' } },
  ))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('student exam results api', () => {
  it('accepts the shape returned by the exam-results endpoint', () => {
    expect(decodeStudentExamResults(payload)).toMatchObject({
      student: { id: 12, name: '匿名学生甲' },
      total_sessions: 21,
      total_pages: 3,
    })
  })

  it('rejects sessions with non-api evidence urls or inconsistent pagination', () => {
    const badUrl = {
      ...payload,
      sessions: [{
        ...payload.sessions[0]!,
        items: [{ ...payload.sessions[0]!.items[0]!, evidence_url: 'file:///D:/secret.png' }],
      }],
    }
    expect(() => decodeStudentExamResults(badUrl)).toThrow('Invalid student exam results')
    expect(() => decodeStudentExamResults({ ...payload, total_pages: 2 }))
      .toThrow('Invalid student exam results')
    expect(() => decodeStudentExamResults({ ...payload, total_sessions: 20 }))
      .toThrow('Invalid student exam results')
  })

  it('rejects extra or missing keys', () => {
    const sessionWithoutKey: Record<string, unknown> = { ...payload.sessions[0]! }
    delete sessionWithoutKey.graded_at
    expect(() => decodeStudentExamResults({ ...payload, sessions: [sessionWithoutKey] }))
      .toThrow('Invalid student exam results')
    expect(() => decodeStudentExamResults({ ...payload, extra: true }))
      .toThrow('Invalid student exam results')
  })

  it('accepts a null or positive-integer bank_question_id and rejects other shapes', () => {
    expect(decodeStudentExamResults(payload).sessions[0]!.items[0]!.bank_question_id).toBe(101)

    const nullLink = {
      ...payload,
      sessions: [{
        ...payload.sessions[0]!,
        items: [{ ...payload.sessions[0]!.items[0]!, bank_question_id: null }],
      }],
    }
    expect(decodeStudentExamResults(nullLink).sessions[0]!.items[0]!.bank_question_id).toBeNull()

    const missingItem: Record<string, unknown> = { ...payload.sessions[0]!.items[0]! }
    delete missingItem.bank_question_id
    expect(() => decodeStudentExamResults({
      ...payload,
      sessions: [{ ...payload.sessions[0]!, items: [missingItem] }],
    })).toThrow('Invalid student exam results')

    const wrongType = {
      ...payload,
      sessions: [{
        ...payload.sessions[0]!,
        items: [{ ...payload.sessions[0]!.items[0]!, bank_question_id: '101' }],
      }],
    }
    expect(() => decodeStudentExamResults(wrongType)).toThrow('Invalid student exam results')
  })

  it('sends only_deducted, page and page_size and verifies the returned student', async () => {
    const fetchMock = stubResponse({ ...payload, page: 2 })

    await expect(
      fetchStudentExamResults('12', { onlyDeducted: false, page: 2, pageSize: 10 }),
    ).resolves.toMatchObject({ page: 2 })
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/api/students/12/exam-results?')
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('only_deducted=false')
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('page=2')
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('page_size=10')

    stubResponse({ ...payload, student: { ...payload.student, id: 99 } })
    await expect(fetchStudentExamResults('12')).rejects.toMatchObject({
      code: 'invalid_success_contract',
    })
  })
})
