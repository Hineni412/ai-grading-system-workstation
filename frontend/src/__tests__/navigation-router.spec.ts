import { createMemoryHistory } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { navigationItems, settingsNavigationItem } from '../navigation'
import { createAppRouter } from '../router'

describe('P2-03 navigation', () => {
  it('keeps the approved seven-item order and one disabled future entry', () => {
    expect(navigationItems.map(({ label }) => label)).toEqual([
      '工作台', '阅卷', '考试', '学生', '分析', '题库与训练', '智能体与自动化',
    ])
    expect(navigationItems.filter(({ availability }) => availability === 'future')).toEqual([
      expect.objectContaining({ label: '智能体与自动化', path: undefined }),
    ])
    expect(settingsNavigationItem).toMatchObject({ label: '设置', path: '/settings' })
  })

  it('resolves every available navigation item from the shared workspace metadata', () => {
    const router = createAppRouter(createMemoryHistory())
    const availableItems = [...navigationItems, settingsNavigationItem].filter(
      (item) => item.availability === 'available',
    )

    for (const item of availableItems) {
      const resolved = router.resolve(item.path!)
      expect(resolved.name).toBe(item.id)
      expect(resolved.meta).toMatchObject({
        title: item.title,
        description: item.description,
        breadcrumb: item.breadcrumb,
      })
    }
  })

  it.each([
    ['/', '/workbench'],
    ['/grading', '/grading'],
    ['/settings', '/settings'],
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
})
