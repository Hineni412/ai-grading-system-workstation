import { createMemoryHistory } from 'vue-router'
import { describe, expect, it } from 'vitest'

import {
  navigationItems,
  resultsRouteDefinition,
  reviewRouteDefinition,
  sessionRouteDefinition,
  studentsRouteDefinition,
  questionBankRouteDefinition,
  questionAssemblyRouteDefinition,
  trainingRouteDefinition,
  templateRegionRouteDefinition,
  gradingRunRouteDefinition,
  knowledgeGraphRouteDefinition,
  modelProfilesRouteDefinition,
  settingsRouteDefinition,
  workbenchRouteDefinition,
} from '../navigation'
import { createAppRouter } from '../router'

const topLevelDefinitions = [
  workbenchRouteDefinition,
  sessionRouteDefinition,
  reviewRouteDefinition,
  resultsRouteDefinition,
  questionBankRouteDefinition,
  questionAssemblyRouteDefinition,
  knowledgeGraphRouteDefinition,
  trainingRouteDefinition,
  studentsRouteDefinition,
  modelProfilesRouteDefinition,
  settingsRouteDefinition,
] as const

describe('source-recalibrated navigation', () => {
  it('exposes the truthful top-level destinations', () => {
    expect(navigationItems).toEqual(topLevelDefinitions)
    expect(navigationItems.map(({ id, path }) => [id, path])).toEqual([
      ['workbench', '/workbench'],
      ['sessions', '/sessions'],
      ['grading', '/grading'],
      ['results', '/results'],
      ['question-bank', '/question-bank'],
      ['question-assembly', '/question-assembly'],
      ['knowledge-graph', '/knowledge-graph'],
      ['training', '/training'],
      ['students', '/students'],
      ['model-profiles', '/model-profiles'],
      ['settings', '/settings'],
    ])
  })

  it.each(topLevelDefinitions)(
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
    ['/sessions', '/sessions'],
    ['/students', '/students'],
    ['/question-bank', '/question-bank'],
    ['/question-assembly', '/question-assembly'],
    ['/training', '/training'],
    ['/sessions/7/regions', '/sessions/7/regions'],
    ['/sessions/7/grading-run', '/sessions/7/grading-run'],
    ['/knowledge-graph?session=7&class=七年级一班', '/knowledge-graph?session=7&class=七年级一班'],
    ['/files', '/results?tab=exports'],
    ['/results', '/results'],
    ['/grading', '/grading'],
    ['/model-profiles', '/model-profiles'],
    ['/settings', '/settings'],
    ['/design-system', '/design-system'],
    ['/missing/deep/path', '/missing/deep/path'],
  ])(
    'resolves %s safely',
    async (target, expectedPath) => {
      const router = createAppRouter(createMemoryHistory())
      await router.push(target)
      await router.isReady()
      expect(router.currentRoute.value.fullPath).toBe(expectedPath)
      expect(router.currentRoute.value.meta.title).toBeTruthy()
      expect(router.currentRoute.value.meta.description).toBeTruthy()
      expect(router.currentRoute.value.meta.breadcrumb).toBeTruthy()
    },
    10_000,
  )

  it.each(['/analytics'])(
    'does not present the former placeholder route %s as a business page',
    async (target) => {
      const router = createAppRouter(createMemoryHistory())
      await router.push(target)
      await router.isReady()

      expect(router.currentRoute.value.name).toBe('not-found')
    },
  )

  it.each([
    ['/grading', 'grading', 'ReviewQueueView'],
    ['/workbench', 'workbench', 'WorkbenchView'],
    ['/sessions', 'sessions', 'SessionConfigView'],
    ['/students', 'students', 'StudentsView'],
    ['/question-bank', 'question-bank', 'QuestionBankView'],
    ['/question-assembly', 'question-assembly', 'QuestionAssemblyView'],
    ['/training', 'training', 'TrainingRecommendationsView'],
    ['/knowledge-graph', 'knowledge-graph', 'KnowledgeGraphView'],
    ['/results', 'results', 'ResultsCenterView'],
    ['/model-profiles', 'model-profiles', 'ModelProfilesView'],
    ['/settings', 'settings', 'SettingsOpsView'],
  ])(
    'loads %s as %s',
    async (path, routeName, componentName) => {
      const router = createAppRouter(createMemoryHistory())
      await router.push(path)
      await router.isReady()

      expect(router.currentRoute.value.name).toBe(routeName)
      const matched = router.currentRoute.value.matched
      const component = matched[matched.length - 1]?.components?.default
      expect((component as { __name?: string } | undefined)?.__name).toBe(componentName)
    },
  )

  it('loads the dedicated template region workspace without adding a top-level destination', async () => {
    const router = createAppRouter(createMemoryHistory())
    await router.push('/sessions/7/regions')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe(templateRegionRouteDefinition.id)
    expect(navigationItems.map((item) => String(item.path))).not.toContain('/sessions/7/regions')
    const matched = router.currentRoute.value.matched
    const component = matched[matched.length - 1]?.components?.default
    expect((component as { __name?: string } | undefined)?.__name).toBe('TemplateRegionView')
  })

  it('loads the dedicated grading run workspace without replacing scoring review', async () => {
    const router = createAppRouter(createMemoryHistory())
    await router.push('/sessions/7/grading-run')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe(gradingRunRouteDefinition.id)
    expect(navigationItems.map((item) => String(item.path))).not.toContain('/sessions/7/grading-run')
    const matched = router.currentRoute.value.matched
    const component = matched[matched.length - 1]?.components?.default
    expect((component as { __name?: string } | undefined)?.__name).toBe('ScanGradingView')
  })
})
