import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { GraphQueryInput } from '../api/graph'
import {
  fetchGraphV2,
  fetchMasteryRollout,
  fetchRelationReviewQueue,
  type GraphV2Response,
} from '../api/graph-v2'
import { fetchStudents } from '../api/students'
import { createAppRouter } from '../router'
import { useSessionStore } from '../stores/session'
import KnowledgeGraphView from '../views/KnowledgeGraphView.vue'

vi.mock('../api/students', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/students')>(),
  fetchStudents: vi.fn(),
}))

vi.mock('../api/graph-v2', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/graph-v2')>(),
  fetchGraphV2: vi.fn(),
  fetchGraphV2Evidence: vi.fn(),
  fetchMasteryRollout: vi.fn(),
  fetchRelationReviewQueue: vi.fn(),
}))

const fakeChart = vi.hoisted(() => ({
  setOption: vi.fn(), on: vi.fn(), off: vi.fn(), resize: vi.fn(),
  dispose: vi.fn(), dispatchAction: vi.fn(),
}))

vi.mock('echarts/core', () => ({ init: vi.fn(() => fakeChart), use: vi.fn() }))

const sessions = [
  { id: 7, name: '匿名考试七', status: 'completed', is_deleted: false, deleted_at: null, created_at: null, updated_at: null },
  { id: 8, name: '匿名考试八', status: 'completed', is_deleted: false, deleted_at: null, created_at: null, updated_at: null },
]

const students = [
  { id: 12, student_code: 'S012', name: '匿名学生甲', class_name: '七年级一班', created_at: null },
  { id: 15, student_code: 'S015', name: '匿名学生乙', class_name: '七年级一班', created_at: null },
  { id: 21, student_code: 'S021', name: '匿名学生丙', class_name: '七年级二班', created_at: null },
]

function responseFor(query: GraphQueryInput): GraphV2Response {
  const sessionIds = query.exam_scope.mode === 'cross_exam'
    ? [7, 8]
    : query.exam_scope.session_ids
  let studentIds: string[]
  let classId: string | null
  if (query.scope.mode === 'class') {
    classId = query.scope.class_id
    studentIds = students
      .filter((student) => student.class_name === classId)
      .map((student) => String(student.id))
  } else {
    classId = null
    studentIds = query.scope.student_ids
  }
  return {
    scope: {
      mode: query.scope.mode,
      student_ids: studentIds,
      class_id: classId,
    },
    exam_scope: {
      mode: query.exam_scope.mode,
      session_ids: sessionIds,
      sessions: sessionIds.map((id) => ({ session_id: id, session_name: `匿名考试${id}` })),
    },
    response_schema_version: 'knowledge-graph-v2',
    response_version: 'a'.repeat(64),
    mastery_mode: 'v1',
    mastery_parameter_version: null,
    nodes: [{
      stable_key: 'kp_geo_triangle_congruence',
      display_name: '三角形全等',
      identity_revision: 1,
      mastery_v1: { status: 'available', value: 0.62, evidence_count: 4, reason: null },
      mastery_v2: {
        status: 'unavailable', value: null, evidence_count: 0,
        reason: 'mastery_v2_not_enabled',
      },
      evidence: {
        student_count: studentIds.length,
        item_count: 4,
        deduction_count: 2,
        tag_context: {},
        error_counts: { primary: {}, secondary: {} },
      },
      missing_reasons: [],
    }],
    edges: [],
    missing: [],
    coverage: { covered_items: 4, total_items: 5, missing_items: { Q5: '未标注' } },
    warnings: ['部分题目没有知识标签'],
    counts: { node_count: 1, edge_count: 0, evidence_row_count: 4, missing_count: 0 },
  }
}

class ResizeObserverStub {
  observe(): void {}
  disconnect(): void {}
}

const mounted: App[] = []

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountView(target: string) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const sessionStore = useSessionStore(pinia)
  sessionStore.$patch({ sessions, selectedSessionId: 7, loadState: 'ready' })
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
  return { host, router, sessionStore }
}

function selectValue(element: HTMLSelectElement, value: string): void {
  element.value = value
  element.dispatchEvent(new Event('change', { bubbles: true }))
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise })
  return { promise, resolve }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  vi.mocked(fetchStudents).mockResolvedValue(students)
  vi.mocked(fetchGraphV2).mockImplementation(async (query) => responseFor(query))
  vi.mocked(fetchMasteryRollout).mockResolvedValue({
    enabled: false,
    active_mode: 'v1',
    active_parameter_version: null,
    approved_evaluation_id: null,
    revision: 1,
    updated_by: null,
    reason: null,
    updated_at: '2026-01-01 00:00:00',
  })
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
  it('uses a validated workbench exam and class without guessing another scope', async () => {
    const { host } = await mountView('/knowledge-graph?session=7&class=七年级一班')
    await vi.waitFor(() => expect(fetchGraphV2).toHaveBeenCalledWith({
      scope: { mode: 'class', class_id: '七年级一班' },
      exam_scope: { mode: 'current', session_ids: [7] },
    }, expect.any(AbortSignal)))
    expect(host.textContent).toContain('已覆盖 4 / 5 份作答')
    expect(host.textContent).toContain('部分题目没有知识标签')
    expect(host.textContent).toContain('三角形全等')
  })

  it('keeps the canvas and inspector skeleton visible during the first graph load', async () => {
    const pending = deferred<GraphV2Response>()
    vi.mocked(fetchGraphV2).mockImplementationOnce(async () => pending.promise)
    const { host } = await mountView('/knowledge-graph?session=7&class=七年级一班')

    await vi.waitFor(() => (
      expect(host.querySelector('.knowledge-graph-loading-skeleton')).not.toBeNull()
    ))
    expect(host.textContent).toContain('已确认知识关系')
    expect(host.textContent).toContain('知识点详情')

    pending.resolve(responseFor({
      scope: { mode: 'class', class_id: '七年级一班' },
      exam_scope: { mode: 'current', session_ids: [7] },
    }))
    await vi.waitFor(() => expect(host.querySelector('.knowledge-graph-loading-skeleton')).toBeNull())
  })

  it('does not request a graph when no valid class was supplied', async () => {
    const { host } = await mountView('/knowledge-graph')
    expect(fetchGraphV2).not.toHaveBeenCalled()
    expect(host.textContent).toContain('请选择班级并应用范围')
  })

  it('rejects an invalid routed class instead of silently choosing the first class', async () => {
    const { host, router } = await mountView('/knowledge-graph?session=7&class=不存在的班级')
    expect(fetchGraphV2).not.toHaveBeenCalled()
    expect(host.textContent).toContain('地址中的考试、班级或学生范围已不可用')
    await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({}))
  })

  it('clears a current-exam graph when the topbar exam changes', async () => {
    const { host, sessionStore } = await mountView('/knowledge-graph?session=7&class=七年级一班')
    await vi.waitFor(() => expect(fetchGraphV2).toHaveBeenCalledTimes(1))
    sessionStore.selectSession(8)
    await nextTick()
    expect(host.textContent).toContain('当前考试已更改，请选择班级并应用范围')
    expect(fetchGraphV2).toHaveBeenCalledTimes(1)
  })

  it('applies an exact manual-exam selected-student scope and switches modes locally', async () => {
    const { host, router } = await mountView('/knowledge-graph')
    selectValue(host.querySelector<HTMLSelectElement>('#graph-exam-mode')!, 'manual')
    await nextTick()
    const examIds = host.querySelector<HTMLSelectElement>('#graph-manual-sessions')!
    for (const option of examIds.options) option.selected = true
    examIds.dispatchEvent(new Event('change', { bubbles: true }))
    selectValue(host.querySelector<HTMLSelectElement>('#graph-student-scope')!, 'selected')
    await nextTick()
    const studentIds = host.querySelector<HTMLSelectElement>('#graph-selected-students')!
    studentIds.options[0]!.selected = true
    studentIds.options[1]!.selected = true
    studentIds.dispatchEvent(new Event('change', { bubbles: true }))
    host.querySelector<HTMLButtonElement>('[data-testid="apply-graph-scope"]')!.click()

    await vi.waitFor(() => expect(fetchGraphV2).toHaveBeenLastCalledWith({
      scope: { mode: 'selected', student_ids: ['12', '15'] },
      exam_scope: { mode: 'manual', session_ids: [7, 8] },
    }, expect.any(AbortSignal)))
    expect(router.currentRoute.value.query).toEqual({
      exam: 'manual', sessions: '7,8', scope: 'selected', students: '12,15',
    })
    const calls = vi.mocked(fetchGraphV2).mock.calls.length
    const overviewButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '全部关系')!
    overviewButton.click()
    await nextTick()
    expect(fetchGraphV2).toHaveBeenCalledTimes(calls)
    expect(host.textContent).toContain('已确认知识关系')
  })

  it('restores a controlled manual and selected-student scope after refresh', async () => {
    const { router } = await mountView(
      '/knowledge-graph?exam=manual&sessions=7,8&scope=selected&students=12,15',
    )
    await vi.waitFor(() => expect(fetchGraphV2).toHaveBeenCalledWith({
      scope: { mode: 'selected', student_ids: ['12', '15'] },
      exam_scope: { mode: 'manual', session_ids: [7, 8] },
    }, expect.any(AbortSignal)))
    expect(router.currentRoute.value.query).toEqual({
      exam: 'manual', sessions: '7,8', scope: 'selected', students: '12,15',
    })
  })

  it('keeps a deep link recoverable when student options fail once', async () => {
    vi.mocked(fetchStudents)
      .mockRejectedValueOnce(new Error('temporary'))
      .mockResolvedValueOnce(students)
    const { host, router } = await mountView('/knowledge-graph?session=7&class=七年级一班')
    expect(host.textContent).toContain('班级和学生列表暂时无法读取')
    expect(router.currentRoute.value.query).toEqual({ session: '7', class: '七年级一班' })
    expect(fetchGraphV2).not.toHaveBeenCalled()

    host.querySelector<HTMLButtonElement>('.knowledge-graph-inline-error button')!.click()
    await vi.waitFor(() => expect(fetchGraphV2).toHaveBeenCalledWith({
      scope: { mode: 'class', class_id: '七年级一班' },
      exam_scope: { mode: 'current', session_ids: [7] },
    }, expect.any(AbortSignal)))
  })
})
