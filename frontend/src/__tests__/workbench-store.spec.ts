import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SessionAnomalyResponse, WorkbenchOverview } from '../api/workbench'
import { useWorkbenchStore } from '../stores/workbench'

const overview7: WorkbenchOverview = {
  current_session: {
    id: 7,
    name: '期中考试',
    status: 'completed',
    is_deleted: false,
    deleted_at: null,
    created_at: null,
    updated_at: null,
  },
  progress: null,
  review: null,
  anomalies: null,
  recent_jobs: [],
  recent_sessions: [],
  updated_at: '2026-07-15T09:00:00Z',
}

const anomalyResponse: SessionAnomalyResponse = {
  items: [{
    anomaly_id: 'failed:1',
    anomaly_type: 'grading_failed',
    display_name: '学生甲',
    student_code: 'S1',
    class_name: '一班',
    status: 'failed',
    detail: null,
    created_at: null,
  }],
  total: 1,
  page: 1,
  page_size: 100,
  total_pages: 1,
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise })
  return { promise, resolve }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.restoreAllMocks()
})

describe('workbench store', () => {
  it('retains anomaly pagination and loads records beyond the first 100', async () => {
    const store = useWorkbenchStore()
    const first = Array.from({ length: 100 }, (_, index) => ({
      ...anomalyResponse.items[0]!,
      anomaly_id: `failed:${index + 1}`,
    }))
    const loader = vi.fn(async (_sessionId, _signal, page = 1): Promise<SessionAnomalyResponse> => ({
      items: page === 1 ? first : [{ ...anomalyResponse.items[0]!, anomaly_id: 'failed:101' }],
      total: 101,
      page,
      page_size: 100,
      total_pages: 2,
    }))

    await store.loadAnomalies(7, loader)
    expect(store.anomaliesTotal).toBe(101)
    expect(store.anomaliesPage).toBe(1)
    expect(store.anomaliesTotalPages).toBe(2)

    await store.loadMoreAnomalies(loader)
    expect(loader).toHaveBeenLastCalledWith(7, expect.any(AbortSignal), 2)
    expect(store.anomalies).toHaveLength(101)
    expect(store.anomalies[100]?.anomaly_id).toBe('failed:101')
  })

  it('keeps the last successful overview when refresh fails', async () => {
    const store = useWorkbenchStore()
    await store.loadOverview(7, async () => overview7)
    const updatedAt = store.overviewUpdatedAt
    await store.loadOverview(7, async () => { throw new Error('private failure') })
    expect(store.overview).toEqual(overview7)
    expect(store.overviewState).toBe('stale-error')
    expect(store.overviewError).toBe('工作台数据暂时无法更新')
    expect(store.overviewUpdatedAt).toBe(updatedAt)
  })

  it('rejects overview data from a different session without replacing same-scope data', async () => {
    const store = useWorkbenchStore()
    await store.loadOverview(7, async () => overview7)
    const updatedAt = store.overviewUpdatedAt

    await store.loadOverview(7, async () => ({
      ...overview7,
      current_session: { ...overview7.current_session!, id: 8 },
    }))

    expect(store.overview).toEqual(overview7)
    expect(store.overviewState).toBe('stale-error')
    expect(store.overviewUpdatedAt).toBe(updatedAt)

    const unscoped = useWorkbenchStore()
    unscoped.resetForSession(null)
    await unscoped.loadOverview(null, async () => overview7)
    expect(unscoped.overview).toBeNull()
    expect(unscoped.overviewState).toBe('error')
  })

  it('loads anomalies independently and permits a later retry', async () => {
    const store = useWorkbenchStore()
    await store.loadOverview(7, async () => overview7)
    await store.loadAnomalies(7, async () => { throw new Error('private failure') })
    expect(store.overviewState).toBe('ready')
    expect(store.anomaliesState).toBe('error')
    expect(store.anomaliesError).toBe('异常清单暂时无法更新')

    await store.loadAnomalies(7, async () => anomalyResponse)
    expect(store.anomalies).toEqual(anomalyResponse.items)
    expect(store.anomaliesState).toBe('ready')
    expect(store.anomaliesError).toBe('')
    expect(Date.parse(store.anomaliesUpdatedAt ?? '')).not.toBeNaN()
  })

  it('aborts and ignores an older overview response', async () => {
    const store = useWorkbenchStore()
    const old = deferred<WorkbenchOverview>()
    let oldSignal: AbortSignal | undefined
    const oldLoad = store.loadOverview(7, (_sessionId, signal) => {
      oldSignal = signal
      return old.promise
    })
    await store.loadOverview(8, async () => ({
      ...overview7,
      current_session: { ...overview7.current_session!, id: 8 },
    }))
    expect(oldSignal?.aborted).toBe(true)
    old.resolve(overview7)
    await oldLoad
    expect(store.sessionId).toBe(8)
    expect(store.overview?.current_session?.id).toBe(8)
  })

  it('clears visible data immediately when switching sessions', async () => {
    const store = useWorkbenchStore()
    await store.loadOverview(7, async () => overview7)
    await store.loadAnomalies(7, async () => anomalyResponse)
    store.resetForSession(8)
    expect(store.sessionId).toBe(8)
    expect(store.overview).toBeNull()
    expect(store.anomalies).toEqual([])
    expect(store.overviewState).toBe('idle')
    expect(store.anomaliesState).toBe('idle')
  })

  it('never uses localStorage', async () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem')
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    await useWorkbenchStore().loadOverview(7, async () => overview7)
    expect(getItem).not.toHaveBeenCalled()
    expect(setItem).not.toHaveBeenCalled()
  })
})
