import { createPinia } from 'pinia'
import { createApp, nextTick, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { teachingPrepWorkbenchApi } from '../api/workbench'
import { useWorkspaceAITaskStore } from '../../shared/ai-tasks/store'
import type { WorkspaceAITask, WorkspaceAITaskStatus } from '../../shared/ai-tasks/contracts'
import { teachingPrepWorkbenchKey } from '../workbench/context'
import LessonMaterialConfirmationWorkspace from './LessonMaterialConfirmationWorkspace.vue'

const primaryLink = {
  link_id: 'link-ppt', link_revision: 1, purpose: 'reference_ppt',
  material_version_id: 'version-ppt', material_name: '1.2 勾股定理（第一课时）',
  material_type: 'pptx', content_sha256: 'a'.repeat(64), start_unit: 1, end_unit: 18,
  units: [{ unit_id: 'unit-ppt-1', unit_index: 1, unit_kind: 'ppt_slide', title: '勾股定理', preview_url: '/ppt/1', text_status: 'embedded', formula_review_required: false }],
}
const textbookLink = {
  link_id: 'link-book', link_revision: 1, purpose: 'textbook',
  material_version_id: 'version-book', material_name: '北师大版八年级数学上册',
  material_type: 'pdf', content_sha256: 'b'.repeat(64), start_unit: 21, end_unit: 26,
  units: [{ unit_id: 'unit-book-21', unit_index: 21, unit_kind: 'pdf_page', title: '探索勾股定理', preview_url: '/book/21', text_status: 'embedded', formula_review_required: false }],
}
const pack = {
  id: 'pack-1', lesson_node_id: 'lesson-1', version_number: 1,
  source_state_sha256: 'c'.repeat(64), pack_sha256: 'd'.repeat(64),
  payload: { materials: [{ link_id: 'link-ppt' }, { link_id: 'link-book' }] }, created_at: '',
}
const draft = {
  id: 'draft-1', resource_pack_id: 'pack-1', version_number: 1,
  status: 'confirmed', payload: {}, created_at: '',
}

function aiTask(status: WorkspaceAITaskStatus = 'running'): WorkspaceAITask {
  return {
    contract_version: 'teacher_workspace_ai_task.v1', task_id: 'task-slide', operation_id: 'op-slide',
    module: 'teaching_prep', task_kind: 'teaching_prep.slide_change_proposal',
    source_ref: { kind: 'lesson', id: 'lesson-1', revision: '3' },
    context_refs: [{ kind: 'lesson_draft', id: 'draft-1', revision: '1' }],
    return_target: 'teaching_prep.lesson.slides', status, phase: 'calling_model', progress: 40,
    send_attempt_count: status === 'prepared' ? 0 : 1, dispatch_evidence: status === 'prepared' ? 'not_started' : 'may_have_started',
    cancel_requested: false, job_id: null, proposal_ref_id: null, proposal_revision: null,
    error_code: null, revision: 1, safe_title: '课件改编', safe_source: '当前课时',
    teacher_message: '正在处理', next_action: '等待完成', handoffs: [], handoff_total: 0,
    adopted_count: 0, discarded_count: 0, stale_count: 0, pending_count: 0,
    created_at: '', updated_at: '', finished_at: null,
  }
}

function mountWorkspace(options: { existingPack?: boolean; existingDraft?: boolean } = {}) {
  const catalog = {
    selectedLessonId: 'lesson-1',
    selectedLesson: { id: 'lesson-1', revision: 3 },
    selectedSemester: { id: 'semester-1', revision: 2 },
    teachingPreferences: { payload: {} },
    resourcePackStatus: options.existingPack ? { local_sources_changed: false } : { local_sources_changed: true },
    resourcePacks: options.existingPack ? [pack] : [],
    selectedResourcePackId: options.existingPack ? 'pack-1' : null,
    lessonDrafts: options.existingDraft ? [draft] : [],
    selectedLessonDraftId: options.existingDraft ? 'draft-1' : null,
    saveState: 'idle',
    selectResourcePack: vi.fn(async () => undefined),
    freezeResourcePack: vi.fn(async () => {
      catalog.resourcePacks = [pack]
      catalog.selectedResourcePackId = 'pack-1'
    }),
    prepareLessonDraft: vi.fn(async () => undefined),
    generateLessonDraft: vi.fn(async () => {
      catalog.lessonDrafts = [draft]
    }),
    selectLessonDraft: vi.fn(async () => {
      catalog.selectedLessonDraftId = 'draft-1'
    }),
  }
  const fake = {
    catalog,
    referencePreflight: ref({
      lesson_node_id: 'lesson-1', source_state_sha256: 'e'.repeat(64),
      catalog: { lesson: {}, material_links: [primaryLink, textbookLink] },
      draft: null, model_available: true, model_label: '测试模型',
      model_destination_fingerprint: 'f'.repeat(64), will_call_model: false,
    }),
    pane: ref('preview'), dirtyReason: ref<string | null>(null),
    setPane: vi.fn(), setDirty: vi.fn((reason: string | null) => { fake.dirtyReason.value = reason }),
    refreshCurrentWorkspace: vi.fn(async () => undefined),
    openStage: vi.fn(async () => undefined),
  }
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(LessonMaterialConfirmationWorkspace)
  const pinia = createPinia()
  app.use(pinia)
  app.provide(teachingPrepWorkbenchKey, fake as never)
  app.mount(host)
  return { app, host, pinia, fake }
}

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('LessonMaterialConfirmationWorkspace', () => {
  it('asks only for source confirmation and contains no teaching-design text field', async () => {
    const { app, host } = mountWorkspace()
    await nextTick()

    expect(host.querySelector('textarea')).toBeNull()
    expect(host.textContent).toContain('确认主课件和参考依据')
    expect(host.textContent).toContain('教学文字')
    expect(host.textContent).toContain('无需填写')
    app.unmount()
  })

  it('saves exactly the checked sources before freezing and dispatching the AI task', async () => {
    const save = vi.spyOn(teachingPrepWorkbenchApi, 'saveReferenceDraft').mockResolvedValue({} as never)
    vi.spyOn(teachingPrepWorkbenchApi, 'resourcePackPreflight').mockResolvedValue({})
    const { app, host, pinia, fake } = mountWorkspace()
    const aiTasks = useWorkspaceAITaskStore(pinia)
    vi.spyOn(aiTasks, 'prepare').mockResolvedValue(aiTask('prepared'))
    const dispatch = vi.spyOn(aiTasks, 'dispatch').mockResolvedValue(aiTask('running'))
    await nextTick()

    host.querySelector<HTMLInputElement>('input[value="link-book"]')?.click()
    const send = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(item => item.textContent?.includes('确认资料并发送给 AI'))
    send?.click()

    await vi.waitFor(() => expect(dispatch).toHaveBeenCalledOnce())
    const selection = save.mock.calls[0]?.[1].selection
    expect(selection?.material_selections.map(item => item.link_id)).toEqual(['link-ppt'])
    expect(selection?.teacher_context).toBeNull()
    expect(fake.catalog.freezeResourcePack).toHaveBeenCalledWith(expect.objectContaining({
      selected_material_link_ids: ['link-ppt'], selected_exercise_candidate_ids: [], teacher_context: null,
    }))
    expect(fake.openStage).toHaveBeenCalledWith('slides', { panel: 'slides' })
    app.unmount()
  })

  it('does not freeze sources or start AI when saving the correspondence fails', async () => {
    vi.spyOn(teachingPrepWorkbenchApi, 'saveReferenceDraft').mockRejectedValue(new Error('版本已变化'))
    const { app, host, pinia, fake } = mountWorkspace()
    const aiTasks = useWorkspaceAITaskStore(pinia)
    const prepare = vi.spyOn(aiTasks, 'prepare')
    await nextTick()

    const send = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(item => item.textContent?.includes('确认资料并发送给 AI'))
    send?.click()

    await vi.waitFor(() => expect(host.textContent).toContain('尚未发送：版本已变化'))
    expect(fake.catalog.freezeResourcePack).not.toHaveBeenCalled()
    expect(prepare).not.toHaveBeenCalled()
    app.unmount()
  })

  it('reuses a running task for the same confirmed draft instead of sending twice', async () => {
    vi.spyOn(teachingPrepWorkbenchApi, 'saveReferenceDraft').mockResolvedValue({} as never)
    const { app, host, pinia, fake } = mountWorkspace({ existingPack: true, existingDraft: true })
    const aiTasks = useWorkspaceAITaskStore(pinia)
    aiTasks.track(aiTask('running'))
    const prepare = vi.spyOn(aiTasks, 'prepare')
    const dispatch = vi.spyOn(aiTasks, 'dispatch')
    await nextTick()

    const send = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(item => item.textContent?.includes('重新确认并查看改编'))
    send?.click()

    await vi.waitFor(() => expect(fake.openStage).toHaveBeenCalledWith('slides', { panel: 'slides' }))
    expect(prepare).not.toHaveBeenCalled()
    expect(dispatch).not.toHaveBeenCalled()
    expect(fake.catalog.freezeResourcePack).not.toHaveBeenCalled()
    app.unmount()
  })
})
