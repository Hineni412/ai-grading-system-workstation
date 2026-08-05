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
    module: 'teaching_prep',
    task_kind: 'teaching_prep.lesson_plan',
    source_ref: { kind: 'lesson', id: 'lesson-001', revision: '2' },
    context_refs: [],
    return_target: 'teaching_prep.lesson.plan',
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
    revision: 2,
    safe_title: '备课 · 形成课堂方案',
    safe_source: '备课',
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
  get = vi.fn(async () => task()),
  getJob = vi.fn(async (id: number) => (
    id === 41 ? job(41, 'workspace_ai.run', 'task-001') : job(id, 'grading_run')
  )),
): WorkspaceAITaskStoreDependencies {
  return {
    api: {
      prepare: vi.fn(async () => task({ status: 'prepared' })),
      dispatch: vi.fn(async () => task()),
      get,
      getByOperation: vi.fn(async () => task()),
      cancel: vi.fn(async () => task({
        status: 'cancelled_before_dispatch',
        revision: 3,
      })),
    },
    legacyJobApi: { getJob, cancelJob: vi.fn() },
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
  })

  it('migrates only server-proven workspace AI jobs from the mixed legacy index', async () => {
    localStorage.setItem(JOB_STORAGE_KEY, JSON.stringify([
      { id: 41, jobType: 'workspace_ai.run', trackedAt: 'now' },
      { id: 42, jobType: 'grading_run', trackedAt: 'now' },
      { id: 43, jobType: 'teaching_prep.parse_material', trackedAt: 'now' },
    ]))
    const store = useWorkspaceAITaskStore()
    await store.initialize(dependencies())

    expect(store.tasks['task-001']?.safe_title).toBe('备课 · 形成课堂方案')
    expect(JSON.parse(localStorage.getItem(JOB_STORAGE_KEY)!)).toEqual([
      { id: 42, jobType: 'grading_run', trackedAt: 'now' },
      { id: 43, jobType: 'teaching_prep.parse_material', trackedAt: 'now' },
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
        module: 'teaching_prep',
        intent: 'review',
        handling_mode: 'record',
        destination_key: 'teaching_prep.lesson.plan',
        subject_refs: [],
        draft_ref: { kind: 'draft', id: 'draft-001', revision: '1' },
        adoption_state: 'pending',
        prefill_keys: ['summary'],
        missing_fields: [],
        source_task_id: 'task-001',
        source_turn_id: null,
        return_destination_key: 'teaching_prep.overview',
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
  })
})
