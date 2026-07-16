import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchScopedGraphRows, type GraphQueryInput, type GraphRowsResponse } from '../api/graph'
import { fetchStudents } from '../api/students'
import { createAppRouter } from '../router'
import { useSessionStore } from '../stores/session'
import KnowledgeGraphView from '../views/KnowledgeGraphView.vue'

vi.mock('../api/students', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/students')>(),
  fetchStudents: vi.fn(),
}))

vi.mock('../api/graph', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/graph')>(),
  fetchScopedGraphRows: vi.fn(),
  fetchScopedGraphEvidence: vi.fn(),
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

function responseFor(query: GraphQueryInput): GraphRowsResponse {
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
    rows: [],
    nodes: [{
      knowledge_key: 'knowledge_point:三角形全等',
      knowledge_label: '三角形全等',
      student_count: studentIds.length,
      item_count: 4,
      deduction_count: 2,
      average_mastery: 0.62,
      tag_context: {},
      error_counts: { primary: {}, secondary: {} },
    }],
    edges: [],
    coverage: { covered_items: 4, total_items: 5, missing_items: { Q5: '未标注' } },
    warnings: ['部分题目没有知识标签'],
    diagnosis_identity: 'question_tag',
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

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  vi.mocked(fetchStudents).mockResolvedValue(students)
  vi.mocked(fetchScopedGraphRows).mockImplementation(async (query) => responseFor(query))
})

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('knowledge graph view', () => {
  it('uses a validated workbench exam and class without guessing another scope', async () => {
    const { host } = await mountView('/knowledge-graph?session=7&class=七年级一班')
    await vi.waitFor(() => expect(fetchScopedGraphRows).toHaveBeenCalledWith({
      scope: { mode: 'class', class_id: '七年级一班' },
      exam_scope: { mode: 'current', session_ids: [7] },
    }, expect.any(AbortSignal)))
    expect(host.textContent).toContain('已覆盖 4 / 5 份作答')
    expect(host.textContent).toContain('部分题目没有知识标签')
    expect(host.textContent).toContain('三角形全等')
  })

  it('does not request a graph when no valid class was supplied', async () => {
    const { host } = await mountView('/knowledge-graph')
    expect(fetchScopedGraphRows).not.toHaveBeenCalled()
    expect(host.textContent).toContain('请选择班级并应用范围')
  })

  it('rejects an invalid routed class instead of silently choosing the first class', async () => {
    const { host, router } = await mountView('/knowledge-graph?session=7&class=不存在的班级')
    expect(fetchScopedGraphRows).not.toHaveBeenCalled()
    expect(host.textContent).toContain('工作台传入的考试或班级已不可用')
    await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({}))
  })

  it('clears a current-exam graph when the topbar exam changes', async () => {
    const { host, sessionStore } = await mountView('/knowledge-graph?session=7&class=七年级一班')
    await vi.waitFor(() => expect(fetchScopedGraphRows).toHaveBeenCalledTimes(1))
    sessionStore.selectSession(8)
    await nextTick()
    expect(host.textContent).toContain('当前考试已更改，请选择班级并应用范围')
    expect(fetchScopedGraphRows).toHaveBeenCalledTimes(1)
  })

  it('applies an exact manual-exam selected-student scope and switches modes locally', async () => {
    const { host } = await mountView('/knowledge-graph')
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

    await vi.waitFor(() => expect(fetchScopedGraphRows).toHaveBeenLastCalledWith({
      scope: { mode: 'selected', student_ids: ['12', '15'] },
      exam_scope: { mode: 'manual', session_ids: [7, 8] },
    }, expect.any(AbortSignal)))
    const calls = vi.mocked(fetchScopedGraphRows).mock.calls.length
    const treeButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '学生分组树')!
    treeButton.click()
    await nextTick()
    expect(fetchScopedGraphRows).toHaveBeenCalledTimes(calls)
    expect(host.textContent).toContain('不是知识点父子、先修或相关关系')
  })
})
