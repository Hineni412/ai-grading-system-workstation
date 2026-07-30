import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import type {
  GraphV2EvidenceResponse,
  GraphV2Node,
  GraphV2Response,
} from '../api/graph-v2'
import type { GraphQueryInput } from '../api/graph'
import { useKnowledgeGraphV2Store } from '../stores/knowledge-graph-v2'

const queryA: GraphQueryInput = {
  scope: { mode: 'class', class_id: '七年级一班' },
  exam_scope: { mode: 'current', session_ids: [7] },
}
const queryB: GraphQueryInput = {
  scope: { mode: 'class', class_id: '七年级二班' },
  exam_scope: { mode: 'current', session_ids: [8] },
}

function graph(query: GraphQueryInput, key: string): GraphV2Response {
  const sessionIds = query.exam_scope.mode === 'cross_exam' ? [7, 8] : query.exam_scope.session_ids
  return {
    response_schema_version: 'knowledge-graph-v2',
    response_version: 'a'.repeat(64),
    scope: {
      mode: query.scope.mode,
      student_ids: query.scope.mode === 'class' ? ['12'] : query.scope.student_ids,
      class_id: query.scope.mode === 'class' ? query.scope.class_id : null,
    },
    exam_scope: {
      mode: query.exam_scope.mode,
      session_ids: sessionIds,
      sessions: sessionIds.map((id) => ({ session_id: id, session_name: `合成考试-${id}` })),
    },
    coverage: { covered_items: 1, total_items: 1, missing_items: {} },
    nodes: [{
      stable_key: key,
      display_name: key,
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
    }],
    edges: [],
    missing: [],
    warnings: [],
    counts: { node_count: 1, edge_count: 0, evidence_row_count: 1, missing_count: 0 },
  }
}

function evidence(source: GraphV2Response, node: GraphV2Node, studentId = 12): GraphV2EvidenceResponse {
  return {
    response_schema_version: 'knowledge-graph-evidence-v2',
    response_version: 'b'.repeat(64),
    scope: source.scope,
    exam_scope: source.exam_scope,
    coverage: source.coverage,
    stable_key: node.stable_key,
    display_name: node.display_name,
    items: [{
      student_id: studentId,
      student_code: `S${studentId}`,
      student_name: '合成学生',
      class_id: source.scope.class_id ?? '七年级一班',
      knowledge_key: 'knowledge_point:合成',
      stable_key: node.stable_key,
      knowledge_label: node.display_name,
      session_id: source.exam_scope.session_ids[0]!,
      session_name: source.exam_scope.sessions[0]!.session_name,
      question_id: 'Q1',
      bank_question_id: 1,
      score_awarded: 3,
      full_score: 5,
      score_rate: 0.6,
      tag_context: {},
      actionable_reasons: [],
      error_counts: { primary: {}, secondary: {} },
    }],
    total: 1,
    page: 1,
    page_size: 20,
    total_pages: 1,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise })
  return { promise, resolve }
}

beforeEach(() => setActivePinia(createPinia()))

describe('knowledge graph v2 store', () => {
  it('does not let an older scope overwrite the current graph', async () => {
    const store = useKnowledgeGraphV2Store()
    const first = deferred<GraphV2Response>()
    const second = deferred<GraphV2Response>()
    const firstLoad = store.loadGraph(queryA, async () => first.promise)
    const secondLoad = store.loadGraph(queryB, async () => second.promise)
    second.resolve(graph(queryB, 'kp_second'))
    await secondLoad
    first.resolve(graph(queryA, 'kp_first'))
    await firstLoad
    expect(store.graph?.nodes[0]?.stable_key).toBe('kp_second')
  })

  it('clears selected evidence as soon as the scope changes', async () => {
    const store = useKnowledgeGraphV2Store()
    const source = graph(queryA, 'kp_first')
    await store.loadGraph(queryA, async () => source)
    await store.selectNode(source.nodes[0]!, async () => evidence(source, source.nodes[0]!))
    const pending = deferred<GraphV2Response>()
    const load = store.loadGraph(queryB, async () => pending.promise)
    expect(store.selectedNodeKey).toBeNull()
    expect(store.evidence).toBeNull()
    pending.resolve(graph(queryB, 'kp_second'))
    await load
  })

  it('rejects evidence from outside the exact applied student scope', async () => {
    const store = useKnowledgeGraphV2Store()
    const source = graph(queryA, 'kp_first')
    await store.loadGraph(queryA, async () => source)
    await store.selectNode(
      source.nodes[0]!,
      async () => evidence(source, source.nodes[0]!, 99),
    )
    expect(store.evidence).toBeNull()
    expect(store.evidenceState).toBe('error')
  })
})
