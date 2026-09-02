import { createPinia } from 'pinia'
import { createApp, defineComponent, h, provide } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { teachingPrepCatalogApi, type LessonNode } from '../api/catalog'
import { teachingPrepWorkbenchApi } from '../api/workbench'
import { useTeachingPrepCatalogStore } from '../stores/catalog'
import { teachingPrepRouteStateKey } from '../workbench/routeContext'
import { useTeachingPrepRouteState } from '../workbench/routeState'
import LessonPage from './LessonPage.vue'

const lessonId = '1'.repeat(32)

const lessonNode: LessonNode = {
  id: lessonId, curriculum_id: 'c'.repeat(32), parent_id: null,
  node_type: 'lesson', title: '第一课时', sort_order: 1, duration_minutes: 45,
  source_kind: 'teacher', is_active: true, revision: 1,
  created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
}

function mockApis(): void {
  vi.spyOn(teachingPrepCatalogApi, 'listMaterialLinks').mockResolvedValue([])
  vi.spyOn(teachingPrepCatalogApi, 'listResourcePacks').mockResolvedValue([])
  vi.spyOn(teachingPrepCatalogApi, 'getResourcePackStatus').mockResolvedValue({} as never)
  vi.spyOn(teachingPrepCatalogApi, 'listMaterials').mockResolvedValue([])
  vi.spyOn(teachingPrepCatalogApi, 'listMaterialParseJobs').mockResolvedValue([])
  vi.spyOn(teachingPrepWorkbenchApi, 'lessonStatuses').mockResolvedValue([])
  vi.spyOn(teachingPrepWorkbenchApi, 'questionBankSections').mockResolvedValue([])
  vi.spyOn(teachingPrepWorkbenchApi, 'listSlideAnimationRuns').mockResolvedValue({
    lesson_node_id: lessonId,
    items: [],
    billed_count: 0,
    billed_limit: 3,
    page_limit: 4,
  })
  vi.spyOn(teachingPrepWorkbenchApi, 'listPptxOutputs').mockResolvedValue([])
}

const Shell = defineComponent({
  setup() {
    const routeState = useTeachingPrepRouteState()
    provide(teachingPrepRouteStateKey, routeState)
    return () => h(LessonPage)
  },
})

async function mountPage(query: Record<string, string>) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/teaching-prep', component: Shell }],
  })
  await router.push({ path: '/teaching-prep', query })
  await router.isReady()
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(Shell)
  const pinia = createPinia()
  app.use(pinia)
  app.use(router)
  const catalog = useTeachingPrepCatalogStore(pinia)
  catalog.lessonNodes = [lessonNode]
  app.mount(host)
  await vi.waitFor(() => {
    expect(teachingPrepCatalogApi.listMaterialLinks).toHaveBeenCalledWith(lessonId)
  })
  await vi.waitFor(() => {
    expect(host.querySelectorAll('[aria-label="备课步骤"] .tp-rail__step')).toHaveLength(2)
  })
  return { app, host, router, catalog }
}

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('LessonPage', () => {
  it('renders the two-step rail and the inspector', async () => {
    mockApis()
    const { app, host } = await mountPage({ view: 'lesson', lesson: lessonId, step: '1' })

    const rail = host.querySelector('[aria-label="备课步骤"]')
    expect(rail?.querySelectorAll('.tp-rail__step')).toHaveLength(2)
    expect(host.textContent).toContain('① 课件与选题')
    expect(host.textContent).toContain('② 对照与导出')
    expect(host.querySelector('[aria-label="本步说明"]')).toBeTruthy()
    expect(host.textContent).toContain('第一课时')
    app.unmount()
  })

  it('switches the step through the route query', async () => {
    mockApis()
    const { app, host, router } = await mountPage({ view: 'lesson', lesson: lessonId, step: '1' })

    const steps = [...host.querySelectorAll<HTMLButtonElement>('.tp-rail__step')]
    steps[1]?.click()
    await vi.waitFor(() => {
      expect(router.currentRoute.value.query.step).toBe('2')
    })
    await vi.waitFor(() => {
      expect(host.textContent).toContain('还没有发送改编任务')
    })
    expect(router.currentRoute.value.query.lesson).toBe(lessonId)
    app.unmount()
  })

  it('returns to the overview from the rail when nothing is unsaved', async () => {
    mockApis()
    const { app, host, router } = await mountPage({ view: 'lesson', lesson: lessonId, step: '1' })

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '← 返回备课首页')?.click()
    await vi.waitFor(() => {
      expect(router.currentRoute.value.query.view).toBe('overview')
    })
    app.unmount()
  })
})
