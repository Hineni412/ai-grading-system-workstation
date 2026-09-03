import { describe, expect, it } from 'vitest'

import { decodeClassAnalysisResponse } from '../api/class-analysis'

// 形状镜像后端 analysis_report_exporter.build_class_page_data 的真实出参；
// 后端对应断言见 tests/test_class_analysis.py 的 ready 用例。
// 两侧任一边改动契约时，这里的夹具必须同步更新。
function makeBackendPayload() {
  return {
    status: 'ready',
    auto_generate: true,
    small_sample: true,
    narrative_failed: false,
    generated_at: '2026-09-01T10:00:00',
    stale: false,
    active_job_id: null,
    data: {
      exam: {
        session_id: 1,
        title: '单元测试',
        subject: '数学',
        full_score: 100,
        graded_at: '2026-08-20',
      },
      present: 2,
      roster_absent: ['王五'],
      score_distribution: {
        avg: 70,
        median: 70,
        max: 90,
        min: 50,
        pass_rate: 0.5,
        bands: { '85 – 100 分': 1, '70 – 84 分': 0, '60 – 69 分': 0, '40 – 59 分': 1, '0 – 39 分': 0 },
      },
      questions: [
        {
          question_id: 'Q2',
          question_type: 'proof',
          max_score: 40,
          class_rate: 0.625,
          class_avg: 25,
          stem_summary: '证明线段数量关系',
          canonical_answer: 'AB=BD+DH',
          records: [
            {
              student_id: 1,
              student_code: '001',
              student_name: '张三',
              score: 30,
              max_score: 40,
              lost_points: 10,
              deduction_reason: '缺关键步骤',
              error_category: '过程不完整',
              error_summary: '缺 BE⊥AC 步骤',
            },
          ],
        },
        {
          question_id: 'Q1',
          question_type: 'choice',
          max_score: 60,
          class_rate: 0.75,
          class_avg: 45,
          stem_summary: '识别轴对称图形',
          canonical_answer: 'B',
          records: [],
        },
      ],
      students: [
        {
          student_id: 1,
          student_code: '001',
          student_name: '张三',
          class_name: '1 班',
          total_score: 90,
          rank: 1,
          needs_review: false,
          lost_points_total: 10,
          lost: [
            {
              question_id: 'Q2',
              lost_points: 10,
              record: {
                deduction_reason: '缺关键步骤',
                error_category: '过程不完整',
                error_summary: '缺 BE⊥AC 步骤',
              },
            },
          ],
        },
        {
          student_id: 2,
          student_code: '002',
          student_name: '李四',
          class_name: '1 班',
          total_score: 50,
          rank: 2,
          needs_review: true,
          lost_points_total: 50,
          lost: [
            { question_id: 'Q1', lost_points: 30, record: null },
          ],
        },
      ],
      skipped: [
        {
          class_name: '1 班',
          student_code: '003',
          student_name: '王五',
          reason: '缺考',
        },
      ],
    },
    narrative: {
      key_findings: [
        { title: '证明题得分率低', detail: '第 2 题全班得分率不足一半', severity: 'high' },
      ],
      common_issues: [
        { title: '证明步骤缺失', evidence: 'S1、S2 均缺关键步骤', teaching_action: '板书示范 10 分钟' },
      ],
      student_notes: [
        { alias: 'S1', note: '高但证明扣分', suggestion: '保持', flags: [] },
      ],
      grouping_advice: '按分数断层分层布置作业',
    },
  }
}

describe('decodeClassAnalysisResponse 与后端真实出参的契约', () => {
  it('接受 build_class_page_data 的完整形状', () => {
    const decoded = decodeClassAnalysisResponse(makeBackendPayload())
    expect(decoded.status).toBe('ready')
    expect(decoded.data?.present).toBe(2)
    expect(decoded.data?.roster_absent).toEqual(['王五'])
    expect(decoded.data?.score_distribution.bands['85 – 100 分']).toBe(1)
    expect(decoded.data?.students[0]?.lost[0]?.record?.deduction_reason).toBe('缺关键步骤')
    expect(decoded.data?.students[1]?.lost[0]?.record).toBeNull()
    expect(decoded.narrative?.key_findings[0]?.title).toBe('证明题得分率低')
  })

  it('class_rate 为 null 的旧出参会被拒绝（防回归）', () => {
    const payload = makeBackendPayload()
    ;(payload.data.questions[0] as { class_rate: unknown }).class_rate = null
    expect(() => decodeClassAnalysisResponse(payload)).toThrow()
  })
})
