import { createMemoryHistory } from 'vue-router'
import { describe, expect, it } from 'vitest'

import {
  knowledgeGraphRouteDefinition,
  navigationItems,
  reviewRouteDefinition,
  workbenchRouteDefinition,
} from '../navigation'
import { createAppRouter } from '../router'

describe('source-recalibrated navigation', () => {
  it('exposes the truthful workbench, knowledge graph and review destinations', () => {
    expect(navigationItems).toEqual([
      workbenchRouteDefinition,
      knowledgeGraphRouteDefinition,
      reviewRouteDefinition,
    ])
    expect(navigationItems.map(({ id, label, path }) => [id, label, path])).toEqual([
      ['workbench', '工作台', '/workbench'],
      ['knowledge-graph', '知识图谱', '/knowledge-graph'],
      ['grading', '评分复核', '/grading'],
    ])
  })

  it.each([workbenchRouteDefinition, knowledgeGraphRouteDefinition, reviewRouteDefinition])(
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
    ['/knowledge-graph?session=7&class=七年级一班', '/knowledge-graph?session=7&class=七年级一班'],
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

  it('loads the knowledge graph view for its route', async () => {
    const router = createAppRouter(createMemoryHistory())
    await router.push('/knowledge-graph?session=7&class=七年级一班')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('knowledge-graph')
    const matched = router.currentRoute.value.matched
    const component = matched[matched.length - 1]?.components?.default
    expect((component as { __name?: string } | undefined)?.__name).toBe('KnowledgeGraphView')
  })
})
