import { createApp, nextTick, type App } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { GraphEdge, GraphEvidenceResponse, GraphNode } from '../api/graph'
import GraphNodeInspector from '../components/knowledge-graph/GraphNodeInspector.vue'
import GraphTextDirectory from '../components/knowledge-graph/GraphTextDirectory.vue'
import KnowledgeGraphCanvas from '../components/knowledge-graph/KnowledgeGraphCanvas.vue'

const standard = { release_id: 'current', content_hash: 'c'.repeat(64), taxonomy_revision: 2 }
const nodes: GraphNode[] = [{
  stable_key: 'kp_equation',
  display_name: '方程',
  definition: '含未知数的等式。',
  include_scope: '一元方程',
  exclude_scope: '不等式',
  curriculum_anchors: ['课程标准 7.2'],
  observable_evidence: '能够列出并求解方程',
  rationale: '课程核心内容',
  evidence_source_ids: ['standard:7.2'],
  mastery: { status: 'available', value: 0.62, evidence_count: 1, parameter_version: 'd'.repeat(64), reason: null },
  evidence: { student_count: 1, item_count: 1, deduction_count: 1, tag_context: {}, error_counts: {} },
  missing_reasons: [],
}, {
  stable_key: 'kp_equality',
  display_name: '等式性质',
  definition: '等式两边同加减乘除的性质。',
  include_scope: '等式变形',
  exclude_scope: '不等式变形',
  curriculum_anchors: ['课程标准 7.1'],
  observable_evidence: '能完成等价变形',
  rationale: '方程先修知识',
  evidence_source_ids: ['standard:7.1'],
  mastery: { status: 'missing', value: null, evidence_count: 0, parameter_version: null, reason: 'missing' },
  evidence: { student_count: 0, item_count: 0, deduction_count: 0, tag_context: {}, error_counts: {} },
  missing_reasons: ['no_evidence_in_scope'],
}]
const edges: GraphEdge[] = [{
  relation_key: 'e'.repeat(64),
  source_key: nodes[0]!.stable_key,
  target_key: nodes[1]!.stable_key,
  relation_type: 'prerequisite',
  rationale: '解方程前需要理解等式性质',
  basis_kind: 'mathematical_logic',
  strength: 'required',
  evidence_source_ids: ['standard:7.1'],
  source_locator: '课程标准 7.1',
}]
const evidence: GraphEvidenceResponse = {
  response_schema_version: 'knowledge-graph-evidence-current',
  response_version: 'b'.repeat(64),
  scope: { mode: 'class', student_ids: ['12'], class_id: '一班' },
  exam_scope: { mode: 'current', session_ids: [7], sessions: [{ session_id: 7, session_name: '合成考试' }] },
  coverage: { covered_items: 1, total_items: 1, missing_items: {} },
  current_standard: standard,
  stable_key: nodes[0]!.stable_key,
  display_name: nodes[0]!.display_name,
  items: [],
  total: 0,
  page: 1,
  page_size: 20,
  total_pages: 1,
}

class ResizeObserverStub { observe(): void {} disconnect(): void {} }
const mounted: App[] = []
async function mount(component: Parameters<typeof createApp>[0], props: Record<string, unknown>) {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(component, props)
  app.mount(host)
  mounted.push(app)
  await nextTick()
  return host
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1 })
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
})
afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('knowledge graph components', () => {
  it('renders relation direction and non-colour state shapes in the canvas', async () => {
    const chart = { setOption: vi.fn(), on: vi.fn(), off: vi.fn(), resize: vi.fn(), dispose: vi.fn(), dispatchAction: vi.fn() }
    const host = await mount(KnowledgeGraphCanvas, {
      nodes,
      edges,
      selectedKey: nodes[0]!.stable_key,
      scopeLabel: '合成考试 · 一班',
      coverage: evidence.coverage,
      chartFactory: () => chart,
    })
    expect(host.textContent).toContain('目标 → 先修')
    expect(host.textContent).toContain('◆')
    expect(chart.setOption).toHaveBeenCalledWith(expect.objectContaining({
      series: [expect.objectContaining({ links: [expect.objectContaining({ id: 'e'.repeat(64) })] })],
    }), true)
  })

  it('supports keyboard selection in the text directory', async () => {
    const selected = vi.fn()
    const host = await mount(GraphTextDirectory, { nodes, edges, selectedKey: null, onSelectNode: selected })
    const button = host.querySelector<HTMLButtonElement>('[data-testid="graph-directory-item"]')!
    button.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    expect(selected).toHaveBeenCalledWith(button.dataset.stableKey)
  })

  it('shows node scope, current standard and relation basis without version switching', async () => {
    const host = await mount(GraphNodeInspector, {
      node: nodes[0], nodes, edges, currentStandard: standard,
      evidence, evidenceState: 'empty', evidenceError: '',
    })
    expect(host.textContent).toContain('含未知数的等式')
    expect(host.textContent).toContain('课程标准 7.2')
    expect(host.textContent).toContain('数学逻辑')
    expect(host.textContent).toContain('当前标准')
    expect(host.textContent).toContain('当前掌握证据')
  })
})
