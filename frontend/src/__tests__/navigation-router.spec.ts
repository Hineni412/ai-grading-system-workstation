import { createMemoryHistory } from 'vue-router'
import { describe, expect, it } from 'vitest'

import {
  navigationItems,
  filesRouteDefinition,
  reviewRouteDefinition,
  sessionRouteDefinition,
  studentsRouteDefinition,
  questionBankRouteDefinition,
  templateRegionRouteDefinition,
  gradingRunRouteDefinition,
  knowledgeGraphRouteDefinition,
  workbenchRouteDefinition,
} from '../navigation'
import { createAppRouter } from '../router'

describe('source-recalibrated navigation', () => {
  it('exposes the truthful workbench, configuration, knowledge graph, files and review destinations', () => {
    expect(navigationItems).toEqual([
      workbenchRouteDefinition,
      sessionRouteDefinition,
      studentsRouteDefinition,
      questionBankRouteDefinition,
      knowledgeGraphRouteDefinition,
      filesRouteDefinition,
      reviewRouteDefinition,
    ])
    expect(navigationItems.map(({ id, label, path }) => [id, label, path])).toEqual([
      ['workbench', '工作台', '/workbench'],
      ['sessions', '考试配置', '/sessions'],
      ['students', '学生名单', '/students'],
      ['question-bank', '题库管理', '/question-bank'],
      ['knowledge-graph', '知识图谱', '/knowledge-graph'],
      ['files', '文件中心', '/files'],
      ['grading', '评分复核', '/grading'],
    ])
  })

  it.each([
    workbenchRouteDefinition,
    sessionRouteDefinition,
    studentsRouteDefinition,
    questionBankRouteDefinition,
    knowledgeGraphRouteDefinition,
    filesRouteDefinition,
    reviewRouteDefinition,
  ])(
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
    ['/sessions/7/regions', '/sessions/7/regions'],
    ['/sessions/7/grading-run', '/sessions/7/grading-run'],
    ['/knowledge-graph?session=7&class=七年级一班', '/knowledge-graph?session=7&class=七年级一班'],
    ['/files', '/files'],
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

  it.each(['/settings', '/analytics'])(
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

  it('loads the configuration workspace for sessions', async () => {
    const router = createAppRouter(createMemoryHistory())
    await router.push('/sessions')
    await router.isReady()

    const matched = router.currentRoute.value.matched
    const component = matched[matched.length - 1]?.components?.default
    expect((component as { __name?: string } | undefined)?.__name).toBe('SessionConfigView')
  })

  it('loads the student roster workspace from the top-level destination', async () => {
    const router = createAppRouter(createMemoryHistory())
    await router.push('/students')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('students')
    const matched = router.currentRoute.value.matched
    const component = matched[matched.length - 1]?.components?.default
    expect((component as { __name?: string } | undefined)?.__name).toBe('StudentsView')
  })

  it('loads the question bank workspace from the top-level destination', async () => {
    const router = createAppRouter(createMemoryHistory())
    await router.push('/question-bank')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('question-bank')
    const matched = router.currentRoute.value.matched
    const component = matched[matched.length - 1]?.components?.default
    expect((component as { __name?: string } | undefined)?.__name).toBe('QuestionBankView')
  })

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

  it('loads the knowledge graph view for its route', async () => {
    const router = createAppRouter(createMemoryHistory())
    await router.push('/knowledge-graph?session=7&class=七年级一班')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('knowledge-graph')
    const matched = router.currentRoute.value.matched
    const component = matched[matched.length - 1]?.components?.default
    expect((component as { __name?: string } | undefined)?.__name).toBe('KnowledgeGraphView')
  })

  it('loads the file center view for controlled downloads', async () => {
    const router = createAppRouter(createMemoryHistory())
    await router.push('/files')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('files')
    const matched = router.currentRoute.value.matched
    const component = matched[matched.length - 1]?.components?.default
    expect((component as { __name?: string } | undefined)?.__name).toBe('FileCenterView')
  })
})
