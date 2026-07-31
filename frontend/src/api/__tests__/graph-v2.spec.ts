import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  decodeGraphV2EvidenceResponse,
  decodeGraphV2Response,
  decodeMasteryComparison,
  decodeMasteryRollout,
  decodeRelationReviewQueue,
  fetchGraphV2,
} from '../graph-v2'

const query = {
  scope: { mode: 'student' as const, student_ids: ['12'] },
  exam_scope: { mode: 'current' as const, session_ids: [14] as [number] },
}
const scope = { mode: 'student', student_ids: ['12'], class_id: null }
const examScope = {
  mode: 'current',
  session_ids: [14],
  sessions: [{ session_id: 14, session_name: '合成考试' }],
}
const coverage = { covered_items: 1, total_items: 1, missing_items: {} }
const node = {
  stable_key: 'kp_alg_linear_equation',
  display_name: '一元一次方程',
  identity_revision: 1,
  mastery_v1: { status: 'available', value: 0.6, evidence_count: 1, reason: null },
  mastery_v2: {
    status: 'unavailable', value: null, evidence_count: 0,
    reason: 'mastery_v2_not_enabled',
  },
  evidence: {
    student_count: 1, item_count: 1, deduction_count: 1,
    tag_context: {}, error_counts: { primary: {}, secondary: {} },
  },
  missing_reasons: [],
}
const graph = {
  response_schema_version: 'knowledge-graph-v2',
  response_version: 'a'.repeat(64),
  mastery_mode: 'v1',
  mastery_parameter_version: null,
  scope,
  exam_scope: examScope,
  coverage,
  nodes: [node],
  edges: [],
  missing: [],
  warnings: ['掌握度 v2 尚未启用'],
  counts: { node_count: 1, edge_count: 0, evidence_row_count: 1, missing_count: 0 },
}
const evidenceItem = {
  student_id: 12,
  student_code: 'S12',
  student_name: '合成学生',
  class_id: '八年级1班',
  knowledge_key: 'knowledge_point:一元一次方程',
  knowledge_label: '一元一次方程',
  session_id: 14,
  session_name: '合成考试',
  question_id: 'Q1',
  bank_question_id: 1,
  score_awarded: 6,
  full_score: 10,
  score_rate: 0.6,
  tag_context: {},
  actionable_reasons: ['合成原因'],
  error_counts: { primary: {}, secondary: {} },
  stable_key: 'kp_alg_linear_equation',
}

afterEach(() => vi.restoreAllMocks())

describe('graph v2 API contract', () => {
  it('accepts stable identities, confirmed edges and explicit dual mastery states', () => {
    expect(decodeGraphV2Response(graph, query)).toEqual(graph)
  })

  it('rejects edges whose endpoint is outside the returned graph', () => {
    expect(() => decodeGraphV2Response({
      ...graph,
      edges: [{
        relation_id: 'rel-1',
        source_key: node.stable_key,
        target_key: 'kp_alg_equation_properties',
        relation_type: 'prerequisite',
        rationale: '合成关系',
        revision: 2,
      }],
      counts: { ...graph.counts, edge_count: 1 },
    }, query)).toThrow('Invalid graph v2 response')
  })

  it('posts stable graph controls without legacy identities', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify(graph),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ))
    await fetchGraphV2(query, undefined, ['kp_alg_linear_equation'], 2)
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      ...query,
      knowledge_keys: ['kp_alg_linear_equation'],
      prerequisite_depth: 2,
    })
  })

  it('keeps evidence bound to the stable key, page and exact scope', () => {
    const response = {
      response_schema_version: 'knowledge-graph-evidence-v2',
      response_version: 'b'.repeat(64),
      scope,
      exam_scope: examScope,
      coverage,
      stable_key: node.stable_key,
      display_name: node.display_name,
      items: [evidenceItem],
      total: 1,
      page: 1,
      page_size: 20,
      total_pages: 1,
    }
    expect(decodeGraphV2EvidenceResponse(response, {
      query, stableKey: node.stable_key, page: 1,
    })).toEqual(response)
    expect(() => decodeGraphV2EvidenceResponse({
      ...response,
      items: [{ ...evidenceItem, student_id: 99 }],
    }, {
      query, stableKey: node.stable_key, page: 1,
    })).toThrow('Invalid graph v2 evidence')
  })

  it('validates the read-only suggested relation queue', () => {
    const queue = {
      status: 'suggested',
      items: [{
        relation_id: 'rel-1',
        source_key: 'kp_alg_linear_equation',
        source_name: '一元一次方程',
        target_key: 'kp_alg_equation_properties',
        target_name: '等式性质',
        relation_type: 'prerequisite',
        source_kind: 'model',
        source_reference: null,
        rationale: '合成候选理由',
        model_name: 'fake',
        model_version: 'v1',
        prompt_version: 'p1',
        confidence: 0.8,
        conflict_codes: [],
        revision: 1,
        updated_at: '2026-07-30T00:00:00Z',
      }],
      total: 1,
      page: 1,
      page_size: 20,
      total_pages: 1,
    }
    expect(decodeRelationReviewQueue(queue)).toEqual(queue)
  })

  it('validates ranked mastery differences and their review gate', () => {
    const evaluationId = 'c'.repeat(64)
    const parameterVersion = 'd'.repeat(64)
    const response = {
      schema_version: 'mastery-v1-v2-comparison-v1',
      evaluation_id: evaluationId,
      as_of: '2026-01-31T00:00:00+00:00',
      parameter_version: parameterVersion,
      review_delta: 0.1,
      items: [{
        item_hash: 'e'.repeat(64),
        student_id: '12',
        student_code: 'S12',
        student_name: '合成学生',
        class_id: '合成班',
        stable_key: node.stable_key,
        display_name: node.display_name,
        mastery_v1: 1,
        mastery_v2: {
          schema_version: 'mastery-v2-result-v1',
          stable_key: node.stable_key,
          status: 'available',
          value: 0.766667,
          as_of: '2026-01-31T00:00:00+00:00',
          parameter_version: parameterVersion,
          direct_evidence_count: 1,
          effective_sample_weight: 1,
          prior_mean: 0.65,
          prior_strength: 2,
          contributions: [],
          layers: [],
          prerequisites: [],
          explanations: ['合成解释'],
        },
        signed_delta: -0.233333,
        absolute_delta: 0.233333,
        reason_codes: ['small_sample_shrinkage'],
        reasons: ['小样本收缩'],
        requires_review: true,
      }],
      required_review_count: 1,
      maximum_absolute_delta: 0.233333,
      performance: { duration_ms: 1, items_per_second: 1000 },
      gate: {
        evaluation_id: evaluationId,
        parameter_version: parameterVersion,
        revision: 1,
        required_review_count: 1,
        accepted_count: 0,
        rejected_count: 0,
        pending_count: 1,
        passed: false,
      },
    }
    expect(decodeMasteryComparison(response)).toEqual(response)
    expect(() => decodeMasteryComparison({
      ...response,
      required_review_count: 0,
    })).toThrow('Invalid mastery comparison')
  })

  it('requires v1 fallback state to clear every active v2 pointer', () => {
    const fallback = {
      enabled: false,
      active_mode: 'v1',
      active_parameter_version: null,
      approved_evaluation_id: null,
      revision: 2,
      updated_by: 'teacher-synthetic',
      reason: '回退演练',
      updated_at: '2026-01-31 08:00:00',
    }
    expect(decodeMasteryRollout(fallback)).toEqual(fallback)
    expect(() => decodeMasteryRollout({
      ...fallback,
      active_parameter_version: 'a'.repeat(64),
    })).toThrow('Invalid mastery rollout state')
  })
})
