import { computed, onScopeDispose, ref } from 'vue'
import { defineStore } from 'pinia'

import { jobApi, type JobApi } from '../../../api/jobs'
import { ApiError } from '../../../api/errors'
import { JOB_STORAGE_KEY } from '../../../stores/jobs'
import { workspaceAITaskApi, type WorkspaceAITaskApi } from './api'
import {
  TERMINAL_WORKSPACE_AI_TASK_STATUSES,
  type PrepareWorkspaceAITaskInput,
  type WorkspaceAITask,
} from './contracts'

export const WORKSPACE_AI_TASK_STORAGE_KEY = 'teacher-platform:tracked-ai-tasks:v1'

interface PersistedTaskReference {
  taskId: string
  operationId: string
  trackedAt: string
}

export interface WorkspaceAITaskStoreDependencies {
  api: WorkspaceAITaskApi
  legacyJobApi: JobApi
  now: () => Date
  schedule: (callback: () => void, milliseconds: number) => ReturnType<typeof setTimeout>
  cancelScheduled: (handle: ReturnType<typeof setTimeout>) => void
  pollIntervalMs: number
  maxBackoffMs: number
}

const defaults: WorkspaceAITaskStoreDependencies = {
  api: workspaceAITaskApi,
  legacyJobApi: jobApi,
  now: () => new Date(),
  schedule: (callback, milliseconds) => setTimeout(callback, milliseconds),
  cancelScheduled: handle => clearTimeout(handle),
  pollIntervalMs: 2_000,
  maxBackoffMs: 30_000,
}

function isReference(value: unknown): value is PersistedTaskReference {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const item = value as Record<string, unknown>
  return typeof item.taskId === 'string'
    && Boolean(item.taskId)
    && typeof item.operationId === 'string'
    && Boolean(item.operationId)
    && typeof item.trackedAt === 'string'
}

function shouldReplace(current: WorkspaceAITask | undefined, next: WorkspaceAITask): boolean {
  if (!current) return true
  if (next.revision !== current.revision) return next.revision > current.revision
  const currentTime = Date.parse(current.updated_at)
  const nextTime = Date.parse(next.updated_at)
  if (Number.isFinite(currentTime) && Number.isFinite(nextTime)) return nextTime >= currentTime
  return true
}

export const useWorkspaceAITaskStore = defineStore('workspace-ai-tasks', () => {
  const tasks = ref<Record<string, WorkspaceAITask>>({})
  const syncErrors = ref<Record<string, string>>({})
  const latestStartedTaskId = ref<string | null>(null)
  const taskNoticeRevision = ref(0)
  const references = new Map<string, PersistedTaskReference>()
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  const generations = new Map<string, number>()
  const retryCounts = new Map<string, number>()
  const controllers = new Map<string, AbortController>()
  const inFlight = new Map<string, Promise<void>>()
  let dependencies = defaults

  const orderedTasks = computed(() => Object.values(tasks.value).sort(
    (left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at),
  ))
  const activeCount = computed(() => orderedTasks.value.filter(
    task => !TERMINAL_WORKSPACE_AI_TASK_STATUSES.has(task.status),
  ).length)

  function configure(next?: WorkspaceAITaskStoreDependencies): void {
    if (next) dependencies = next
  }

  function persist(): void {
    if (references.size === 0) {
      localStorage.removeItem(WORKSPACE_AI_TASK_STORAGE_KEY)
      return
    }
    localStorage.setItem(WORKSPACE_AI_TASK_STORAGE_KEY, JSON.stringify(
      [...references.values()].sort((left, right) => left.taskId.localeCompare(right.taskId)),
    ))
  }

  function readReferences(): PersistedTaskReference[] {
    const raw = localStorage.getItem(WORKSPACE_AI_TASK_STORAGE_KEY)
    if (!raw) return []
    try {
      const parsed: unknown = JSON.parse(raw)
      if (!Array.isArray(parsed)) throw new Error('invalid task index')
      const valid = parsed.filter(isReference)
      references.clear()
      valid.forEach(reference => references.set(reference.taskId, reference))
      persist()
      return valid
    } catch {
      references.clear()
      localStorage.removeItem(WORKSPACE_AI_TASK_STORAGE_KEY)
      return []
    }
  }

  function generation(taskId: string): number {
    return generations.get(taskId) ?? 0
  }

  function schedule(taskId: string, delay?: number): void {
    if (timers.has(taskId) || !references.has(taskId)) return
    const task = tasks.value[taskId]
    if (task && TERMINAL_WORKSPACE_AI_TASK_STATUSES.has(task.status)) return
    const handle = dependencies.schedule(() => {
      timers.delete(taskId)
      void refresh(taskId)
    }, delay ?? dependencies.pollIntervalMs)
    timers.set(taskId, handle)
  }

  function stop(taskId: string): void {
    const timer = timers.get(taskId)
    if (timer !== undefined) dependencies.cancelScheduled(timer)
    timers.delete(taskId)
    controllers.get(taskId)?.abort()
    controllers.delete(taskId)
    generations.set(taskId, generation(taskId) + 1)
    retryCounts.delete(taskId)
  }

  function track(task: WorkspaceAITask): void {
    if (shouldReplace(tasks.value[task.task_id], task)) tasks.value[task.task_id] = task
    syncErrors.value[task.task_id] = ''
    if (!references.has(task.task_id)) {
      references.set(task.task_id, {
        taskId: task.task_id,
        operationId: task.operation_id,
        trackedAt: dependencies.now().toISOString(),
      })
      persist()
    }
    if (TERMINAL_WORKSPACE_AI_TASK_STATUSES.has(task.status)) stop(task.task_id)
    else schedule(task.task_id)
  }

  async function refresh(taskId: string): Promise<void> {
    const existing = inFlight.get(taskId)
    if (existing) return existing
    if (!references.has(taskId)) return
    const expectedGeneration = generation(taskId)
    const controller = new AbortController()
    controllers.set(taskId, controller)
    const request = (async () => {
      try {
        const task = await dependencies.api.get(taskId, controller.signal)
        if (generation(taskId) !== expectedGeneration || !references.has(taskId)) return
        track(task)
        retryCounts.delete(taskId)
      } catch (error) {
        if (generation(taskId) !== expectedGeneration || !references.has(taskId)) return
        if (error instanceof ApiError && error.kind === 'not_found') {
          remove(taskId)
          return
        }
        syncErrors.value[taskId] = '任务状态暂时无法更新，已保留上次安全状态。'
        const count = (retryCounts.get(taskId) ?? 0) + 1
        retryCounts.set(taskId, count)
        schedule(taskId, Math.min(
          dependencies.pollIntervalMs * 2 ** Math.max(0, count - 1),
          dependencies.maxBackoffMs,
        ))
      } finally {
        if (controllers.get(taskId) === controller) controllers.delete(taskId)
        inFlight.delete(taskId)
      }
    })()
    inFlight.set(taskId, request)
    return request
  }

  async function prepare(input: PrepareWorkspaceAITaskInput): Promise<WorkspaceAITask> {
    const task = await dependencies.api.prepare(input)
    track(task)
    return task
  }

  async function dispatch(task: WorkspaceAITask): Promise<WorkspaceAITask> {
    const next = await dependencies.api.dispatch(task.operation_id, task.task_id)
    track(next)
    latestStartedTaskId.value = next.task_id
    taskNoticeRevision.value += 1
    return next
  }

  async function cancel(taskId: string): Promise<void> {
    const current = tasks.value[taskId]
    if (!current) throw new Error('Workspace AI task is not tracked')
    const next = await dependencies.api.cancel(current.operation_id)
    track(next)
  }

  function remove(taskId: string): void {
    stop(taskId)
    references.delete(taskId)
    persist()
    delete tasks.value[taskId]
    delete syncErrors.value[taskId]
  }

  async function migrateLegacyMixedIndex(): Promise<void> {
    const raw = localStorage.getItem(JOB_STORAGE_KEY)
    if (!raw) return
    let entries: unknown[]
    try {
      const parsed: unknown = JSON.parse(raw)
      if (!Array.isArray(parsed)) return
      entries = parsed
    } catch {
      return
    }
    const migrated = new Set<number>()
    await Promise.all(entries.map(async (entry) => {
      if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) return
      const id = Number((entry as Record<string, unknown>).id)
      if (!Number.isSafeInteger(id) || id <= 0) return
      try {
        const job = await dependencies.legacyJobApi.getJob(id)
        if (!job.job_type.startsWith('workspace_ai.')) return
        const taskId = String(job.result.task_id ?? job.payload.task_id ?? '')
        if (!taskId) return
        const task = await dependencies.api.get(taskId)
        track(task)
        migrated.add(id)
      } catch {
        // No proven server mapping: keep the legacy reference untouched.
      }
    }))
    if (migrated.size === 0) return
    const preserved = entries.filter((entry) => {
      if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) return true
      return !migrated.has(Number((entry as Record<string, unknown>).id))
    })
    if (preserved.length === 0) localStorage.removeItem(JOB_STORAGE_KEY)
    else localStorage.setItem(JOB_STORAGE_KEY, JSON.stringify(preserved))
  }

  async function initialize(next?: WorkspaceAITaskStoreDependencies): Promise<void> {
    configure(next)
    const stored = readReferences()
    await Promise.all(stored.map(reference => refresh(reference.taskId)))
    await migrateLegacyMixedIndex()
  }

  function stopAll(): void {
    ;[...references.keys()].forEach(stop)
  }

  onScopeDispose(stopAll)

  return {
    tasks,
    orderedTasks,
    activeCount,
    syncErrors,
    latestStartedTaskId,
    taskNoticeRevision,
    initialize,
    prepare,
    dispatch,
    track,
    refresh,
    cancel,
    remove,
    stopAll,
  }
})
