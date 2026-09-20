import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fetchGraph,
  fetchRelationReviewQueue,
  type GraphResponse,
} from '../api/graph'
import { fetchStudents } from '../api/students'
import { createAppRouter } from '../router'
import { useSessionStore } from '../stores/session'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import KnowledgeGraphView from '../views/KnowledgeGraphView.vue'

vi.mock('../api/students', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/students')>(),
  fetchStudents: vi.fn(),
}))
vi.mock('../api/graph', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/graph')>(),
  fetchGraph: vi.fn(),
  fetchGraphEvidence: vi.fn(),
  fetchRelationReviewQueue: vi.fn(),
}))
const fakeChart = vi.hoisted(() => ({
  setOption: vi.fn(), on: vi.fn(), off: vi.fn(), resize: vi.fn(), dispose: vi.fn(), dispatchAction: vi.fn(),
}))
vi.mock('echarts/core', () => ({ init: vi.fn(() => fakeChart), use: vi.fn() }))

const sessions = [{
  id: 7, name: '匿名考试七', status: 'completed', is_deleted: false,
  deleted_at: null, created_at: null, updated_at: null,
}]
const students = [{
  id: 12, student_code: 'S012', name: '匿名学生甲', class_name: '七年级一班', created_at: null,
}]

function responseFor(): GraphResponse {
  return {
    response_schema_version: 'knowledge-graph-current',
    response_version: 'a'.repeat(64),
    scope: { mode: 'class', student_ids: ['12'], class_id: '七年级一班' },
    exam_scope: { mode: 'semester', curriculum_volume_id: 'bnu24-math-g8-upper', session_ids: [7], sessions: [{ session_id: 7, session_name: '匿名考试七' }] },
    coverage: { covered_items: 4, total_items: 5, missing_items: { Q5: '未标注' } },
    current_standard: { release_id: 'current', content_hash: 'c'.repeat(64), taxonomy_revision: 2 },
    nodes: [{
      stable_key: 'kp_geo_triangle_congruence',
      display_name: '三角形全等',
      definition: '能够判断两个三角形全等。',
      include_scope: '全等判定',
      exclude_scope: '相似判定',
      curriculum_anchors: ['课程标准 8.2'],
      observable_evidence: '写出判定条件',
      rationale: '课程核心内容',
      evidence_source_ids: ['standard:8.2'],
      mastery: { status: 'available', value: 0.62, evidence_count: 4, parameter_version: 'd'.repeat(64), reason: null },
      evidence: { student_count: 1, item_count: 4, deduction_count: 2, tag_context: {}, error_counts: {} },
      missing_reasons: [],
    }],
    edges: [],
    missing: [],
    warnings: ['部分题目没有知识标签'],
    counts: { node_count: 1, edge_count: 0, evidence_row_count: 4, missing_count: 0 },
  }
}

class ResizeObserverStub { observe(): void {} disconnect(): void {} }
const mounted: App[] = []
async function settle() { await nextTick(); await Promise.resolve(); await nextTick() }

async function mountView(target: string) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useCurriculumScopeStore(pinia).$patch({ loadState: 'ready', selectedVolumeId: 'bnu24-math-g8-upper' })
  useSessionStore(pinia).$patch({ sessions, selectedSessionId: 7, loadState: 'ready' })
  const router = createAppRouter(createMemoryHistory())
  await router.push(target)
  await router.isReady()
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(KnowledgeGraphView)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  mounted.push(app)
  await settle()
  return host
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1 })
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
  vi.mocked(fetchStudents).mockResolvedValue(students)
  vi.mocked(fetchGraph).mockImplementation(async () => responseFor())
  vi.mocked(fetchRelationReviewQueue).mockResolvedValue({
    status: 'suggested', items: [], total: 0, page: 1, page_size: 20, total_pages: 1,
  })
})
afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('knowledge graph view', () => {
  it('loads the current graph for the route scope and shows no version switch language', async () => {
    const host = await mountView('/knowledge-graph?session=7&class=%E4%B8%83%E5%B9%B4%E7%BA%A7%E4%B8%80%E7%8F%AD')
    await vi.waitFor(() => expect(fetchGraph).toHaveBeenCalledWith({
      scope: {
        mode: 'class',
        class_id: '七年级一班',
        class_ids: ['七年级一班'],
        use_historical_fallback: false,
      },
      exam_scope: { mode: 'semester', curriculum_volume_id: 'bnu24-math-g8-upper' },
    }, expect.any(AbortSignal)))
    expect(host.textContent).toContain('知识图谱')
    expect(host.textContent).toContain('三角形全等')
    expect(host.textContent).toContain('知识点')
    expect(host.textContent).not.toContain('已确认关系')
  })
})
