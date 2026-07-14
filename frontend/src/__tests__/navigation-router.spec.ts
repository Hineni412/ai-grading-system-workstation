import { createMemoryHistory } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { navigationItems, reviewRouteDefinition } from '../navigation'
import { createAppRouter } from '../router'

describe('source-recalibrated navigation', () => {
  it('exposes only the truthful review destination', () => {
    expect(navigationItems).toEqual([reviewRouteDefinition])
    expect(navigationItems.map(({ id, label, path }) => [id, label, path])).toEqual([
      ['grading', '评分复核', '/grading'],
    ])
  })

  it('resolves the review route from the shared metadata', () => {
    const router = createAppRouter(createMemoryHistory())
    const resolved = router.resolve(reviewRouteDefinition.path)

    expect(resolved.name).toBe(reviewRouteDefinition.id)
    expect(resolved.meta).toMatchObject({
      title: reviewRouteDefinition.title,
      description: reviewRouteDefinition.description,
      breadcrumb: reviewRouteDefinition.breadcrumb,
    })
  })

  it.each([
    ['/', '/grading'],
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

  it.each(['/workbench', '/settings', '/students', '/analytics', '/question-bank'])(
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
})
