import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  classAnalysisApi,
  decodeClassAnalysisResponse,
  decodeClassAnalysisSettings,
} from '../class-analysis'

const job = {
  id: 91,
  job_type: 'class_analysis',
  payload: { session_id: 7 },
  result: {},
  status: 'queued',
  progress: 0,
  stage: 'class_analysis',
  detail: 'queued',
  error: null,
  cancel_requested: false,
  created_at: '2026-08-30T10:00:00Z',
  started_at: null,
  updated_at: '2026-08-30T10:00:00Z',
  finished_at: null,
} as const

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
  it('decodes a full ready response and rejects path-like keys', () => {
    expect(decodeClassAnalysisResponse(analysisPayload)).toEqual(analysisPayload)
    expect(() => decodeClassAnalysisResponse({
      ...analysisPayload,
      data: { ...analysisPayload.data, output_path: 'C:/private/a.html' },
    })).toThrow()
  })

  it('accepts empty and generating states with nullable fields', () => {
    expect(decodeClassAnalysisResponse({
      ...analysisPayload,
      status: 'no_data',
      data: null,
      narrative: null,
      generated_at: null,
    }).status).toBe('no_data')
    expect(decodeClassAnalysisResponse({
      ...analysisPayload,
      status: 'generating',
      data: null,
      narrative: null,
      active_job_id: 91,
    }).active_job_id).toBe(91)
    expect(decodeClassAnalysisResponse({
      ...analysisPayload,
      status: 'generating',
      data: null,
      narrative: null,
      active_job_id: '91',
    }).active_job_id).toBe(91)
  })

  it('rejects malformed responses', () => {
    expect(() => decodeClassAnalysisResponse({
      ...analysisPayload,
      status: 'archived',
    })).toThrow()
    expect(() => decodeClassAnalysisResponse({
      ...analysisPayload,
      data: {
        ...analysisPayload.data,
        score_distribution: { ...analysisPayload.data.score_distribution, bands: { 优秀: -1 } },
      },
    })).toThrow()
    expect(() => decodeClassAnalysisResponse({
      ...analysisPayload,
      narrative: { ...analysisPayload.narrative, student_notes: [{ alias: 'S1' }] },
    })).toThrow()
  })

  it('requests the class analysis through the dedicated endpoint', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(analysisPayload), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )

    const result = await classAnalysisApi.getClassAnalysis(7)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sessions/7/class-analysis',
      expect.anything(),
    )
    expect(result.status).toBe('ready')
  })

  it('writes the auto-generate setting with the frozen body shape', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ auto_generate: false }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )

    const result = await classAnalysisApi.updateSettings(7, false)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sessions/7/class-analysis/settings',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ auto_generate: false }),
      }),
    )
    expect(result).toEqual({ auto_generate: false })
    expect(decodeClassAnalysisSettings({ auto_generate: true }))
      .toEqual({ auto_generate: true })
    expect(() => decodeClassAnalysisSettings({ auto_generate: 'yes' })).toThrow()
  })

  it('submits regenerate as a job and validates identifiers before fetch', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(job), {
        status: 202,
        headers: { 'content-type': 'application/json' },
      }),
    )

    const result = await classAnalysisApi.regenerate(7)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sessions/7/class-analysis/regenerate',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(result.id).toBe(91)

    fetchMock.mockClear()
    await expect(classAnalysisApi.regenerate(0)).rejects.toThrow()
    await expect(classAnalysisApi.getClassAnalysis(-1)).rejects.toThrow()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
