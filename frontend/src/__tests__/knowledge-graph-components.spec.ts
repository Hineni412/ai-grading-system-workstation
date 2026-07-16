import { createApp, defineComponent, h, nextTick, reactive, type App } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  GraphEvidenceResponse,
  GraphNode,
  GraphRow,
  GraphRowsResponse,
} from '../api/graph'
import GraphNodeInspector from '../components/knowledge-graph/GraphNodeInspector.vue'
import GraphTextDirectory from '../components/knowledge-graph/GraphTextDirectory.vue'
import KnowledgeGraphCanvas from '../components/knowledge-graph/KnowledgeGraphCanvas.vue'

const nodes: GraphNode[] = [
  {
    knowledge_key: 'knowledge_point:三角形全等',
    knowledge_label: '三角形全等',
    student_count: 2,
    item_count: 4,
    deduction_count: 3,
    average_mastery: 0.62,
    tag_context: { prerequisite: ['角的关系'] },
    error_counts: { primary: { 步骤不完整: 2 }, secondary: {} },
  },
  {
    knowledge_key: 'knowledge_point:一次函数',
    knowledge_label: '一次函数',
    student_count: 1,
    item_count: 2,
    deduction_count: 0,
    average_mastery: 0.92,
    tag_context: {},
    error_counts: { primary: {}, secondary: {} },
  },
]

const rows: GraphRow[] = [{
  student_id: 12,
  student_code: 'S012',
  student_name: '匿名学生',
  knowledge_key: nodes[0]!.knowledge_key,
  knowledge_label: nodes[0]!.knowledge_label,
  weighted_score_rate: 62,
  deduction_count: 3,
  item_count: 4,
  sample_reasons: '步骤不完整',
  source_question_refs: [],
  tag_context: {},
  error_counts: { primary: { 步骤不完整: 2 }, secondary: {} },
}]

const graph: GraphRowsResponse = {
  scope: { mode: 'class', student_ids: ['12'], class_id: '七年级一班' },
  exam_scope: {
    mode: 'current',
    session_ids: [7],
    sessions: [{ session_id: 7, session_name: '匿名考试' }],
  },
  rows,
  nodes,
  edges: [],
  coverage: { covered_items: 4, total_items: 5, missing_items: { Q5: '未标注' } },
  warnings: ['部分题目没有知识标签'],
  diagnosis_identity: 'question_tag',
}

const evidence: GraphEvidenceResponse = {
  scope: graph.scope,
  exam_scope: graph.exam_scope,
  knowledge_key: nodes[0]!.knowledge_key,
  knowledge_label: nodes[0]!.knowledge_label,
  items: [{
    student_id: 12,
    student_code: 'S012',
    student_name: '匿名学生',
    class_id: '七年级一班',
    knowledge_key: nodes[0]!.knowledge_key,
    knowledge_label: nodes[0]!.knowledge_label,
    session_id: 7,
    session_name: '匿名考试',
    question_id: 'Q1',
    bank_question_id: 101,
    score_awarded: 3,
    full_score: 5,
    score_rate: 0.6,
    tag_context: {},
    actionable_reasons: ['步骤不完整'],
    error_counts: { primary: { 步骤不完整: 1 }, secondary: {} },
  }],
  total: 21,
  page: 1,
  page_size: 20,
  total_pages: 2,
  coverage: graph.coverage,
  warnings: [],
  diagnosis_identity: 'question_tag',
}

class ResizeObserverStub {
  static instances: ResizeObserverStub[] = []
  readonly observe = vi.fn()
  readonly disconnect = vi.fn()

  constructor(readonly callback: ResizeObserverCallback) {
    ResizeObserverStub.instances.push(this)
  }

  emit(): void {
    this.callback([] as ResizeObserverEntry[], this as unknown as ResizeObserver)
  }
}

const mounted: App[] = []

async function mount(component: Parameters<typeof createApp>[0], props: Record<string, unknown>) {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(component, props)
  app.mount(host)
  mounted.push(app)
  await nextTick()
  return { host, app }
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    callback(0)
    return 1
  })
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
  ResizeObserverStub.instances = []
})

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('knowledge graph canvas', () => {
  it('creates one canvas chart, keeps graph links empty and disposes it', async () => {
    const handlers = new Map<string, (event: { data?: { knowledgeKey?: string } }) => void>()
    const chart = {
      setOption: vi.fn(),
      on: vi.fn((name: string, handler: (event: { data?: { knowledgeKey?: string } }) => void) => handlers.set(name, handler)),
      off: vi.fn(),
      resize: vi.fn(),
      dispose: vi.fn(),
      dispatchAction: vi.fn(),
    }
    const chartFactory = vi.fn(() => chart)
    const selected: string[] = []
    const props = reactive({
      nodes,
      rows,
      mode: 'graph' as const,
      selectedKey: null as string | null,
      scopeLabel: '匿名考试 · 七年级一班',
      chartFactory,
    })
    const Root = defineComponent({
      setup: () => () => h(KnowledgeGraphCanvas, {
        ...props,
        onSelectNode: (key: string) => selected.push(key),
      }),
    })
    const { app } = await mount(Root, {})

    expect(chartFactory).toHaveBeenCalledTimes(1)
    expect(chart.setOption).toHaveBeenCalledWith(expect.objectContaining({
      series: [expect.objectContaining({ type: 'graph', links: [] })],
    }), true)
    handlers.get('click')?.({ data: { knowledgeKey: nodes[0]!.knowledge_key } })
    expect(selected).toEqual([nodes[0]!.knowledge_key])

    props.selectedKey = nodes[0]!.knowledge_key
    await nextTick()
    expect(chart.setOption).toHaveBeenCalledTimes(2)
    ResizeObserverStub.instances[0]!.emit()
    expect(chart.resize).toHaveBeenCalledTimes(1)
    app.unmount()
    mounted.splice(mounted.indexOf(app), 1)
    expect(chart.dispose).toHaveBeenCalledTimes(1)
  })

  it('labels tree lines as temporary grouping rather than knowledge relationships', async () => {
    const chart = {
      setOption: vi.fn(), on: vi.fn(), off: vi.fn(), resize: vi.fn(),
      dispose: vi.fn(), dispatchAction: vi.fn(),
    }
    const { host } = await mount(KnowledgeGraphCanvas, {
      nodes, rows, mode: 'tree', selectedKey: null,
      scopeLabel: '匿名考试 · 七年级一班', chartFactory: () => chart,
    })
    expect(host.textContent).toContain('虚线仅表示筛选范围、学生与知识标签的分组归属')
    expect(host.textContent).toContain('不是知识点父子、先修或相关关系')
    expect(chart.setOption).toHaveBeenCalledWith(expect.objectContaining({
      series: [expect.objectContaining({
        type: 'tree',
        lineStyle: expect.objectContaining({ type: 'dotted' }),
      })],
    }), true)
  })
})

describe('knowledge graph text directory', () => {
  it('supports search and keyboard selection with a non-colour description', async () => {
    const selected: string[] = []
    const { host } = await mount(GraphTextDirectory, {
      nodes,
      selectedKey: null,
      onSelectNode: (key: string) => selected.push(key),
    })
    const input = host.querySelector<HTMLInputElement>('input[type="search"]')!
    input.value = '三角形'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    const button = host.querySelector<HTMLButtonElement>('[data-testid="graph-directory-item"]')!
    expect(button.getAttribute('aria-label')).toContain('62%')
    expect(button.getAttribute('aria-label')).toContain('需要讲评')
    button.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    expect(selected).toEqual([nodes[0]!.knowledge_key])
  })
})

describe('knowledge graph node inspector', () => {
  it('shows node facts, allowed context, reasons and evidence pagination', async () => {
    const loadedMore = vi.fn()
    const { host } = await mount(GraphNodeInspector, {
      node: nodes[0], evidence, evidenceState: 'ready', evidenceError: '',
      onLoadMoreEvidence: loadedMore,
    })
    expect(host.textContent).toContain('需要讲评')
    expect(host.textContent).toContain('4 条证据')
    expect(host.textContent).toContain('支持标签')
    expect(host.textContent).toContain('角的关系')
    expect(host.textContent).toContain('步骤不完整')
    expect(host.textContent).not.toContain('建议')
    const more = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('加载更多'))!
    more.click()
    expect(loadedMore).toHaveBeenCalledTimes(1)
  })

  it('keeps evidence visible while reporting a stale refresh', async () => {
    const retried = vi.fn()
    const { host } = await mount(GraphNodeInspector, {
      node: nodes[0], evidence, evidenceState: 'stale-error', evidenceError: '读取失败',
      onRetryEvidence: retried,
    })
    expect(host.textContent).toContain('上次读取的证据仍可查看')
    expect(host.textContent).toContain('匿名学生')
    host.querySelector<HTMLButtonElement>('[data-testid="retry-graph-evidence"]')!.click()
    expect(retried).toHaveBeenCalledTimes(1)
  })
})
