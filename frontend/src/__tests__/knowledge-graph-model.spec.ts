import { describe, expect, it } from 'vitest'

import type { GraphEdge, GraphNode } from '../api/graph'
import {
  buildGraphDisplayNodes,
  findGraphPath,
  summarizeGraph,
} from '../features/knowledge-graph/model'

function node(key: string, value: number | null): GraphNode {
  return {
    stable_key: key,
    display_name: key,
    definition: '定义',
    include_scope: '包含',
    exclude_scope: '排除',
    curriculum_anchors: ['课程标准'],
    observable_evidence: '可观察证据',
    rationale: '节点依据',
    evidence_source_ids: ['source'],
    mastery: value === null
      ? { status: 'missing', value: null, evidence_count: 0, parameter_version: null, reason: 'missing' }
      : { status: 'available', value, evidence_count: 1, parameter_version: 'a'.repeat(64), reason: null },
    evidence: {
      student_count: 1,
      item_count: 2,
      deduction_count: 1,
      tag_context: {},
      error_counts: {},
    },
    missing_reasons: value === null ? ['no_evidence_in_scope'] : [],
  }
}

const edges: GraphEdge[] = [{
  relation_key: 'b'.repeat(64),
  source_key: 'kp_a',
  target_key: 'kp_b',
  relation_type: 'prerequisite',
  rationale: '先修',
  basis_kind: 'mathematical_logic',
  strength: 'required',
  evidence_source_ids: ['source'],
  source_locator: '课程标准',
}]

describe('knowledge graph model', () => {
  it('uses the one current mastery value for display and summary', () => {
    const nodes = [node('kp_a', null), node('kp_b', 0.55), node('kp_c', 0.95)]
    expect(buildGraphDisplayNodes(nodes).map((item) => item.state)).toEqual([
      'missing', 'weak', 'stable',
    ])
    expect(summarizeGraph(nodes, edges)).toMatchObject({
      total: 3,
      relationTotal: 1,
      missing: 1,
      weak: 1,
      stable: 1,
    })
  })

  it('finds paths using the current relation key', () => {
    expect(findGraphPath(edges, 'kp_a', 'kp_b', new Set(['prerequisite'])))
      .toEqual({ nodeKeys: ['kp_a', 'kp_b'], edgeIds: ['b'.repeat(64)] })
    expect(findGraphPath(edges, 'kp_b', 'kp_a', new Set(['prerequisite']))).toBeNull()
  })
})
