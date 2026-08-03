import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  decodeGraphEvidenceResponse,
  decodeGraphResponse,
  fetchGraph,
  fetchGraphEvidence,
} from '../graph'

const query = {
  scope: { mode: 'student' as const, student_ids: ['12'] },
  exam_scope: { mode: 'current' as const, session_ids: [14] as [number] },
}
const scope = { mode: 'student' as const, student_ids: ['12'], class_id: null }
const examScope = {
  mode: 'current' as const,
  session_ids: [14],
  sessions: [{ session_id: 14, session_name: '合成考试' }],
}
const coverage = { covered_items: 1, total_items: 1, missing_items: {} }
const currentStandard = {
  release_id: 'current-2026',
  content_hash: 'c'.repeat(64),
  taxonomy_revision: 3,
}
const node = {
  stable_key: 'kp_alg_linear_equation',
  display_name: '一元一次方程',
  definition: '含有一个未知数且次数为一的整式方程。',
  include_scope: '解与应用',
  exclude_scope: '二元方程',
  curriculum_anchors: ['义务教育数学课程标准'],
  observable_evidence: '能列式并求解',
  rationale: '课程核心知识点',
  evidence_source_ids: ['curriculum:2022'],
  mastery: {
    status: 'available' as const,
    value: 0.6,
    evidence_count: 1,
    parameter_version: 'd'.repeat(64),
    reason: null,
  },
  evidence: {
    student_count: 1,
    item_count: 1,
    deduction_count: 1,
    tag_context: {},
    error_counts: { primary: {}, secondary: {} },
  },
  missing_reasons: [],
}
const graph = {
  response_schema_version: 'knowledge-graph-current' as const,
  response_version: 'a'.repeat(64),
  scope,
  exam_scope: examScope,
  coverage,
  current_standard: currentStandard,
  nodes: [node],
  edges: [],
  missing: [],
  warnings: [],
  counts: { node_count: 1, edge_count: 0, evidence_row_count: 1, missing_count: 0 },
}
const evidenceItem = {
  student_id: 12,
  student_code: 'S12',
  student_name: '合成学生',
  class_id: '八年级1班',
  knowledge_key: node.stable_key,
  stable_key: node.stable_key,
  knowledge_label: node.display_name,
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
}

afterEach(() => vi.restoreAllMocks())

describe('current graph API contract', () => {
  it('accepts one current mastery plus node and relation authority fields', () => {
    expect(decodeGraphResponse(graph, query)).toEqual(graph)
    expect(() => decodeGraphResponse({ ...graph, current_standard: undefined }, query))
      .toThrow('Invalid knowledge graph response')
  })

  it('rejects edges whose endpoint is outside the returned graph', () => {
    expect(() => decodeGraphResponse({
      ...graph,
      edges: [{
        relation_key: 'e'.repeat(64),
        source_key: node.stable_key,
        target_key: 'kp_alg_equation_properties',
        relation_type: 'prerequisite',
        rationale: '先修依据',
        basis_kind: 'mathematical_logic',
        strength: 'required',
        evidence_source_ids: ['curriculum:2022'],
        source_locator: '课程标准第 3 节',
      }],
      counts: { ...graph.counts, edge_count: 1 },
    }, query)).toThrow('Invalid knowledge graph response')
  })

  it('uses only the neutral query and evidence routes', async () => {
    const evidence = {
      response_schema_version: 'knowledge-graph-evidence-current' as const,
      response_version: 'b'.repeat(64),
      scope,
      exam_scope: examScope,
      coverage,
      current_standard: currentStandard,
      stable_key: node.stable_key,
      display_name: node.display_name,
      items: [evidenceItem],
      total: 1,
      page: 1,
      page_size: 20,
      total_pages: 1,
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify(graph), { status: 200, headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(evidence), { status: 200, headers: { 'content-type': 'application/json' } }))
    await fetchGraph(query, undefined, [node.stable_key], 2)
    await fetchGraphEvidence(query, node.stable_key)
    expect(fetchMock.mock.calls.map(([request]) => String(request))).toEqual([
      expect.stringContaining('/api/graph/query'),
      expect.stringContaining('/api/graph/evidence'),
    ])
    expect(decodeGraphEvidenceResponse(evidence, {
      query,
      stableKey: node.stable_key,
      page: 1,
    })).toEqual(evidence)
  })
})
