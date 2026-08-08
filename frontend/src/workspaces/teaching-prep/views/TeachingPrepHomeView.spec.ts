import { createPinia } from 'pinia'
import { createApp, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { teachingPrepCatalogApi } from '../api/catalog'
import { teachingPrepWorkbenchApi, type LessonPreparationStatus } from '../api/workbench'
import { useTeachingPrepCatalogStore } from '../stores/catalog'
import { hasFormalLessonTree } from '../workbench/state'
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

function mockEmptyCatalog(): void {
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

async function mountAt(query = '') {
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
  app.use(pinia)
  app.use(router)
  app.mount(host)
  for (let index = 0; index < 8; index += 1) {
    await Promise.resolve()
    await nextTick()
  }
  await new Promise(resolve => setTimeout(resolve, 0))
  await nextTick()
  return { app, host, router, pinia }
}

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('TeachingPrepHomeView workbench shell', () => {
  it('opens the overview first and removes the redundant local header', async () => {
    mockEmptyCatalog()
    const { app, host, router } = await mountAt()

    expect(host.querySelector('.tp-shell-header')).toBeNull()
    expect(host.querySelectorAll('.tp-stage-ruler__step')).toHaveLength(0)
    expect(host.textContent).toContain('近期课时')
    expect(router.currentRoute.value.query).toMatchObject({
      view: 'overview',
      workspace: 'lesson-tree',
      stage: 'select',
    })
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

  it('restores the selected internal workspace from query parameters', async () => {
    mockEmptyCatalog()
    const { app, host, router } = await mountAt('?workspace=materials&stage=materials')

    expect(host.textContent).toContain('建立可复用的学期资料目录')
    expect(router.currentRoute.value.query).toMatchObject({
      workspace: 'materials',
      stage: 'materials',
    })
    app.unmount()
  })

  it('gives the manifest library view priority over stale internal route state', async () => {
    mockEmptyCatalog()
    const { app, host, router } = await mountAt(
      '?view=library&workspace=lesson-tree&stage=select',
    )

    expect(host.textContent).toContain('建立可复用的学期资料目录')
    expect(router.currentRoute.value.query).toMatchObject({
      view: 'library',
      workspace: 'materials',
      stage: 'materials',
    })
    app.unmount()
  })

  it('handles topbar workspace events through the workbench navigation flow', async () => {
    mockEmptyCatalog()
    const { app, router } = await mountAt()

    globalThis.dispatchEvent(new CustomEvent('teaching-prep:open-workspace', {
      detail: { workspace: 'lesson-tree' },
    }))
    await vi.waitFor(() => expect(router.currentRoute.value.query).toMatchObject({
      workspace: 'lesson-tree',
      stage: 'select',
    }))

    app.unmount()
  })

  it('returns an invalid lesson deep link to overview without selecting another lesson', async () => {
    const { semesterId } = mockCatalogWithLessons()
    const { app, host, router } = await mountAt(`?view=lesson&semester=${semesterId}&lesson=${'x'.repeat(32)}&stage=materials&panel=exercises`)

    expect(host.textContent).toContain('没有改选其他课时')
    expect(host.textContent).toContain('近期课时')
    expect(router.currentRoute.value.query.view).toBe('overview')
    expect(router.currentRoute.value.query.lesson).toBeUndefined()
    app.unmount()
  })

  it('restores semester lesson stage panel and focus_ref for a valid lesson link', async () => {
    const { semesterId, lessonIds } = mockCatalogWithLessons()
    const focus = 'candidate-001'
    const { app, host, router } = await mountAt(`?view=lesson&semester=${semesterId}&lesson=${lessonIds[0]}&stage=materials&panel=sources&focus_ref=${focus}`)

    expect(router.currentRoute.value.query).toMatchObject({ view: 'lesson', semester: semesterId, lesson: lessonIds[0], stage: 'materials', panel: 'sources', focus_ref: focus })
    expect(host.querySelector('.tp-lesson-frame__mobile-tabs')).toBeNull()
    expect(host.querySelectorAll('.tp-document-workspace__mobile-tabs button')).toHaveLength(3)
    app.unmount()
  })

  it('selects the linked semester before validating a cross-semester lesson', async () => {
    mockCatalogWithLessons(1)
    const firstCurriculum = 'a'.repeat(32)
    const secondCurriculum = 'b'.repeat(32)
    const firstSemester = 'u'.repeat(32)
    const secondSemester = 'v'.repeat(32)
    const firstLesson = '1'.repeat(32)
    const secondLesson = '2'.repeat(32)
    const curriculum = (id: string, title: string) => ({ id, title, grade_level: 7, volume: 'first' as const, publisher: null, edition_label: null, revision: 1, is_active: true, created_at: '', updated_at: '' })
    const semester = (id: string, curriculumId: string, title: string) => ({ id, curriculum_id: curriculumId, curriculum_title: title, school_year: '2026', term: 'first' as const, planned_new_lesson_count: 1, status: 'active' as const, active_lesson_count: 1, not_started_lesson_count: 1, preparing_lesson_count: 0, ready_lesson_count: 0, taught_lesson_count: 0, skipped_lesson_count: 0, material_count: 0, parsed_material_count: 0, mapped_material_count: 0, revision: 1, created_at: '', updated_at: '' })
    const lesson = (id: string, curriculumId: string, title: string) => ({ id, curriculum_id: curriculumId, parent_id: null, node_type: 'lesson' as const, title, sort_order: 1, duration_minutes: 45, source_kind: 'teacher' as const, is_active: true, revision: 1, created_at: '', updated_at: '' })
    vi.mocked(teachingPrepCatalogApi.listCurricula).mockResolvedValue([
      curriculum(firstCurriculum, '七年级上'),
      curriculum(secondCurriculum, '七年级下'),
    ])
    vi.mocked(teachingPrepCatalogApi.listSemesters).mockResolvedValue([
      semester(firstSemester, firstCurriculum, '七年级上'),
      semester(secondSemester, secondCurriculum, '七年级下'),
    ])
    vi.mocked(teachingPrepCatalogApi.listLessons).mockImplementation(async curriculumId => (
      curriculumId === firstCurriculum
        ? [lesson(firstLesson, firstCurriculum, '第一学期课时')]
        : [lesson(secondLesson, secondCurriculum, '第二学期课时')]
    ))
    vi.mocked(teachingPrepWorkbenchApi.lessonStatuses).mockImplementation(async semesterId => (
      semesterId === firstSemester
        ? [statusFor(firstLesson, 1)]
        : [statusFor(secondLesson, 1)]
    ))

    const { app, host, router } = await mountAt(`?view=lesson&semester=${secondSemester}&lesson=${secondLesson}&stage=materials&panel=sources`)

    expect(router.currentRoute.value.query).toMatchObject({
      view: 'lesson', semester: secondSemester, lesson: secondLesson,
      stage: 'materials', panel: 'sources',
    })
    expect(host.textContent).toContain('第二学期课时')
    expect(teachingPrepCatalogApi.listLessons).toHaveBeenCalledWith(secondCurriculum)
    app.unmount()
  })

  it('switches semester before an already-mounted library return and replays history', async () => {
    mockCatalogWithLessons(1)
    const firstCurriculum = 'a'.repeat(32)
    const secondCurriculum = 'b'.repeat(32)
    const firstSemester = 'u'.repeat(32)
    const secondSemester = 'v'.repeat(32)
    const firstLesson = '1'.repeat(32)
    const secondLesson = '2'.repeat(32)
    const curriculum = (id: string, title: string) => ({ id, title, grade_level: 7, volume: 'first' as const, publisher: null, edition_label: null, revision: 1, is_active: true, created_at: '', updated_at: '' })
    const semester = (id: string, curriculumId: string, title: string) => ({ id, curriculum_id: curriculumId, curriculum_title: title, school_year: '2026', term: 'first' as const, planned_new_lesson_count: 1, status: 'active' as const, active_lesson_count: 1, not_started_lesson_count: 1, preparing_lesson_count: 0, ready_lesson_count: 0, taught_lesson_count: 0, skipped_lesson_count: 0, material_count: 0, parsed_material_count: 0, mapped_material_count: 0, revision: 1, created_at: '', updated_at: '' })
    const lesson = (id: string, curriculumId: string, title: string) => ({ id, curriculum_id: curriculumId, parent_id: null, node_type: 'lesson' as const, title, sort_order: 1, duration_minutes: 45, source_kind: 'teacher' as const, is_active: true, revision: 1, created_at: '', updated_at: '' })
    vi.mocked(teachingPrepCatalogApi.listCurricula).mockResolvedValue([
      curriculum(firstCurriculum, '七年级上'),
      curriculum(secondCurriculum, '七年级下'),
    ])
    vi.mocked(teachingPrepCatalogApi.listSemesters).mockResolvedValue([
      semester(firstSemester, firstCurriculum, '七年级上'),
      semester(secondSemester, secondCurriculum, '七年级下'),
    ])
    vi.mocked(teachingPrepCatalogApi.listLessons).mockImplementation(async curriculumId => (
      curriculumId === firstCurriculum
        ? [lesson(firstLesson, firstCurriculum, '第一学期课时')]
        : [lesson(secondLesson, secondCurriculum, '第二学期课时')]
    ))
    vi.mocked(teachingPrepWorkbenchApi.lessonStatuses).mockImplementation(async semesterId => (
      semesterId === firstSemester
        ? [statusFor(firstLesson, 1)]
        : [statusFor(secondLesson, 1)]
    ))
    let releaseSecondMaterials!: () => void
    const secondMaterials = new Promise<never[]>((resolve) => {
      releaseSecondMaterials = () => resolve([])
    })
    vi.mocked(teachingPrepCatalogApi.listSemesterMaterials).mockImplementation(semesterId => (
      semesterId === secondSemester ? secondMaterials : Promise.resolve([])
    ))

    const { app, host, router, pinia } = await mountAt(
      `?view=overview&semester=${firstSemester}`,
    )
    const catalog = useTeachingPrepCatalogStore(pinia)
    expect(catalog.selectedSemester?.id).toBe(firstSemester)

    await router.push({
      path: '/teaching-prep',
      query: {
        view: 'library',
        semester: secondSemester,
        source_task_id: 'task-cross-semester-library',
      },
    })
    await vi.waitFor(() => {
      expect(teachingPrepCatalogApi.listSemesterMaterials).toHaveBeenCalledWith(
        secondSemester,
      )
    })
    expect(router.currentRoute.value.query.semester).toBe(secondSemester)
    expect(host.textContent).not.toContain('建立可复用的学期资料目录')

    releaseSecondMaterials()
    await vi.waitFor(() => {
      expect(catalog.selectedSemester?.id).toBe(secondSemester)
      expect(host.textContent).toContain('建立可复用的学期资料目录')
      expect(router.currentRoute.value.query).toMatchObject({
        view: 'library',
        semester: secondSemester,
        workspace: 'materials',
        stage: 'materials',
        source_task_id: 'task-cross-semester-library',
      })
    })

    router.back()
    await vi.waitFor(() => {
      expect(catalog.selectedSemester?.id).toBe(firstSemester)
      expect(router.currentRoute.value.query).toMatchObject({
        view: 'overview',
        semester: firstSemester,
        workspace: 'lesson-tree',
        stage: 'select',
      })
    })
    router.forward()
    await vi.waitFor(() => {
      expect(catalog.selectedSemester?.id).toBe(secondSemester)
      expect(host.textContent).toContain('建立可复用的学期资料目录')
      expect(router.currentRoute.value.query.semester).toBe(secondSemester)
    })
    app.unmount()
  })

  it('shows at most eight nearby non-skipped lessons and keeps only limited taught context', async () => {
    mockCatalogWithLessons(10)
    const { app, host } = await mountAt()

    const rows = host.querySelectorAll('.tp-readiness-matrix__row')
    expect(rows.length).toBeGreaterThanOrEqual(5)
    expect(rows.length).toBeLessThanOrEqual(8)
    expect(host.textContent).not.toContain('课时 3')
    app.unmount()
  })

  it('backfills five to eight recent lessons when the active point is at semester end', async () => {
    const { lessonIds } = mockCatalogWithLessons(10)
    vi.mocked(teachingPrepWorkbenchApi.lessonStatuses).mockResolvedValue(lessonIds.map((id, index) => (
      statusFor(id, index + 1, index < 9 ? 'taught' : 'not_started')
    )))
    const { app, host } = await mountAt()

    const rows = host.querySelectorAll('.tp-readiness-matrix__row')
    expect(rows.length).toBeGreaterThanOrEqual(5)
    expect(rows.length).toBeLessThanOrEqual(8)
    expect(host.textContent).toContain('课时 10')
    app.unmount()
  })

  it('shows the latest five to eight lessons when every lesson is taught', async () => {
    const { lessonIds } = mockCatalogWithLessons(10)
    vi.mocked(teachingPrepWorkbenchApi.lessonStatuses).mockResolvedValue(lessonIds.map((id, index) => (
      statusFor(id, index + 1, 'taught')
    )))
    const { app, host } = await mountAt()

    const rows = host.querySelectorAll('.tp-readiness-matrix__row')
    expect(rows.length).toBeGreaterThanOrEqual(5)
    expect(rows.length).toBeLessThanOrEqual(8)
    expect(host.textContent).toContain('课时 10')
    app.unmount()
  })

  it('blocks manifest query navigation while the current lesson has unsaved edits', async () => {
    const { semesterId, lessonIds } = mockCatalogWithLessons()
    vi.mocked(teachingPrepWorkbenchApi.referencePreflight).mockImplementation(async lessonId => ({
      lesson_node_id: lessonId,
      source_state_sha256: 'a'.repeat(64),
      catalog: {
        lesson: {},
        material_links: ['主课件 A', '主课件 B'].map((name, index) => ({
          link_id: `ppt-${index + 1}`,
          link_revision: 1,
          purpose: 'reference_ppt',
          material_version_id: `version-${index + 1}`,
          material_name: name,
          material_type: 'pptx',
          content_sha256: String(index + 1).repeat(64),
          start_unit: 1,
          end_unit: 10,
          units: [],
        })),
      },
      draft: null,
      model_available: false,
      model_label: null,
      model_destination_fingerprint: 'f'.repeat(64),
      will_call_model: false,
    }))
    const confirm = vi.spyOn(globalThis, 'confirm').mockReturnValue(false)
    const { app, host, router } = await mountAt(`?view=lesson&semester=${semesterId}&lesson=${lessonIds[0]}&stage=materials&panel=sources`)
    const radios = host.querySelectorAll<HTMLInputElement>('input[name="primary-reference-ppt"]')
    radios[1]?.click()
    await nextTick()

    await router.push('/teaching-prep?view=library')

    expect(confirm).toHaveBeenCalledOnce()
    expect(router.currentRoute.value.query.view).toBe('lesson')
    app.unmount()
  })
})
