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
  curriculumVolumeId: string | null,
  signal: AbortSignal,
) => Promise<WorkbenchOverview>
export type AnomalyLoader = (
  sessionId: number,
  signal: AbortSignal,
  page?: number,
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
  const anomaliesTotal = ref(0)
  const anomaliesPage = ref(1)
  const anomaliesPageSize = ref(100)
  const anomaliesTotalPages = ref(0)

  let overviewController: AbortController | null = null
  let anomaliesController: AbortController | null = null
  let overviewGeneration = 0
  let overviewVolumeId: string | null | undefined
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
    anomaliesTotal.value = 0
    anomaliesPage.value = 1
    anomaliesPageSize.value = 100
    anomaliesTotalPages.value = 0
  }

  async function loadOverview(
    nextSessionId: number | null,
    curriculumVolumeId: string | null = null,
    loader: OverviewLoader = fetchWorkbenchOverview,
  ): Promise<void> {
    resetForSession(nextSessionId)
    if (overviewVolumeId !== curriculumVolumeId) {
      overview.value = null
      overviewUpdatedAt.value = null
      overviewVolumeId = curriculumVolumeId
    }
    overviewController?.abort()
    const controller = new AbortController()
    overviewController = controller
    const generation = ++overviewGeneration
    overviewState.value = 'loading'
    overviewError.value = ''

    try {
      const loaded = await loader(nextSessionId, curriculumVolumeId, controller.signal)
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
    page = 1,
    append = false,
  ): Promise<void> {
    resetForSession(nextSessionId)
    anomaliesController?.abort()
    const controller = new AbortController()
    anomaliesController = controller
    const generation = ++anomaliesGeneration
    anomaliesState.value = 'loading'
    anomaliesError.value = ''

    try {
      const loaded = await loader(nextSessionId, controller.signal, page)
      if (generation !== anomaliesGeneration || sessionId.value !== nextSessionId) return
      const seen = new Set(anomalies.value.map((item) => item.anomaly_id))
      anomalies.value = append
        ? [...anomalies.value, ...loaded.items.filter((item) => {
            if (seen.has(item.anomaly_id)) return false
            seen.add(item.anomaly_id)
            return true
          })]
        : [...loaded.items]
      anomaliesTotal.value = loaded.total
      anomaliesPage.value = loaded.page
      anomaliesPageSize.value = loaded.page_size
      anomaliesTotalPages.value = loaded.total_pages
      anomaliesState.value = anomalies.value.length === 0 ? 'empty' : 'ready'
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

  async function loadMoreAnomalies(loader: AnomalyLoader = fetchSessionAnomalies): Promise<void> {
    if (sessionId.value === null || anomaliesPage.value >= anomaliesTotalPages.value) return
    await loadAnomalies(sessionId.value, loader, anomaliesPage.value + 1, true)
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
    anomaliesTotal,
    anomaliesPage,
    anomaliesPageSize,
    anomaliesTotalPages,
    loadOverview,
    loadAnomalies,
    loadMoreAnomalies,
    resetForSession,
  }
})
