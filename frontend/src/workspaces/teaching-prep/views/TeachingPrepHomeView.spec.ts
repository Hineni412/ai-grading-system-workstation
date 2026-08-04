import { createPinia } from 'pinia'
import { createApp, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { teachingPrepCatalogApi } from '../api/catalog'
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
  app.use(createPinia())
  app.use(router)
  app.mount(host)
  for (let index = 0; index < 8; index += 1) {
    await Promise.resolve()
    await nextTick()
  }
  await new Promise(resolve => setTimeout(resolve, 0))
  await nextTick()
  return { app, host, router }
}

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('TeachingPrepHomeView workbench shell', () => {
  it('opens the library first and removes the redundant local header', async () => {
    mockEmptyCatalog()
    const { app, host, router } = await mountAt()

    expect(host.querySelector('.tp-shell-header')).toBeNull()
    expect(host.querySelectorAll('.tp-stage-ruler__step')).toHaveLength(0)
    expect(host.textContent).toContain('建立可复用的学期资料目录')
    expect(router.currentRoute.value.query).toMatchObject({
      workspace: 'materials',
      stage: 'materials',
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
})
