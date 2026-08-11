import { createApp, defineComponent, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it } from 'vitest'

import {
  useTeachingPrepRouteState,
  type TeachingPrepRouteState,
} from './routeState'

let captured: TeachingPrepRouteState | null = null

const Probe = defineComponent({
  setup() {
    captured = useTeachingPrepRouteState()
    return () => null
  },
})

async function mountAt(query: Record<string, string>) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/teaching-prep', name: 'teaching-prep', component: Probe }],
  })
  await router.push({ path: '/teaching-prep', query })
  await router.isReady()
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(Probe)
  app.use(router)
  app.mount(host)
  await nextTick()
  if (!captured) throw new Error('route state probe did not mount')
  return { app, host, router, state: captured }
}

afterEach(() => {
  captured = null
  document.body.innerHTML = ''
})

describe('teaching-prep route state', () => {
  it('defaults to overview step 1 without query params', async () => {
    const { app, state } = await mountAt({})
    expect(state.currentView.value).toBe('overview')
    expect(state.currentLessonId.value).toBeNull()
    expect(state.currentStep.value).toBe(1)
    app.unmount()
  })

  it('normalizes unknown view and step values', async () => {
    const { app, state } = await mountAt({ view: 'nope', step: '9' })
    expect(state.currentView.value).toBe('overview')
    expect(state.currentStep.value).toBe(1)
    app.unmount()
  })

  it('exposes lesson id only in lesson view', async () => {
    const first = await mountAt({ view: 'library', lesson: 'lesson-1' })
    expect(first.state.currentLessonId.value).toBeNull()
    first.app.unmount()

    const second = await mountAt({ view: 'lesson', lesson: 'lesson-1', step: '3' })
    expect(second.state.currentView.value).toBe('lesson')
    expect(second.state.currentLessonId.value).toBe('lesson-1')
    expect(second.state.currentStep.value).toBe(3)
    second.app.unmount()
  })

  it('openLesson writes view/lesson/step into the query', async () => {
    const { app, router, state } = await mountAt({})
    await state.openLesson('lesson-9', 2)
    expect(router.currentRoute.value.query).toEqual({
      view: 'lesson',
      lesson: 'lesson-9',
      step: '2',
    })
    app.unmount()
  })

  it('openOverview and openLibrary clear lesson and step', async () => {
    const { app, router, state } = await mountAt({
      view: 'lesson',
      lesson: 'lesson-1',
      step: '3',
    })
    await state.openLibrary()
    expect(router.currentRoute.value.query).toEqual({ view: 'library' })
    await state.openOverview()
    expect(router.currentRoute.value.query).toEqual({ view: 'overview' })
    app.unmount()
  })

  it('setStep only applies in lesson view', async () => {
    const { app, router, state } = await mountAt({ view: 'library' })
    await state.setStep(3)
    expect(router.currentRoute.value.query).toEqual({ view: 'library' })

    await state.openLesson('lesson-1', 1)
    await state.setStep(2)
    expect(router.currentRoute.value.query).toEqual({
      view: 'lesson',
      lesson: 'lesson-1',
      step: '2',
    })
    app.unmount()
  })
})
