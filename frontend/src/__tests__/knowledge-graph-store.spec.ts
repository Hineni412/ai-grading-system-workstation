import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import type {
  GraphEvidenceResponse,
  GraphNode,
  GraphQueryInput,
  GraphRowsResponse,
} from '../api/graph'
import { useKnowledgeGraphStore } from '../stores/knowledge-graph'

const queryA: GraphQueryInput = {
  scope: { mode: 'class', class_id: '七年级一班' },
  exam_scope: { mode: 'current', session_ids: [7] },
}
const queryB: GraphQueryInput = {
  scope: { mode: 'class', class_id: '七年级二班' },
  exam_scope: { mode: 'current', session_ids: [8] },
}

function graph(query: GraphQueryInput, label: string): GraphRowsResponse {
  const node: GraphNode = {
    knowledge_key: `knowledge_point:${label}`,
    knowledge_label: label,
    student_count: 1,
    item_count: 1,
    deduction_count: 1,
    average_mastery: 0.62,
    tag_context: {},
    error_counts: { primary: {}, secondary: {} },
  }
  const sessionIds = query.exam_scope.mode === 'cross_exam' ? [7, 8] : query.exam_scope.session_ids
  return {
    scope: {
      mode: query.scope.mode,
      student_ids: query.scope.mode === 'class' ? ['12', '15'] : query.scope.student_ids,
      class_id: query.scope.mode === 'class' ? query.scope.class_id : null,
    },
    exam_scope: {
      mode: query.exam_scope.mode,
      session_ids: [...sessionIds],
      sessions: sessionIds.map((id) => ({ session_id: id, session_name: `匿名考试-${id}` })),
    },
    rows: [],
    nodes: [node],
    edges: [],
    coverage: { covered_items: 1, total_items: 1, missing_items: {} },
    warnings: [],
    diagnosis_identity: 'question_tag',
  }
}

function evidence(
  source: GraphRowsResponse,
  knowledgeKey: string,
  page = 1,
  studentId = 12,
): GraphEvidenceResponse {
  return {
    scope: source.scope,
    exam_scope: source.exam_scope,
    knowledge_key: knowledgeKey,
    knowledge_label: knowledgeKey.replace('knowledge_point:', ''),
    items: [{
      student_id: studentId,
      student_code: `S${studentId}`,
      student_name: `匿名学生-${studentId}`,
      class_id: source.scope.class_id ?? '七年级一班',
      knowledge_key: knowledgeKey,
      knowledge_label: knowledgeKey.replace('knowledge_point:', ''),
      session_id: source.exam_scope.session_ids[0]!,
      session_name: source.exam_scope.sessions[0]!.session_name,
      question_id: `Q${studentId}`,
      bank_question_id: 100 + studentId,
      score_awarded: 3,
      full_score: 5,
      score_rate: 0.6,
      tag_context: {},
      actionable_reasons: [],
      error_counts: { primary: {}, secondary: {} },
    }],
    total: 21,
    page,
    page_size: 20,
    total_pages: 2,
    coverage: source.coverage,
    warnings: [],
    diagnosis_identity: 'question_tag',
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

async function settle(): Promise<void> {
  await Promise.resolve()
  await Promise.resolve()
}

beforeEach(() => setActivePinia(createPinia()))

describe('knowledge graph store', () => {
  it('does not let an older scope overwrite the current graph', async () => {
    const store = useKnowledgeGraphStore()
    const first = deferred<GraphRowsResponse>()
    const second = deferred<GraphRowsResponse>()
    const firstLoad = store.loadGraph(queryA, async () => first.promise)
    const secondLoad = store.loadGraph(queryB, async () => second.promise)

    second.resolve(graph(queryB, '一次函数'))
    await secondLoad
    first.resolve(graph(queryA, '三角形全等'))
    await firstLoad

    expect(store.appliedQuery).toEqual(queryB)
    expect(store.graph?.nodes[0]?.knowledge_label).toBe('一次函数')
  })

  it('clears node evidence as soon as the scope changes', async () => {
    const store = useKnowledgeGraphStore()
    const graphA = graph(queryA, '三角形全等')
    await store.loadGraph(queryA, async () => graphA)
    await store.selectNode(graphA.nodes[0]!, async () => evidence(graphA, graphA.nodes[0]!.knowledge_key))

    const pending = deferred<GraphRowsResponse>()
    const load = store.loadGraph(queryB, async () => pending.promise)
    expect(store.selectedNodeKey).toBeNull()
    expect(store.evidence).toBeNull()
    pending.resolve(graph(queryB, '一次函数'))
    await load
  })

  it('keeps the last successful graph when a same-scope refresh fails', async () => {
    const store = useKnowledgeGraphStore()
    const graphA = graph(queryA, '三角形全等')
    await store.loadGraph(queryA, async () => graphA)
    await store.loadGraph(queryA, async () => { throw new Error('temporary') })

    expect(store.graph).toEqual(graphA)
    expect(store.graphState).toBe('stale-error')
    expect(store.graphError).toBe('知识图谱暂时无法更新')
  })

  it('does not let old evidence replace a newly selected node', async () => {
    const store = useKnowledgeGraphStore()
    const source = graph(queryA, '三角形全等')
    const other: GraphNode = { ...source.nodes[0]!, knowledge_key: 'knowledge_point:一次函数', knowledge_label: '一次函数' }
    source.nodes.push(other)
    await store.loadGraph(queryA, async () => source)
    const first = deferred<GraphEvidenceResponse>()

    const firstLoad = store.selectNode(source.nodes[0]!, async () => first.promise)
    await store.selectNode(other, async () => evidence(source, other.knowledge_key))
    first.resolve(evidence(source, source.nodes[0]!.knowledge_key))
    await firstLoad
    await settle()

    expect(store.selectedNodeKey).toBe(other.knowledge_key)
    expect(store.evidence?.knowledge_key).toBe(other.knowledge_key)
  })

  it('appends a later evidence page without duplicate identities', async () => {
    const store = useKnowledgeGraphStore()
    const source = graph(queryA, '三角形全等')
    const selected = source.nodes[0]!
    const firstPage = evidence(source, selected.knowledge_key, 1, 12)
    const secondPage = evidence(source, selected.knowledge_key, 2, 15)
    secondPage.items.unshift({ ...firstPage.items[0]! })
    await store.loadGraph(queryA, async () => source)
    await store.selectNode(selected, async () => firstPage)
    await store.loadMoreEvidence(async () => secondPage)

    expect(store.evidence?.items.map((item) => item.student_id)).toEqual([12, 15])
    expect(store.evidence?.page).toBe(2)
  })

  it('rejects injected evidence from a student outside the applied graph scope', async () => {
    const store = useKnowledgeGraphStore()
    const source = graph(queryA, '三角形全等')
    const selected = source.nodes[0]!
    await store.loadGraph(queryA, async () => source)
    await store.selectNode(selected, async () => evidence(source, selected.knowledge_key, 1, 99))

    expect(store.evidence).toBeNull()
    expect(store.evidenceState).toBe('error')
    expect(store.evidenceError).toBe('知识点证据暂时无法加载')
  })
})
