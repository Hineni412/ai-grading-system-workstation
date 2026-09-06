import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { JOB_STORAGE_KEY } from '../../../stores/jobs'
import type { JobResponse } from '../../../api/jobs'
import type { WorkspaceAITask } from './contracts'
import {
  useWorkspaceAITaskStore,
  WORKSPACE_AI_TASK_STORAGE_KEY,
  type WorkspaceAITaskStoreDependencies,
} from './store'

function task(overrides: Partial<WorkspaceAITask> = {}): WorkspaceAITask {
  return {
    contract_version: 'teacher_workspace_ai_task.v1',
    task_id: 'task-001',
    operation_id: 'operation-001',
    module: 'class_teacher',
    task_kind: 'class_teacher.intake_triage',
    source_ref: { kind: 'student', id: 'student-001', revision: '2' },
    context_refs: [],
    return_target: 'class_teacher.home',
    status: 'running',
    phase: 'claimed',
    progress: 0.2,
    send_attempt_count: 0,
    dispatch_evidence: 'not_started',
    cancel_requested: false,
    job_id: 41,
    proposal_ref_id: null,
    proposal_revision: null,
    error_code: null,
    error_detail: null,
    revision: 2,
    safe_title: '班主任 · 事项整理',
    safe_source: '班主任',
    teacher_message: '任务正在准备，尚未发送。',
    next_action: '等待或取消',
    handoffs: [],
    handoff_total: 0,
    adopted_count: 0,
    discarded_count: 0,
    stale_count: 0,
    pending_count: 0,
    created_at: '2026-08-05T00:00:00Z',
    updated_at: '2026-08-05T00:01:00Z',
    finished_at: null,
    ...overrides,
  }
}

function job(id: number, jobType: string, taskId?: string): JobResponse {
  return {
    id,
    job_type: jobType,
    payload: taskId ? { task_id: taskId } : {},
    result: taskId ? { task_id: taskId } : {},
    status: 'succeeded',
    progress: 1,
    stage: '',
    detail: '',
    error: null,
    cancel_requested: false,
    created_at: '2026-08-05T00:00:00Z',
    started_at: null,
    updated_at: '2026-08-05T00:01:00Z',
    finished_at: '2026-08-05T00:01:00Z',
  }
}

function dependencies(
  get: WorkspaceAITaskStoreDependencies['api']['get'] = vi.fn(async () => task()),
  list: WorkspaceAITaskStoreDependencies['api']['list'] = vi.fn(async () => []),
  getJob = vi.fn(async (id: number) => (
    id === 41 ? job(41, 'workspace_ai.run', 'task-001') : job(id, 'grading_run')
  )),
): WorkspaceAITaskStoreDependencies {
  return {
    api: {
      list,
      prepare: vi.fn(async () => task({ status: 'prepared' })),
      dispatch: vi.fn(async () => task()),
      get,
      getByOperation: vi.fn(async () => task()),
      cancel: vi.fn(async () => task({
        status: 'cancelled_before_dispatch',
        revision: 3,
      })),
      discard: vi.fn(async () => task({
        status: 'discarded',
        revision: 4,
      })),
    },
    legacyJobApi: { getJob, cancelJob: vi.fn(), getJobStatusBatch: vi.fn(async () => []) },
    now: () => new Date('2026-08-05T00:00:00Z'),
    schedule: vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>),
    cancelScheduled: vi.fn(),
    pollIntervalMs: 100,
    maxBackoffMs: 800,
  }
}

describe('workspace AI task store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('migrates only server-proven workspace AI jobs from the mixed legacy index', async () => {
    localStorage.setItem(JOB_STORAGE_KEY, JSON.stringify([
      { id: 41, jobType: 'workspace_ai.run', trackedAt: 'now' },
      { id: 42, jobType: 'grading_run', trackedAt: 'now' },
      { id: 43, jobType: 'question_import', trackedAt: 'now' },
    ]))
    const store = useWorkspaceAITaskStore()
    const api = dependencies()
    await store.initialize(api)

    expect(store.tasks['task-001']?.safe_title).toBe('班主任 · 事项整理')
    expect(api.legacyJobApi.getJob).toHaveBeenCalledExactlyOnceWith(41)
    expect(JSON.parse(localStorage.getItem(JOB_STORAGE_KEY)!)).toEqual([
      { id: 42, jobType: 'grading_run', trackedAt: 'now' },
      { id: 43, jobType: 'question_import', trackedAt: 'now' },
    ])
    expect(JSON.parse(localStorage.getItem(WORKSPACE_AI_TASK_STORAGE_KEY)!)).toEqual([
      {
        taskId: 'task-001',
        operationId: 'operation-001',
        trackedAt: '2026-08-05T00:00:00.000Z',
      },
    ])
  })

  it('does not let an older response overwrite a newer task revision', () => {
    const store = useWorkspaceAITaskStore()
    store.track(task({ revision: 5, status: 'proposal_ready', progress: 1 }))
    store.track(task({ revision: 4, status: 'running', progress: 0.3 }))
    expect(store.tasks['task-001']?.status).toBe('proposal_ready')
  })

  it('persists references only and never task or handoff bodies', () => {
    const store = useWorkspaceAITaskStore()
    store.track(task({
      status: 'proposal_ready',
      handoffs: [{
        contract_version: 'teacher_workspace_handoff.v1',
        handoff_id: 'handoff-001',
        work_item_id: 'work-001',
        module: 'class_teacher',
        intent: 'review',
        handling_mode: 'record',
        destination_key: 'class_teacher.home',
        subject_refs: [],
        draft_ref: { kind: 'draft', id: 'draft-001', revision: '1' },
        adoption_state: 'pending',
        prefill_keys: ['summary'],
        missing_fields: [],
        source_task_id: 'task-001',
        source_turn_id: null,
        return_destination_key: 'class_teacher.student.record',
        return_focus_ref: null,
        expires_on_source_change: true,
        adoption_id: null,
        revision: 1,
      }],
    }))
    const stored = localStorage.getItem(WORKSPACE_AI_TASK_STORAGE_KEY)!
    expect(stored).toContain('task-001')
    expect(stored).not.toContain('handoff-001')
    expect(stored).not.toContain('summary')
    expect(stored).not.toContain('terminal')
  })

  it('coalesces concurrent initialization and ignores later repeats', async () => {
    localStorage.setItem(WORKSPACE_AI_TASK_STORAGE_KEY, JSON.stringify([{
      taskId: 'task-001', operationId: 'operation-001', trackedAt: 'now',
    }]))
    const api = dependencies()
    const store = useWorkspaceAITaskStore()

    await Promise.all([
      store.initialize(api),
      store.initialize(api),
    ])
    await store.initialize(api)

    expect(api.api.get).toHaveBeenCalledTimes(1)
    expect(api.api.list).not.toHaveBeenCalled()
    expect(api.schedule).toHaveBeenCalledTimes(1)
  })

  it('allows initialization to retry after the initialization process fails', async () => {
    localStorage.setItem(WORKSPACE_AI_TASK_STORAGE_KEY, JSON.stringify([{
      taskId: 'task-001', operationId: 'operation-001', trackedAt: 'now',
    }]))
    vi.spyOn(Storage.prototype, 'getItem')
      .mockImplementationOnce(() => { throw new Error('storage temporarily unavailable') })
    const api = dependencies()
    const store = useWorkspaceAITaskStore()

    await expect(store.initialize(api)).rejects.toThrow('storage temporarily unavailable')
    await expect(store.initialize(api)).resolves.toBeUndefined()

    expect(api.api.get).toHaveBeenCalledTimes(1)
  })

  it('resolves stored references without querying retired module lists', async () => {
    const statuses = [
      'needs_input',
      'proposal_ready',
      'failed_before_dispatch',
      'result_unknown',
    ] as const
    const classTasks = Array.from({ length: 13 }, (_value, index) => task({
      task_id: `class-${String(index + 1).padStart(2, '0')}`,
      operation_id: `class-operation-${index + 1}`,
      status: statuses[index % statuses.length],
      revision: index + 1,
    }))
    localStorage.setItem(WORKSPACE_AI_TASK_STORAGE_KEY, JSON.stringify(
      classTasks.map(item => ({
        taskId: item.task_id,
        operationId: item.operation_id,
        trackedAt: '2026-08-05T00:00:00Z',
        terminal: true,
      })),
    ))
    const get = vi.fn(async (id: string) => classTasks.find(item => item.task_id === id)!)
    const list = vi.fn(async () => classTasks)
    const api = dependencies(get, list)
    const store = useWorkspaceAITaskStore()

    await store.initialize(api)

    expect(list).not.toHaveBeenCalled()
    expect(get).toHaveBeenCalledTimes(13)
    expect(store.orderedTasks.map(item => item.task_id).sort())
      .toEqual(classTasks.map(item => item.task_id).sort())
    const stored = localStorage.getItem(WORKSPACE_AI_TASK_STORAGE_KEY)!
    expect(JSON.parse(stored)).toHaveLength(13)
    expect(stored).not.toContain('terminal')
  })

  it('resolves each stored task directly', async () => {
    const references = [
      { taskId: 'class-found', operationId: 'operation-found', trackedAt: 'now' },
      { taskId: 'class-missing', operationId: 'operation-missing', trackedAt: 'now' },
      { taskId: 'class-fallback', operationId: 'operation-class', trackedAt: 'now' },
    ]
    localStorage.setItem(WORKSPACE_AI_TASK_STORAGE_KEY, JSON.stringify(references))
    const found = task({
      task_id: 'class-found',
      operation_id: 'operation-found',
      status: 'proposal_ready',
    })
    const list = vi.fn(async () => [found])
    const get = vi.fn(async (taskId: string) => task({
      task_id: taskId,
      operation_id: taskId === 'class-missing' ? 'operation-missing' : 'operation-class',
      status: 'needs_input',
    }))
    const api = dependencies(get, list)
    const store = useWorkspaceAITaskStore()

    await store.initialize(api)

    expect(list).not.toHaveBeenCalled()
    expect(get.mock.calls.map(([taskId]) => taskId).sort())
      .toEqual(['class-fallback', 'class-found', 'class-missing'])
    expect(store.orderedTasks.map(item => item.task_id).sort())
      .toEqual(references.map(reference => reference.taskId).sort())
    expect(JSON.parse(localStorage.getItem(WORKSPACE_AI_TASK_STORAGE_KEY)!)).toEqual(
      [...references].sort((left, right) => left.taskId.localeCompare(right.taskId)),
    )
  })

  it('keeps references when the list fails and a detail fallback is temporarily unavailable', async () => {
    const references = [
      { taskId: 'task-restored', operationId: 'operation-restored', trackedAt: 'now' },
      { taskId: 'task-offline', operationId: 'operation-offline', trackedAt: 'now' },
    ]
    localStorage.setItem(WORKSPACE_AI_TASK_STORAGE_KEY, JSON.stringify(references))
    const list = vi.fn(async () => { throw new Error('list unavailable') })
    const get = vi.fn(async (taskId: string) => {
      if (taskId === 'task-offline') throw new Error('detail unavailable')
      return task({
        task_id: taskId,
        operation_id: 'operation-restored',
        status: 'proposal_ready',
      })
    })
    const api = dependencies(get, list)
    const store = useWorkspaceAITaskStore()

    await store.initialize(api)

    expect(list).not.toHaveBeenCalled()
    expect(get).toHaveBeenCalledTimes(2)
    expect(store.tasks['task-restored']?.status).toBe('proposal_ready')
    expect(store.tasks['task-offline']).toBeUndefined()
    expect(store.syncErrors['task-offline']).toContain('暂时无法更新')
    expect(JSON.parse(localStorage.getItem(WORKSPACE_AI_TASK_STORAGE_KEY)!)).toEqual(
      [...references].sort((left, right) => left.taskId.localeCompare(right.taskId)),
    )
    expect(api.schedule).toHaveBeenCalledTimes(1)
  })

  it('announces an explicit dispatch but not a restored historical task', async () => {
    localStorage.setItem(WORKSPACE_AI_TASK_STORAGE_KEY, JSON.stringify([{
      taskId: 'task-001', operationId: 'operation-001', trackedAt: 'now',
    }]))
    const store = useWorkspaceAITaskStore()
    const api = dependencies()

    await store.initialize(api)
    expect(store.taskNoticeRevision).toBe(0)

    const prepared = await store.prepare({
      operation_id: 'operation-new', module: 'class_teacher',
      task_kind: 'class_teacher.intake_triage',
      source_ref: { kind: 'student', id: 'student-001', revision: '2' },
      context_refs: [], prompt_contract_version: 'v1',
      model_destination_fingerprint: 'fingerprint',
      return_target: 'class_teacher.home',
    })
    await store.dispatch(prepared)

    expect(store.latestStartedTaskId).toBe('task-001')
    expect(store.taskNoticeRevision).toBe(1)
  })
})
