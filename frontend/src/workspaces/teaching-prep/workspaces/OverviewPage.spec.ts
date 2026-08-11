import { createPinia } from 'pinia'
import { createApp, nextTick, computed } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { LessonNode, TeachingSemester } from '../api/catalog'
import { teachingPrepWorkbenchApi } from '../api/workbench'
import { useTeachingPrepCatalogStore } from '../stores/catalog'
import { teachingPrepRouteStateKey } from '../workbench/routeContext'
import type { TeachingPrepRouteState } from '../workbench/routeState'
import OverviewPage from './OverviewPage.vue'

const semester: TeachingSemester = {
  id: 's'.repeat(32),
  curriculum_id: 'c'.repeat(32),
  curriculum_title: '七年级数学',
  school_year: '2026',
  term: 'first',
  planned_new_lesson_count: 2,
  status: 'active',
  active_lesson_count: 2,
  not_started_lesson_count: 2,
  preparing_lesson_count: 0,
  ready_lesson_count: 0,
  taught_lesson_count: 0,
  skipped_lesson_count: 0,
  material_count: 0,
  parsed_material_count: 0,
  mapped_material_count: 0,
  revision: 1,
  created_at: '',
  updated_at: '',
}

function lesson(id: string, title: string, isActive = true): LessonNode {
  return {
    id,
    curriculum_id: semester.curriculum_id,
    parent_id: null,
    node_type: 'lesson',
    title,
    sort_order: 1,
    duration_minutes: 45,
    source_kind: 'teacher',
    is_active: isActive,
    revision: 1,
    created_at: '',
    updated_at: '',
  }
}

function fakeRouteState(): TeachingPrepRouteState {
  return {
    currentView: computed(() => 'overview' as const),
    currentLessonId: computed(() => null),
    currentStep: computed(() => 1 as const),
    openOverview: vi.fn(async () => {}),
    openLibrary: vi.fn(async () => {}),
    openLesson: vi.fn(async () => {}),
    setStep: vi.fn(async () => {}),
  }
}

async function mountPage(options: { lessons?: LessonNode[]; withSemester?: boolean } = {}) {
  vi.spyOn(teachingPrepWorkbenchApi, 'lessonStatuses').mockResolvedValue([])
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(OverviewPage)
  const pinia = createPinia()
  app.use(pinia)
  const routeState = fakeRouteState()
  app.provide(teachingPrepRouteStateKey, routeState)
  const catalog = useTeachingPrepCatalogStore(pinia)
  if (options.withSemester !== false) {
    catalog.semesters = [semester]
    catalog.selectedCurriculumId = semester.curriculum_id
    catalog.selectedSemesterId = semester.id
  }
  catalog.lessonNodes = options.lessons ?? [lesson('1'.repeat(32), '课时 1')]
  app.mount(host)
  await nextTick()
  await nextTick()
  return { app, host, catalog, routeState }
}

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('OverviewPage', () => {
  it('renders the semester card and lesson rows', async () => {
    const { app, host } = await mountPage()

    expect(host.textContent).toContain('七年级数学 · 2026 第一学期')
    expect(host.querySelector('[aria-label="课时列表"]')).toBeTruthy()
    expect(host.textContent).toContain('课时 1')
    expect(teachingPrepWorkbenchApi.lessonStatuses).toHaveBeenCalledWith(semester.id)
    app.unmount()
  })

  it('creates a lesson through the store', async () => {
    const { app, host, catalog } = await mountPage()
    const createLesson = vi.spyOn(catalog, 'createLesson').mockResolvedValue(undefined)

    const buttons = [...host.querySelectorAll('button')]
    buttons.find(button => button.textContent?.trim() === '新建课时')?.click()
    await nextTick()
    const input = host.querySelector<HTMLInputElement>('[data-testid="new-lesson-title"]')
    expect(input).toBeTruthy()
    input!.value = '新课时'
    input!.dispatchEvent(new Event('input'))
    await nextTick()
    ;[...host.querySelectorAll('button')].find(button => button.textContent?.trim() === '保存课时')?.click()
    await vi.waitFor(() => expect(createLesson).toHaveBeenCalled())
    expect(createLesson.mock.calls[0]?.[0]).toMatchObject({ title: '新课时', node_type: 'lesson' })
    app.unmount()
  })

  it('deactivates a lesson after confirmation', async () => {
    const { app, host, catalog } = await mountPage()
    const updateLesson = vi.spyOn(catalog, 'updateLesson').mockResolvedValue(undefined)
    vi.stubGlobal('confirm', vi.fn(() => true))

    ;[...host.querySelectorAll('button')].find(button => button.textContent?.trim() === '停用')?.click()
    await vi.waitFor(() => expect(updateLesson).toHaveBeenCalled())
    expect(updateLesson.mock.calls[0]?.[1]).toEqual({ isActive: false })
    app.unmount()
  })

  it('changes manual progress through the store', async () => {
    const { app, host, catalog } = await mountPage()
    const setProgress = vi.spyOn(catalog, 'setSemesterLessonProgress').mockResolvedValue({} as never)

    const select = host.querySelector<HTMLSelectElement>('select[aria-label="修改授课状态"]')
    expect(select).toBeTruthy()
    select!.value = 'taught'
    select!.dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect(setProgress).toHaveBeenCalledWith('1'.repeat(32), 'taught'))
    app.unmount()
  })

  it('opens the lesson workbench via the route state', async () => {
    const { app, host, routeState } = await mountPage()

    ;[...host.querySelectorAll('button')].find(button => button.textContent?.trim() === '进入备课')?.click()
    await nextTick()
    expect(routeState.openLesson).toHaveBeenCalledWith('1'.repeat(32))
    app.unmount()
  })

  it('shows the empty state when no semester exists', async () => {
    const { app, host } = await mountPage({ withSemester: false, lessons: [] })

    expect(host.textContent).toContain('还没有本学期')
    expect(host.textContent).toContain('建立本学期')
    app.unmount()
  })
})
