import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  decodeTrainingDiagnosis,
  decodeTrainingPlanResponse,
  trainingApi,
  type TrainingPlanRequest,
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
    score_rate: 55,
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

const planRequest: TrainingPlanRequest = {
  scope: {
    mode: 'selected',
    student_ids: ['12', '13'],
  },
  exam_scope: {
    mode: 'manual',
    session_ids: [14],
  },
  variant_mode: 'individual',
  question_count: 10,
  stage_ratios: {
    direct: 0.6,
    prerequisite: 0.3,
    transfer: 0.1,
  },
  exclude_current_exam_originals: true,
}

const planPayload = {
  plan_revision: 'a'.repeat(64),
  plan: {
    scope_snapshot: diagnosisPayload.scope,
    exam_scope: diagnosisPayload.exam_scope,
    diagnosis_snapshot: diagnosisPayload,
    generation_config: {
      question_count: 10,
      stage_ratios: {
        direct: 0.6,
        prerequisite: 0.3,
        transfer: 0.1,
      },
      exclude_current_exam_originals: true,
    },
    variant_mode: 'individual',
    variants: [{
      variant_key: 'student-12',
      variant_type: 'individual',
      student_ids: ['12'],
      grouping_reason: {
        rule: 'individual',
        covered_knowledge_points: ['三角形全等'],
      },
      diagnosis_snapshot: {
        diagnosis_identity: 'question_tag',
        students: [diagnosisPayload.students[0]],
      },
      items: [{
        question_id: 201,
        item_order: 1,
        stage: 'direct',
        knowledge_key: 'knowledge_point:三角形全等',
        knowledge_point: '三角形全等',
        match_kind: 'exact',
        reason: '与薄弱知识点标签完全相同',
        recommend_score: 0.91,
        score_components: { tag: 1 },
        tag_matches: { method: ['构造辅助线'] },
        tags: { knowledge_point: ['三角形全等'] },
        warnings: [],
        question_fingerprint: 'fixture-201',
        question_text: '利用边角关系证明两个三角形全等',
        question_number: '11',
        difficulty: '5',
        source_paper: '合成练习',
        frequency: {},
      }],
      stage_counts: {
        direct: 6,
        prerequisite: 3,
        transfer: 1,
      },
      shortages: [{
        stage: 'transfer',
        requested_count: 1,
        selected_count: 0,
        missing_count: 1,
        decision_required: false,
      }],
      warnings: ['transfer 阶段缺少 1 道知识点完全相同的候选题。'],
      dedupe_summary: {
        removed_count: 0,
        reason_counts: {},
      },
      generation_config: {
        question_count: 10,
        stage_ratios: {
          direct: 0.6,
          prerequisite: 0.3,
          transfer: 0.1,
        },
      },
    }],
    warnings: ['transfer 阶段缺少 1 道知识点完全相同的候选题。'],
    ungrouped_students: [],
    teacher_override: {
      allowed: true,
      applied: false,
      assignments: {},
    },
  },
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

  it('decodes exact-tag plan variants, reasons and shortages', () => {
    const decoded = decodeTrainingPlanResponse(planPayload)

    expect(decoded.plan_revision).toBe('a'.repeat(64))
    expect(decoded.plan.variants[0]?.items[0]).toMatchObject({
      stage: 'direct',
      match_kind: 'exact',
      knowledge_point: '三角形全等',
    })
    expect(decoded.plan.variants[0]?.shortages[0]?.missing_count).toBe(1)
  })

  it('rejects non-exact recommendation identity and invalid revision', () => {
    expect(() => decodeTrainingPlanResponse({
      ...planPayload,
      plan_revision: 'stale',
    })).toThrow('Invalid training plan')
    expect(() => decodeTrainingPlanResponse({
      ...planPayload,
      plan: {
        ...planPayload.plan,
        variants: [{
          ...planPayload.plan.variants[0],
          items: [{
            ...planPayload.plan.variants[0].items[0],
            match_kind: 'neighbor',
          }],
        }],
      },
    })).toThrow('Invalid training plan')
  })

  it('posts diagnosis, preview and confirmation without adding legacy controls', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify(diagnosisPayload), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(planPayload), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: 31,
        task_code: 'TRN-CFM-12345678123456781234567812345678',
        created_by: 'teacher',
        scope_snapshot: diagnosisPayload.scope,
        exam_scope: diagnosisPayload.exam_scope,
        generation_config: {
          ...planPayload.plan.generation_config,
          plan_revision: planPayload.plan_revision,
        },
        warnings: [],
        status: 'ready',
        created_at: '2026-07-19T01:30:00Z',
        updated_at: '2026-07-19T01:30:00Z',
        diagnosis_snapshot: diagnosisPayload,
        variants: [],
        exports: [],
      }), {
        status: 201,
        headers: { 'content-type': 'application/json' },
      }))

    await trainingApi.diagnose({
      scope: planRequest.scope,
      exam_scope: planRequest.exam_scope,
    })
    await trainingApi.preview(planRequest)
    await trainingApi.confirm({
      ...planRequest,
      confirmation_id: '12345678-1234-5678-1234-567812345678',
      expected_plan_revision: planPayload.plan_revision,
    })

    const calls = fetchMock.mock.calls.map(([, init]) => (
      JSON.parse(String(init?.body)) as Record<string, unknown>
    ))
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/training/diagnosis',
      '/api/training/plans/preview',
      '/api/training/tasks',
    ])
    expect(calls[1]).not.toHaveProperty('allow_broad_fallback')
    expect(calls[1]).not.toHaveProperty('read_mode')
    expect(calls[2]).toMatchObject({
      confirmation_id: '12345678-1234-5678-1234-567812345678',
      expected_plan_revision: 'a'.repeat(64),
    })
  })
})
