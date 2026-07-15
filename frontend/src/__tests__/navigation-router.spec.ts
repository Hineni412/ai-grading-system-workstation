import { createMemoryHistory } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { navigationItems, reviewRouteDefinition, workbenchRouteDefinition } from '../navigation'
import { createAppRouter } from '../router'

describe('source-recalibrated navigation', () => {
  it('exposes only the truthful workbench and review destinations', () => {
    expect(navigationItems).toEqual([workbenchRouteDefinition, reviewRouteDefinition])
    expect(navigationItems.map(({ id, label, path }) => [id, label, path])).toEqual([
      ['workbench', '工作台', '/workbench'],
      ['grading', '评分复核', '/grading'],
    ])
  })

  it.each([workbenchRouteDefinition, reviewRouteDefinition])(
    'resolves $id from the shared metadata',
    (definition) => {
      const router = createAppRouter(createMemoryHistory())
      const resolved = router.resolve(definition.path)

      expect(resolved.name).toBe(definition.id)
      expect(resolved.meta).toMatchObject({
        title: definition.title,
        description: definition.description,
        breadcrumb: definition.breadcrumb,
      })
    },
  )

  it.each([
    ['/', '/workbench'],
    ['/workbench', '/workbench'],
    ['/grading', '/grading'],
    ['/design-system', '/design-system'],
    ['/missing/deep/path', '/missing/deep/path'],
  ])('resolves %s safely', async (target, expectedPath) => {
    const router = createAppRouter(createMemoryHistory())
    await router.push(target)
    await router.isReady()
    expect(router.currentRoute.value.fullPath).toBe(expectedPath)
    expect(router.currentRoute.value.meta.title).toBeTruthy()
    expect(router.currentRoute.value.meta.description).toBeTruthy()
    expect(router.currentRoute.value.meta.breadcrumb).toBeTruthy()
  })

  it.each(['/settings', '/students', '/analytics', '/question-bank'])(
    'does not present the former placeholder route %s as a business page',
    async (target) => {
      const router = createAppRouter(createMemoryHistory())
      await router.push(target)
      await router.isReady()

      expect(router.currentRoute.value.name).toBe('not-found')
    },
  )

  it('loads the review queue view for grading', async () => {
    const router = createAppRouter(createMemoryHistory())
    await router.push('/grading')
    await router.isReady()

    const matched = router.currentRoute.value.matched
    const component = matched[matched.length - 1]?.components?.default
    expect((component as { __name?: string } | undefined)?.__name).toBe('ReviewQueueView')
  })

  it('loads the workbench view for the workbench route', async () => {
    const router = createAppRouter(createMemoryHistory())
    await router.push('/workbench')
    await router.isReady()

    const matched = router.currentRoute.value.matched
    const component = matched[matched.length - 1]?.components?.default
    expect((component as { __name?: string } | undefined)?.__name).toBe('WorkbenchView')
  })
})
