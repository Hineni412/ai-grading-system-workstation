import { createApp, defineComponent, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { JobResponse } from '../../../api/jobs'
import { useJobStore } from '../../../stores/jobs'
import { useWorkspaceAITaskStore } from './store'
import type { WorkspaceAITask } from './contracts'
import WorkspaceAITaskDrawer from './WorkspaceAITaskDrawer.vue'

function job(status: JobResponse['status'], progress: number): JobResponse {
  return {
    id: 72,
    job_type: 'grading_run',
    payload: {},
    result: {},
    status,
    progress,
    stage: '',
    detail: '',
    error: null,
    cancel_requested: false,
    created_at: '2026-08-08T00:00:00Z',
    started_at: '2026-08-08T00:00:01Z',
    updated_at: '2026-08-08T00:00:02Z',
    finished_at: null,
  }
}

function workspaceTask(status: WorkspaceAITask['status']): WorkspaceAITask {
  return {
    contract_version: 'teacher_workspace_ai_task.v1',
    task_id: 'task-timeline-01',
    operation_id: 'operation-timeline-01',
    module: 'class_teacher',
    task_kind: 'intake_turn',
    source_ref: { kind: 'conversation', id: 'conversation-1', revision: '1' },
    context_refs: [],
    return_target: 'class_teacher.home',
    status,
    phase: 'draft',
    progress: 1,
    send_attempt_count: 1,
    dispatch_evidence: 'response_persisted',
    cancel_requested: false,
    job_id: null,
    proposal_ref_id: null,
    proposal_revision: null,
    error_code: null,
    error_detail: null,
    revision: 1,
    safe_title: '合成整理任务',
    safe_source: '班主任案头',
    teacher_message: '草稿已返回，请核对。',
    next_action: '打开核对',
    handoffs: [],
    handoff_total: 0,
    adopted_count: 0,
    discarded_count: 0,
    stale_count: 0,
    pending_count: 0,
    created_at: '2026-08-08T00:00:00Z',
    updated_at: '2026-08-08T00:00:01Z',
    finished_at: '2026-08-08T00:00:01Z',
  }
}

async function mountDrawer() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: defineComponent({ template: '<div />' }) }],
  })
  await router.push('/')
  await router.isReady()
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(WorkspaceAITaskDrawer)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  await nextTick()
  return { app, host, jobs: useJobStore(), tasks: useWorkspaceAITaskStore() }
}

beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('WorkspaceAITaskDrawer 时间线任务列表', () => {
  it('renders tasks and jobs as status-toned timeline items while keeping card hooks', async () => {
    const { app, host, jobs, tasks } = await mountDrawer()
    tasks.track(workspaceTask('proposal_ready'))
    jobs.track(job('running', 0.4))
    await nextTick()

    host.querySelector<HTMLButtonElement>('.workspace-ai-drawer-toggle')!.click()
    await nextTick()

    const timeline = host.querySelector('.workspace-ai-task-drawer__timeline')
    expect(timeline).not.toBeNull()
    const items = [...timeline!.querySelectorAll('.timeline-item')]
    expect(items).toHaveLength(2)
    expect(items[0]?.getAttribute('data-tone')).toBe('success')
    expect(items[1]?.getAttribute('data-tone')).toBe('info')

    const articles = timeline!.querySelectorAll('article')
    expect(articles).toHaveLength(2)
    expect(articles[0]?.querySelector('.workspace-ai-task-drawer__heading')?.textContent).toContain('合成整理任务')
    expect(articles[0]?.textContent).toContain('返回原页')
    expect(articles[1]?.textContent).toContain('返回相关页面')
    expect(timeline!.querySelector('.timeline-item__node')).not.toBeNull()
    app.unmount()
  })

  it('maps a failed workspace task to the danger node tone', async () => {
    const { app, host, tasks } = await mountDrawer()
    tasks.track(workspaceTask('failed'))
    await nextTick()

    host.querySelector<HTMLButtonElement>('.workspace-ai-drawer-toggle')!.click()
    await nextTick()

    expect(host.querySelector('.timeline-item')?.getAttribute('data-tone')).toBe('danger')
    app.unmount()
  })
})
