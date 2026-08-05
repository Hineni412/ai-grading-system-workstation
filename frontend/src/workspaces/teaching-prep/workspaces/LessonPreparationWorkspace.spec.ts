import { createPinia } from 'pinia'
import { createApp, nextTick, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { teachingPrepWorkbenchApi } from '../api/workbench'
import { teachingPrepWorkbenchKey } from '../workbench/context'
import LessonPreparationWorkspace from './LessonPreparationWorkspace.vue'

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('LessonPreparationWorkspace common AI submission', () => {
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
