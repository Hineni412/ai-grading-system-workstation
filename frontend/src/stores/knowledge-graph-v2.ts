import { ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '../api/errors'
import {
  decodeGraphV2Response,
  fetchGraphV2,
  fetchGraphV2Evidence,
  type GraphV2EvidenceItem,
  type GraphV2EvidenceResponse,
  type GraphV2Node,
  type GraphV2Response,
} from '../api/graph-v2'
import { normalizeGraphQuery, type GraphQueryInput } from '../api/graph'
import type { ResourceState } from './workbench'

export type GraphV2Loader = (
  query: GraphQueryInput,
  signal?: AbortSignal,
) => Promise<GraphV2Response>

export type GraphV2EvidenceLoader = (
  query: GraphQueryInput,
  stableKey: string,
  signal?: AbortSignal,
  page?: number,
) => Promise<GraphV2EvidenceResponse>

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
  loaded: GraphV2EvidenceResponse,
  source: GraphV2Response,
  stableKey: string,
  page: number,
): boolean {
  const node = source.nodes.find((candidate) => candidate.stable_key === stableKey)
  return (
    node !== undefined &&
    loaded.display_name === node.display_name &&
    loaded.stable_key === stableKey &&
    loaded.page === page &&
    loaded.scope.mode === source.scope.mode &&
    loaded.scope.class_id === source.scope.class_id &&
    arraysEqual(loaded.scope.student_ids, source.scope.student_ids) &&
    loaded.exam_scope.mode === source.exam_scope.mode &&
    arraysEqual(loaded.exam_scope.session_ids, source.exam_scope.session_ids) &&
    loaded.items.every((item) => (
      item.stable_key === stableKey &&
      loaded.scope.student_ids.includes(String(item.student_id)) &&
      loaded.exam_scope.session_ids.includes(item.session_id) &&
      (loaded.scope.mode !== 'class' || item.class_id === loaded.scope.class_id)
    ))
  )
}

function evidenceIdentity(item: GraphV2EvidenceItem): string {
  return [item.session_id, item.student_id, item.question_id, item.bank_question_id].join('\u0000')
}

export const useKnowledgeGraphV2Store = defineStore('knowledge-graph-v2', () => {
  const requestedQuery = ref<GraphQueryInput | null>(null)
  const appliedQuery = ref<GraphQueryInput | null>(null)
  const graph = ref<GraphV2Response | null>(null)
  const graphState = ref<ResourceState>('idle')
  const graphError = ref('')
  const graphUpdatedAt = ref<string | null>(null)

  const selectedNodeKey = ref<string | null>(null)
  const evidence = ref<GraphV2EvidenceResponse | null>(null)
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
    loader: GraphV2Loader = fetchGraphV2,
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
      const loaded = decodeGraphV2Response(await loader(normalized, controller.signal), normalized)
      if (generation !== graphGeneration || queryKey(requestedQuery.value!) !== nextKey) return
      clearSelection()
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
    node: GraphV2Node,
    loader: GraphV2EvidenceLoader = fetchGraphV2Evidence,
  ): Promise<void> {
    if (
      appliedQuery.value === null ||
      graph.value === null ||
      !graph.value.nodes.some((candidate) => candidate.stable_key === node.stable_key)
    ) return

    evidenceController?.abort()
    const controller = new AbortController()
    evidenceController = controller
    const generation = ++evidenceGeneration
    const query = appliedQuery.value
    const source = graph.value
    const stableKey = node.stable_key
    const changedNode = selectedNodeKey.value !== stableKey
    selectedNodeKey.value = stableKey
    evidenceState.value = 'loading'
    evidenceError.value = ''
    if (changedNode) evidence.value = null

    try {
      const loaded = await loader(query, stableKey, controller.signal, 1)
      if (
        generation !== evidenceGeneration ||
        selectedNodeKey.value !== stableKey ||
        graph.value !== source
      ) return
      if (!evidenceMatchesGraph(loaded, source, stableKey, 1)) {
        throw new Error('Graph v2 evidence scope mismatch')
      }
      evidence.value = loaded
      evidenceState.value = loaded.items.length === 0 ? 'empty' : 'ready'
    } catch (error) {
      if (generation !== evidenceGeneration || selectedNodeKey.value !== stableKey) return
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
    loader: GraphV2EvidenceLoader = fetchGraphV2Evidence,
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
    const stableKey = selectedNodeKey.value
    const previous = evidence.value
    const nextPage = previous.page + 1
    evidenceState.value = 'loading'
    evidenceError.value = ''

    try {
      const loaded = await loader(query, stableKey, controller.signal, nextPage)
      if (
        generation !== evidenceGeneration ||
        selectedNodeKey.value !== stableKey ||
        graph.value !== source ||
        evidence.value !== previous
      ) return
      if (!evidenceMatchesGraph(loaded, source, stableKey, nextPage)) {
        throw new Error('Graph v2 evidence scope mismatch')
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
      if (generation !== evidenceGeneration || selectedNodeKey.value !== stableKey) return
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

  async function retryGraph(loader: GraphV2Loader = fetchGraphV2): Promise<void> {
    if (requestedQuery.value !== null) await loadGraph(requestedQuery.value, loader)
  }

  async function retryEvidence(
    loader: GraphV2EvidenceLoader = fetchGraphV2Evidence,
  ): Promise<void> {
    if (selectedNodeKey.value === null || graph.value === null) return
    const node = graph.value.nodes.find(
      (candidate) => candidate.stable_key === selectedNodeKey.value,
    )
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
