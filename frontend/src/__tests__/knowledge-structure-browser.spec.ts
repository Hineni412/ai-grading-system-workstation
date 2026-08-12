import { createApp, h, nextTick } from 'vue'
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
  const app = createApp({
    setup() {
      return () => h(KnowledgeStructureBrowser, {
        nodes: NODES,
        edges: [],
        selectedKey,
        onSelect: (key: string) => selected.push(key),
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
  it('groups nodes into an expandable chapter-section tree on the left', () => {
    const { host } = mountBrowser()
    const chapterButtons = [...host.querySelectorAll('.structure-browser__chapter-button')]
    expect(chapterButtons.map((node) => node.querySelector('span')?.textContent))
      .toEqual(['第二章 相交线与平行线', '第一章 整式的乘除'])
    // 首章默认展开，显示小节；次章未展开，不显示其小节。
    const sectionButtons = [...host.querySelectorAll('.structure-browser__section-list button')]
    expect(sectionButtons.map((node) => node.querySelector('span')?.textContent))
      .toEqual(['章级知识点', '1 两条直线的位置关系', '3 平行线的性质'])
    expect(host.textContent).not.toContain('同底数幂相乘')
  })

  it('expands a collapsed chapter from its toggle', async () => {
    const { host } = mountBrowser()
    const toggles = [...host.querySelectorAll('.structure-browser__chapter-toggle')]
    expect(toggles).toHaveLength(2)
    await click(toggles[1]!)
    const sectionButtons = [...host.querySelectorAll('.structure-browser__section-list button')]
    expect(sectionButtons.map((node) => node.querySelector('span')?.textContent))
      .toContain('1 同底数幂的乘法')
  })

  it('shows section mastery cards for the active chapter and drills into knowledge points', async () => {
    const { host } = mountBrowser()
    const cards = [...host.querySelectorAll('.structure-browser__section-card')]
    expect(cards).toHaveLength(3)
    expect(cards[0]!.textContent).toContain('章级知识点')
    expect(cards[0]!.textContent).toContain('51%')
    expect(cards[1]!.textContent).toContain('1 两条直线的位置关系')
    expect(cards[1]!.textContent).toContain('46%')
    expect(cards[2]!.textContent).toContain('68%')

    await click(cards[1]!)
    expect(host.querySelector('.structure-browser__back')?.textContent).toContain('返回')
    const points = [...host.querySelectorAll('.structure-browser__point')]
    expect(points.map((node) => node.querySelector('span')?.textContent))
      .toEqual(['1 两条直线的位置关系', '对顶角相等'])
  })

  it('selects a section from the left tree and emits select for a clicked point', async () => {
    const { host, selected } = mountBrowser()
    const sectionButtons = [...host.querySelectorAll('.structure-browser__section-list button')]
    await click(sectionButtons[2]!)
    const points = [...host.querySelectorAll('.structure-browser__point')]
    expect(points.map((node) => node.querySelector('span')?.textContent))
      .toEqual(['两直线平行同旁内角互补'])

    await click(points[0]!)
    expect(selected).toEqual(['kp_leaf2_3a'])
  })

  it('keeps the back action returning to the chapter section list', async () => {
    const { host } = mountBrowser()
    const sectionButtons = [...host.querySelectorAll('.structure-browser__section-list button')]
    await click(sectionButtons[1]!)
    expect(host.querySelectorAll('.structure-browser__point')).toHaveLength(2)
    await click(host.querySelector('.structure-browser__back')!)
    expect(host.querySelectorAll('.structure-browser__section-card')).toHaveLength(3)
  })
})
