import { onScopeDispose, reactive, ref } from 'vue'
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
  terminal?: true
  readAt?: string
  dismissedAt?: string
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

export const DEFAULT_JOB_POLL_INTERVAL_MS = 2_000
export const DEFAULT_JOB_MAX_BACKOFF_MS = 30_000

export function jobPollDelay(
  retryCount: number,
  pollIntervalMs = DEFAULT_JOB_POLL_INTERVAL_MS,
  maxBackoffMs = DEFAULT_JOB_MAX_BACKOFF_MS,
): number {
  if (retryCount <= 1) return pollIntervalMs
  return Math.min(
    pollIntervalMs * 2 ** (retryCount - 1),
    maxBackoffMs,
  )
}

const defaultDependencies: JobStoreDependencies = {
  api: jobApi,
  now: () => new Date(),
  schedule: (callback, milliseconds) => setTimeout(callback, milliseconds),
  cancelScheduled: (handle) => clearTimeout(handle),
  pollIntervalMs: DEFAULT_JOB_POLL_INTERVAL_MS,
  maxBackoffMs: DEFAULT_JOB_MAX_BACKOFF_MS,
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
    candidate.trackedAt.length > 0 &&
    (candidate.readAt === undefined || (typeof candidate.readAt === 'string' && Number.isFinite(Date.parse(candidate.readAt)))) &&
    (candidate.dismissedAt === undefined || (typeof candidate.dismissedAt === 'string' && Number.isFinite(Date.parse(candidate.dismissedAt)))) &&
    (candidate.terminal === undefined || candidate.terminal === true)
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
  const announcements = ref<number[]>([])
  const completions = ref<{ id: number; live: boolean }[]>([])
  const references = reactive(new Map<number, PersistedJobReference>())
  const timers = new Map<number, ReturnType<typeof setTimeout>>()
  const controllers = new Map<number, AbortController>()
  const inFlight = new Map<number, Promise<void>>()
  const cancelControllers = new Map<number, AbortController>()
  const cancelInFlight = new Map<number, Promise<void>>()
  const generations = new Map<number, number>()
  const retryCounts = new Map<number, number>()
  let dependencies = defaultDependencies
  let initialized = false
  let restoring = false
  let initializationPromise: Promise<void> | null = null

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
      const valid: PersistedJobReference[] = parsed
        .filter(isPersistedReference)
        .map((reference) => ({
          id: reference.id,
          jobType: reference.jobType,
          trackedAt: reference.trackedAt,
          ...(reference.terminal === true ? { terminal: true as const } : {}),
          ...(reference.readAt ? { readAt: reference.readAt } : {}),
          ...(reference.dismissedAt ? { dismissedAt: reference.dismissedAt } : {}),
        }))
        .filter(reference => !reference.dismissedAt || dependencies.now().getTime() - Date.parse(reference.dismissedAt) < 86_400_000)
      references.clear()
      for (const reference of valid) references.set(reference.id, reference)
      persistReferences()
      return [...references.values()]
    } catch {
      references.clear()
      localStorage.removeItem(JOB_STORAGE_KEY)
      return []
    }
  }

  function currentGeneration(id: number): number {
    return generations.get(id) ?? 0
  }

  function updateSnapshot(job: JobResponse): void {
    const previous = jobs.value[job.id]
    if (!shouldReplaceJob(previous, job)) return
    const reference = references.get(job.id)
    const wasActive = previous ? !TERMINAL_JOB_STATUSES.has(previous.status)
      : reference !== undefined && reference.terminal !== true
    jobs.value[job.id] = job
    if (wasActive && TERMINAL_JOB_STATUSES.has(job.status)) {
      completions.value.push({ id: job.id, live: !restoring && previous !== undefined })
    }
  }

  const dueIds = new Set<number>()
  let flushHandle: ReturnType<typeof setTimeout> | null = null

  function schedulePolling(id: number, delay?: number): void {
    if (timers.has(id) || !references.has(id)) return
    const snapshot = jobs.value[id]
    if (snapshot && TERMINAL_JOB_STATUSES.has(snapshot.status)) return
    const handle = dependencies.schedule(() => {
      timers.delete(id)
      // Coalesce timer firings into one flush so N active jobs produce one
      // batch request per tick instead of N individual GETs.
      dueIds.add(id)
      scheduleFlush()
    }, delay ?? dependencies.pollIntervalMs)
    timers.set(id, handle)
  }

  function scheduleFlush(): void {
    if (flushHandle !== null) return
    flushHandle = dependencies.schedule(() => {
      flushHandle = null
      const ids = [...dueIds]
      dueIds.clear()
      if (ids.length === 0) return
      if (ids.length === 1) {
        void refresh(ids[0]!)
        return
      }
      void refreshMany(ids)
    }, 0)
  }

  async function refreshMany(ids: number[]): Promise<void> {
    const pending = ids.filter((id) => references.has(id) && !inFlight.has(id))
    if (pending.length === 0) return
    const generationBy = new Map(pending.map((id) => [id, currentGeneration(id)]))
    const promise = (async () => {
      try {
        const response = await dependencies.api.getJobStatusBatch(pending)
        for (const item of response) {
          const id = item.id
          if (generationBy.get(id) !== currentGeneration(id)) continue
          if (!references.has(id)) continue
          if (!item.found || !item.job) {
            stopPolling(id)
            removeReference(id)
            delete jobs.value[id]
            continue
          }
          const job = item.job
          updateSnapshot(job)
          delete syncErrors.value[id]
          retryCounts.delete(id)
          const snapshot = jobs.value[id]!
          trackResultJobs(snapshot)
          const terminal = TERMINAL_JOB_STATUSES.has(snapshot.status)
          persistTerminalState(id, terminal)
          if (terminal) stopPolling(id)
          else schedulePolling(id)
        }
      } catch (error) {
        for (const id of pending) {
          if (generationBy.get(id) !== currentGeneration(id)) continue
          if (!references.has(id)) continue
          const safe = safeSyncError(error)
          syncErrors.value[id] = safe
          if (safe.retryable && (safe.kind === 'network' || safe.kind === 'server')) {
            const retryCount = (retryCounts.get(id) ?? 0) + 1
            retryCounts.set(id, retryCount)
            schedulePolling(
              id,
              jobPollDelay(
                retryCount,
                dependencies.pollIntervalMs,
                dependencies.maxBackoffMs,
              ),
            )
          }
        }
      } finally {
        for (const id of pending) inFlight.delete(id)
      }
    })()
    for (const id of pending) inFlight.set(id, promise)
    await promise
  }

  function trackResultJobs(job: JobResponse): void {
    if (!['question_import', 'tagging_sync', 'question_bank_sync', 'config_generation'].includes(job.job_type)) return
    for (const [field, jobType] of [['question_bank_sync_job_id', 'question_bank_sync'], ['chapter_type_job_id', 'chapter_type_organize']] as const) {
      const id = job.result[field]
      if (!Number.isSafeInteger(id) || Number(id) <= 0 || id === job.id || references.has(Number(id))) continue
      references.set(Number(id), { id: Number(id), jobType, trackedAt: dependencies.now().toISOString() })
      persistReferences()
      void refresh(Number(id))
    }
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

  function persistTerminalState(id: number, terminal: boolean): void {
    const reference = references.get(id)
    if (!reference || (reference.terminal === true) === terminal) return
    references.set(id, {
      ...reference,
      terminal: terminal ? true : undefined,
    })
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
        updateSnapshot(job)
        delete syncErrors.value[id]
        retryCounts.delete(id)
        const snapshot = jobs.value[id]!
        trackResultJobs(snapshot)
        const terminal = TERMINAL_JOB_STATUSES.has(snapshot.status)
        persistTerminalState(id, terminal)
        if (terminal) stopPolling(id)
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
            jobPollDelay(
              retryCount,
              dependencies.pollIntervalMs,
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

  function initialize(next?: JobStoreDependencies): Promise<void> {
    if (initialized) return Promise.resolve()
    if (initializationPromise) return initializationPromise
    configure(next)
    const attempt = (async () => {
      restoring = true
      try {
        const stored = readReferences()
        const activeOrUnknown = stored.filter(reference => reference.terminal !== true)
        const terminal = stored.filter(reference => reference.terminal === true)
        await Promise.all(activeOrUnknown.map(reference => refresh(reference.id)))
        await Promise.all(terminal.map(reference => refresh(reference.id)))
        try {
          const active = await dependencies.api.listActiveJobs()
          const missing = active.items.filter(item => !references.has(item.id)).map(item => item.id)
          if (missing.length) {
            const records = await dependencies.api.getJobStatusBatch(missing)
            for (const item of records) if (item.found && item.job) track(item.job, { announce: false })
          }
        } catch {
          // 已有引用继续恢复和轮询；找回列表失败不阻断这些任务。
        }
      } finally { restoring = false }
    })()
    initializationPromise = attempt
    void attempt.then(
      () => {
        initialized = true
        if (initializationPromise === attempt) initializationPromise = null
      },
      () => {
        if (initializationPromise === attempt) initializationPromise = null
      },
    )
    return attempt
  }

  function track(job: JobResponse, options?: { announce?: boolean }): void {
    updateSnapshot(job)
    delete syncErrors.value[job.id]
    const isNew = !references.has(job.id)
    if (isNew) {
      references.set(job.id, {
        id: job.id,
        jobType: job.job_type,
        trackedAt: dependencies.now().toISOString(),
        ...(TERMINAL_JOB_STATUSES.has(job.status) ? { terminal: true as const } : {}),
        ...(TERMINAL_JOB_STATUSES.has(job.status) ? { readAt: dependencies.now().toISOString() } : {}),
      })
      persistReferences()
      const age = dependencies.now().getTime() - Date.parse(job.created_at)
      if (options?.announce !== false && age >= 0 && age <= 15_000) announce(job.id)
    } else {
      const snapshot = jobs.value[job.id]!
      persistTerminalState(job.id, TERMINAL_JOB_STATUSES.has(snapshot.status))
    }
    const snapshot = jobs.value[job.id]!
    trackResultJobs(snapshot)
    if (TERMINAL_JOB_STATUSES.has(snapshot.status)) stopPolling(job.id)
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
        updateSnapshot(next)
        delete syncErrors.value[id]
        trackResultJobs(jobs.value[id]!)
        const terminal = TERMINAL_JOB_STATUSES.has(jobs.value[id]!.status)
        persistTerminalState(id, terminal)
        if (terminal) stopPolling(id)
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
    dismiss(Object.values(jobs.value).filter(job => TERMINAL_JOB_STATUSES.has(job.status)).map(job => job.id))
  }

  function announce(id: number): void {
    const job = jobs.value[id]
    if (job && !TERMINAL_JOB_STATUSES.has(job.status)) announcements.value.push(id)
  }

  function markRead(id: number): void {
    const reference = references.get(id)
    if (!reference || reference.readAt) return
    references.set(id, { ...reference, readAt: dependencies.now().toISOString() })
    persistReferences()
  }

  function dismiss(ids: number[]): void {
    const now = dependencies.now().toISOString()
    let changed = false
    for (const id of ids) {
      const reference = references.get(id)
      const job = jobs.value[id]
      if (!reference || reference.dismissedAt || !job || !TERMINAL_JOB_STATUSES.has(job.status)) continue
      references.set(id, { ...reference, dismissedAt: now })
      changed = true
    }
    if (changed) persistReferences()
  }

  function stopAllPolling(): void {
    for (const id of [...references.keys()]) stopPolling(id)
  }

  onScopeDispose(stopAllPolling)

  return {
    jobs,
    syncErrors,
    references,
    announcements,
    completions,
    initialize,
    track,
    announce,
    markRead,
    dismiss,
    refresh,
    cancel,
    stopPolling,
    stopAllPolling,
    remove,
    clearCompleted,
  }
})
