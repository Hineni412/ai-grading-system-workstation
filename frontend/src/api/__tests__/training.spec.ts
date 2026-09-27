import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  decodeTrainingDiagnosis,
  trainingApi,
} from '../training'

const diagnosisPayload = {
  scope: {
    mode: 'selected',
    student_ids: ['12', '13'],
  },
  exam_scope: {
    mode: 'manual',
    session_ids: [14],
    sessions: [{ session_id: 14, session_name: '七年级阶段测验' }],
  },
  students: [{
    student_id: '12',
    student_code: '20260012',
    student_name: '张同学',
    class_id: '七年级一班',
    score_rate: 0.55,
    weak_points: [{
      knowledge_key: 'knowledge_point:三角形全等',
      knowledge_point: '三角形全等',
      mastery: 0.55,
      score_sum: 11,
      full_score_sum: 20,
      deduction_count: 2,
      evidence_count: 2,
      exam_count: 1,
      source_question_refs: [{
        session_id: 14,
        session_name: '七年级阶段测验',
        question_id: 'Q1',
        bank_question_id: 101,
        score_awarded: 6,
        full_score: 10,
        score_rate: 0.6,
      }],
      actionable_reasons: ['辅助线思路缺失'],
      tag_context: {
        knowledge_point: ['三角形全等'],
        method: ['构造辅助线'],
      },
      error_counts: {
        primary: { 逻辑断裂: 1 },
        secondary: { 审题错误: 1 },
      },
    }],
  }, {
    student_id: '13',
    student_code: '20260013',
    student_name: '李同学',
    class_id: '七年级一班',
    score_rate: null,
    weak_points: [],
  }],
  coverage: {
    covered_items: 1,
    total_items: 2,
    missing_items: { Q2: '题目尚未关联题库来源' },
  },
  confirmed_concept_ids: [],
  suggested_terms: ['三角形全等'],
  unmapped_terms: [],
  warnings: ['知识图谱仅覆盖 1/2 个评分题；缺失题目已列出。'],
  diagnosis_identity: 'question_tag',
} as const

afterEach(() => {
  vi.restoreAllMocks()
})

describe('training API', () => {
  it('decodes tag-only diagnosis with evidence and coverage', () => {
    const decoded = decodeTrainingDiagnosis(diagnosisPayload)

    expect(decoded.diagnosis_identity).toBe('question_tag')
    expect(decoded.students[0]?.weak_points[0]?.mastery).toBe(0.55)
    expect(decoded.coverage.missing_items).toEqual({
      Q2: '题目尚未关联题库来源',
    })
  })

  it('rejects legacy diagnosis identity and unsafe storage fields', () => {
    expect(() => decodeTrainingDiagnosis({
      ...diagnosisPayload,
      diagnosis_identity: 'skill',
    })).toThrow('Invalid training diagnosis')
    expect(() => decodeTrainingDiagnosis({
      ...diagnosisPayload,
      output_path: 'C:\\private\\training.docx',
    })).toThrow()
  })

  it('decodes weak points whose mastery was dropped by exclude_none serialization', () => {
    const weakPointWithoutMastery: Record<string, unknown> = { ...diagnosisPayload.students[0].weak_points[0] }
    delete weakPointWithoutMastery.mastery
    const decoded = decodeTrainingDiagnosis({
      ...diagnosisPayload,
      students: [{
        ...diagnosisPayload.students[0],
        weak_points: [weakPointWithoutMastery],
      }],
    })

    expect(decoded.students[0]?.weak_points[0]?.mastery).toBeUndefined()
  })

  it('rejects impossible mastery and malformed evidence', () => {
    expect(() => decodeTrainingDiagnosis({
      ...diagnosisPayload,
      students: [{
        ...diagnosisPayload.students[0],
        weak_points: [{
          ...diagnosisPayload.students[0].weak_points[0],
          mastery: 1.2,
        }],
      }],
    })).toThrow('Invalid training diagnosis')
  })

  it('posts diagnosis without adding legacy controls', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify(diagnosisPayload), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }))

    await trainingApi.diagnose({
      scope: {
        mode: 'selected',
        student_ids: ['12', '13'],
      },
      exam_scope: {
        mode: 'manual',
        session_ids: [14],
      },
    })

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/training/diagnosis',
    ])
    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body)) as Record<string, unknown>
    expect(body).not.toHaveProperty('allow_broad_fallback')
    expect(body).not.toHaveProperty('read_mode')
  })
})
