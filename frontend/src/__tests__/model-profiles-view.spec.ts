import { createApp, nextTick, type App as VueApp } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '../App.vue'
import { aiDiagnosticsApi, type AiDiagnosticSummary } from '../api/ai-diagnostics'
import { modelProfilesApi } from '../api/model-profiles'
import { createAppRouter } from '../router'
import { workspaceAITaskApi } from '../workspaces/shared/ai-tasks/api'
import type { WorkspaceAITask } from '../workspaces/shared/ai-tasks/contracts'

vi.mock('../api/sessions', () => ({
  fetchSessions: vi.fn(async () => []),
}))

const mounted: VueApp[] = []

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

afterEach(() => {
  mounted.splice(0).forEach((app) => app.unmount())
  document.body.replaceChildren()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('ModelProfilesView', () => {
  it('opens normally before the first API site is configured', async () => {
    const nativeStructuredClone = structuredClone
    let taskBindingCloneCount = 0
    vi.stubGlobal('structuredClone', (value: unknown) => {
      const keys = value !== null && typeof value === 'object'
        ? Object.keys(value)
        : []
      if (keys.includes('content_generation') && keys.includes('class_teacher')) {
        taskBindingCloneCount += 1
      }
      if (taskBindingCloneCount === 2) {
        throw new DOMException('Reactive objects cannot be cloned', 'DataCloneError')
      }
      return nativeStructuredClone(value)
    })
    vi.spyOn(modelProfilesApi, 'getState').mockResolvedValue({
      profiles: [],
      active_profile_name: null,
      active_profile: null,
      task_bindings: {
        content_generation: { profile_name: null, model: '' },
        grading: { profile_name: null, model: '' },
        teaching_prep: { profile_name: null, model: '' },
        class_teacher: { profile_name: null, model: '' },
      },
    })

    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createAppRouter(createMemoryHistory())
    await router.push('/model-profiles')
    await router.isReady()
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(App)
    app.use(pinia)
    app.use(router)
    app.mount(host)
    mounted.push(app)

    await settle()
    await vi.waitFor(() => {
      expect(host.textContent).toContain('请先在下方新增一个 API 站点')
    })

    expect(host.querySelector('.settings-hub__section-heading h2')?.textContent).toBe('AI 服务')
    expect(host.textContent).toContain('高级设置：API 站点、密钥与请求速度')
    expect(host.querySelectorAll('.model-task-row')).toHaveLength(4)
    expect(host.querySelectorAll('.model-task-row select:disabled')).toHaveLength(4)
    expect(host.querySelectorAll('.model-task-row input:disabled')).toHaveLength(4)
  })

  it('filters workbench call records by workbench and task category', async () => {
    vi.spyOn(modelProfilesApi, 'getState').mockResolvedValue({
      profiles: [],
      active_profile_name: null,
      active_profile: null,
      task_bindings: {
        content_generation: { profile_name: null, model: '' },
        grading: { profile_name: null, model: '' },
        teaching_prep: { profile_name: null, model: '' },
        class_teacher: { profile_name: null, model: '' },
      },
    })
    const diagnostic = (
      operationId: string,
      callId: string,
      workspaceModule: '' | 'teaching_prep' | 'class_teacher',
      workspaceTaskKind: string,
    ): AiDiagnosticSummary => ({
      call_id: callId,
      operation_id: operationId,
      request_id: operationId,
      attempt: 1,
      request_kind: 'workspace',
      workspace_module: workspaceModule,
      workspace_task_kind: workspaceTaskKind,
      protocol: 'chat_completions',
      model: 'synthetic-model',
      started_at_utc: '2026-08-07T12:00:00Z',
      finished_at_utc: '2026-08-07T12:00:01Z',
      outcome: 'success',
      elapsed_ms: 1000,
      image_count: 0,
      response_chars: 20,
      will_retry: false,
    })
    const task = (
      module: WorkspaceAITask['module'],
      taskKind: string,
      operationId: string,
    ): WorkspaceAITask => ({
      contract_version: 'teacher_workspace_ai_task.v1',
      task_id: `task-${operationId}`,
      operation_id: operationId,
      module,
      task_kind: taskKind,
      source_ref: { kind: 'synthetic', id: 'source-1', revision: '1' },
      context_refs: [],
      return_target: `${module}.home`,
      status: 'proposal_ready',
      phase: 'handoff_ready',
      progress: 1,
      send_attempt_count: 1,
      dispatch_evidence: 'response_persisted',
      cancel_requested: false,
      job_id: 1,
      proposal_ref_id: 'proposal-1',
      proposal_revision: '1',
      error_code: null,
      revision: 1,
      safe_title: '合成任务',
      safe_source: '合成来源',
      teacher_message: '',
      next_action: '',
      handoffs: [],
      handoff_total: 0,
      adopted_count: 0,
      discarded_count: 0,
      stale_count: 0,
      pending_count: 0,
      created_at: '2026-08-07T12:00:00Z',
      updated_at: '2026-08-07T12:00:01Z',
      finished_at: '2026-08-07T12:00:01Z',
    })
    const diagnostics = [
      diagnostic(
        'operation-teaching-prep',
        'a'.repeat(24),
        'teaching_prep',
        'lesson_draft',
      ),
      diagnostic(
        'operation-class-teacher',
        'b'.repeat(24),
        'class_teacher',
        'class_teacher_intake',
      ),
    ]
    const listDiagnostics = vi.spyOn(aiDiagnosticsApi, 'list').mockResolvedValue({
      items: diagnostics,
      returned: diagnostics.length,
      matching: diagnostics.length,
      scanned_event_count: 4,
      truncated: false,
    })
    vi.spyOn(workspaceAITaskApi, 'list').mockImplementation(async (module) => (
      module === 'teaching_prep'
        ? [task('teaching_prep', 'teaching_prep.lesson_plan', 'operation-teaching-prep')]
        : [task('class_teacher', 'class_teacher.intake_triage', 'operation-class-teacher')]
    ))

    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createAppRouter(createMemoryHistory())
    await router.push('/model-profiles')
    await router.isReady()
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(App)
    app.use(pinia)
    app.use(router)
    app.mount(host)
    mounted.push(app)
    await settle()

    const disclosure = host.querySelector<HTMLDetailsElement>('.ai-diagnostics-disclosure')
    expect(disclosure).not.toBeNull()
    disclosure!.open = true
    disclosure!.dispatchEvent(new Event('toggle'))
    await vi.waitFor(() => expect(listDiagnostics).toHaveBeenCalled())
    await settle()

    const sourceSelect = host.querySelector<HTMLSelectElement>(
      '.ai-diagnostics__filters label:first-child select',
    )
    expect(sourceSelect?.textContent).toContain('工作台')
    sourceSelect!.value = 'workspace'
    sourceSelect!.dispatchEvent(new Event('change', { bubbles: true }))
    await vi.waitFor(() => {
      expect(listDiagnostics).toHaveBeenLastCalledWith(
        expect.objectContaining({ requestKind: 'workspace' }),
      )
    })
    await settle()

    const workbenchSelect = [...host.querySelectorAll<HTMLLabelElement>(
      '.ai-diagnostics__filters label',
    )].find((label) => label.querySelector('span')?.textContent === '工作台')
      ?.querySelector<HTMLSelectElement>('select')
    expect(workbenchSelect).toBeDefined()
    workbenchSelect!.value = 'class_teacher'
    workbenchSelect!.dispatchEvent(new Event('change', { bubbles: true }))
    await vi.waitFor(() => {
      expect(listDiagnostics).toHaveBeenLastCalledWith(
        expect.objectContaining({ workspaceModule: 'class_teacher' }),
      )
    })
    await settle()

    const ledger = host.querySelector('.ai-diagnostics-ledger')
    expect(ledger?.textContent).toContain('班主任 · 事项整理')
    expect(ledger?.textContent).not.toContain('备课 · 课时备课方案')

    const categorySelect = [...host.querySelectorAll<HTMLLabelElement>(
      '.ai-diagnostics__filters label',
    )].find((label) => label.querySelector('span')?.textContent === '具体功能')
      ?.querySelector<HTMLSelectElement>('select')
    expect(categorySelect?.textContent).toContain('事项整理')
    categorySelect!.value = 'class_teacher_intake'
    categorySelect!.dispatchEvent(new Event('change', { bubbles: true }))
    await vi.waitFor(() => {
      expect(listDiagnostics).toHaveBeenLastCalledWith(expect.objectContaining({
        workspaceModule: 'class_teacher',
        workspaceTaskKind: 'class_teacher_intake',
      }))
    })

    const clearClassTeacher = vi.spyOn(aiDiagnosticsApi, 'clearClassTeacher')
      .mockResolvedValue({
        workspace_module: 'class_teacher',
        deleted_event_count: 4,
        retained_event_count: 6,
        unclassified_event_count: 2,
      })
    vi.stubGlobal('confirm', vi.fn(() => true))
    const clearButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((item) => item.textContent?.includes('清除班主任正文日志'))
    expect(clearButton).toBeDefined()
    clearButton!.click()
    await vi.waitFor(() => expect(clearClassTeacher).toHaveBeenCalledTimes(1))
    await settle()

    expect(host.textContent).toContain('已清除 4 条班主任正文日志')
    expect(host.textContent).toContain('唯一正文日志')
    expect(host.textContent).toContain('每条最多 1 MB')
    expect(host.textContent).toContain('不会复制到终端、访问日志、任务摘要或浏览器存储')
    expect(host.textContent).toContain('未分类旧记录不会被这次清除')
  })
})
