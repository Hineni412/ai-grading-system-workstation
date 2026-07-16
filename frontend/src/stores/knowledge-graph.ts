import { ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '../api/errors'
import {
  decodeGraphRowsResponse,
  fetchScopedGraphEvidence,
  fetchScopedGraphRows,
  hasMatchingGraphEvidenceScope,
  normalizeGraphQuery,
  type GraphEvidenceItem,
  type GraphEvidenceResponse,
  type GraphNode,
  type GraphQueryInput,
  type GraphRowsResponse,
} from '../api/graph'
import type { ResourceState } from './workbench'

export type GraphLoader = (
  query: GraphQueryInput,
  signal?: AbortSignal,
) => Promise<GraphRowsResponse>

export type GraphEvidenceLoader = (
  query: GraphQueryInput,
  knowledgeKey: string,
  signal?: AbortSignal,
  page?: number,
) => Promise<GraphEvidenceResponse>

function isCancelled(error: unknown): boolean {
  return (
    (typeof error === 'object' && error !== null && 'name' in error && error.name === 'AbortError') ||
    (error instanceof ApiError && error.kind === 'cancelled')
  )
}

function queryKey(query: GraphQueryInput): string {
  return JSON.stringify(query)
}

function arraysEqual<T>(left: T[], right: T[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}

function evidenceMatchesGraph(
  loaded: GraphEvidenceResponse,
  source: GraphRowsResponse,
  knowledgeKey: string,
  page: number,
): boolean {
  if (
    loaded.knowledge_key !== knowledgeKey ||
    loaded.page !== page ||
    loaded.scope.mode !== source.scope.mode ||
    loaded.scope.class_id !== source.scope.class_id ||
    !arraysEqual(loaded.scope.student_ids, source.scope.student_ids) ||
    loaded.exam_scope.mode !== source.exam_scope.mode ||
    !arraysEqual(loaded.exam_scope.session_ids, source.exam_scope.session_ids)
  ) return false

  return loaded.items.every((item) => (
    item.knowledge_key === knowledgeKey
  )) && hasMatchingGraphEvidenceScope(loaded)
}

function evidenceIdentity(item: GraphEvidenceItem): string {
  return [item.session_id, item.student_id, item.question_id, item.bank_question_id].join('\u0000')
}

export const useKnowledgeGraphStore = defineStore('knowledge-graph', () => {
  const requestedQuery = ref<GraphQueryInput | null>(null)
  const appliedQuery = ref<GraphQueryInput | null>(null)
  const graph = ref<GraphRowsResponse | null>(null)
  const graphState = ref<ResourceState>('idle')
  const graphError = ref('')
  const graphUpdatedAt = ref<string | null>(null)

  const selectedNodeKey = ref<string | null>(null)
  const evidence = ref<GraphEvidenceResponse | null>(null)
  const evidenceState = ref<ResourceState>('idle')
  const evidenceError = ref('')

  let graphController: AbortController | null = null
  let evidenceController: AbortController | null = null
  let graphGeneration = 0
  let evidenceGeneration = 0

  function clearSelection(): void {
    evidenceController?.abort()
    evidenceController = null
    evidenceGeneration += 1
    selectedNodeKey.value = null
    evidence.value = null
    evidenceState.value = 'idle'
    evidenceError.value = ''
  }

  function clearScope(): void {
    graphController?.abort()
    graphController = null
    graphGeneration += 1
    requestedQuery.value = null
    appliedQuery.value = null
    graph.value = null
    graphState.value = 'idle'
    graphError.value = ''
    graphUpdatedAt.value = null
    clearSelection()
  }

  async function loadGraph(
    query: GraphQueryInput,
    loader: GraphLoader = fetchScopedGraphRows,
  ): Promise<void> {
    const normalized = normalizeGraphQuery(query)
    const nextKey = queryKey(normalized)
    const scopeChanged = appliedQuery.value === null || queryKey(appliedQuery.value) !== nextKey

    graphController?.abort()
    const controller = new AbortController()
    graphController = controller
    const generation = ++graphGeneration
    requestedQuery.value = normalized
    graphState.value = 'loading'
    graphError.value = ''

    if (scopeChanged) {
      graph.value = null
      graphUpdatedAt.value = null
      clearSelection()
    }

    try {
      const loaded = decodeGraphRowsResponse(
        await loader(normalized, controller.signal),
        normalized,
      )
      if (generation !== graphGeneration || queryKey(requestedQuery.value!) !== nextKey) return
      appliedQuery.value = normalized
      graph.value = loaded
      graphState.value = loaded.nodes.length === 0 ? 'empty' : 'ready'
      graphUpdatedAt.value = new Date().toISOString()
    } catch (error) {
      if (generation !== graphGeneration || queryKey(requestedQuery.value!) !== nextKey) return
      if (isCancelled(error)) {
        graphState.value = graph.value === null
          ? 'idle'
          : graph.value.nodes.length === 0 ? 'empty' : 'ready'
        return
      }
      const hasSameScopeGraph = graph.value !== null && appliedQuery.value !== null &&
        queryKey(appliedQuery.value) === nextKey
      graphState.value = hasSameScopeGraph ? 'stale-error' : 'error'
      graphError.value = '知识图谱暂时无法更新'
    } finally {
      if (graphController === controller) graphController = null
    }
  }

  async function selectNode(
    node: GraphNode,
    loader: GraphEvidenceLoader = fetchScopedGraphEvidence,
  ): Promise<void> {
    if (
      appliedQuery.value === null ||
      graph.value === null ||
      !graph.value.nodes.some((candidate) => candidate.knowledge_key === node.knowledge_key)
    ) return

    evidenceController?.abort()
    const controller = new AbortController()
    evidenceController = controller
    const generation = ++evidenceGeneration
    const query = appliedQuery.value
    const source = graph.value
    const knowledgeKey = node.knowledge_key
    const changedNode = selectedNodeKey.value !== knowledgeKey
    selectedNodeKey.value = knowledgeKey
    evidenceState.value = 'loading'
    evidenceError.value = ''
    if (changedNode) evidence.value = null

    try {
      const loaded = await loader(query, knowledgeKey, controller.signal, 1)
      if (
        generation !== evidenceGeneration ||
        selectedNodeKey.value !== knowledgeKey ||
        graph.value !== source
      ) return
      if (!evidenceMatchesGraph(loaded, source, knowledgeKey, 1)) {
        throw new Error('Graph evidence scope mismatch')
      }
      evidence.value = loaded
      evidenceState.value = loaded.items.length === 0 ? 'empty' : 'ready'
    } catch (error) {
      if (generation !== evidenceGeneration || selectedNodeKey.value !== knowledgeKey) return
      if (isCancelled(error)) {
        evidenceState.value = evidence.value === null
          ? 'idle'
          : evidence.value.items.length === 0 ? 'empty' : 'ready'
        return
      }
      evidenceState.value = evidence.value === null ? 'error' : 'stale-error'
      evidenceError.value = '知识点证据暂时无法加载'
    } finally {
      if (evidenceController === controller) evidenceController = null
    }
  }

  async function loadMoreEvidence(
    loader: GraphEvidenceLoader = fetchScopedGraphEvidence,
  ): Promise<void> {
    if (
      appliedQuery.value === null ||
      graph.value === null ||
      selectedNodeKey.value === null ||
      evidence.value === null ||
      evidence.value.page >= evidence.value.total_pages ||
      evidenceState.value === 'loading'
    ) return

    evidenceController?.abort()
    const controller = new AbortController()
    evidenceController = controller
    const generation = ++evidenceGeneration
    const query = appliedQuery.value
    const source = graph.value
    const knowledgeKey = selectedNodeKey.value
    const previous = evidence.value
    const nextPage = previous.page + 1
    evidenceState.value = 'loading'
    evidenceError.value = ''

    try {
      const loaded = await loader(query, knowledgeKey, controller.signal, nextPage)
      if (
        generation !== evidenceGeneration ||
        selectedNodeKey.value !== knowledgeKey ||
        graph.value !== source ||
        evidence.value !== previous
      ) return
      if (!evidenceMatchesGraph(loaded, source, knowledgeKey, nextPage)) {
        throw new Error('Graph evidence scope mismatch')
      }
      const seen = new Set(previous.items.map(evidenceIdentity))
      const appended = loaded.items.filter((item) => {
        const identity = evidenceIdentity(item)
        if (seen.has(identity)) return false
        seen.add(identity)
        return true
      })
      evidence.value = { ...loaded, items: [...previous.items, ...appended] }
      evidenceState.value = evidence.value.items.length === 0 ? 'empty' : 'ready'
    } catch (error) {
      if (generation !== evidenceGeneration || selectedNodeKey.value !== knowledgeKey) return
      if (isCancelled(error)) {
        evidenceState.value = previous.items.length === 0 ? 'empty' : 'ready'
        return
      }
      evidenceState.value = 'stale-error'
      evidenceError.value = '更多证据暂时无法加载'
    } finally {
      if (evidenceController === controller) evidenceController = null
    }
  }

  async function retryGraph(loader: GraphLoader = fetchScopedGraphRows): Promise<void> {
    if (requestedQuery.value !== null) await loadGraph(requestedQuery.value, loader)
  }

  async function retryEvidence(loader: GraphEvidenceLoader = fetchScopedGraphEvidence): Promise<void> {
    if (selectedNodeKey.value === null || graph.value === null) return
    const node = graph.value.nodes.find((candidate) => candidate.knowledge_key === selectedNodeKey.value)
    if (node) await selectNode(node, loader)
  }

  return {
    requestedQuery,
    appliedQuery,
    graph,
    graphState,
    graphError,
    graphUpdatedAt,
    selectedNodeKey,
    evidence,
    evidenceState,
    evidenceError,
    loadGraph,
    selectNode,
    loadMoreEvidence,
    retryGraph,
    retryEvidence,
    clearScope,
  }
})
