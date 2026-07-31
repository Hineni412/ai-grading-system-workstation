import { createApp, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { teachingPrepCatalogApi } from '../api/catalog'
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

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('TeachingPrepHomeView', () => {
  it('loads reactive teaching preferences without crashing the page', async () => {
    vi.spyOn(teachingPrepCatalogApi, 'status').mockResolvedValue({
      module: 'teaching-prep',
      enabled: true,
      schema_version: '012_generation_performance',
      real_model_enabled: false,
      semester_mapping_model_available: false,
      real_wps_enabled: false,
      wps_execution_available: false,
    })
    vi.spyOn(teachingPrepCatalogApi, 'getTeachingPreferences')
      .mockResolvedValue({
        revision: 1,
        payload: preferences,
        updated_at: '2026-07-31T00:00:00Z',
      })
    vi.spyOn(teachingPrepCatalogApi, 'listCurricula').mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesters').mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listMaterials').mockResolvedValue([])

    const errors: unknown[] = []
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp(TeachingPrepHomeView)
    app.config.errorHandler = (error) => errors.push(error)
    app.use(createPinia())
    app.mount(host)

    await Promise.resolve()
    await nextTick()
    await Promise.resolve()
    await nextTick()

    expect(errors).toEqual([])
    expect(host.textContent).toContain('备课工作台')
    app.unmount()
  })

  it('lets the teacher update the semester plan and status', async () => {
    const curriculum = {
      id: 'c'.repeat(32),
      title: '八年级上册',
      grade_level: 8,
      volume: 'first' as const,
      publisher: null,
      edition_label: null,
      revision: 1,
      is_active: true,
      created_at: '2026-07-31T00:00:00Z',
      updated_at: '2026-07-31T00:00:00Z',
    }
    const semester = {
      id: 's'.repeat(32),
      curriculum_id: curriculum.id,
      curriculum_title: curriculum.title,
      school_year: '2026-2027',
      term: 'first' as const,
      planned_new_lesson_count: 48,
      status: 'planning' as const,
      active_lesson_count: 0,
      not_started_lesson_count: 0,
      preparing_lesson_count: 0,
      ready_lesson_count: 0,
      taught_lesson_count: 0,
      skipped_lesson_count: 0,
      material_count: 0,
      parsed_material_count: 0,
      mapped_material_count: 0,
      revision: 1,
      created_at: '2026-07-31T00:00:00Z',
      updated_at: '2026-07-31T00:00:00Z',
    }
    vi.spyOn(teachingPrepCatalogApi, 'status').mockResolvedValue({
      module: 'teaching-prep',
      enabled: true,
      schema_version: '012_generation_performance',
      real_model_enabled: false,
      semester_mapping_model_available: false,
      real_wps_enabled: false,
      wps_execution_available: false,
    })
    vi.spyOn(teachingPrepCatalogApi, 'getTeachingPreferences')
      .mockResolvedValue({
        revision: 1,
        payload: preferences,
        updated_at: '2026-07-31T00:00:00Z',
      })
    vi.spyOn(teachingPrepCatalogApi, 'listCurricula')
      .mockResolvedValue([curriculum])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesters')
      .mockResolvedValue([semester])
    vi.spyOn(teachingPrepCatalogApi, 'listMaterials').mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listLessons').mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterLessonProgress')
      .mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMaterials')
      .mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals')
      .mockResolvedValue([])
    const update = vi.spyOn(teachingPrepCatalogApi, 'updateSemester')
      .mockResolvedValue({ ...semester, status: 'active', revision: 2 })
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp(TeachingPrepHomeView)
    app.use(createPinia())
    app.mount(host)

    await Promise.resolve()
    await nextTick()
    await Promise.resolve()
    await nextTick()
    const form = host.querySelector('.teaching-prep-semester-summary__edit')
    const status = form?.querySelector('select')
    const save = form?.querySelector('button')
    expect(form?.textContent).toContain('保存学期状态')
    expect(status).toBeInstanceOf(HTMLSelectElement)
    expect(save).toBeInstanceOf(HTMLButtonElement)
    ;(status as HTMLSelectElement).value = 'active'
    status?.dispatchEvent(new Event('change'))
    save?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await Promise.resolve()
    await nextTick()

    expect(update).toHaveBeenCalledWith(
      semester,
      {
        planned_new_lesson_count: 48,
        status: 'active',
      },
    )
    app.unmount()
  })
})
