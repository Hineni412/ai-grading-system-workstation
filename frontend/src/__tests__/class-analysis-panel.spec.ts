import { createApp, h, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ClassAnalysisResponse,
} from '../api/class-analysis'
import type { JobResponse } from '../api/jobs'
import { useJobStore } from '../stores/jobs'
import ClassAnalysisPanel from '../components/results-center/ClassAnalysisPanel.vue'

const apiMock = vi.hoisted(() => ({
  getClassAnalysis: vi.fn(),
  getQuestionPreview: vi.fn(),
  editCausePattern: vi.fn(),
}))

const jobsMock = vi.hoisted(() => ({
  getJob: vi.fn(),
  cancelJob: vi.fn(),
  getJobStatusBatch: vi.fn(),
}))

vi.mock('../api/class-analysis', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/class-analysis')>(),
  classAnalysisApi: apiMock,
}))

vi.mock('../api/jobs', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/jobs')>(),
  jobApi: jobsMock,
}))

vi.mock('../components/review/review-rubric-cache', async (importOriginal) => ({
  ...await importOriginal<typeof import('../components/review/review-rubric-cache')>(),
  loadReviewRubric: vi.fn(async () => null),
}))

function makeJob(overrides: Partial<JobResponse> = {}): JobResponse {
  return {
    id: 91,
    job_type: 'class_analysis',
    payload: { session_id: 7 },
    result: {},
    status: 'running',
    progress: 0.4,
    stage: 'class_analysis',
    detail: 'generating',
    error: null,
    cancel_requested: false,
    created_at: '2026-08-30T10:00:00Z',
    started_at: '2026-08-30T10:00:01Z',
    updated_at: '2026-08-30T10:00:01Z',
    finished_at: null,
    ...overrides,
  }
}

function makeAnalysis(
  overrides: Partial<ClassAnalysisResponse> = {},
): ClassAnalysisResponse {
  return {
    status: 'ready',
    auto_generate: true,
    small_sample: false,
    data: {
      exam: {
        title: '数学阶段测试',
        subject: '数学',
        full_score: 100,
        graded_at: '2026-08-30T09:00:00Z',
      },
      present: 4,
      roster_absent: ['王五'],
      skipped: [],
      score_distribution: {
        avg: 54.75,
        median: 55.5,
        max: 88,
        min: 20,
        pass_rate: 0.5,
        bands: { '85–100': 1, '0–39': 2 },
      },
      questions: [
        {
          question_id: '12(3)',
          max_score: 8,
          class_rate: 0.25,
          stem_summary: '证明',
          canonical_answer: 'AB＝BD＋DH',
          causes: [{ reason: '未作答', count: 1 }],
          records: [
            {
              student_name: '钱肖白',
              score: 0,
              deduction_reason: '空白',
              error_category: 'blank',
              error_summary: '未作答',
            },
          ],
        },
      ],
      students: [
        {
          student_name: '陈维懋',
          student_code: 'S001',
          total_score: 88,
          rank: 1,
          needs_review: false,
          lost: [
            {
              question_id: '4',
              lost_points: 5,
              record: {
                deduction_reason: '选 D',
                error_category: 'choice',
                error_summary: '轴对称图形识别错误',
              },
            },
          ],
        },
      ],
    },
    generated_at: '2026-08-30T10:00:00Z',
    active_job_id: null,
    ...overrides,
  }
}

const mounted: App[] = []

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountPanel(
  sessionId: number | null = 7,
  props: { focusQuestion?: string | null; initialClass?: string | null } = {},
): Promise<{ host: HTMLElement; router: Router }> {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { render: () => h('div') } },
      { path: '/results', component: { render: () => h('div') } },
      { path: '/grading', component: { render: () => h('div', '合成深评页') } },
    ],
  })
  await router.push('/')
  await router.isReady()
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(ClassAnalysisPanel, { sessionId, ...props })
  app.use(pinia)
  app.use(router)
  app.mount(host)
  mounted.push(app)
  await settle()
  return { host, router }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis())
  jobsMock.getJob.mockResolvedValue(makeJob())
  jobsMock.getJobStatusBatch.mockResolvedValue([
    { id: 91, found: true, job: makeJob() },
  ])
})

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  try {
    useJobStore().stopAllPolling()
  } catch {
    // 无活动 pinia 时无需清理。
  }
  document.body.innerHTML = ''
})

describe('class analysis panel', () => {
  it('separates saved mathematical causes from process, state and review, and shows answer evidence after reentry', async () => {
    const result = makeAnalysis({ cause_analysis: { status: 'ready', pending_questions: 0, total_questions: 2,
      failed_questions: 0, question_states: { current: 2, stale: 0, old_prompt: 0, failed: 0 },
      stale: false, generated_at: '2026-09-09T10:00:00', origin: 'assistant' } })
    const question = result.data!.questions[0]!
    question.records[0]!.student_id = 31
    const evidence = [{ text: '绳长求和有误', student_ids: [31], student_answer: '10+6=16',
      evidence_steps: ['已算出斜段10'], previous_answers: [{ question_id: 'Q12(P1)', student_answer: '前问保留的作答', text: '前问批语', evidence_steps: [] }] }]
    const carryEvidence = [{ text: '沿用数值', student_ids: [31], student_answer: '直接沿用',
      previous_answers: [
        { question_id: 'Q12(P1)', student_answer: '前问保留的作答', text: '前问批语', evidence_steps: [] },
        { question_id: 'Q12(P2)', student_answer: '另一小问的作答', text: '', evidence_steps: [] },
      ] }]
    question.causes_grouped = true
    question.causes = [
      { kind: 'error', reason: '选错目标量的组成部分', count: 1, evidence,
        manifestations: [{ description: '用水平边替换竖直绳段', source_question_id: null, evidence }] },
      { kind: 'process', reason: '未写依据', count: 1, evidence },
      { kind: 'response_state', reason: '未作答', count: 1, evidence: [{ text: '空白', student_ids: [31] }] },
      { kind: 'carry_forward', reason: '前问错误结果延续', count: 1, evidence: carryEvidence,
        manifestations: [{ description: '沿用前问数值', source_question_id: 'Q12(P1)', evidence: carryEvidence }] },
      { kind: 'review', reason: '书写要求待核对', count: 1, evidence },
    ]
    apiMock.getClassAnalysis.mockResolvedValue(result)
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(host.querySelector('[data-kind="error"]')).not.toBeNull())
    expect(host.querySelector<HTMLDetailsElement>('[data-kind="error"]')!.open).toBe(true)
    expect(host.querySelector<HTMLDetailsElement>('[data-kind="process"]')!.open).toBe(false)
    expect(host.querySelector('[data-kind="error"]')!.textContent).not.toContain('未作答')
    host.querySelector<HTMLElement>('[data-kind="error"] .class-analysis__cause-detail summary')!.click()
    await vi.waitFor(() => expect(
      host.querySelector('[data-kind="error"]')!.textContent,
    ).toContain('用水平边替换竖直绳段'))
    expect(host.querySelector('[data-kind="error"]')!.textContent).toContain('10+6=16')
    expect(host.querySelector('[data-kind="error"]')!.textContent).toContain('原始批语')
    // 非 carry_forward 错因的证据即使有 previous_answers 也不显示前问作答。
    expect(host.querySelector('[data-kind="error"]')!.textContent).not.toContain('前问保留的作答')
    host.querySelector<HTMLElement>('[data-kind="carry_forward"] .class-analysis__cause-detail summary')!.click()
    await vi.waitFor(() => expect(
      host.querySelector('[data-kind="carry_forward"]')!.textContent,
    ).toContain('延续自 Q12(P1)'))
    // 延续错因只显示来源小问的前问作答，其他小问的 previous_answers 不渲染。
    expect(host.querySelector('[data-kind="carry_forward"]')!.textContent).toContain('前问保留的作答')
    expect(host.querySelector('[data-kind="carry_forward"]')!.textContent).not.toContain('另一小问的作答')
    mounted.pop()!.unmount()
    host.remove()
    const reopened = await mountPanel()
    await vi.waitFor(() => expect(
      reopened.host.querySelector('[data-kind="error"]'),
    ).not.toBeNull())
    reopened.host.querySelector<HTMLElement>('[data-kind="error"] .class-analysis__cause-detail summary')!.click()
    await vi.waitFor(() => expect(reopened.host.textContent).toContain('用水平边替换竖直绳段'))
  })

  it('shows saved cause categories and evidence without software-version notices', async () => {
    const result = makeAnalysis({ cause_analysis: { status: 'partial', pending_questions: 1,
      total_questions: 2, failed_questions: 0, question_states: { current: 1, stale: 0, old_prompt: 1, failed: 0 },
      stale: false, generated_at: '2026-09-09T10:00:00', origin: 'model' } })
    const question = result.data!.questions[0]!
    question.causes_grouped = true
    question.causes = [
      { kind: 'error', category: '审题与条件', reason: '选错目标量', count: 2,
        pattern_status: 'existing',
        evidence: [{ text: '多加一段', student_ids: [31] }] },
      { kind: 'process', category: '过程与依据', reason: '未写依据', count: 1,
        evidence: [{ text: '缺依据', student_ids: [32] }] },
    ]
    apiMock.getClassAnalysis.mockResolvedValue(result)
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(host.querySelector('[data-testid="cause-category"]')).not.toBeNull())
    expect(host.querySelector('[data-kind="error"]')!.textContent).toContain('审题与条件')
    // 大类统计并入区块标题，不再渲染独立 chips 行。
    expect(host.querySelector('[data-kind="error"] > summary')!.textContent).toContain('审题与条件 1')
    expect(host.querySelector('[data-testid="cause-category-chips"]')).toBeNull()
    expect(host.querySelector('[data-kind="process"]')!.textContent).toContain('过程与依据')
    expect(host.textContent).not.toContain('旧版提示词')
    mounted.pop()!.unmount()
    host.remove()

    // 旧存正文仍按实际错因展示。
    const legacy = makeAnalysis({ cause_analysis: { status: 'partial', pending_questions: 1,
      total_questions: 2, failed_questions: 0, question_states: { current: 1, stale: 0, old_prompt: 1, failed: 0 },
      stale: false, generated_at: '2026-09-01T10:00:00', origin: 'model' } })
    const legacyQuestion = legacy.data!.questions[0]!
    legacyQuestion.causes_grouped = true
    legacyQuestion.state = 'current'
    legacyQuestion.causes = [
      { kind: 'error', reason: '旧版归并的错因', count: 1,
        evidence: [{ text: '旧证据', student_ids: [31] }] },
    ]
    apiMock.getClassAnalysis.mockResolvedValue(legacy)
    const second = await mountPanel()
    await vi.waitFor(() => expect(second.host.querySelector('[data-kind="error"]')).not.toBeNull())
    expect(second.host.textContent).not.toContain('旧版提示词')
    expect(second.host.querySelector('[data-kind="error"]')!.textContent).toContain('旧版归并的错因')
    expect(second.host.querySelector('[data-testid="cause-category"]')).toBeNull()
  })

  it('shows the stale notice for results generated from changed inputs', async () => {
    const result = makeAnalysis({ cause_analysis: { status: 'partial', pending_questions: 1,
      total_questions: 2, failed_questions: 0, question_states: { current: 1, stale: 1, old_prompt: 0, failed: 0 },
      stale: true, generated_at: '2026-09-09T10:00:00', origin: 'model' } })
    const question = result.data!.questions[0]!
    question.causes_grouped = true
    question.causes_by_step = false
    question.state = 'stale'
    question.causes = [
      { kind: 'error', category: '概念理解', reason: '按步骤整理前的错因', count: 1,
        evidence: [{ text: '旧证据', student_ids: [31] }] },
    ]
    apiMock.getClassAnalysis.mockResolvedValue(result)
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="cause-analysis-status"]'),
    ).not.toBeNull())
    expect(host.querySelector('[data-testid="cause-analysis-status"]')!.textContent)
      .toContain('1 题作答或批语已变化，显示上次整理结果')
    // 过期结果照常按生成时的证据展示。
    expect(host.querySelector('[data-kind="error"]')!.textContent).toContain('按步骤整理前的错因')
    await vi.waitFor(() => expect(host.querySelector('[data-testid="cause-state"]')).not.toBeNull())
    expect(host.querySelector('[data-testid="cause-state"]')!.textContent).toContain('作答或批语已变化')
  })

  it('opens source evidence on demand without any generation entry', async () => {
    const result = makeAnalysis({ cause_analysis: { status: 'partial', pending_questions: 1, total_questions: 2,
      failed_questions: 0, question_states: { current: 1, stale: 0, old_prompt: 0, failed: 0 },
      stale: false, generated_at: null, origin: 'assistant' } })
    const question = result.data!.questions[0]!
    question.records[0]!.student_id = 31
    question.causes_grouped = true
    question.causes = [{ reason: '未写明直角条件就用勾股定理', count: 1,
      evidence: [{ text: '未明确推出 ADB 为直角，扣对应步骤分', student_ids: [31] }] }]
    question.cause_review = { positive: [{ text: '方程及求解正确', student_ids: [31] }], uncertain: [] }
    apiMock.getClassAnalysis.mockResolvedValue(result)
    const { host } = await mountPanel()
    const detail = host.querySelector<HTMLDetailsElement>('.class-analysis__cause-detail')!
    expect(detail.open).toBe(false)
    detail.querySelector('summary')!.click()
    expect(detail.open).toBe(true)
    await vi.waitFor(() => expect(detail.textContent).toContain('未明确推出 ADB 为直角'))
    expect(detail.textContent).toContain('钱肖白（0 分）')
    expect(host.querySelector('.class-analysis__causes')!.textContent).not.toContain('方程及求解正确')
    expect(host.querySelector('.class-analysis__cause-review')!.textContent).toContain('未归为错因的批语')
    expect(host.textContent).toContain('「AI 整理」')
  })

  it('shows diagnostics while AI generation is still running', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({ status: 'generating', active_job_id: 91 }))
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(host.textContent).toContain('满分'))
    expect(host.querySelector('.class-analysis')!.getAttribute('aria-label')).toBe('试题诊断')
    expect(host.querySelector('[data-testid="class-analysis-generating"]')).toBeNull()
  })

  it('selects the question row named by focusQuestion', async () => {
    const scrollSpy = vi.spyOn(Element.prototype, 'scrollIntoView')
      .mockImplementation(() => undefined)
    const { host } = await mountPanel(7, { focusQuestion: '12(3)' })
    await vi.waitFor(() => expect(
      host.querySelector('[role="option"][data-question-id="12(3)"]')?.getAttribute('aria-selected'),
    ).toBe('true'))
    expect(scrollSpy).toHaveBeenCalled()
  })

  it('renders a master-detail list, syncs selection to the question query and scopes the detail', async () => {
    const result = makeAnalysis()
    result.data!.questions = [
      {
        question_id: '2',
        max_score: 4,
        class_rate: 0.8,
        stem_summary: '高得分率题',
        canonical_answer: 'A',
        causes: [{ reason: '第二题错因', count: 1, kind: 'error' as const,
          evidence: [{ text: '第二题证据', student_ids: [31] }] }],
        records: [],
      },
      {
        question_id: '1',
        max_score: 8,
        class_rate: 0.25,
        stem_summary: '低得分率题',
        canonical_answer: 'B',
        causes: [{ reason: '第一题错因', count: 1, kind: 'error' as const,
          evidence: [{ text: '第一题证据', student_ids: [32] }] }],
        records: [],
      },
    ]
    apiMock.getClassAnalysis.mockResolvedValue(result)
    const { host, router } = await mountPanel()
    await vi.waitFor(() => expect(host.querySelectorAll('[role="option"]').length).toBe(2))

    // 默认按得分率升序，选中第一行，详情只显示选中题的错因。
    const options = [...host.querySelectorAll<HTMLElement>('[role="option"]')]
    expect(options[0]!.dataset.questionId).toBe('1')
    expect(options[0]!.getAttribute('aria-selected')).toBe('true')
    expect(host.textContent).toContain('第一题错因')
    expect(host.querySelector('.class-analysis__detail')!.textContent).not.toContain('第二题错因')

    // 点击另一行 → question 查询参数同步，详情切换。
    options[1]!.click()
    await vi.waitFor(() => expect(router.currentRoute.value.query.question).toBe('2'))
    await vi.waitFor(() => expect(
      host.querySelector('.class-analysis__detail')!.textContent,
    ).toContain('第二题错因'))
    expect(host.querySelector('.class-analysis__detail')!.textContent).not.toContain('第一题错因')

    // 按题号排序 + 列表内方向键移动选择。
    host.querySelector<HTMLButtonElement>('[data-testid="sort-number"]')!.click()
    await settle()
    const reordered = [...host.querySelectorAll<HTMLElement>('[role="option"]')]
    expect(reordered[0]!.dataset.questionId).toBe('1')
    reordered[0]!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    await vi.waitFor(() => expect(router.currentRoute.value.query.question).toBe('2'))
  })

  it('opens a失分 chip student in the review deep page', async () => {
    const result = makeAnalysis()
    const question = result.data!.questions[0]!
    question.records[0]!.student_id = 31
    apiMock.getClassAnalysis.mockResolvedValue(result)
    const { host, router } = await mountPanel()
    await vi.waitFor(() => expect(host.querySelector('.class-analysis__score-band-toggle:not(:disabled)')).not.toBeNull())

    const { useResultsCenterStore } = await import('../stores/results-center')
    useResultsCenterStore().results = {
      session_id: 7,
      session_name: '数学阶段测试',
      summary: {},
      questions: [],
      students: [{
        student_id: 31,
        student_code: 'S31',
        student_name: '钱肖白',
        class_name: '9',
        pinyin_initials: 'QXB',
        pinyin_full: 'qianxiaobai',
        current_score: 90,
        max_score: 100,
        ungraded_count: 0,
        failed_count: 0,
        needs_review_count: 0,
        status: 'complete',
        items: [{
          review_item_id: '7:12(3):31',
          question_id: '12(3)',
          score_awarded: 0,
          max_score: 8,
          score_status: 'scored',
          score_source: 'teacher',
          confidence_score: null,
          needs_review: false,
          review_reason: null,
          result_id: 1,
          detail_id: 2,
        }],
      }],
    } as never
    await settle()

    host.querySelector<HTMLButtonElement>('.class-analysis__score-band-toggle:not(:disabled)')!.click()
    await settle()
    const chip = host.querySelector<HTMLElement>('button.class-analysis__score-chip')
    expect(chip).not.toBeNull()
    chip!.click()
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/grading'))
    expect(router.currentRoute.value.query).toMatchObject({
      session: '7', scope: 'all', question: '12(3)',
      item: '7:12(3):31', student: '31', entry: 'results',
    })
    expect(useResultsCenterStore().reviewNavigation?.studentIds).toEqual([31])
  })

  it('loads the chosen class without keeping the previous class data visible', async () => {
    const first = makeAnalysis({ class_names: ['9', '10'], selected_class: '9' })
    let finish: (value: ClassAnalysisResponse) => void = () => undefined
    apiMock.getClassAnalysis.mockResolvedValueOnce(first).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(host.querySelector('select')).not.toBeNull())
    const select = host.querySelector<HTMLSelectElement>('select')!
    select.value = '10'
    select.dispatchEvent(new Event('change'))
    await settle()
    expect(apiMock.getClassAnalysis).toHaveBeenLastCalledWith(7, expect.any(AbortSignal), '10', 'summary')
    expect(host.textContent).toContain('正在读取班级分析')
    expect(host.querySelector('#class-analysis-overview-title')).toBeNull()
    finish(makeAnalysis({ ...first, selected_class: '10' }))
    await vi.waitFor(() => expect(host.querySelector<HTMLSelectElement>('select')!.value).toBe('10'))
    apiMock.getClassAnalysis.mockResolvedValueOnce(makeAnalysis({ class_names: ['9', '10'], selected_class: null }))
    select.value = ''
    select.dispatchEvent(new Event('change'))
    await settle()
    expect(apiMock.getClassAnalysis).toHaveBeenLastCalledWith(7, expect.any(AbortSignal), '', 'summary')
    expect(host.textContent).toContain('全部班级')
  })

  it('shows the empty state when the session has no graded results', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      status: 'no_data',
      data: null,
      generated_at: null,
    }))

    const { host } = await mountPanel()

    expect(host.textContent).toContain('当前范围尚无已批改成绩')
  })

  it('shows the generating state and reloads when the tracked job finishes', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      status: 'generating',
      data: null,
      active_job_id: 91,
    }))

    const { host } = await mountPanel()
    await vi.waitFor(() => expect(jobsMock.getJob).toHaveBeenCalledWith(91))

    expect(host.textContent).toContain('班级分析生成中')

    useJobStore().track(makeJob({
      status: 'succeeded',
      progress: 1,
      updated_at: '2026-08-30T10:02:00Z',
      finished_at: '2026-08-30T10:02:00Z',
    }))

    await vi.waitFor(() => expect(apiMock.getClassAnalysis).toHaveBeenCalledTimes(2))
  })

  it('keeps only statistics and diagnostics on the page; the AI report entry lives on the overview', async () => {
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(host.textContent).toContain('满分'))
    expect(apiMock.getClassAnalysis).toHaveBeenCalledTimes(1)
    expect(apiMock.getClassAnalysis).toHaveBeenCalledWith(7, expect.any(AbortSignal), '', 'summary')
    expect(host.querySelector('.class-analysis__score-list')).toBeNull()
    host.querySelector<HTMLButtonElement>('.class-analysis__score-band-toggle:not(:disabled)')!.click()
    await settle()
    expect(host.querySelector('.class-analysis__score-list')?.textContent).toContain('钱肖白0 分')
    expect(host.querySelector('.class-analysis__causes')?.textContent).toContain('未作答1 人')
    expect(host.textContent).not.toContain('逐人失分点')
    expect(host.textContent).not.toContain('陈维懋')
    expect(host.textContent).not.toContain('两极分化严重')
    expect(host.querySelector('[data-testid="ai-analysis-open"]')).toBeNull()
  })

  it('points to the unified pipeline instead of offering a local generate action', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      cause_analysis: { status: 'partial', pending_questions: 1, total_questions: 2,
        failed_questions: 0, question_states: { current: 1, stale: 0, old_prompt: 0, failed: 0 },
        stale: false, generated_at: null, origin: 'assistant' },
    }))
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(host.textContent).toContain('满分'))

    expect(host.querySelector('[data-testid="cause-analysis-status"]')?.textContent)
      .toContain('点页面顶部「AI 整理」')
    expect(host.querySelector('[data-testid="group-causes-open"]')).toBeNull()
    expect(host.querySelector('[data-testid="regenerate-confirm"]')).toBeNull()
  })

  it('edits a cause name and category via the optional 修改 dialog', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      cause_analysis: { status: 'ready', pending_questions: 0, total_questions: 1,
        failed_questions: 0, question_states: { current: 1, stale: 0, old_prompt: 0, failed: 0 },
        stale: false, generated_at: '2026-08-30T10:00:00Z', origin: 'assistant' },
      data: {
        ...makeAnalysis().data!,
        questions: [{
          question_id: '1',
          max_score: 4,
          class_rate: 0.5,
          stem_summary: '轴对称图形识别',
          canonical_answer: 'B',
          bank_question_id: 101,
          cause_category_counts: [
            { category: '概念理解', count: 2 },
            { category: '审题与条件', count: 1 },
          ],
          causes: [{
            reason: '误认梯形为轴对称',
            count: 1,
            kind: 'error',
            category: '概念理解',
            evidence: [{ text: '识别为C', student_ids: [1], student_answer: 'C' }],
            manifestations: [{ description: '选了 C', source_question_id: null,
              evidence: [{ text: '识别为C', student_ids: [1], student_answer: 'C' }] }],
          }],
          records: [{
            student_id: 1, student_name: '钱肖白', score: 0,
            deduction_reason: '识别为C', error_category: '答错', error_summary: null,
          }],
        }],
      },
    }))
    apiMock.editCausePattern.mockResolvedValue({ ok: true })
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(host.textContent).toContain('满分'))

    // 大类统计并入区块标题（按该节证据人数）。
    expect(host.querySelector('[data-kind="error"] > summary')!.textContent)
      .toContain('（概念理解 1）')
    expect(host.querySelector('[data-testid="cause-category-chips"]')).toBeNull()
    // 自动归并结果不再有“写入题库/已入库”流程。
    expect(host.textContent).not.toContain('写入题库')
    expect(host.textContent).not.toContain('已入库')

    const trigger = host.querySelector<HTMLButtonElement>('[data-testid="cause-edit"]')
    expect(trigger).not.toBeNull()
    trigger!.click()
    await vi.waitFor(() => expect(
      document.body.querySelector('[data-testid="cause-edit-dialog"]'),
    ).not.toBeNull())
    const nameInput = document.body.querySelector<HTMLInputElement>(
      '[data-testid="cause-edit-reason"]',
    )!
    expect(nameInput.value).toBe('误认梯形为轴对称')
    nameInput.value = '误认等腰梯形'
    nameInput.dispatchEvent(new Event('input'))
    const categorySelect = document.body.querySelector<HTMLSelectElement>(
      '[data-testid="cause-edit-category"]',
    )!
    const options = [...categorySelect.options].map((option) => option.value)
    expect(options).toEqual(['概念理解', '计算与化简', '审题与条件', '方法与思路'])
    categorySelect.value = '审题与条件'
    categorySelect.dispatchEvent(new Event('change'))

    // 提交成功后页面重新加载；先把下一次 GET 改成“老师改过”结果。
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      data: {
        ...makeAnalysis().data!,
        questions: [{
          question_id: '1',
          max_score: 4,
          class_rate: 0.5,
          stem_summary: '轴对称图形识别',
          canonical_answer: 'B',
          bank_question_id: 101,
          causes: [{ reason: '误认等腰梯形', count: 1, kind: 'error' as const,
            category: '审题与条件', teacher_edited: true }],
          records: [],
        }],
      },
    }))
    document.body.querySelector<HTMLButtonElement>('[data-testid="cause-edit-submit"]')!.click()
    await vi.waitFor(() => expect(apiMock.editCausePattern).toHaveBeenCalledTimes(1))
    const [sessionId, payload] = apiMock.editCausePattern.mock.calls[0]!
    expect(sessionId).toBe(7)
    expect(payload).toMatchObject({
      question_id: '1',
      kind: 'error',
      category: '审题与条件',
      reason: '误认梯形为轴对称',
      new_reason: '误认等腰梯形',
    })
    expect(payload.operation_token).toBeTruthy()
    await vi.waitFor(() => expect(
      document.body.querySelector('[data-testid="cause-edit-dialog"]'),
    ).toBeNull())
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="cause-teacher-edited"]'),
    ).not.toBeNull())
    expect(host.querySelector('[data-testid="cause-teacher-edited"]')!.textContent)
      .toContain('老师改过')
    expect(host.textContent).toContain('误认等腰梯形')
  })
  it('opens key step hotspots and returns to the existing question diagnosis', async () => {
    const result = makeAnalysis()
    const data = result.data
    if (!data) throw new Error('Missing synthetic analysis')
    data.step_diagnosis = { students: 4, confirmed_ai_rows: 2, excluded_teacher_total_only: 1, hotspots: [{
      key: 'sk_test', name: '列等量关系', independent: 2, observed: 4, carried: 1, blank: 1, uncertain: 0,
      confirmed_ai: 1, questions: ['12(3)'], causes_status: 'ready',
      causes: [{ reason: '漏列条件', category: '方法与思路', count: 2, questions: ['12(3)'], examples: ['TEST-没有列出等式'] }],
      patterns: [{ pattern: '合成题库错法', actual: 0, question_id: 1 }],
    }] }
    const question = data.questions[0]
    if (!question) throw new Error('Missing synthetic question')
    question.key_steps = [{ step_id: 'S2', part_id: '12(3)', goal: '建立等量关系', score: 2,
      key_steps: [{ key: 'sk_test', name: '列等量关系' }], counts: { independent: 2, carried: 1, blank: 1 } }]
    apiMock.getClassAnalysis.mockResolvedValue(result)
    const { host, router } = await mountPanel()
    const sort = host.querySelector<HTMLButtonElement>('[data-testid="sort-step"]')
    if (!sort) throw new Error('Missing key step sort')
    sort.click()
    await settle()
    expect(host.querySelector('[data-testid="key-step-detail"]')?.textContent).toContain('独立失分 2 / 4 人')
    expect(host.textContent).toContain('漏列条件')
    expect(host.textContent).toContain('教师确认总分，步骤为 AI 判断')
    const row = host.querySelector<HTMLButtonElement>('.key-step__question')
    if (!row) throw new Error('Missing question link')
    row.click()
    await settle()
    await vi.waitFor(() => expect(router.currentRoute.value.query.question).toBe('12(3)'))
    expect(host.querySelector('[data-testid="key-step-detail"]')).toBeNull()
    expect(host.textContent).toContain('满分 8 分')
  })

  it('shows saved causes without step classification as unclassified', async () => {
    const result = makeAnalysis()
    if (!result.data) throw new Error('Missing synthetic analysis')
    result.data.step_diagnosis = { students: 4, confirmed_ai_rows: 0, excluded_teacher_total_only: 0, hotspots: [{
      key: 'sk_test', name: '确认直角与斜边', independent: 2, observed: 4, carried: 0, blank: 0,
      uncertain: 0, confirmed_ai: 0, questions: ['12(3)'], causes_status: 'unclassified', causes: [], patterns: [],
    }] }
    apiMock.getClassAnalysis.mockResolvedValue(result)
    const { host } = await mountPanel()
    host.querySelector<HTMLButtonElement>('[data-testid="sort-step"]')?.click()
    await settle()
    expect(host.querySelector('[data-testid="key-step-detail"]')?.textContent).toContain('未按步骤归类')
  })

})
