import { createApp, h, nextTick, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it } from 'vitest'

import type { GraphNode } from '../api/graph'
import KnowledgeStructureBrowser from '../components/knowledge-training/KnowledgeStructureBrowser.vue'

const apps: Array<ReturnType<typeof createApp>> = []

function makeNode(
  stableKey: string,
  displayName: string,
  mastery: { value: number | null; evidenceCount: number },
): GraphNode {
  return {
    stable_key: stableKey,
    display_name: displayName,
    definition: '',
    include_scope: '',
    exclude_scope: '',
    curriculum_anchors: [],
    observable_evidence: '',
    rationale: '',
    evidence_source_ids: [],
    mastery: {
      status: mastery.value === null ? 'missing' : 'available',
      value: mastery.value,
      evidence_count: mastery.evidenceCount,
      parameter_version: null,
      reason: null,
    },
    evidence: {
      student_count: 1,
      item_count: mastery.evidenceCount,
      deduction_count: 0,
      tag_context: {},
      error_counts: {},
    },
    missing_reasons: [],
  }
}

const NODES: GraphNode[] = [
  makeNode('kp_chapter2', '七年级下册｜第二章 相交线与平行线', { value: 0.51, evidenceCount: 16 }),
  makeNode('kp_section2_1', '七年级下册｜第二章 相交线与平行线｜1 两条直线的位置关系', { value: 0.46, evidenceCount: 4 }),
  makeNode('kp_leaf2_1a', '七年级下册｜第二章 相交线与平行线｜1 两条直线的位置关系｜对顶角相等', { value: 0.46, evidenceCount: 4 }),
  makeNode('kp_leaf2_3a', '七年级下册｜第二章 相交线与平行线｜3 平行线的性质｜两直线平行同旁内角互补', { value: 0.68, evidenceCount: 4 }),
  makeNode('kp_leaf1_1a', '七年级下册｜第一章 整式的乘除｜1 同底数幂的乘法｜同底数幂相乘', { value: null, evidenceCount: 0 }),
]

function mountBrowser(selectedKey = '') {
  const host = document.createElement('div')
  document.body.append(host)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', name: 'training', component: { template: '<div />' } }],
  })
  const selected: string[] = []
  const activeKey = ref(selectedKey)
  const app = createApp({
    setup() {
      return () => h(KnowledgeStructureBrowser, {
        nodes: NODES,
        edges: [],
        selectedKey: activeKey.value,
        onSelect: (key: string) => { selected.push(key); activeKey.value = key },
      })
    },
  })
  app.use(router)
  app.mount(host)
  apps.push(app)
  return { host, selected }
}

async function click(element: Element): Promise<void> {
  element.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await nextTick()
}

afterEach(() => {
  for (const app of apps.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('knowledge structure browser', () => {
  it('uses chapter and section as navigation while only leaf evidence colors the heatmap', async () => {
    const { host } = mountBrowser()
    const chapterButtons = [...host.querySelectorAll('.structure-browser__chapter-button')]
    expect(chapterButtons.map(node => node.querySelector('span')?.textContent))
      .toEqual(['第一章 整式的乘除', '第二章 相交线与平行线'])
    expect(host.querySelector('.structure-browser__point')?.textContent).toContain('证据不足')
    expect(host.querySelector('.structure-browser__point')?.classList.contains('is-empty')).toBe(true)
    await click(chapterButtons[1]!)
    const points = [...host.querySelectorAll('.structure-browser__point')]
    expect(points.map(node => node.querySelector('span')?.textContent)).toEqual(['对顶角相等', '两直线平行同旁内角互补'])
    expect(host.querySelectorAll('.structure-browser__heatmap-section')).toHaveLength(2)
    expect(host.textContent).not.toContain('51%')
    expect(points[0]!.textContent).toContain('46%')
    expect(points[1]!.textContent).toContain('68%')
  })

  it('selects a point without hiding the other sections and updates details when switching chapter', async () => {
    const { host, selected } = mountBrowser()
    const chapters = [...host.querySelectorAll('.structure-browser__chapter-button')]
    await click(chapters[1]!)
    await click(host.querySelector('.structure-browser__point')!)
    expect(selected).toEqual(['kp_leaf2_1a'])
    expect(host.querySelectorAll('.structure-browser__point')).toHaveLength(2)
    await click(chapters[0]!)
    expect(host.querySelector('.structure-browser__detail h2')?.textContent).toContain('同底数幂相乘')
    expect(host.querySelector('.structure-browser__score')?.textContent).toContain('证据不足')
    expect(host.querySelector('.structure-browser__score')?.classList.contains('is-empty')).toBe(true)
  })

  it('drills into a section and returns to the whole chapter', async () => {
    const { host } = mountBrowser()
    await click([...host.querySelectorAll('.structure-browser__chapter-button')][1]!)
    const section = [...host.querySelectorAll('.structure-browser__section-list button')]
      .find(item => item.textContent?.includes('3 平行线的性质'))!
    await click(section)
    expect(host.querySelectorAll('.structure-browser__point')).toHaveLength(1)
    expect(host.querySelector('.structure-browser__point')?.textContent).toContain('两直线平行同旁内角互补')
    await click(host.querySelector('.structure-browser__back')!)
    expect(host.querySelectorAll('.structure-browser__point')).toHaveLength(2)
  })
})
