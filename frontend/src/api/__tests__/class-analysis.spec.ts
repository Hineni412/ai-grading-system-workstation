import { afterEach, describe, expect, it, vi } from 'vitest';

import { decodeClassAnalysisResponse } from '../class-analysis';

const analysisPayload = {
  status: 'ready',
  auto_generate: true,
  small_sample: false,
  data: {
    exam: {
      title: '数学阶段测试',
      subject: '数学',
      full_score: 100,
      graded_at: '2026-08-30T09:00:00Z',
    },
    present: 4,
    roster_absent: ['王五'],
    skipped: [],
    score_distribution: {
      avg: 54.75,
      median: 55.5,
      max: 88,
      min: 20,
      pass_rate: 0.5,
      bands: { '85–100': 1, '70–84': 1, '0–39': 2 },
    },
    questions: [
      {
        question_id: '12(3)',
        max_score: 8,
        class_rate: 0.25,
        stem_summary: '证明',
        canonical_answer: 'AB＝BD＋DH',
        cause_category_counts: [],
        records: [
          {
            student_name: '钱肖白',
            score: 0,
            deduction_reason: '空白',
            error_category: 'blank',
            error_summary: '未作答',
          },
        ],
      },
    ],
    students: [
      {
        student_name: '陈维懋',
        student_code: 'S001',
        total_score: 88,
        rank: 1,
        needs_review: false,
        lost: [
          {
            question_id: '4',
            lost_points: 5,
            record: {
              deduction_reason: '选 D',
              error_category: 'choice',
              error_summary: '轴对称图形识别错误',
            },
          },
        ],
      },
    ],
  },
  narrative: {
    key_findings: [
      { title: '两极分化严重', detail: '出现 47 分断层', severity: 'high' },
    ],
    common_issues: [
      {
        title: '证明题书写能力断层',
        evidence: '第 11(3)、12(2)(3) 问得分率 25%',
        teaching_action: '用第 12 题整题做板书示范',
      },
    ],
    student_notes: [
      {
        alias: 'S1',
        note: '会做的题完成质量高',
        suggestion: '压轴小问不要轻易放弃',
        flags: ['保持'],
      },
    ],
    grouping_advice: '高分组布置压轴变式，低分组重做基础题。',
  },
  narrative_failed: false,
  generated_at: '2026-08-30T10:00:00Z',
  stale: false,
  active_job_id: null,
} as const

afterEach(() => vi.restoreAllMocks())

describe('class analysis API contract', () => {

  it('decodes classified manifestations and their answer evidence without losing legacy compatibility', () => {
    const evidence = [{ text: '原批语', student_ids: [1], student_answer: '保留的算式', evidence_steps: ['已有步骤'],
      missing_steps: [], previous_answers: [{ question_id: 'Q12(P1)', student_answer: '前问算式', text: '前问批语', evidence_steps: [] }] }]
    const cause = { kind: 'carry_forward', reason: '前问错误延续', count: 1, evidence,
      manifestations: [{ description: '沿用前问结果', source_question_id: 'Q12(P1)', evidence }] }
    const payload = { ...analysisPayload, data: { ...analysisPayload.data, questions: [{
      ...analysisPayload.data.questions[0], causes: [cause], causes_grouped: true, causes_legacy: false,
    }] } }
    const parsed = decodeClassAnalysisResponse(payload)
    expect(parsed.data!.questions[0]!.causes).toEqual([cause])
    expect(() => decodeClassAnalysisResponse({ ...payload, data: { ...payload.data, questions: [{
      ...payload.data.questions[0], causes: [{ ...cause, kind: '猜测学生态度' }],
    }] } })).toThrow('Invalid class cause')
    expect(() => decodeClassAnalysisResponse({ ...payload, data: { ...payload.data, questions: [{
      ...payload.data.questions[0], causes: [{ ...cause, manifestations: [{ description: '缺少证据映射' }] }],
    }] } })).toThrow()
  })

})
