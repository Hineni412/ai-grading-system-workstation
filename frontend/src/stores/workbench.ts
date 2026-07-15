import { ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '../api/errors'
import {
  fetchSessionAnomalies,
  fetchWorkbenchOverview,
  type SessionAnomaly,
  type SessionAnomalyResponse,
  type WorkbenchOverview,
} from '../api/workbench'

export type ResourceState = 'idle' | 'loading' | 'ready' | 'empty' | 'stale-error' | 'error'
export type OverviewLoader = (
  sessionId: number | null,
  signal: AbortSignal,
) => Promise<WorkbenchOverview>
export type AnomalyLoader = (
  sessionId: number,
  signal: AbortSignal,
) => Promise<SessionAnomalyResponse>

function isCancelled(error: unknown): boolean {
  return (
    (typeof error === 'object' && error !== null && 'name' in error && error.name === 'AbortError') ||
    (error instanceof ApiError && error.kind === 'cancelled')
  )
}

export const useWorkbenchStore = defineStore('workbench', () => {
  const sessionId = ref<number | null>(null)
  const overview = ref<WorkbenchOverview | null>(null)
  const anomalies = ref<SessionAnomaly[]>([])
  const overviewState = ref<ResourceState>('idle')
  const anomaliesState = ref<ResourceState>('idle')
  const overviewError = ref('')
  const anomaliesError = ref('')
  const overviewUpdatedAt = ref<string | null>(null)
  const anomaliesUpdatedAt = ref<string | null>(null)

  let overviewController: AbortController | null = null
  let anomaliesController: AbortController | null = null
  let overviewGeneration = 0
  let anomaliesGeneration = 0

  function resetForSession(nextSessionId: number | null): void {
    if (sessionId.value === nextSessionId) return
    overviewController?.abort()
    anomaliesController?.abort()
    overviewController = null
    anomaliesController = null
    overviewGeneration += 1
    anomaliesGeneration += 1
    sessionId.value = nextSessionId
    overview.value = null
    anomalies.value = []
    overviewState.value = 'idle'
    anomaliesState.value = 'idle'
    overviewError.value = ''
    anomaliesError.value = ''
    overviewUpdatedAt.value = null
    anomaliesUpdatedAt.value = null
  }

  async function loadOverview(
    nextSessionId: number | null,
    loader: OverviewLoader = fetchWorkbenchOverview,
  ): Promise<void> {
    resetForSession(nextSessionId)
    overviewController?.abort()
    const controller = new AbortController()
    overviewController = controller
    const generation = ++overviewGeneration
    overviewState.value = 'loading'
    overviewError.value = ''

    try {
      const loaded = await loader(nextSessionId, controller.signal)
      if (generation !== overviewGeneration || sessionId.value !== nextSessionId) return
      if (
        (nextSessionId === null && loaded.current_session !== null) ||
        (nextSessionId !== null && loaded.current_session?.id !== nextSessionId)
      ) {
        throw new Error('Workbench overview scope mismatch')
      }
      overview.value = loaded
      overviewState.value = loaded.current_session === null ? 'empty' : 'ready'
      overviewUpdatedAt.value = new Date().toISOString()
    } catch (error) {
      if (generation !== overviewGeneration || sessionId.value !== nextSessionId) return
      if (isCancelled(error)) {
        overviewState.value = overviewUpdatedAt.value === null
          ? 'idle'
          : overview.value?.current_session === null ? 'empty' : 'ready'
        return
      }
      overviewState.value = overviewUpdatedAt.value === null ? 'error' : 'stale-error'
      overviewError.value = '工作台数据暂时无法更新'
    } finally {
      if (overviewController === controller) overviewController = null
    }
  }

  async function loadAnomalies(
    nextSessionId: number,
    loader: AnomalyLoader = fetchSessionAnomalies,
  ): Promise<void> {
    resetForSession(nextSessionId)
    anomaliesController?.abort()
    const controller = new AbortController()
    anomaliesController = controller
    const generation = ++anomaliesGeneration
    anomaliesState.value = 'loading'
    anomaliesError.value = ''

    try {
      const loaded = await loader(nextSessionId, controller.signal)
      if (generation !== anomaliesGeneration || sessionId.value !== nextSessionId) return
      anomalies.value = [...loaded.items]
      anomaliesState.value = loaded.items.length === 0 ? 'empty' : 'ready'
      anomaliesUpdatedAt.value = new Date().toISOString()
    } catch (error) {
      if (generation !== anomaliesGeneration || sessionId.value !== nextSessionId) return
      if (isCancelled(error)) {
        anomaliesState.value = anomaliesUpdatedAt.value === null
          ? 'idle'
          : anomalies.value.length === 0 ? 'empty' : 'ready'
        return
      }
      anomaliesState.value = anomaliesUpdatedAt.value === null ? 'error' : 'stale-error'
      anomaliesError.value = '异常清单暂时无法更新'
    } finally {
      if (anomaliesController === controller) anomaliesController = null
    }
  }

  return {
    sessionId,
    overview,
    anomalies,
    overviewState,
    anomaliesState,
    overviewError,
    anomaliesError,
    overviewUpdatedAt,
    anomaliesUpdatedAt,
    loadOverview,
    loadAnomalies,
    resetForSession,
  }
})
