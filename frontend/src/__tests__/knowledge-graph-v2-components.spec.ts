import { createApp, nextTick, type App } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  GraphV2Edge,
  GraphV2EvidenceResponse,
  GraphV2Node,
} from '../api/graph-v2'
import GraphV2NodeInspector from '../components/knowledge-graph/GraphV2NodeInspector.vue'
import GraphV2TextDirectory from '../components/knowledge-graph/GraphV2TextDirectory.vue'
import KnowledgeGraphV2Canvas from '../components/knowledge-graph/KnowledgeGraphV2Canvas.vue'

const nodes: GraphV2Node[] = [
  {
    stable_key: 'kp_alg_linear_equation',
    display_name: '一元一次方程',
    identity_revision: 2,
    mastery_v1: { status: 'available', value: 0.6, evidence_count: 1, reason: null },
    mastery_v2: {
      status: 'unavailable', value: null, evidence_count: 0,
      reason: 'mastery_v2_not_enabled',
    },
    evidence: {
      student_count: 1, item_count: 1, deduction_count: 1,
      tag_context: { method: ['移项'] },
      error_counts: { primary: { 符号错误: 1 }, secondary: {} },
    },
    missing_reasons: [],
  },
  {
    stable_key: 'kp_alg_equation_properties',
    display_name: '等式性质',
    identity_revision: 1,
    mastery_v1: { status: 'missing', value: null, evidence_count: 0, reason: null },
    mastery_v2: {
      status: 'unavailable', value: null, evidence_count: 0,
      reason: 'mastery_v2_not_enabled',
    },
    evidence: {
      student_count: 0, item_count: 0, deduction_count: 0,
      tag_context: {}, error_counts: { primary: {}, secondary: {} },
    },
    missing_reasons: ['no_evidence_in_scope'],
  },
]
const edges: GraphV2Edge[] = [{
  relation_id: 'rel-confirmed',
  source_key: nodes[0]!.stable_key,
  target_key: nodes[1]!.stable_key,
  relation_type: 'prerequisite',
  rationale: '解方程前需要理解等式性质',
  revision: 2,
}]
const evidence: GraphV2EvidenceResponse = {
  response_schema_version: 'knowledge-graph-evidence-v2',
  response_version: 'b'.repeat(64),
  scope: { mode: 'student', student_ids: ['12'], class_id: null },
  exam_scope: {
    mode: 'current', session_ids: [14],
    sessions: [{ session_id: 14, session_name: '合成考试' }],
  },
  coverage: { covered_items: 1, total_items: 1, missing_items: {} },
  stable_key: nodes[0]!.stable_key,
  display_name: nodes[0]!.display_name,
  items: [{
    student_id: 12,
    student_code: 'S12',
    student_name: '合成学生',
    class_id: '八年级1班',
    knowledge_key: 'knowledge_point:一元一次方程',
    stable_key: nodes[0]!.stable_key,
    knowledge_label: nodes[0]!.display_name,
    session_id: 14,
    session_name: '合成考试',
    question_id: 'Q1',
    bank_question_id: 1,
    score_awarded: 6,
    full_score: 10,
    score_rate: 0.6,
    tag_context: {},
    actionable_reasons: ['符号错误'],
    error_counts: { primary: { 符号错误: 1 }, secondary: {} },
  }],
  total: 1,
  page: 1,
  page_size: 20,
  total_pages: 1,
}

class ResizeObserverStub {
  observe(): void {}
  disconnect(): void {}
}

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
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    callback(0)
    return 1
  })
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
})
afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('knowledge graph v2 components', () => {
  it('renders confirmed relation direction and non-colour state shapes in the chart', async () => {
    const chart = {
      setOption: vi.fn(), on: vi.fn(), off: vi.fn(), resize: vi.fn(),
      dispose: vi.fn(), dispatchAction: vi.fn(),
    }
    const host = await mount(KnowledgeGraphV2Canvas, {
      nodes,
      edges,
      selectedKey: nodes[0]!.stable_key,
      scopeLabel: '合成考试 · 1 名学生',
      coverage: evidence.coverage,
      chartFactory: () => chart,
    })
    expect(host.textContent).toContain('目标 → 先修')
    expect(host.textContent).toContain('◆')
    expect(host.textContent).toContain('当前无证据')
    expect(chart.setOption).toHaveBeenCalledWith(expect.objectContaining({
      series: [expect.objectContaining({
        links: [expect.objectContaining({
          id: 'rel-confirmed',
          relationType: 'prerequisite',
          symbol: ['none', 'arrow'],
        })],
      })],
    }), true)
  })

  it('supports keyboard selection in the paginated text fallback', async () => {
    const selected = vi.fn()
    const host = await mount(GraphV2TextDirectory, {
      nodes,
      edges,
      selectedKey: null,
      onSelectNode: selected,
    })
    const button = host.querySelector<HTMLButtonElement>('[data-testid="graph-v2-directory-item"]')!
    expect(button.getAttribute('aria-label')).toContain('已确认关系')
    button.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    expect(selected).toHaveBeenCalled()
  })

  it('shows relation rationale, missing explanations, source and sample count', async () => {
    const host = await mount(GraphV2NodeInspector, {
      node: nodes[0],
      nodes,
      edges,
      evidence,
      evidenceState: 'ready',
      evidenceError: '',
    })
    expect(host.textContent).toContain('1 条')
    expect(host.textContent).toContain('解方程前需要理解等式性质')
    expect(host.textContent).toContain('掌握度 v2 尚未启用')
    expect(host.textContent).toContain('来源：合成考试 · Q1')
    expect(host.textContent).toContain('当前接口未提供单条证据时间')
  })
})
