import { createPinia, setActivePinia } from 'pinia'
import { createApp, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { LessonNode } from '../../api/catalog'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import LessonTreeView from './LessonTreeView.vue'

function node(
  id: string,
  nodeType: LessonNode['node_type'],
  title: string,
  parentId: string | null,
  sortOrder: number,
): LessonNode {
  return {
    id, curriculum_id: 'c'.repeat(32), parent_id: parentId,
    node_type: nodeType, title, sort_order: sortOrder, duration_minutes: 45,
    source_kind: 'teacher', is_active: true, revision: 1,
    created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
  }
}

async function mountView(nodes: LessonNode[]) {
  const catalog = useTeachingPrepCatalogStore()
  catalog.lessonNodes = nodes
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(LessonTreeView)
  app.mount(host)
  await nextTick()
  return { app, host }
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('LessonTreeView', () => {
  it('groups active lessons under their chapters read-only', async () => {
    const chapter = node('a'.repeat(32), 'chapter', '第一章 勾股定理', null, 1)
    const section = node('b'.repeat(32), 'section', '1.1 探索勾股定理', chapter.id, 1)
    const lessonA = node('d'.repeat(32), 'lesson', '第1课时 认识勾股定理', section.id, 1)
    const lessonB = node('e'.repeat(32), 'lesson', '第2课时 验证勾股定理', section.id, 2)
    const { app, host } = await mountView([chapter, section, lessonA, lessonB])

    expect(host.textContent).toContain('本学期课时树')
    expect(host.textContent).toContain('2 个课时已生效')
    expect(host.textContent).toContain('第一章 勾股定理')
    expect(host.textContent).toContain('第1课时 认识勾股定理')
    expect(host.querySelector('button')).toBeNull()
    app.unmount()
  })

  it('lists flat lessons when there is no chapter layer', async () => {
    const { app, host } = await mountView([
      node('d'.repeat(32), 'lesson', '第1课时 认识勾股定理', null, 1),
    ])

    expect(host.textContent).toContain('1 个课时已生效')
    expect(host.textContent).toContain('第1课时 认识勾股定理')
    app.unmount()
  })

  it('points teachers back to the overview when the tree is empty', async () => {
    const { app, host } = await mountView([])

    expect(host.textContent).toContain('本学期还没有课时')
    expect(host.textContent).toContain('备课首页')
    app.unmount()
  })
})
