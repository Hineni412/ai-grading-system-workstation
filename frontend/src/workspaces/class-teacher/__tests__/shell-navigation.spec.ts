import { createApp, defineComponent, h, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it } from 'vitest'

import { useClassTeacherRouteState } from '../shell/useClassTeacherRouteState'

const mounted: Array<ReturnType<typeof createApp>> = []

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

async function mountAt(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/class-teacher', component: { render: () => null } }],
  })
  await router.push(path)
  const observed: ReturnType<typeof useClassTeacherRouteState>[] = []
  const component = defineComponent({
    setup() {
      const state = useClassTeacherRouteState()
      observed.push(state)
      return () => h('div', state.state.value.surface)
    },
  })
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(component)
  app.use(router)
  app.mount(host)
  mounted.push(app)
  await nextTick()
  return { router, routeState: observed[0]! }
}

describe('class teacher shell navigation', () => {
  it('canonicalizes unknown and body-like query values to the home desk', async () => {
    const { router } = await mountAt(
      '/class-teacher?surface=students&panel=support&subject_id=secret&search=student',
    )

    await new Promise((resolve) => setTimeout(resolve, 0))
    await nextTick()

    expect(router.currentRoute.value.query).toEqual({ surface: 'home' })
  })

  it('keeps only whitelisted ordinary route state and supports history', async () => {
    const { router, routeState } = await mountAt('/class-teacher?surface=home')

    await routeState.navigate({ surface: 'calendar', range: 'week', week: '2026-08-03' })
    await routeState.navigate({ surface: 'students', panel: 'academic' })

    expect(router.currentRoute.value.query).toEqual({ surface: 'students', panel: 'academic' })
    router.back()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(router.currentRoute.value.query).toEqual({
      surface: 'calendar',
      range: 'week',
      week: '2026-08-03',
    })
  })

  it('rejects the removed legacy draft route instead of restoring old body workflows', async () => {
    const { router } = await mountAt(`/class-teacher?surface=affairs&draft=${'a'.repeat(32)}`)
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(router.currentRoute.value.query).toEqual({ surface: 'home' })
  })

  it('keeps only opaque conversation and handoff references', async () => {
    const conversation = 'conversation-1234'
    const turn = 'turn-12345678'
    const workItem = 'work-item-1234'
    const handoff = 'handoff-12345'
    const { router } = await mountAt(`/class-teacher?surface=home&conversation=${conversation}&turn=${turn}&work_item=${workItem}&handoff=${handoff}`)

    expect(router.currentRoute.value.query).toEqual({
      surface: 'home', conversation, turn, work_item: workItem, handoff,
    })
    expect(String(router.currentRoute.value.fullPath)).not.toContain('学生正文')
  })

  it('returns from the shared AI task drawer to the opaque conversation', async () => {
    const { router } = await mountAt(
      '/class-teacher?destination=class_teacher.home&source_task_id=task-12345678&source_ref=conversation-1234',
    )
    await new Promise((resolve) => setTimeout(resolve, 0))
    await nextTick()

    expect(router.currentRoute.value.query).toEqual({
      surface: 'home',
      conversation: 'conversation-1234',
    })
  })

  it('returns a shared draft revision task to its opaque handoff', async () => {
    const { router } = await mountAt(
      '/class-teacher?destination=class_teacher.plan.calendar&source_task_id=task-revision-01&source_ref=handoff-12345',
    )
    await new Promise((resolve) => setTimeout(resolve, 0))
    await nextTick()

    expect(router.currentRoute.value.query).toEqual({
      surface: 'home',
      handoff: 'handoff-12345',
    })
  })

  it('keeps an opaque affair reference on the affairs surface and writes it back on navigate', async () => {
    const affair = 'affair-1234567'
    const { router, routeState } = await mountAt(`/class-teacher?surface=affairs&affair=${affair}`)

    expect(router.currentRoute.value.query).toEqual({ surface: 'affairs', affair })

    await routeState.navigate({ surface: 'affairs', affairId: null })
    expect(router.currentRoute.value.query).toEqual({ surface: 'affairs' })
    await routeState.navigate({ surface: 'affairs', affairId: affair })
    expect(router.currentRoute.value.query).toEqual({ surface: 'affairs', affair })
  })

  it('drops an affair reference outside the affairs surface', async () => {
    const { router } = await mountAt(`/class-teacher?surface=calendar&range=week&affair=${'a'.repeat(16)}`)
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(router.currentRoute.value.query).toEqual({ surface: 'home' })
  })
})
