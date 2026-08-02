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
  it('canonicalizes unknown and sensitive query values to today', async () => {
    const { router } = await mountAt(
      '/class-teacher?surface=students&panel=support&subject_id=secret&search=student',
    )

    await new Promise((resolve) => setTimeout(resolve, 0))
    await nextTick()

    expect(router.currentRoute.value.query).toEqual({ surface: 'today' })
  })

  it('keeps only whitelisted ordinary route state and supports history', async () => {
    const { router, routeState } = await mountAt('/class-teacher?surface=today')

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
})
