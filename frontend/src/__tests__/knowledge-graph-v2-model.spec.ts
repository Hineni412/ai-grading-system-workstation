import { describe, expect, it } from 'vitest'

import type { GraphV2Edge, GraphV2Node } from '../api/graph-v2'
import {
  buildGraphV2DisplayNodes,
  connectedNodeKeys,
  findGraphV2Path,
  summarizeGraphV2,
} from '../features/knowledge-graph/v2-model'

function node(key: string, value: number | null): GraphV2Node {
  return {
    stable_key: key,
    display_name: key,
    identity_revision: 1,
    mastery_v1: value === null
      ? { status: 'missing', value: null, evidence_count: 0, reason: null }
      : { status: 'available', value, evidence_count: 1, reason: null },
    mastery_v2: {
      status: 'unavailable', value: null, evidence_count: 0,
      reason: 'mastery_v2_not_enabled',
    },
    evidence: {
      student_count: value === null ? 0 : 1,
      item_count: value === null ? 0 : 1,
      deduction_count: 0,
      tag_context: {},
      error_counts: { primary: {}, secondary: {} },
    },
    missing_reasons: value === null ? ['no_evidence_in_scope'] : [],
  }
}

const edges: GraphV2Edge[] = [
  {
    relation_id: 'r1', source_key: 'kp_a', target_key: 'kp_b',
    relation_type: 'prerequisite', rationale: 'a 先看 b', revision: 1,
  },
  {
    relation_id: 'r2', source_key: 'kp_b', target_key: 'kp_c',
    relation_type: 'parent', rationale: 'b 属于 c', revision: 1,
  },
  {
    relation_id: 'r3', source_key: 'kp_c', target_key: 'kp_d',
    relation_type: 'related', rationale: 'c 与 d 相关', revision: 1,
  },
]

describe('knowledge graph v2 view model', () => {
  it('keeps missing evidence separate from mastery bands and summarizes relations', () => {
    const nodes = [
      node('kp_missing', null),
      node('kp_weak', 0.5),
      node('kp_review', 0.6),
      node('kp_slight', 0.75),
      node('kp_stable', 0.9),
    ]
    expect(buildGraphV2DisplayNodes(nodes).map((item) => item.state)).toEqual([
      'missing', 'weak', 'review', 'slight', 'stable',
    ])
    expect(summarizeGraphV2(nodes, edges)).toMatchObject({
      total: 5, relationTotal: 3, missing: 1, weak: 1, review: 1, slight: 1, stable: 1,
    })
  })

  it('focuses the selected node and its immediate filtered neighbours', () => {
    expect([...connectedNodeKeys(edges, 'kp_b', new Set(['prerequisite']))]).toEqual([
      'kp_b', 'kp_a',
    ])
  })

  it('finds a directed path while treating related relations as bidirectional', () => {
    expect(findGraphV2Path(
      edges, 'kp_a', 'kp_d', new Set(['prerequisite', 'parent', 'related']),
    )).toEqual({
      nodeKeys: ['kp_a', 'kp_b', 'kp_c', 'kp_d'],
      edgeIds: ['r1', 'r2', 'r3'],
    })
    expect(findGraphV2Path(
      edges, 'kp_b', 'kp_a', new Set(['prerequisite']),
    )).toBeNull()
    expect(findGraphV2Path(
      edges, 'kp_d', 'kp_c', new Set(['related']),
    )).toEqual({ nodeKeys: ['kp_d', 'kp_c'], edgeIds: ['r3'] })
  })

  it('lays out 1000 nodes with finite values for text-directory fallback', () => {
    const model = buildGraphV2DisplayNodes(Array.from(
      { length: 1000 },
      (_, index) => node(`kp_synthetic_${index}`, index % 7 === 0 ? null : (index % 101) / 100),
    ))
    expect(model).toHaveLength(1000)
    expect(model.every((item) => (
      Number.isFinite(item.x) && Number.isFinite(item.y) && Number.isFinite(item.size)
    ))).toBe(true)
  })
})
