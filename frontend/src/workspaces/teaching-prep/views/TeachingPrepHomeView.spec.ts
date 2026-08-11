import { createPinia } from 'pinia'
import { createApp, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { questionBankApi, type CurriculumCatalog, type CurriculumVolume } from '../../../api/question-bank'
import { useCurriculumScopeStore } from '../../../stores/curriculum-scope'
import { teachingPrepCatalogApi } from '../api/catalog'
import { teachingPrepWorkbenchApi, type LessonPreparationStatus } from '../api/workbench'
import { useTeachingPrepCatalogStore } from '../stores/catalog'
import { hasFormalLessonTree } from '../workbench/lessonStatus'
import TeachingPrepHomeView from './TeachingPrepHomeView.vue'

const preferences = {
  schema_version: 1 as const,
  label_textbook_pages: true,
  page_label_font_size: 28 as const,
  trim_excess_practice: true,
  practice_trim_level: 'moderate' as const,
  preserve_teaching_examples: true,
  prefer_short_practice: true,
  supplement_from_references: true,
  supplement_question_limit: 2,
  supplement_as_source_image: true,
  prioritize_homework_workbook: true,
  avoid_direct_homework_copy: true,
  avoid_ppt_duplicates: true,
}

const globalVolume: CurriculumVolume = {
  id: 'g7-first',
  order: 1,
  label: '七年级上册',
  grade: '七年级',
  semester: '上册',
  textbook_version: '数学',
  source: {},
  statistics: { raw_nodes: 0, excluded_nodes: 0, retained_nodes: 0 },
  chapters: [],
}

const globalCurriculumCatalog: CurriculumCatalog = {
  schema_version: 2,
  catalog_id: 'test-catalog',
  knowledge_standard_id: 'test-standard',
  publisher: '测试出版社',
  subject: '数学',
  edition: '测试版',
  statistics: {
    raw_nodes: 0,
    excluded_nodes: 0,
    retained_nodes: 0,
    chapters: 0,
    sections: 0,
    knowledge_points: 0,
  },
  volumes: [globalVolume],
}

function mockEmptyCatalog(): void {
  vi.spyOn(questionBankApi, 'getCurriculum').mockResolvedValue({
    ...globalCurriculumCatalog,
    volumes: [],
  })
  vi.spyOn(teachingPrepCatalogApi, 'status').mockResolvedValue({
    module: 'teaching-prep',
    enabled: true,
    schema_version: '013_workbench_iteration',
    real_model_enabled: false,
    semester_mapping_model_available: false,
    exercise_suggestion_model_available: false,
    real_wps_enabled: false,
    wps_execution_available: false,
  })
  vi.spyOn(teachingPrepCatalogApi, 'getTeachingPreferences').mockResolvedValue({
    revision: 1,
    payload: preferences,
    updated_at: '2026-08-01T00:00:00Z',
  })
  vi.spyOn(teachingPrepCatalogApi, 'listCurricula').mockResolvedValue([])
  vi.spyOn(teachingPrepCatalogApi, 'listSemesters').mockResolvedValue([])
  vi.spyOn(teachingPrepCatalogApi, 'listMaterials').mockResolvedValue([])
  vi.spyOn(teachingPrepCatalogApi, 'listMaterialParseJobs').mockResolvedValue([])
}

function statusFor(id: string, sortOrder: number, manual: LessonPreparationStatus['manual_progress'] = 'not_started'): LessonPreparationStatus {
  const cell = { status: 'not_started' as const, summary: '尚未开始', target_panel: 'sources' }
  return {
    lesson_node_id: id, title: `课时 ${sortOrder}`, sort_order: sortOrder, duration_minutes: 45,
    manual_progress: manual, manual_progress_revision: 1, preparation_stage: 'materials',
    next_action: '核对资料', blockers: [],
    cells: { materials: cell, plan: { ...cell, target_panel: 'plan' }, exercises: { ...cell, target_panel: 'exercises' }, slides: { ...cell, target_panel: 'slides' } },
    ai_tasks: [], summary_revision: 'a'.repeat(64),
    latest: { resource_pack_id: null, lesson_draft_id: null, slide_plan_id: null, pptx_version_id: null, pptx_revision: null, up_class_package_id: null },
  }
}

function mockCatalogWithLessons(count = 1): { semesterId: string; lessonIds: string[] } {
  mockEmptyCatalog()
  const curriculumId = 'c'.repeat(32)
  const semesterId = 's'.repeat(32)
  const lessonIds = Array.from({ length: count }, (_, index) => `${index + 1}`.padStart(32, '0'))
  vi.mocked(teachingPrepCatalogApi.listCurricula).mockResolvedValue([{ id: curriculumId, title: '七年级数学', grade_level: 7, volume: 'first', publisher: null, edition_label: null, revision: 1, is_active: true, created_at: '', updated_at: '' }])
  vi.mocked(teachingPrepCatalogApi.listSemesters).mockResolvedValue([{ id: semesterId, curriculum_id: curriculumId, curriculum_title: '七年级数学', school_year: '2026', term: 'first', planned_new_lesson_count: count, status: 'active', active_lesson_count: count, not_started_lesson_count: count, preparing_lesson_count: 0, ready_lesson_count: 0, taught_lesson_count: 0, skipped_lesson_count: 0, material_count: 0, parsed_material_count: 0, mapped_material_count: 0, revision: 1, created_at: '', updated_at: '' }])
  vi.spyOn(teachingPrepCatalogApi, 'listLessons').mockResolvedValue(lessonIds.map((id, index) => ({ id, curriculum_id: curriculumId, parent_id: null, node_type: 'lesson' as const, title: `课时 ${index + 1}`, sort_order: index + 1, duration_minutes: 45, source_kind: 'teacher' as const, is_active: true, revision: 1, created_at: '', updated_at: '' })))
  vi.spyOn(teachingPrepCatalogApi, 'listSemesterLessonProgress').mockResolvedValue([])
  vi.spyOn(teachingPrepCatalogApi, 'listSemesterMaterials').mockResolvedValue([])
  vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals').mockResolvedValue([])
  vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposalJobs').mockResolvedValue([])
  vi.spyOn(teachingPrepCatalogApi, 'listMaterialLinks').mockResolvedValue([])
  vi.spyOn(teachingPrepCatalogApi, 'listExerciseCandidates').mockResolvedValue([])
  vi.spyOn(teachingPrepCatalogApi, 'listResourcePacks').mockResolvedValue([])
  vi.spyOn(teachingPrepCatalogApi, 'getResourcePackStatus').mockResolvedValue({} as never)
  vi.spyOn(teachingPrepCatalogApi, 'listClassVariants').mockResolvedValue([])
  vi.spyOn(teachingPrepCatalogApi, 'listUpClassPackages').mockResolvedValue([])
  vi.spyOn(teachingPrepCatalogApi, 'listPostLessonReviews').mockResolvedValue([])
  vi.spyOn(teachingPrepWorkbenchApi, 'lessonStatuses').mockResolvedValue(lessonIds.map((id, index) => statusFor(id, index + 1, index === 0 ? 'taught' : index === 2 ? 'skipped' : 'not_started')))
  vi.spyOn(teachingPrepWorkbenchApi, 'referencePreflight').mockImplementation(async lessonId => ({ lesson_node_id: lessonId, source_state_sha256: 'a'.repeat(64), catalog: { lesson: {}, material_links: [] }, draft: null, model_available: false, model_label: null, model_destination_fingerprint: 'f'.repeat(64), will_call_model: false }))
  return { semesterId, lessonIds }
}

function mockCatalogWithGlobalTextbookSwitch(): { targetSemesterId: string } {
  mockCatalogWithLessons()
  const targetCurriculumId = 'c'.repeat(32)
  const targetSemesterId = 's'.repeat(32)
  const initialCurriculumId = 'd'.repeat(32)
  const initialSemesterId = 't'.repeat(32)
  vi.mocked(teachingPrepCatalogApi.listCurricula).mockResolvedValue([
    { id: initialCurriculumId, title: '八年级数学', grade_level: 8, volume: 'first', publisher: null, edition_label: null, revision: 1, is_active: true, created_at: '', updated_at: '' },
    { id: targetCurriculumId, title: '七年级数学', grade_level: 7, volume: 'first', publisher: null, edition_label: null, revision: 1, is_active: true, created_at: '', updated_at: '' },
  ])
  vi.mocked(teachingPrepCatalogApi.listSemesters).mockResolvedValue([
    { id: initialSemesterId, curriculum_id: initialCurriculumId, curriculum_title: '八年级数学', school_year: '2026', term: 'first', planned_new_lesson_count: 1, status: 'active', active_lesson_count: 1, not_started_lesson_count: 1, preparing_lesson_count: 0, ready_lesson_count: 0, taught_lesson_count: 0, skipped_lesson_count: 0, material_count: 0, parsed_material_count: 0, mapped_material_count: 0, revision: 1, created_at: '', updated_at: '' },
    { id: targetSemesterId, curriculum_id: targetCurriculumId, curriculum_title: '七年级数学', school_year: '2026', term: 'first', planned_new_lesson_count: 1, status: 'active', active_lesson_count: 1, not_started_lesson_count: 1, preparing_lesson_count: 0, ready_lesson_count: 0, taught_lesson_count: 0, skipped_lesson_count: 0, material_count: 0, parsed_material_count: 0, mapped_material_count: 0, revision: 1, created_at: '', updated_at: '' },
  ])
  return { targetSemesterId }
}

async function mountAt(
  query = '',
  configurePinia?: (pinia: ReturnType<typeof createPinia>) => void,
) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/teaching-prep', component: TeachingPrepHomeView }],
  })
  await router.push(`/teaching-prep${query}`)
  await router.isReady()
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(TeachingPrepHomeView)
  const pinia = createPinia()
  configurePinia?.(pinia)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  for (let index = 0; index < 8; index += 1) {
    await Promise.resolve()
    await nextTick()
  }
  await new Promise(resolve => setTimeout(resolve, 0))
  await nextTick()
  await vi.waitFor(() => expect(host.textContent).not.toBe(''))
  return { app, host, router, pinia }
}

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
  localStorage.clear()
})

describe('TeachingPrepHomeView three-view shell', () => {
  it('opens the overview by default without loading material collections', async () => {
    mockCatalogWithLessons()

    const { app, host } = await mountAt()

    expect(host.querySelector('[aria-label="备课首页"]')).toBeTruthy()
    expect(teachingPrepCatalogApi.listMaterials).not.toHaveBeenCalled()
    expect(teachingPrepCatalogApi.listMaterialParseJobs).not.toHaveBeenCalled()
    expect(teachingPrepCatalogApi.listSemesterMaterials).not.toHaveBeenCalled()
    expect(teachingPrepCatalogApi.listSemesterMappingProposals).not.toHaveBeenCalled()
    app.unmount()
  })

  it('loads material collections for a direct library link', async () => {
    mockCatalogWithLessons()

    const { app, host } = await mountAt('?view=library')

    await vi.waitFor(() => {
      expect(host.querySelector('[aria-label="资料库"]')).toBeTruthy()
    })
    expect(teachingPrepCatalogApi.listMaterials).toHaveBeenCalled()
    expect(teachingPrepCatalogApi.listMaterialParseJobs).toHaveBeenCalled()
    expect(teachingPrepCatalogApi.listSemesterMaterials).toHaveBeenCalled()
    app.unmount()
  })

  it('renders the lesson workbench rail for a valid lesson deep link', async () => {
    const { lessonIds } = mockCatalogWithLessons()

    const { app, host } = await mountAt(`?view=lesson&lesson=${lessonIds[0]}&step=1`)

    await vi.waitFor(() => {
      expect(host.querySelector('[aria-label="备课步骤"]')).toBeTruthy()
    })
    expect(teachingPrepWorkbenchApi.referencePreflight).toHaveBeenCalledWith(lessonIds[0])
    app.unmount()
  })

  it('returns an invalid lesson deep link to the overview', async () => {
    mockCatalogWithLessons()

    const { app, router } = await mountAt(`?view=lesson&lesson=${'x'.repeat(32)}`)

    await vi.waitFor(() => {
      expect(router.currentRoute.value.query.view).toBe('overview')
    })
    expect(router.currentRoute.value.query.lesson).toBeUndefined()
    app.unmount()
  })

  it('aligns the selected semester when the global textbook switches', async () => {
    const { targetSemesterId } = mockCatalogWithGlobalTextbookSwitch()

    const { app, pinia } = await mountAt('', (nextPinia) => {
      const scope = useCurriculumScopeStore(nextPinia)
      scope.volumes = [globalVolume]
      scope.selectedVolumeId = globalVolume.id
      scope.loadState = 'ready'
    })

    await vi.waitFor(() => {
      expect(useTeachingPrepCatalogStore(pinia).selectedSemester?.id).toBe(targetSemesterId)
    })
    expect(teachingPrepCatalogApi.listMaterials).not.toHaveBeenCalled()
    app.unmount()
  })

  it('treats only an active lesson as a completed formal lesson tree', () => {
    expect(hasFormalLessonTree([
      { node_type: 'chapter', is_active: true },
      { node_type: 'section', is_active: true },
      { node_type: 'lesson', is_active: false },
    ])).toBe(false)
    expect(hasFormalLessonTree([
      { node_type: 'lesson', is_active: true },
    ])).toBe(true)
  })
})
