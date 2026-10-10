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

  it('reads legacy method states as saved results without exposing a version state', () => {
    const payload = { ...analysisPayload, narrative_state: 'old_prompt',
      cause_analysis: { status: 'ready', pending_questions: 0, total_questions: 2,
        failed_questions: 0, question_states: { current: 1, stale: 0, old_prompt: 1, failed: 0 },
        stale: false, generated_at: null, origin: 'model' },
      data: { ...analysisPayload.data, questions: [{ ...analysisPayload.data.questions[0], state: 'old_prompt' }] },
    }
    const parsed = decodeClassAnalysisResponse(payload)
    expect(parsed.narrative_state).toBe('current')
    expect(parsed.data?.questions[0]?.state).toBe('current')
    expect(parsed.cause_analysis?.question_states).toEqual({ current: 2, stale: 0, failed: 0 })
  })

  it('preserves a read warning without dropping saved narrative and accepts omitted legacy warnings', () => {
    const warning = '保存文件暂无法读取，仍可查看兼容正文。'
    const parsed = decodeClassAnalysisResponse({ ...analysisPayload, read_error: warning })
    expect(parsed.read_error).toBe(warning)
    expect(parsed.narrative).toEqual(analysisPayload.narrative)
    expect(decodeClassAnalysisResponse(analysisPayload).read_error).toBeNull()
    expect(() => decodeClassAnalysisResponse({ ...analysisPayload, read_error: false })).toThrow()
  })

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

  it('decodes step-organized causes with the current three-state response', () => {
    const evidence = [{ text: '未列等量关系', student_ids: [1], student_answer: '作答',
      failed_steps: [{ id: 'E1.S1', step_id: 'S2', part_id: 'Q2', core_goal: '列等量关系', step_score: 10 }] }]
    const cause = { kind: 'process', category: '过程与依据', reason: '缺少等量关系', count: 1,
      evidence, step_ids: ['S2'],
      manifestations: [{ description: '未列关系式', source_question_id: null, evidence }] }
    const payload = { ...analysisPayload,
      cause_analysis: { status: 'partial', pending_questions: 1, total_questions: 2,
        failed_questions: 0, question_states: { current: 1, stale: 0, failed: 0 },
        stale: false, generated_at: '2026-09-09T10:00:00', origin: 'model' },
      data: { ...analysisPayload.data, questions: [{
        ...analysisPayload.data.questions[0], causes: [cause], causes_grouped: true,
        causes_by_step: true, causes_legacy: false,
      }] } }
    const parsed = decodeClassAnalysisResponse(payload)
    expect(parsed.cause_analysis?.question_states).toEqual({ current: 1, stale: 0, failed: 0 })
    const question = parsed.data!.questions[0]!
    expect(question.causes_by_step).toBe(true)
    expect(question.causes![0]!.step_ids).toEqual(['S2'])
    expect(question.causes![0]!.evidence![0]!.failed_steps).toHaveLength(1)
  })

  it('preserves validated key step statistics and rejects a negative denominator', () => {
    const payload = structuredClone(analysisPayload)
    const summary = { students: 4, confirmed_ai_rows: 1, excluded_teacher_total_only: 2, hotspots: [{
      key: 'sk_test', name: '合成步骤', independent: 2, observed: 4, carried: 1, uncertain: 0, blank: 1,
      confirmed_ai: 1, questions: ['12(3)'], causes_status: 'unclassified', causes: [], patterns: [],
    }] }
    const augmented = { ...payload, data: { ...payload.data, step_diagnosis: summary } }
    expect(decodeClassAnalysisResponse(augmented).data?.step_diagnosis).toEqual(summary)
    expect(() => decodeClassAnalysisResponse({ ...augmented,
      data: { ...augmented.data, step_diagnosis: { ...summary, hotspots: [{ ...summary.hotspots[0], observed: -1 }] } },
    })).toThrow('Invalid step diagnosis summary')
  })

})
