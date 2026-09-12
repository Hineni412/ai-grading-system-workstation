import { createApp, h, nextTick } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import {
  decodeQuestionSolutionEvidenceResponse,
  type QuestionSolutionEvidenceResponse,
} from '../api/question-bank'
import SolutionEvidenceReview from '../components/question-bank/SolutionEvidenceReview.vue'

const mounted: Array<ReturnType<typeof createApp>> = []

function evidenceResponse(): QuestionSolutionEvidenceResponse {
  return {
    question_id: 41,
    available: true,
    evidence_version_id: 'c'.repeat(64),
    status: 'proposed',
    evidence: {
      schema_version: 'question-solution-evidence-v1',
      question_id: 41,
      source_content_hash: 'a'.repeat(64),
      parts: [
        {
          part_id: 'part-1',
          label: '（1）',
          response_mode: 'process_required',
          canonical_answer: 'x = 2',
          accepted_forms: ['x=2'],
          full_answer: '配方后得到 x = 2。',
          proof_obligations: [],
          visual_requirements: [],
          deduction_policy: [],
          allow_alternative_methods: true,
          evidence_points: [
            {
              evidence_point_id: 'point-1',
              target: '完成方程求解',
              observable_evidence: '写出配方过程并得到正确解。',
              fine_term_links: [
                {
                  fine_term_id: 'term-direct',
                  fine_term_name: '一元二次方程求根',
                  role: 'direct',
                  core_resolution: {
                    status: 'resolved',
                    stable_keys: ['kp_equation'],
                    reason: '明确细化到方程核心节点',
                  },
                },
                {
                  fine_term_id: 'term-supporting',
                  fine_term_name: '配方法',
                  role: 'supporting_prerequisite',
                  core_resolution: {
                    status: 'ambiguous',
                    stable_keys: ['kp_equation', 'kp_algebra'],
                    reason: '待治理确认主投影',
                  },
                },
                {
                  fine_term_id: 'term-unmapped',
                  fine_term_name: '规范整理步骤',
                  role: 'supporting_prerequisite',
                  core_resolution: {
                    status: 'unmapped',
                    stable_keys: [],
                    reason: '过程词不强行映射',
                  },
                },
              ],
              equivalent_rules: [],
              counterexamples: [],
            },
          ],
        },
      ],
      auxiliary_rules: [],
      rationale: '按小问和踩分点拆解。',
      confidence: 0.91,
      content_hash: 'b'.repeat(64),
      version_id: 'c'.repeat(64),
      whole_question_classification: {
        direct_fine_terms: [
          { fine_term_id: 'term-direct', fine_term_name: '一元二次方程求根' },
        ],
        supporting_prerequisite_fine_terms: [
          { fine_term_id: 'term-supporting', fine_term_name: '配方法' },
          { fine_term_id: 'term-unmapped', fine_term_name: '规范整理步骤' },
        ],
        direct_resolved_core_node_ids: ['kp_equation'],
        supporting_resolved_core_node_ids: [],
        direct_ambiguous_core_node_ids: [],
        supporting_ambiguous_core_node_ids: ['kp_equation', 'kp_algebra'],
        direct_unmapped_fine_term_ids: [],
        supporting_unmapped_fine_term_ids: ['term-unmapped'],
        resolved_core_node_ids: ['kp_equation'],
        ambiguous_core_node_ids: ['kp_equation', 'kp_algebra'],
        unmapped_fine_term_ids: ['term-unmapped'],
      },
    },
  }
}

async function mountReview(
  response: QuestionSolutionEvidenceResponse,
  extra: { embedded?: boolean } = {},
) {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp({
    setup() {
      return () => h(SolutionEvidenceReview, {
        questionId: response.question_id,
        embedded: extra.embedded ?? false,
        loader: async () => response,
      })
    },
  })
  app.mount(host)
  mounted.push(app)
  await Promise.resolve()
  await nextTick()
  return host
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('solution evidence review', () => {
  it('decodes and displays persisted small-part estimates', async () => {
    const payload = { ...evidenceResponse(), assessment_revision: 1,
      part_assessments: [{ part_id: 'part-1', difficulty: 3, source: 'model',
        rationale: '先识别直角，再代入求边长。', review_note: '' }] }
    expect(decodeQuestionSolutionEvidenceResponse(payload)).toEqual(payload)
    const host = await mountReview(payload)
    expect(host.textContent).toContain('3 / 10')
    expect(host.textContent).toContain('先识别直角，再代入求边长。')
    expect(host.textContent).toContain('非实测难度')
  })
  it('decodes the exact public contract and rejects drift or private fields', () => {
    const payload = evidenceResponse()
    expect(decodeQuestionSolutionEvidenceResponse(payload)).toEqual(payload)
    expect(() => decodeQuestionSolutionEvidenceResponse({
      ...payload,
      local_path: 'C:/private/question.json',
    })).toThrow()
    expect(() => decodeQuestionSolutionEvidenceResponse({
      ...payload,
      available: false,
    })).toThrow('Invalid question solution evidence')
  })

  it('accepts v2 evidence points even when a step has no fine-term links', () => {
    const payload = evidenceResponse()
    const v2 = {
      ...payload,
      evidence: {
        ...payload.evidence!,
        schema_version: 'question-solution-evidence-v2' as const,
        parts: [{
          ...payload.evidence!.parts[0],
          evidence_points: [{
            evidence_point_id: 'point-1',
            step_index: 1,
            target: '完成方程求解',
            justification: '配方后得到正确解',
            answer_anchor: 'x = 2',
            observable_evidence: '写出配方过程并得到正确解。',
            depends_on: [],
            fine_term_links: [],
            equivalent_rules: [],
            counterexamples: [],
          }],
        }],
      },
    }
    expect(decodeQuestionSolutionEvidenceResponse(v2)).toEqual(v2)
  })

  it('keeps point-level direct, supporting, ambiguous and unmapped semantics visible', async () => {
    const host = await mountReview(evidenceResponse())
    const text = host.textContent?.replace(/\s+/g, '') ?? ''
    expect(text).toContain('小问1')
    expect(text).toContain('完成方程求解')
    expect(text).toContain('直接一元二次方程求根已映射到知识图谱')
    expect(text).toContain('前置配方法存在多个图谱候选，待治理确认')
    expect(text).toContain('前置规范整理步骤尚未建立图谱映射')
    expect(text).toContain('“直接考查”用于掌握度统计')
    expect(text).toContain('整题并集（只读汇总）')
    expect(text).toContain('候选节点2个；未映射词条1个')
    expect(text).not.toContain('kp_equation')
    expect(text).not.toContain('term-direct')
    expect(text).not.toContain('解题证据')
    expect(host.querySelector('#solution-evidence-title')?.textContent).toBe('知识细项与图谱映射')
  })

  it('shows an explicit empty state without inventing whole-question tags', async () => {
    const host = await mountReview({
      question_id: 42,
      available: false,
      evidence_version_id: null,
      status: null,
      evidence: null,
    })
    expect(host.textContent).toContain('这道题还没有知识细项映射')
    expect(host.querySelector('.solution-evidence__part')).toBeNull()
    expect(host.querySelector('.solution-evidence__summary')).toBeNull()
  })

  it('hides the empty mapping panel when folded into 判定点', async () => {
    const host = await mountReview({
      question_id: 42,
      available: false,
      evidence_version_id: null,
      status: null,
      evidence: null,
    }, { embedded: true })
    expect(host.querySelector('.solution-evidence')).toBeNull()
    expect(host.textContent).not.toContain('这道题还没有知识细项映射')
    expect(host.textContent).not.toContain('解题证据')
  })

  it('keeps mapping as a collapsed supplement without a second heading', async () => {
    const host = await mountReview(evidenceResponse(), { embedded: true })
    expect(host.querySelector('#solution-evidence-title')).toBeNull()
    expect(host.querySelector('.solution-evidence.is-embedded')).not.toBeNull()
    expect(host.querySelector('summary')?.textContent).toContain('知识细项与图谱映射')
    expect(host.textContent).toContain('完成方程求解')
    expect(host.textContent).not.toContain('解题证据')
    expect(host.textContent).not.toContain('拆分点、精细词条')
  })
})
