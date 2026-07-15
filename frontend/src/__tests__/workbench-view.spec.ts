import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { QuestionAnalysisResponse, StudentAnalysisResponse } from '../api/analysis'
import {
  fetchGraphEvidence,
  type GraphEvidenceResponse,
  type GraphRowsResponse,
} from '../api/graph'
import type { WorkbenchOverview } from '../api/workbench'
import { createAppRouter } from '../router'
import { useAnalysisStore } from '../stores/analysis'
import { useSessionStore } from '../stores/session'
import { useWorkbenchStore, type ResourceState } from '../stores/workbench'
import WorkbenchView from '../views/WorkbenchView.vue'

vi.mock('../api/graph', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/graph')>(),
  fetchGraphEvidence: vi.fn(),
}))

const overview: WorkbenchOverview = {
  current_session: {
    id: 7,
    name: '七年级数学期末质量监测',
    status: 'grading',
    is_deleted: false,
    deleted_at: null,
    created_at: '2026-07-14T08:00:00Z',
    updated_at: '2026-07-15T09:30:00Z',
  },
  progress: {
    total_papers: 36,
    matched_papers: 35,
    unmatched_papers: 1,
    graded_papers: 12,
    failed_papers: 1,
    grading_papers: 2,
    needs_human_review: 3,
    absent_students: 0,
    scan_issue_students: 1,
    progress_percent: 33.33,
  },
  review: { question_count: 2, item_count: 3 },
  anomalies: { unmatched_papers: 1, scan_issue_students: 1, failed_papers: 1 },
  recent_jobs: [{
    id: 41,
    job_type: 'grading',
    status: 'running',
    progress: 0.58,
    stage: '评分中',
    detail: '正在读取已匹配答卷',
    created_at: '2026-07-15T09:00:00Z',
    started_at: '2026-07-15T09:01:00Z',
    updated_at: '2026-07-15T09:32:00Z',
    finished_at: null,
  }],
  recent_sessions: [{
    session: {
      id: 8,
      name: '八年级物理单元检测（长名称用于验证稳定布局）',
      status: 'completed',
      is_deleted: false,
      deleted_at: null,
      created_at: '2026-07-12T08:00:00Z',
      updated_at: '2026-07-13T10:00:00Z',
    },
    progress: {
      total_papers: 40,
      matched_papers: 40,
      unmatched_papers: 0,
      graded_papers: 40,
      failed_papers: 0,
      grading_papers: 0,
      needs_human_review: 0,
      absent_students: 0,
      scan_issue_students: 0,
      progress_percent: 100,
    },
  }],
  updated_at: '2026-07-15T09:35:00Z',
}

const questions: QuestionAnalysisResponse = {
  scope: { session_id: 7, class_name: null, question_id: null },
  classes: ['七年级一班', '七年级二班'],
  items: [{
    class_name: '全部班级',
    question_id: 'Q1',
    max_score: 10,
    score_rate: 82.5,
    average_score: 8.25,
    deduction_count: 4,
    attempt_count: 12,
    metric_status: 'ready',
  }, {
    class_name: '全部班级',
    question_id: 'Q2',
    max_score: null,
    score_rate: null,
    average_score: null,
    deduction_count: 0,
    attempt_count: 1,
    metric_status: 'missing_max_score',
  }],
  total: 2,
  page: 1,
  page_size: 100,
  total_pages: 1,
}

const students: StudentAnalysisResponse = {
  scope: { session_id: 7, class_name: null, question_id: 'Q1' },
  items: [{
    result_id: 71,
    detail_id: 701,
    student_id: 17,
    student_code: 'S017',
    student_name: '一位名字较长的学生用于验证布局',
    class_name: '七年级一班',
    question_id: 'Q1',
    score_awarded: 8,
    max_score: 10,
    deduction_amount: 2,
    deduction_reason: '计算过程漏写单位',
    needs_review: true,
    evidence_url: '/api/sessions/7/review/questions/Q1/items/701/media/crop',
  }],
  total: 1,
  page: 1,
  page_size: 100,
  total_pages: 1,
}

const graph: GraphRowsResponse = {
  scope: { mode: 'class', student_ids: [], class_id: '七年级一班' },
  exam_scope: {
    mode: 'current',
    session_ids: [7],
    sessions: [{ session_id: 7, session_name: '七年级数学期末质量监测' }],
  },
  rows: [],
  nodes: [{
    knowledge_key: 'knowledge_point:fraction',
    knowledge_label: '分数运算',
    student_count: 12,
    item_count: 18,
    deduction_count: 5,
    average_mastery: 0.78,
    tag_context: { 章节: ['数与代数'] },
    error_counts: {},
  }],
  edges: [],
  coverage: { covered_items: 18, total_items: 20, missing_items: {} },
  warnings: ['2 份作答未关联知识标签'],
  diagnosis_identity: 'question_tag',
}

function graphEvidenceResponse(): GraphEvidenceResponse {
  return {
    scope: graph.scope,
    exam_scope: graph.exam_scope,
    knowledge_key: 'knowledge_point:fraction',
    knowledge_label: '分数运算',
    items: [{
      student_id: 17,
      student_code: 'S017',
      student_name: '学生甲',
      class_id: '七年级一班',
      knowledge_key: 'knowledge_point:fraction',
      knowledge_label: '分数运算',
      session_id: 7,
      session_name: '七年级数学期末质量监测',
      question_id: 'Q1',
      bank_question_id: 101,
      score_awarded: 8,
      full_score: 10,
      score_rate: 0.8,
      tag_context: { 章节: ['数与代数'] },
      actionable_reasons: ['计算过程漏写单位'],
      error_counts: {},
    }],
    total: 1,
    page: 1,
    page_size: 20,
    total_pages: 1,
    coverage: graph.coverage,
    warnings: graph.warnings,
    diagnosis_identity: 'question_tag',
  }
}

interface MountOptions {
  sessionId?: number | null
  overviewState?: ResourceState
  questionState?: ResourceState
  graphState?: ResourceState
  studentState?: ResourceState
  overviewValue?: WorkbenchOverview | null
  questionValue?: QuestionAnalysisResponse
  studentValue?: StudentAnalysisResponse
  graphValue?: GraphRowsResponse | null
  questionUpdatedAt?: string | null
  graphUpdatedAt?: string | null
}

const mountedApps: App[] = []

async function settleUi(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountView({
  sessionId = 7,
  overviewState = 'ready',
  questionState = 'ready',
  graphState = 'ready',
  studentState = 'ready',
  overviewValue = overview,
  questionValue = questions,
  studentValue = students,
  graphValue = graph,
  questionUpdatedAt = '2026-07-15T09:36:00Z',
  graphUpdatedAt = '2026-07-15T09:38:00Z',
}: MountOptions = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createAppRouter(createMemoryHistory())
  await router.push('/design-system')
  await router.isReady()

  const sessionStore = useSessionStore(pinia)
  sessionStore.$patch({
    sessions: [overview.current_session!, overview.recent_sessions[0]!.session],
    selectedSessionId: sessionId,
    loadState: 'ready',
  })

  const workbenchStore = useWorkbenchStore(pinia)
  const loadOverview = vi.spyOn(workbenchStore, 'loadOverview').mockImplementation(async (id) => {
    workbenchStore.$patch({
      sessionId: id,
      overview: overviewValue,
      overviewState,
      overviewError: overviewState === 'stale-error' || overviewState === 'error'
        ? '工作台数据暂时无法更新'
        : '',
      overviewUpdatedAt: overviewValue === null ? null : '2026-07-15T09:35:00Z',
    })
  })
  const loadAnomalies = vi.spyOn(workbenchStore, 'loadAnomalies').mockResolvedValue()

  const analysisStore = useAnalysisStore(pinia)
  const resetStudents = vi.spyOn(analysisStore, 'resetStudents')
  const loadQuestions = vi.spyOn(analysisStore, 'loadQuestions').mockImplementation(async (id, className) => {
    analysisStore.$patch({
      sessionId: id,
      questions: questionValue.items.map((item) => ({
        ...item,
        class_name: className ?? item.class_name,
      })),
      classes: questionValue.classes,
      questionsScope: { ...questionValue.scope, class_name: className },
      questionsState: questionState,
      questionsError: questionState === 'error' || questionState === 'stale-error'
        ? '题目分析暂时无法更新'
        : '',
      questionsUpdatedAt: questionUpdatedAt,
      questionsTotal: questionValue.total,
      questionsPage: questionValue.page,
      questionsPageSize: questionValue.page_size,
      questionsTotalPages: questionValue.total_pages,
    })
  })
  const loadStudents = vi.spyOn(analysisStore, 'loadStudents').mockImplementation(async (id, questionId, className) => {
    analysisStore.$patch({
      sessionId: id,
      students: studentValue.items.map((item) => ({ ...item, question_id: questionId })),
      studentsScope: { session_id: id, question_id: questionId, class_name: className },
      studentsState: studentState,
      studentsError: studentState === 'error' || studentState === 'stale-error'
        ? '学生明细暂时无法更新'
        : '',
      studentsUpdatedAt: '2026-07-15T09:37:00Z',
      studentsTotal: studentValue.total,
      studentsPage: studentValue.page,
      studentsPageSize: studentValue.page_size,
      studentsTotalPages: studentValue.total_pages,
    })
  })
  const loadGraph = vi.spyOn(analysisStore, 'loadGraph').mockImplementation(async (id, className) => {
    analysisStore.$patch({
      sessionId: id,
      graph: graphValue === null
        ? null
        : { ...graphValue, scope: { ...graphValue.scope, class_id: className } },
      graphState,
      graphError: graphState === 'error' || graphState === 'stale-error'
        ? '标签覆盖暂时无法更新'
        : '',
      graphUpdatedAt,
    })
  })
  const loadMoreQuestions = vi.spyOn(analysisStore, 'loadMoreQuestions').mockResolvedValue()
  const loadMoreStudents = vi.spyOn(analysisStore, 'loadMoreStudents').mockResolvedValue()
  const loadMoreAnomalies = vi.spyOn(workbenchStore, 'loadMoreAnomalies').mockResolvedValue()

  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(WorkbenchView)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  mountedApps.push(app)
  await settleUi()

  return {
    host,
    router,
    sessionStore,
    workbenchStore,
    analysisStore,
    loadOverview,
    loadAnomalies,
    loadQuestions,
    loadStudents,
    loadGraph,
    loadMoreQuestions,
    loadMoreStudents,
    loadMoreAnomalies,
    resetStudents,
  }
}

function clickButton(host: HTMLElement, label: string): void {
  const button = [...host.querySelectorAll<HTMLButtonElement>('button')]
    .find((candidate) => candidate.textContent?.trim().includes(label))
  expect(button, `button ${label}`).toBeDefined()
  button!.click()
}

function selectValue(select: HTMLSelectElement, value: string): void {
  select.value = value
  select.dispatchEvent(new Event('change', { bubbles: true }))
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
  vi.mocked(fetchGraphEvidence).mockResolvedValue(graphEvidenceResponse())
})

afterEach(() => {
  while (mountedApps.length) mountedApps.pop()?.unmount()
  document.body.innerHTML = ''
})

describe('workbench view', () => {
  it('labels partial question and student pages and exposes load-more actions', async () => {
    const mounted = await mountView({
      questionValue: { ...questions, total: 102, total_pages: 2 },
      studentValue: { ...students, total: 101, total_pages: 2 },
    })

    expect(mounted.host.textContent).toContain('当前显示 2 / 102 道题目')
    expect(mounted.host.textContent).toContain('当前显示 1 / 101 名学生')
    clickButton(mounted.host, '加载更多题目')
    clickButton(mounted.host, '加载更多学生')
    expect(mounted.loadMoreQuestions).toHaveBeenCalledTimes(1)
    expect(mounted.loadMoreStudents).toHaveBeenCalledTimes(1)
  })

  it('labels a partial anomaly page and exposes its remaining records', async () => {
    const mounted = await mountView()
    clickButton(mounted.host, '查看异常')
    mounted.workbenchStore.$patch({
      anomalies: [{
        anomaly_id: 'failed:1', anomaly_type: 'grading_failed', display_name: 'first anomaly',
        student_code: null, class_name: null, status: 'failed', detail: null, created_at: null,
      }],
      anomaliesState: 'ready', anomaliesUpdatedAt: '2026-07-15T09:30:00Z',
      anomaliesTotal: 101, anomaliesPage: 1, anomaliesPageSize: 100, anomaliesTotalPages: 2,
    })
    await settleUi()

    expect(mounted.host.textContent).toContain('当前显示 1 / 101 条异常')
    clickButton(mounted.host, '加载更多异常')
    expect(mounted.loadMoreAnomalies).toHaveBeenCalledTimes(1)
  })

  it('loads tag evidence beyond the first 20 items and keeps the total visible', async () => {
    const first = graphEvidenceResponse()
    first.total = 21
    first.total_pages = 2
    const second = {
      ...first,
      items: [{ ...first.items[0]!, student_id: 18, student_name: 'later evidence' }],
      page: 2,
    }
    vi.mocked(fetchGraphEvidence).mockResolvedValueOnce(first).mockResolvedValueOnce(second)
    const mounted = await mountView()
    selectValue(mounted.host.querySelector<HTMLSelectElement>('#analysis-class')!, '七年级一班')
    await settleUi()
    mounted.host.querySelector<HTMLButtonElement>('.tag-node')!.click()
    await settleUi()

    expect(mounted.host.textContent).toContain('当前显示 1 / 21 条证据')
    clickButton(mounted.host, '加载更多证据')
    await settleUi()
    expect(fetchGraphEvidence).toHaveBeenLastCalledWith(
      7, '七年级一班', 'knowledge_point:fraction', expect.any(AbortSignal), 2,
    )
    expect(mounted.host.textContent).toContain('later evidence')
  })

  it('uses one continuous column for the progress rail and content grids at 1024px', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/styles/workbench.css'), 'utf-8')
    const tabletStart = css.indexOf('@media (max-width: 1100px)')
    const compactStart = css.indexOf('@media (max-width: 900px)')
    expect(tabletStart).toBeGreaterThanOrEqual(0)
    expect(compactStart).toBeGreaterThan(tabletStart)
    const tabletRules = css.slice(tabletStart, compactStart)
    expect(tabletRules).toContain('.workbench-progress-rail')
    expect(tabletRules).toContain('.workbench-primary-grid')
    expect(tabletRules).toContain('.workbench-secondary-grid')
    expect(tabletRules.match(/grid-template-columns:\s*(?:minmax\(0,\s*)?1fr\)?;/g)?.length ?? 0)
      .toBeGreaterThanOrEqual(2)
  })

  it('shows the approved analysis-first structure without decorative metric cards', async () => {
    const { host } = await mountView()

    const view = host.querySelector('.workbench-view')
    const heading = host.querySelector('h1')
    expect(view?.tagName).toBe('SECTION')
    expect(view?.getAttribute('aria-labelledby')).toBe('workbench-title')
    expect(heading?.textContent).toBe('工作台')
    expect(heading?.id).toBe('workbench-title')
    expect(heading?.getAttribute('tabindex')).toBe('-1')
    expect(host.querySelector('[data-testid="progress-action-rail"]')).not.toBeNull()
    expect(host.textContent).toContain('本题基于 12 份已批改作答')
    expect(host.textContent).toContain('班级题目分析')
    expect(host.textContent).toContain('最近考试')
    expect(host.textContent).toContain('知识标签覆盖')
    expect(host.querySelectorAll('[data-metric-card]')).toHaveLength(0)
    expect(host.querySelector('[data-testid="progress-action-rail"]')?.tagName).toBe('OL')
    expect(host.querySelectorAll('.workbench-progress-rail > li')).toHaveLength(4)
    expect(host.querySelector('[data-testid="progress-action-rail"]')?.textContent).toContain('58%')
    expect(host.querySelector('[data-testid="progress-action-rail"]')?.textContent).not.toContain('0.58%')
    expect(host.querySelectorAll('.workbench-progress-rail > li')[3]?.querySelector('strong')?.getAttribute('aria-label'))
      .toBe('最近任务：58%')
  })

  it('keeps all actions read-only and routes only to the existing grading page', async () => {
    const { host, router, sessionStore, loadStudents, loadAnomalies } = await mountView()

    clickButton(host, '去复核')
    expect(sessionStore.selectedSessionId).toBe(7)
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/grading'))

    clickButton(host, 'Q1')
    await settleUi()
    expect(loadStudents).toHaveBeenCalledWith(7, 'Q1', null)
    expect(host.textContent).toContain('一位名字较长的学生用于验证布局')
    const evidence = [...host.querySelectorAll<HTMLAnchorElement>('a')]
      .find((anchor) => anchor.textContent?.includes('查看答卷证据'))
    expect(evidence?.getAttribute('href')).toMatch(/^\/api\//)
    expect(evidence?.getAttribute('target')).toBeNull()

    clickButton(host, '进入评分复核')
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/grading?question=Q1'))

    clickButton(host, '查看异常')
    await settleUi()
    expect(loadAnomalies).toHaveBeenCalledWith(7)
    expect(host.textContent).toContain('异常记录')

    const recent = host.querySelector<HTMLButtonElement>('[data-session-id="8"]')
    expect(recent).not.toBeNull()
    recent!.click()
    expect(sessionStore.selectedSessionId).toBe(8)

    expect(host.textContent).not.toMatch(/取消任务|重试任务|提交任务/)
  })

  it('loads graph only after a class is selected and exposes controlled evidence', async () => {
    const { host, loadGraph } = await mountView()
    expect(loadGraph).not.toHaveBeenCalled()

    selectValue(host.querySelector<HTMLSelectElement>('#analysis-class')!, '七年级一班')
    await settleUi()
    expect(loadGraph).toHaveBeenCalledWith(7, '七年级一班')
    expect(host.textContent).toContain('已覆盖 18 / 20 份')
    expect(host.textContent).toContain('2 份作答未关联知识标签')

    clickButton(host, '分数运算')
    await settleUi()
    expect(fetchGraphEvidence).toHaveBeenCalledWith(
      7,
      '七年级一班',
      'knowledge_point:fraction',
      expect.any(AbortSignal),
    )
    expect(host.textContent).toContain('计算过程漏写单位')

  })

  it('loads the first question detail and invalidates the old student scope before a class change', async () => {
    const { host, loadStudents, resetStudents } = await mountView()
    await vi.waitFor(() => expect(loadStudents).toHaveBeenCalledWith(7, 'Q1', null))
    expect(host.textContent).toContain('一位名字较长的学生用于验证布局')

    selectValue(host.querySelector<HTMLSelectElement>('#analysis-class')!, '七年级一班')
    await vi.waitFor(() => expect(loadStudents).toHaveBeenCalledWith(7, 'Q1', '七年级一班'))
    expect(resetStudents).toHaveBeenCalledTimes(1)
  })

  it('does not present an idle student resource as a confirmed empty result', async () => {
    const { host } = await mountView({
      studentState: 'idle',
      studentValue: { ...students, items: [], total: 0, total_pages: 0 },
    })
    expect(host.textContent).toContain('正在准备学生明细')
    expect(host.textContent).not.toContain('当前题目没有学生明细')
  })

  it('distinguishes no session, no grading, no review, no anomalies and unknown values', async () => {
    const noSession = await mountView({ sessionId: null, overviewValue: null, overviewState: 'empty' })
    expect(noSession.host.textContent).toContain('请选择考试后查看工作台')
    noSession.host.remove()

    const emptyOverview: WorkbenchOverview = {
      ...overview,
      progress: { ...overview.progress!, graded_papers: 0, progress_percent: 0 },
      review: { question_count: 0, item_count: 0 },
      anomalies: { unmatched_papers: 0, scan_issue_students: 0, failed_papers: 0 },
      recent_jobs: [],
    }
    const empty = await mountView({
      overviewValue: emptyOverview,
      questionState: 'empty',
      questionValue: { ...questions, items: [], total: 0, total_pages: 0 },
    })
    expect(empty.host.textContent).toContain('当前考试还没有已批改题目')
    expect(empty.host.textContent).toContain('当前没有待复核项目')
    expect(empty.host.textContent).toContain('当前没有异常记录')

    const unknown = await mountView({
      overviewValue: { ...overview, progress: null, review: null, anomalies: null, recent_jobs: [] },
    })
    expect(unknown.host.textContent).toContain('暂不可用')
    expect(unknown.host.textContent).not.toContain('批改进度 0')
  })

  it('keeps the last successful overview usable when its refresh fails', async () => {
    const stale = await mountView({ overviewState: 'stale-error' })
    expect(stale.host.textContent).toContain('数据可能不是最新 · 上次更新')
    expect(stale.host.textContent).toContain('七年级数学期末质量监测')
    clickButton(stale.host, '重新加载工作台')
    expect(stale.loadOverview).toHaveBeenCalledTimes(2)
  })

  it('keeps overview usable when analysis fails', async () => {
    const analysisError = await mountView({ questionState: 'error' })
    expect(analysisError.host.textContent).toContain('分析数据暂时无法读取；工作台其他内容仍可使用')
    expect(analysisError.host.textContent).toContain('批改进度')
    clickButton(analysisError.host, '重新加载分析')
    expect(analysisError.loadQuestions).toHaveBeenCalledTimes(2)
  })

  it('distinguishes first-load, fresh-empty, refreshing-empty and stale-empty question analysis', async () => {
    const emptyQuestions = { ...questions, items: [], total: 0, total_pages: 0 }
    const firstLoad = await mountView({
      questionState: 'loading',
      questionValue: emptyQuestions,
      questionUpdatedAt: null,
    })
    expect(firstLoad.host.textContent).toContain('正在读取题目分析')
    expect(firstLoad.host.textContent).not.toContain('上次成功读取时没有已批改题目')

    const freshEmpty = await mountView({ questionState: 'empty', questionValue: emptyQuestions })
    expect(freshEmpty.host.textContent).toContain('当前考试还没有已批改题目')

    const refreshingEmpty = await mountView({ questionState: 'loading', questionValue: emptyQuestions })
    expect(refreshingEmpty.host.textContent).toContain('正在更新题目分析')
    expect(refreshingEmpty.host.textContent).toContain('上次成功读取时没有已批改题目')
    expect(refreshingEmpty.host.textContent).not.toContain('正在读取题目分析')
    expect(refreshingEmpty.host.textContent).not.toContain('当前考试还没有已批改题目')

    const staleEmpty = await mountView({ questionState: 'stale-error', questionValue: emptyQuestions })
    expect(staleEmpty.host.textContent).toContain('题目分析更新失败 · 上次更新')
    expect(staleEmpty.host.textContent).toContain('上次成功读取时没有已批改题目')
    expect(staleEmpty.host.textContent).toContain('重新加载分析')
    expect(staleEmpty.host.textContent).not.toContain('当前考试还没有已批改题目')
  })

  it('keeps question analysis usable when graph fails', async () => {
    const graphError = await mountView({ graphState: 'error' })
    selectValue(graphError.host.querySelector<HTMLSelectElement>('#analysis-class')!, '七年级一班')
    await settleUi()
    expect(graphError.host.textContent).toContain('标签覆盖暂时无法读取')
    expect(graphError.host.textContent).toContain('Q1')
    clickButton(graphError.host, '重新加载标签覆盖')
    expect(graphError.loadGraph).toHaveBeenCalledTimes(2)
  })

  it('preserves the meaning of an empty successful tag snapshot when its refresh fails', async () => {
    const emptyGraph: GraphRowsResponse = {
      ...graph,
      nodes: [],
      coverage: { covered_items: 0, total_items: 0, missing_items: {} },
      warnings: [],
    }
    const mounted = await mountView({ graphState: 'empty', graphValue: emptyGraph })
    selectValue(mounted.host.querySelector<HTMLSelectElement>('#analysis-class')!, '七年级一班')
    await settleUi()
    expect(mounted.host.textContent).toContain('当前班级没有知识标签记录')

    mounted.analysisStore.$patch({ graphState: 'loading' })
    await settleUi()
    expect(mounted.host.textContent).toContain('正在更新标签覆盖')
    expect(mounted.host.textContent).toContain('上次成功读取时没有知识标签记录')
    expect(mounted.host.textContent).not.toContain('正在读取标签覆盖')
    expect(mounted.host.textContent).not.toContain('当前班级没有知识标签记录')

    mounted.analysisStore.$patch({ graphState: 'stale-error' })
    await settleUi()
    expect(mounted.host.textContent).toContain('标签覆盖可能不是最新 · 上次更新')
    expect(mounted.host.textContent).toContain('上次成功读取时没有知识标签记录')
    expect(mounted.host.textContent).toContain('重新加载标签覆盖')
    expect(mounted.host.textContent).not.toContain('当前班级没有知识标签记录')

    mounted.analysisStore.$patch({
      graphState: 'loading',
      graph: null,
      graphUpdatedAt: null,
    })
    await settleUi()
    expect(mounted.host.textContent).toContain('正在读取标签覆盖')
    expect(mounted.host.textContent).not.toContain('上次成功读取时没有知识标签记录')
  })

  it('keeps anomaly list and empty snapshots visible while updating or stale', async () => {
    const mounted = await mountView()
    clickButton(mounted.host, '查看异常')
    await settleUi()
    expect(mounted.host.textContent).toContain('正在读取异常记录')
    expect(mounted.host.textContent).not.toContain('当前没有异常记录')
    const anomaly = {
      anomaly_id: 'failed:17',
      anomaly_type: 'grading_failed' as const,
      display_name: '学生甲',
      student_code: 'S017',
      class_name: '七年级一班',
      status: 'failed',
      detail: '评分结果未生成',
      created_at: '2026-07-15T09:20:00Z',
    }

    mounted.workbenchStore.$patch({
      anomalies: [],
      anomaliesState: 'empty',
      anomaliesUpdatedAt: '2026-07-15T09:30:00Z',
    })
    await settleUi()
    expect(mounted.host.textContent).toContain('当前没有异常记录')

    mounted.workbenchStore.$patch({ anomaliesState: 'loading' })
    await settleUi()
    expect(mounted.host.textContent).toContain('正在更新异常记录')
    expect(mounted.host.textContent).toContain('上次检查未发现异常')
    expect(mounted.host.textContent).not.toContain('当前没有异常记录')

    mounted.workbenchStore.$patch({
      anomalies: [anomaly],
      anomaliesState: 'loading',
      anomaliesUpdatedAt: '2026-07-15T09:30:00Z',
    })
    await settleUi()
    expect(mounted.host.textContent).toContain('正在更新异常记录')
    expect(mounted.host.textContent).toContain('学生甲')

    mounted.workbenchStore.$patch({ anomaliesState: 'stale-error' })
    await settleUi()
    expect(mounted.host.textContent).toContain('异常记录可能不是最新 · 上次更新')
    expect(mounted.host.textContent).toContain('学生甲')

    mounted.workbenchStore.$patch({ anomalies: [] })
    await settleUi()
    expect(mounted.host.textContent).toContain('上次检查未发现异常')
    expect(mounted.host.textContent).not.toContain('当前没有异常记录')
  })

  it('retains same-session graph and student content when refreshes fail', async () => {
    const retained = await mountView({ graphState: 'stale-error', studentState: 'stale-error' })
    selectValue(retained.host.querySelector<HTMLSelectElement>('#analysis-class')!, '七年级一班')
    await settleUi()
    expect(retained.host.textContent).toContain('分数运算')
    expect(retained.host.textContent).toContain('标签覆盖可能不是最新 · 上次更新')

    clickButton(retained.host, 'Q1')
    await settleUi()
    expect(retained.host.textContent).toContain('一位名字较长的学生用于验证布局')
    expect(retained.host.textContent).toContain('学生明细可能不是最新')
  })

  it('keeps same-session analysis, graph and student content visible while refreshing', async () => {
    const refreshing = await mountView({
      questionState: 'loading',
      graphState: 'loading',
      studentState: 'loading',
    })
    expect(refreshing.host.textContent).toContain('正在更新题目分析')
    expect(refreshing.host.textContent).toContain('Q1')
    selectValue(refreshing.host.querySelector<HTMLSelectElement>('#analysis-class')!, '七年级一班')
    await settleUi()
    expect(refreshing.host.textContent).toContain('分数运算')
    expect(refreshing.host.textContent).toContain('正在更新标签覆盖')

    clickButton(refreshing.host, 'Q1')
    await settleUi()
    expect(refreshing.host.textContent).toContain('一位名字较长的学生用于验证布局')
    expect(refreshing.host.textContent).toContain('正在更新学生明细')
  })

  it('retries a failed tag evidence request without reloading other sections', async () => {
    vi.mocked(fetchGraphEvidence).mockRejectedValueOnce(new Error('private graph failure'))
    const retained = await mountView()
    selectValue(retained.host.querySelector<HTMLSelectElement>('#analysis-class')!, '七年级一班')
    await settleUi()

    clickButton(retained.host, '分数运算')
    await settleUi()
    expect(retained.host.textContent).toContain('标签证据暂时无法读取')
    clickButton(retained.host, '重新加载标签证据')
    await settleUi()
    expect(fetchGraphEvidence).toHaveBeenCalledTimes(2)
    expect(retained.host.textContent).toContain('计算过程漏写单位')
    expect(retained.loadGraph).toHaveBeenCalledTimes(1)
  })

  it.each(['knowledge', 'class', 'session'] as const)(
    'shows a retryable error instead of leaving %s-mismatched tag evidence loading',
    async (mismatch) => {
      const response = graphEvidenceResponse()
      if (mismatch === 'knowledge') response.knowledge_key = 'knowledge_point:other'
      if (mismatch === 'class') response.scope = { ...response.scope, class_id: 'other-class' }
      if (mismatch === 'session') {
        response.exam_scope = {
          ...response.exam_scope,
          session_ids: [8],
          sessions: [{ session_id: 8, session_name: 'other exam' }],
        }
      }
      vi.mocked(fetchGraphEvidence).mockResolvedValueOnce(response)
      const mounted = await mountView()
      selectValue(mounted.host.querySelector<HTMLSelectElement>('#analysis-class')!, '七年级一班')
      await settleUi()

      mounted.host.querySelector<HTMLButtonElement>('.tag-node')!.click()
      await settleUi()

      expect(mounted.host.querySelector('.tag-coverage .workbench-inline-error')).not.toBeNull()
      expect(mounted.host.querySelector('.tag-coverage .workbench-inline-error')?.textContent)
        .toContain('标签证据暂时无法读取')
      expect(mounted.host.querySelector('.tag-coverage .workbench-state-copy')?.textContent ?? '')
        .not.toContain('正在读取标签证据')
    },
  )

  it('warns that a single graded answer is not representative', async () => {
    const single = {
      ...questions,
      items: [{ ...questions.items[0]!, attempt_count: 1 }],
      total: 1,
    }
    const { host } = await mountView({ questionValue: single })
    expect(host.textContent).toContain('当前仅 1 份已批改作答，不代表整体情况')
  })
})
