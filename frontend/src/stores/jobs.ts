import { onScopeDispose, ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '../api/errors'
import {
  jobApi,
  type JobApi,
  type JobResponse,
  TERMINAL_JOB_STATUSES,
} from '../api/jobs'

export const JOB_STORAGE_KEY = 'ai-grading:tracked-jobs:v1'

export interface PersistedJobReference {
  id: number
  jobType: string
  trackedAt: string
}

export interface JobSyncError {
  kind: 'not_found' | 'network' | 'server' | 'other'
  message: string
  retryable: boolean
  requestId: string
}

export interface JobStoreDependencies {
  api: JobApi
  now: () => Date
  schedule: (
    callback: () => void,
    milliseconds: number,
  ) => ReturnType<typeof setTimeout>
  cancelScheduled: (handle: ReturnType<typeof setTimeout>) => void
  pollIntervalMs: number
  maxBackoffMs: number
}

const defaultDependencies: JobStoreDependencies = {
  api: jobApi,
  now: () => new Date(),
  schedule: (callback, milliseconds) => setTimeout(callback, milliseconds),
  cancelScheduled: (handle) => clearTimeout(handle),
  pollIntervalMs: 2_000,
  maxBackoffMs: 30_000,
}

function isPersistedReference(value: unknown): value is PersistedJobReference {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const candidate = value as Record<string, unknown>
  return (
    Number.isSafeInteger(candidate.id) &&
    Number(candidate.id) > 0 &&
    typeof candidate.jobType === 'string' &&
    candidate.jobType.length > 0 &&
    typeof candidate.trackedAt === 'string' &&
    candidate.trackedAt.length > 0
  )
}

function safeSyncError(error: unknown): JobSyncError {
  if (error instanceof ApiError) {
    const kind =
      error.kind === 'not_found' || error.kind === 'network' || error.kind === 'server'
        ? error.kind
        : 'other'
    return {
      kind,
      message:
        kind === 'not_found'
          ? '该任务已无法恢复'
          : '任务状态暂时无法更新',
      retryable: error.retryable,
      requestId: error.requestId,
    }
  }
  return {
    kind: 'other',
    message: '任务状态暂时无法更新',
    retryable: true,
    requestId: '',
  }
}

function shouldReplaceJob(
  current: JobResponse | undefined,
  next: JobResponse,
): boolean {
  if (!current) return true
  const currentTime = Date.parse(current.updated_at)
  const nextTime = Date.parse(next.updated_at)
  if (Number.isFinite(currentTime) && Number.isFinite(nextTime)) {
    if (nextTime > currentTime) return true
    if (nextTime < currentTime) return false
  }
  const currentTerminal = TERMINAL_JOB_STATUSES.has(current.status)
  const nextTerminal = TERMINAL_JOB_STATUSES.has(next.status)
  if (nextTerminal !== currentTerminal) return nextTerminal
  if (next.cancel_requested !== current.cancel_requested) return next.cancel_requested
  return true
}

export const useJobStore = defineStore('jobs', () => {
  const jobs = ref<Record<number, JobResponse>>({})
  const syncErrors = ref<Record<number, JobSyncError>>({})
  const references = new Map<number, PersistedJobReference>()
  const timers = new Map<number, ReturnType<typeof setTimeout>>()
  const controllers = new Map<number, AbortController>()
  const inFlight = new Map<number, Promise<void>>()
  const cancelControllers = new Map<number, AbortController>()
  const cancelInFlight = new Map<number, Promise<void>>()
  const generations = new Map<number, number>()
  const retryCounts = new Map<number, number>()
  let dependencies = defaultDependencies

  function configure(next?: JobStoreDependencies) {
    if (next) dependencies = next
  }

  function persistReferences(): void {
    if (references.size === 0) {
      localStorage.removeItem(JOB_STORAGE_KEY)
      return
    }
    localStorage.setItem(
      JOB_STORAGE_KEY,
      JSON.stringify([...references.values()].sort((left, right) => left.id - right.id)),
    )
  }

  function readReferences(): PersistedJobReference[] {
    const raw = localStorage.getItem(JOB_STORAGE_KEY)
    if (!raw) return []
    try {
      const parsed: unknown = JSON.parse(raw)
      if (!Array.isArray(parsed)) throw new Error('invalid index')
      const valid = parsed.filter(isPersistedReference).map((reference) => ({
        id: reference.id,
        jobType: reference.jobType,
        trackedAt: reference.trackedAt,
      }))
      references.clear()
      for (const reference of valid) references.set(reference.id, reference)
      persistReferences()
      return valid
    } catch {
      references.clear()
      localStorage.removeItem(JOB_STORAGE_KEY)
      return []
    }
  }

  function currentGeneration(id: number): number {
    return generations.get(id) ?? 0
  }

  function schedulePolling(id: number, delay?: number): void {
    if (timers.has(id) || !references.has(id)) return
    const snapshot = jobs.value[id]
    if (snapshot && TERMINAL_JOB_STATUSES.has(snapshot.status)) return
    const handle = dependencies.schedule(() => {
      timers.delete(id)
      void refresh(id)
    }, delay ?? dependencies.pollIntervalMs)
    timers.set(id, handle)
  }

  function stopPolling(id: number): void {
    const timer = timers.get(id)
    if (timer !== undefined) dependencies.cancelScheduled(timer)
    timers.delete(id)
    controllers.get(id)?.abort()
    controllers.delete(id)
    cancelControllers.get(id)?.abort()
    cancelControllers.delete(id)
    generations.set(id, currentGeneration(id) + 1)
    retryCounts.delete(id)
  }

  function removeReference(id: number): void {
    references.delete(id)
    persistReferences()
  }

  async function refresh(id: number): Promise<void> {
    const existing = inFlight.get(id)
    if (existing) return existing
    if (!references.has(id)) return

    const generation = currentGeneration(id)
    const controller = new AbortController()
    controllers.set(id, controller)
    const request = (async () => {
      try {
        const job = await dependencies.api.getJob(id, controller.signal)
        if (currentGeneration(id) !== generation || !references.has(id)) return
        if (shouldReplaceJob(jobs.value[id], job)) jobs.value[id] = job
        delete syncErrors.value[id]
        retryCounts.delete(id)
        if (TERMINAL_JOB_STATUSES.has(job.status)) stopPolling(id)
        else schedulePolling(id)
      } catch (error) {
        if (currentGeneration(id) !== generation || !references.has(id)) return
        const safe = safeSyncError(error)
        syncErrors.value[id] = safe
        if (safe.kind === 'not_found') {
          stopPolling(id)
          removeReference(id)
          delete jobs.value[id]
          return
        }
        if (safe.retryable && (safe.kind === 'network' || safe.kind === 'server')) {
          const retryCount = (retryCounts.get(id) ?? 0) + 1
          retryCounts.set(id, retryCount)
          schedulePolling(
            id,
            Math.min(
              dependencies.pollIntervalMs * 2 ** Math.max(0, retryCount - 1),
              dependencies.maxBackoffMs,
            ),
          )
        }
      } finally {
        if (controllers.get(id) === controller) controllers.delete(id)
        inFlight.delete(id)
      }
    })()
    inFlight.set(id, request)
    return request
  }

  async function initialize(next?: JobStoreDependencies): Promise<void> {
    configure(next)
    const stored = readReferences()
    await Promise.all(stored.map((reference) => refresh(reference.id)))
  }

  function track(job: JobResponse, next?: JobStoreDependencies): void {
    configure(next)
    jobs.value[job.id] = job
    delete syncErrors.value[job.id]
    if (!references.has(job.id)) {
      references.set(job.id, {
        id: job.id,
        jobType: job.job_type,
        trackedAt: dependencies.now().toISOString(),
      })
      persistReferences()
    }
    if (TERMINAL_JOB_STATUSES.has(job.status)) stopPolling(job.id)
    else schedulePolling(job.id)
  }

  async function cancel(id: number): Promise<void> {
    const existing = cancelInFlight.get(id)
    if (existing) return existing
    const current = jobs.value[id]
    if (!current || !references.has(id)) throw new Error('Job is not tracked')
    if (TERMINAL_JOB_STATUSES.has(current.status)) throw new Error('Job is already complete')
    const generation = currentGeneration(id)
    const controller = new AbortController()
    cancelControllers.set(id, controller)
    const request = (async () => {
      try {
        const next = await dependencies.api.cancelJob(id, controller.signal)
        if (currentGeneration(id) !== generation || !references.has(id)) return
        if (shouldReplaceJob(jobs.value[id], next)) jobs.value[id] = next
        delete syncErrors.value[id]
        if (TERMINAL_JOB_STATUSES.has(jobs.value[id]!.status)) stopPolling(id)
        else schedulePolling(id)
      } catch (error) {
        if (currentGeneration(id) !== generation || !references.has(id)) return
        syncErrors.value[id] = safeSyncError(error)
      } finally {
        if (cancelControllers.get(id) === controller) cancelControllers.delete(id)
        cancelInFlight.delete(id)
      }
    })()
    cancelInFlight.set(id, request)
    return request
  }

  function remove(id: number): void {
    stopPolling(id)
    removeReference(id)
    delete jobs.value[id]
    delete syncErrors.value[id]
  }

  function clearCompleted(): void {
    for (const [id, job] of Object.entries(jobs.value)) {
      if (TERMINAL_JOB_STATUSES.has(job.status)) remove(Number(id))
    }
  }

  function stopAllPolling(): void {
    for (const id of [...references.keys()]) stopPolling(id)
  }

  onScopeDispose(stopAllPolling)

  return {
    jobs,
    syncErrors,
    initialize,
    track,
    refresh,
    cancel,
    stopPolling,
    stopAllPolling,
    remove,
    clearCompleted,
  }
})
