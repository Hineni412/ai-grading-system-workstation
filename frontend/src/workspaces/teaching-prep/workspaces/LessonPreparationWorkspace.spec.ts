import { createPinia } from 'pinia'
import { createApp, nextTick, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import * as aiAdoption from '../aiAdoption'
import {
  teachingPrepWorkbenchApi,
  type ExerciseSuggestion,
} from '../api/workbench'
import { useWorkspaceAITaskStore } from '../../shared/ai-tasks/store'
import { adoptedTeachingPrepTask } from '../aiAdoptionTestFixture'
import { teachingPrepWorkbenchKey } from '../workbench/context'
import LessonPreparationWorkspace from './LessonPreparationWorkspace.vue'

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('LessonPreparationWorkspace common AI submission', () => {
  it('recovers the same adopted handoff instead of falling back after workspace refresh failed', async () => {
    const draft = {
      id: 'draft-current', resource_pack_id: 'pack-1', version_number: 3,
      based_on_draft_id: null, operation_id: null, source_kind: 'model',
      model_label: null, status: 'draft', capacity: {}, created_at: '',
      payload: {
        knowledge_objectives: [{ text: '理解勾股定理', citations: [] }],
        anticipated_difficulties: [],
        lesson_flow: [{ phase: 'teach', title: '新授', suggested_minutes: 20, citations: [] }],
        exercise_recommendations: [],
      },
    }
    const reviseLessonDraft = vi.fn()
    const fake = {
      stage: ref('plan'), panel: ref('sources'), pane: ref('source'),
      dirtyReason: ref(null), referencePreflight: ref(null), activeSuggestionRun: ref(null),
      catalog: {
        selectedLessonId: 'lesson-1', selectedLesson: { id: 'lesson-1', revision: 7 },
        resourcePacks: [{ id: 'pack-1', version_number: 1 }], selectedResourcePackId: 'pack-1',
        lessonDrafts: [draft], selectedLessonDraftId: draft.id,
        teachingPreferences: { payload: {} }, saveState: 'idle', reviseLessonDraft,
        selectLesson: vi.fn(),
      },
      setDirty: vi.fn(), refreshCurrentWorkspace: vi.fn().mockRejectedValue(new Error('offline')),
      openPanel: vi.fn(), openStage: vi.fn(), watchSuggestionRun: vi.fn(), setPane: vi.fn(),
    }
    const adoptedTask = adoptedTeachingPrepTask({
      taskId: 'task-lesson',
      taskKind: 'teaching_prep.lesson_plan',
      proposalId: draft.id,
      sourceRef: { kind: 'lesson', id: 'lesson-1', revision: '7' },
      draftKind: 'lesson_draft',
    })
    const receipt = {
      adoption_id: 'adoption-lesson', handoff_id: 'handoff-lesson',
      task_kind: adoptedTask.task_kind, proposal_ref_id: draft.id,
      object_kind: 'lesson_draft', object_id: 'teacher-version',
      object_ref: 'teaching_prep:lesson_draft:teacher-version', object_status: 'confirmed',
      draft_revision: '3', target_revision: '7', receipt_revision: '1', adopted_at: '',
    }
    const adopt = vi.spyOn(teachingPrepWorkbenchApi, 'adoptAIHandoff').mockResolvedValue(receipt)
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp(LessonPreparationWorkspace)
    const pinia = createPinia()
    const aiTasks = useWorkspaceAITaskStore(pinia)
    aiTasks.track(adoptedTask)
    vi.spyOn(aiTasks, 'refresh').mockResolvedValue()
    app.use(pinia)
    app.provide(teachingPrepWorkbenchKey, fake as never)
    app.mount(host)
    await nextTick()

    const button = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(item => item.textContent?.includes('保存并确认课堂草稿'))
    button?.click()

    await vi.waitFor(() => expect(adopt).toHaveBeenCalledOnce())
    expect(reviseLessonDraft).not.toHaveBeenCalled()
    await vi.waitFor(() => {
      expect(host.textContent).toContain('课堂草稿已确认并生成采用回执')
      expect(host.textContent).not.toContain('课堂草稿尚未确认')
    })
    app.unmount()
  })

  it('keeps exercise finalization visible after refresh, disabled until all decisions are saved', async () => {
    const suggestion: ExerciseSuggestion = {
      id: 'suggestion-1', run_id: 'run-current', lesson_node_id: 'lesson-1',
      source_state_sha256: 'a'.repeat(64), decision: 'pending' as const,
      original_payload: {
        material_version_id: 'material-1', question_number: '1',
        content_label: '候选题', difficulty: 'medium' as const,
        classroom_use: 'guided_practice', estimated_minutes: 4,
        teaching_focus: null, reason: '匹配本节目标', uncertainties: [],
        question_regions: [], answer_regions: [],
      },
      teacher_payload: null, rejection_reason: null,
      exercise_candidate_id: null, revision: 1, created_at: '', updated_at: '',
    }
    const run = ref({
      id: 'run-current', snapshot_id: 'snapshot-1', operation_id: 'operation-1',
      status: 'succeeded' as const, error_code: null, model_call_count: 1,
      created_at: '', updated_at: '', finished_at: '', suggestions: [suggestion],
    })
    const fake = {
      stage: ref('materials'), panel: ref('exercises'), pane: ref('source'),
      dirtyReason: ref(null), referencePreflight: ref(null), activeSuggestionRun: run,
      catalog: {
        selectedLessonId: 'lesson-1', selectedLesson: { id: 'lesson-1', revision: 7 },
        resourcePacks: [], selectedResourcePackId: null, lessonDrafts: [],
        selectedLessonDraftId: null, teachingPreferences: { payload: {} },
        saveState: 'idle',
      },
      setDirty: vi.fn(), refreshCurrentWorkspace: vi.fn(), openPanel: vi.fn(),
      openStage: vi.fn(), watchSuggestionRun: vi.fn(), setPane: vi.fn(),
    }
    vi.spyOn(aiAdoption, 'findTeachingPrepAdoption').mockReturnValue({
      task: { task_id: 'task-exercise' }, handoff: { handoff_id: 'handoff-exercise' },
    } as never)
    const adopt = vi.spyOn(aiAdoption, 'adoptTeachingPrepProposal')
      .mockResolvedValue({
        match: { task: { task_id: 'task-exercise' } },
        receipt: { object_id: 'run-current' },
      } as never)
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp(LessonPreparationWorkspace)
    const pinia = createPinia()
    vi.spyOn(useWorkspaceAITaskStore(pinia), 'refresh')
      .mockRejectedValue(new Error('synthetic task refresh failure'))
    app.use(pinia)
    app.provide(teachingPrepWorkbenchKey, fake as never)
    app.mount(host)
    await nextTick()

    const finalize = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(item => item.textContent?.includes('确认本轮候选题决定'))
    expect(finalize).toBeDefined()
    expect(finalize?.disabled).toBe(true)

    run.value = {
      ...run.value,
      suggestions: [{ ...suggestion, decision: 'accepted' as const }],
    }
    await nextTick()
    expect(finalize?.disabled).toBe(false)
    finalize?.click()
    await vi.waitFor(() => expect(adopt).toHaveBeenCalledWith(
      expect.any(Array),
      'teaching_prep.exercise_suggestions',
      'run-current',
      { kind: 'finalize_exercise_suggestions' },
    ))
    await vi.waitFor(() => {
      expect(host.textContent).toContain('本轮候选题决定已确认并形成回执')
      expect(host.textContent).not.toContain('本轮确认尚未完成')
    })
    app.unmount()
  })

  it.each([
    ['AI proposal', true],
    ['local draft', false],
  ])('routes %s confirmation through the correct adoption path', async (_label, isAI) => {
    const draft = {
      id: 'draft-current', resource_pack_id: 'pack-1', version_number: 3,
      based_on_draft_id: null, operation_id: null, source_kind: isAI ? 'model' : 'local',
      model_label: null, status: 'draft', capacity: {}, created_at: '',
      payload: {
        knowledge_objectives: [{ text: '理解勾股定理', citations: [] }],
        anticipated_difficulties: [],
        lesson_flow: [{ phase: 'teach', title: '新授', suggested_minutes: 20, citations: [] }],
        exercise_recommendations: [],
      },
    }
    const reviseLessonDraft = vi.fn()
    const fake = {
      stage: ref('plan'), panel: ref('sources'), pane: ref('source'),
      dirtyReason: ref(null), referencePreflight: ref(null), activeSuggestionRun: ref(null),
      catalog: {
        selectedLessonId: 'lesson-1', selectedLesson: { id: 'lesson-1', revision: 7 },
        resourcePacks: [{ id: 'pack-1', version_number: 1 }], selectedResourcePackId: 'pack-1',
        lessonDrafts: [draft], selectedLessonDraftId: draft.id,
        teachingPreferences: { payload: {} }, saveState: 'idle',
        reviseLessonDraft,
      },
      setDirty: vi.fn(), refreshCurrentWorkspace: vi.fn(), openPanel: vi.fn(),
      openStage: vi.fn(), watchSuggestionRun: vi.fn(), setPane: vi.fn(),
    }
    const adoption = vi.spyOn(aiAdoption, 'adoptTeachingPrepProposal')
      .mockResolvedValue(isAI ? ({
        match: { task: { task_id: 'task-lesson' } },
        receipt: { object_id: 'teacher-version' },
      } as never) : null)
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp(LessonPreparationWorkspace)
    const pinia = createPinia()
    if (isAI) {
      vi.spyOn(useWorkspaceAITaskStore(pinia), 'refresh')
        .mockRejectedValue(new Error('synthetic task refresh failure'))
    }
    app.use(pinia)
    app.provide(teachingPrepWorkbenchKey, fake as never)
    app.mount(host)
    await nextTick()

    const button = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(item => item.textContent?.includes('保存并确认课堂草稿'))
    button?.click()
    await vi.waitFor(() => expect(adoption).toHaveBeenCalled())

    if (isAI) {
      expect(reviseLessonDraft).not.toHaveBeenCalled()
      await vi.waitFor(() => {
        expect(host.textContent).toContain('课堂草稿已确认并生成采用回执')
        expect(host.textContent).not.toContain('课堂草稿尚未确认')
      })
    } else expect(reviseLessonDraft).toHaveBeenCalledOnce()
    app.unmount()
  })

  it('does not freeze or dispatch an old reference draft when saving the visible selection fails', async () => {
    const lessonId = 'l'.repeat(32)
    const preflight = {
      lesson_node_id: lessonId, source_state_sha256: 'a'.repeat(64),
      catalog: { lesson: {}, material_links: [{ link_id: 'link-1', link_revision: 1, purpose: 'textbook', material_version_id: 'material-1', material_name: '教材', material_type: 'pdf', content_sha256: 'b'.repeat(64), start_unit: 1, end_unit: 1, units: [{ unit_id: 'unit-1', unit_index: 1, unit_kind: 'page', title: '第一页', preview_url: '/preview/1', text_status: 'ready', formula_review_required: false }] }] },
      draft: { lesson_node_id: lessonId, payload: { material_selections: [{ link_id: 'link-1', start_unit: 1, end_unit: 1, ppt_intent: 'keep' as const }], exercise_candidate_ids: [], question_ids: [], assessment_ids: [], knowledge_scope: [], preparation_preferences: {}, class_name: null, teacher_context: null }, source_state_sha256: 'a'.repeat(64), revision: 2, created_at: '', updated_at: '' },
      model_available: true, model_label: '合成模型', model_destination_fingerprint: 'f'.repeat(64), will_call_model: false as const,
    }
    const pane = ref<'source' | 'preview' | 'review'>('source')
    const fake = {
      stage: ref('materials'), panel: ref('sources'), pane, dirtyReason: ref(null), referencePreflight: ref(preflight), activeSuggestionRun: ref(null),
      catalog: { selectedLessonId: lessonId, selectedLesson: { id: lessonId, revision: 3 }, resourcePacks: [], selectedResourcePackId: null, lessonDrafts: [], selectedLessonDraftId: null, teachingPreferences: { payload: {} }, saveState: 'idle' },
      setDirty: () => undefined, refreshCurrentWorkspace: async () => undefined,
      openPanel: async () => undefined, openStage: async () => undefined, watchSuggestionRun: () => undefined,
      setPane: (value: 'source' | 'preview' | 'review') => { pane.value = value },
    }
    vi.spyOn(teachingPrepWorkbenchApi, 'saveReferenceDraft').mockRejectedValue(new Error('conflict'))
    const freeze = vi.spyOn(teachingPrepWorkbenchApi, 'freezeReferenceSnapshot')
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp(LessonPreparationWorkspace)
    app.use(createPinia())
    app.provide(teachingPrepWorkbenchKey, fake as never)
    app.mount(host)
    await nextTick()

    const button = [...host.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent?.includes('生成 AI 候选题'))
    button?.click()
    await vi.waitFor(() => expect(host.textContent).toContain('保存失败'))

    expect(freeze).not.toHaveBeenCalled()
    app.unmount()
  })

  it('keeps every source page navigable and exposes page jump zoom and fit-width controls', async () => {
    const lessonId = 'm'.repeat(32)
    const pane = ref<'source' | 'preview' | 'review'>('source')
    const units = [
      { unit_id: 'unit-1', unit_index: 1, unit_kind: 'page', title: '第一页', preview_url: '/preview/1', text_status: 'ready', formula_review_required: false },
      { unit_id: 'unit-2', unit_index: 2, unit_kind: 'page', title: '第二页', preview_url: '/preview/2', text_status: 'ready', formula_review_required: false },
      { unit_id: 'unit-3', unit_index: 3, unit_kind: 'page', title: '第三页', preview_url: '/preview/3', text_status: 'ready', formula_review_required: false },
    ]
    const preflight = {
      lesson_node_id: lessonId, source_state_sha256: 'a'.repeat(64),
      catalog: { lesson: {}, material_links: [{ link_id: 'link-1', link_revision: 1, purpose: 'textbook', material_version_id: 'material-1', material_name: '教材', material_type: 'pdf', content_sha256: 'b'.repeat(64), start_unit: 1, end_unit: 3, units }] },
      draft: null, model_available: false, model_label: null, model_destination_fingerprint: 'f'.repeat(64), will_call_model: false as const,
    }
    const fake = {
      stage: ref('materials'), panel: ref('sources'), pane, dirtyReason: ref(null), referencePreflight: ref(preflight), activeSuggestionRun: ref(null),
      catalog: { selectedLessonId: lessonId, selectedLesson: { id: lessonId, revision: 3 }, selectedSemester: { id: 'semester-2', revision: 4 }, resourcePacks: [], selectedResourcePackId: null, lessonDrafts: [], selectedLessonDraftId: null, teachingPreferences: { payload: {} }, saveState: 'idle' },
      setDirty: () => undefined, refreshCurrentWorkspace: async () => undefined,
      openPanel: async () => undefined, openStage: async () => undefined, watchSuggestionRun: () => undefined,
      setPane: (value: 'source' | 'preview' | 'review') => { pane.value = value },
    }
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp(LessonPreparationWorkspace)
    app.use(createPinia())
    app.provide(teachingPrepWorkbenchKey, fake as never)
    app.mount(host)
    await nextTick()

    expect(host.querySelectorAll('.tp-document-workspace__mobile-tabs button')).toHaveLength(3)
    const next = host.querySelector<HTMLButtonElement>('button[aria-label="下一页"]')
    next?.click()
    await nextTick()
    expect(host.querySelector<HTMLImageElement>('.tp-document-workspace__preview')?.getAttribute('src')).toBe('/preview/2')

    const jump = host.querySelector<HTMLInputElement>('input[aria-label="跳转页码"]')
    if (jump) {
      jump.value = '3'
      jump.dispatchEvent(new Event('change', { bubbles: true }))
    }
    await nextTick()
    expect(host.querySelector<HTMLImageElement>('.tp-document-workspace__preview')?.getAttribute('src')).toBe('/preview/3')

    host.querySelector<HTMLButtonElement>('button[aria-label="放大预览"]')?.click()
    await nextTick()
    expect(host.querySelector<HTMLImageElement>('.tp-document-workspace__preview')?.style.inlineSize).toBe('110%')

    const fit = host.querySelector<HTMLButtonElement>('button[aria-label="适合宽度"]')
    fit?.click()
    await nextTick()
    expect(fit?.getAttribute('aria-pressed')).toBe('true')
    expect(host.querySelector<HTMLImageElement>('.tp-document-workspace__preview')?.style.inlineSize).toBe('100%')
    app.unmount()
  })
})
