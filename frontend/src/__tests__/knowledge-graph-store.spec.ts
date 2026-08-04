import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  GraphEvidenceResponse,
  GraphNode,
  GraphQueryInput,
  GraphResponse,
} from '../api/graph'
import { useKnowledgeGraphStore } from '../stores/knowledge-graph'

const query: GraphQueryInput = {
  scope: { mode: 'class', class_id: '一班' },
  exam_scope: { mode: 'current', session_ids: [7] },
}
const standard = { release_id: 'current', content_hash: 'c'.repeat(64), taxonomy_revision: 1 }
const node: GraphNode = {
  stable_key: 'kp_algebra',
  display_name: '代数',
  definition: '代数定义',
  include_scope: '代数内容',
  exclude_scope: '几何内容',
  curriculum_anchors: ['课程标准'],
  observable_evidence: '能列出代数式',
  rationale: '课程依据',
  evidence_source_ids: ['source'],
  mastery: { status: 'available', value: 0.7, evidence_count: 1, parameter_version: 'd'.repeat(64), reason: null },
  evidence: { student_count: 1, item_count: 1, deduction_count: 0, tag_context: {}, error_counts: {} },
  missing_reasons: [],
}
const graph: GraphResponse = {
  response_schema_version: 'knowledge-graph-current',
  response_version: 'a'.repeat(64),
  scope: { mode: 'class', student_ids: ['12'], class_id: '一班' },
  exam_scope: { mode: 'current', session_ids: [7], sessions: [{ session_id: 7, session_name: '期中' }] },
  coverage: { covered_items: 1, total_items: 1, missing_items: {} },
  current_standard: standard,
  nodes: [node],
  edges: [],
  missing: [],
  warnings: [],
  counts: { node_count: 1, edge_count: 0, evidence_row_count: 1, missing_count: 0 },
}
const evidence: GraphEvidenceResponse = {
  response_schema_version: 'knowledge-graph-evidence-current',
  response_version: 'b'.repeat(64),
  scope: graph.scope,
  exam_scope: graph.exam_scope,
  coverage: graph.coverage,
  current_standard: standard,
  stable_key: node.stable_key,
  display_name: node.display_name,
  items: [],
  total: 0,
  page: 1,
  page_size: 20,
  total_pages: 1,
}

beforeEach(() => setActivePinia(createPinia()))

describe('knowledge graph store', () => {
  it('loads one current graph and then evidence for its selected node', async () => {
    const store = useKnowledgeGraphStore()
    const graphLoader = vi.fn(async () => graph)
    const evidenceLoader = vi.fn(async () => evidence)
    await store.loadGraph(query, graphLoader)
    await store.selectNode(node, evidenceLoader)
    expect(store.graphState).toBe('ready')
    expect(store.graph?.current_standard).toEqual(standard)
    expect(store.selectedNodeKey).toBe(node.stable_key)
    expect(store.evidenceState).toBe('empty')
  })

  it('rejects evidence from a different current graph scope', async () => {
    const store = useKnowledgeGraphStore()
    await store.loadGraph(query, async () => graph)
    await store.selectNode(node, async () => ({
      ...evidence,
      scope: { ...evidence.scope, class_id: '二班' },
    }))
    expect(store.evidenceState).toBe('error')
    expect(store.evidence).toBeNull()
  })
})
