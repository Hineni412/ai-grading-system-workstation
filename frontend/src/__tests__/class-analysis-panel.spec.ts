import { createApp, h, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ClassAnalysisResponse,
} from '../api/class-analysis'
import { ApiError } from '../api/errors'
import type { JobResponse } from '../api/jobs'
import { useJobStore } from '../stores/jobs'
import ClassAnalysisPanel from '../components/results-center/ClassAnalysisPanel.vue'

const apiMock = vi.hoisted(() => ({
  getClassAnalysis: vi.fn(),
  updateSettings: vi.fn(),
  regenerate: vi.fn(),
  getQuestionPreview: vi.fn(),
  editCausePattern: vi.fn(),
}))

const modelProfilesMock = vi.hoisted(() => ({
  getState: vi.fn(),
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

vi.mock('../api/model-profiles', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/model-profiles')>(),
  modelProfilesApi: modelProfilesMock,
}))

vi.mock('../api/jobs', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/jobs')>(),
  jobApi: jobsMock,
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
    narrative: {
      key_findings: [
        { title: '两极分化严重', detail: '出现 47 分断层', severity: 'high' },
      ],
      common_issues: [
        {
          title: '证明题书写能力断层',
          evidence: '第 11(3)、12(2)(3) 问得分率 25%',
          teaching_action: '用第 12 题整题做板书示范',
        },
      ],
      student_notes: [
        {
          alias: 'S1',
          note: '会做的题完成质量高',
          suggestion: '压轴小问不要轻易放弃',
          flags: ['保持优势'],
        },
      ],
      grouping_advice: '高分组布置压轴变式，低分组重做基础题。',
    },
    narrative_failed: false,
    generated_at: '2026-08-30T10:00:00Z',
    stale: false,
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
      { path: '/class-report', component: { render: () => h('div', '合成班级报告页') } },
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
  apiMock.updateSettings.mockImplementation(
    async (_id: number, autoGenerate: boolean) => ({ auto_generate: autoGenerate }),
  )
  apiMock.regenerate.mockResolvedValue(makeJob({ id: 92, status: 'queued', progress: 0 }))
  jobsMock.getJob.mockResolvedValue(makeJob())
  jobsMock.getJobStatusBatch.mockResolvedValue([
    { id: 91, found: true, job: makeJob() },
  ])
  modelProfilesMock.getState.mockResolvedValue({
    profiles: [],
    active_profile_name: null,
    active_profile: null,
    task_bindings: {
      content_generation: { profile_name: '默认内容服务', model: 'qwen-plus' },
      grading: { profile_name: '默认内容服务', model: 'qwen-plus' },
      class_teacher: { profile_name: '默认内容服务', model: 'qwen-plus' },
    },
  })
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
      failed_questions: 0, legacy_questions: 0, stale: false, generated_at: '2026-09-09T10:00:00', origin: 'assistant' } })
    const question = result.data!.questions[0]!
    question.records[0]!.student_id = 31
    const evidence = [{ text: '绳长求和有误', student_ids: [31], student_answer: '10+6=16',
      evidence_steps: ['已算出斜段10'], previous_answers: [{ question_id: 'Q12(P1)', student_answer: '前问保留的作答', text: '前问批语', evidence_steps: [] }] }]
    question.causes_grouped = true
    question.causes = [
      { kind: 'error', reason: '选错目标量的组成部分', count: 1, evidence,
        manifestations: [{ description: '用水平边替换竖直绳段', source_question_id: null, evidence }] },
      { kind: 'process', reason: '未写依据', count: 1, evidence },
      { kind: 'response_state', reason: '未作答', count: 1, evidence: [{ text: '空白', student_ids: [31] }] },
      { kind: 'carry_forward', reason: '前问错误结果延续', count: 1, evidence,
        manifestations: [{ description: '沿用前问数值', source_question_id: 'Q12(P1)', evidence }] },
      { kind: 'review', reason: '书写要求待核对', count: 1, evidence },
    ]
    apiMock.getClassAnalysis.mockResolvedValue(result)
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(host.querySelector('[data-kind="error"]')).not.toBeNull())
    expect(host.querySelector<HTMLDetailsElement>('[data-kind="error"]')!.open).toBe(true)
    expect(host.querySelector<HTMLDetailsElement>('[data-kind="process"]')!.open).toBe(false)
    expect(host.querySelector('[data-kind="error"]')!.textContent).not.toContain('未作答')
    host.querySelector<HTMLElement>('[data-kind="error"] .class-analysis__cause-detail summary')!.click()
    expect(host.querySelector('[data-kind="error"]')!.textContent).toContain('用水平边替换竖直绳段')
    expect(host.querySelector('[data-kind="error"]')!.textContent).toContain('10+6=16')
    expect(host.querySelector('[data-kind="error"]')!.textContent).toContain('原始批语')
    expect(host.querySelector('[data-kind="carry_forward"]')!.textContent).toContain('延续自 Q12(P1)')
    mounted.pop()!.unmount()
    host.remove()
    const reopened = await mountPanel()
    await vi.waitFor(() => expect(reopened.host.textContent).toContain('用水平边替换竖直绳段'))
    expect(apiMock.regenerate).not.toHaveBeenCalled()
    expect(reopened.host.querySelector<HTMLButtonElement>('[data-testid="group-causes-open"]')!.disabled).toBe(true)
  })

  it('shows top-level cause categories and marks outdated v2 results for upgrade', async () => {
    const result = makeAnalysis({ cause_analysis: { status: 'partial', pending_questions: 1,
      total_questions: 2, failed_questions: 0, outdated_questions: 1,
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
    expect(host.querySelector('[data-kind="process"]')!.textContent).toContain('过程与依据')
    expect(host.textContent).toContain('1 题为旧版整理，暂无错误大类')
    mounted.pop()!.unmount()
    host.remove()

    // v2 旧版结果：仍按 kind 展示但没有大类，题级出现「待升级」标记。
    const legacy = makeAnalysis({ cause_analysis: { status: 'partial', pending_questions: 1,
      total_questions: 2, failed_questions: 0, outdated_questions: 1,
      stale: false, generated_at: '2026-09-01T10:00:00', origin: 'model' } })
    const legacyQuestion = legacy.data!.questions[0]!
    legacyQuestion.causes_grouped = true
    legacyQuestion.causes_outdated = true
    legacyQuestion.causes = [
      { kind: 'error', reason: '旧版归并的错因', count: 1,
        evidence: [{ text: '旧证据', student_ids: [31] }] },
    ]
    apiMock.getClassAnalysis.mockResolvedValue(legacy)
    const second = await mountPanel()
    await vi.waitFor(() => expect(second.host.querySelector('[data-testid="causes-outdated"]')).not.toBeNull())
    expect(second.host.querySelector('[data-testid="causes-outdated"]')!.textContent).toContain('待升级')
    expect(second.host.querySelector('[data-kind="error"]')!.textContent).toContain('旧版归并的错因')
    expect(second.host.querySelector('[data-testid="cause-category"]')).toBeNull()
  })

  it('opens source evidence and only submits cause grouping after explicit confirmation', async () => {
    const result = makeAnalysis({ cause_analysis: { status: 'partial', pending_questions: 1, total_questions: 2,
      failed_questions: 0, stale: false, generated_at: null, origin: 'assistant' } })
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
    await settle()
    expect(detail.open).toBe(true)
    expect(detail.textContent).toContain('未明确推出 ADB 为直角')
    expect(detail.textContent).toContain('钱肖白（0 分）')
    expect(host.querySelector('.class-analysis__causes')!.textContent).not.toContain('方程及求解正确')
    expect(host.querySelector('.class-analysis__cause-review')!.textContent).toContain('未归为错因的批语')
    expect(apiMock.regenerate).not.toHaveBeenCalled()
    host.querySelector<HTMLButtonElement>('[data-testid="group-causes-open"]')!.click()
    await settle()
    expect(host.textContent).toContain('各班共用逐题归并结果')
    expect(apiMock.regenerate).not.toHaveBeenCalled()
    host.querySelector<HTMLButtonElement>('[data-testid="regenerate-confirm"]')!.click()
    await vi.waitFor(() => expect(apiMock.regenerate).toHaveBeenCalledWith(7, undefined, 'causes'))
  })

  it('shows diagnostics while AI generation is still running', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({ status: 'generating', active_job_id: 91 }))
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(host.textContent).toContain('数学阶段测试'))
    expect(host.textContent).toContain('试题诊断')
    expect(host.querySelector('[data-testid="class-analysis-generating"]')).toBeNull()
  })

  it('scrolls to and highlights the question row named by focusQuestion', async () => {
    const scrollSpy = vi.spyOn(Element.prototype, 'scrollIntoView')
      .mockImplementation(() => undefined)
    const { host } = await mountPanel(7, { focusQuestion: '12(3)' })
    await vi.waitFor(() => expect(
      host.querySelector('tr.is-focused[data-question-id="12(3)"]'),
    ).not.toBeNull())
    expect(scrollSpy).toHaveBeenCalled()
  })

  it('loads original questions only when opened and reuses the preview', async () => {
    apiMock.getQuestionPreview.mockResolvedValue({
      question_id: '12(3)', parent_question_id: 'Q12', text: '完整原题与全部条件', notice: '',
      rich_content: { available: false, question_blocks: [], answer_blocks: [], question_block_count: 0, answer_block_count: 0 },
    })
    const { host } = await mountPanel()
    expect(apiMock.getQuestionPreview).not.toHaveBeenCalled()
    host.querySelector<HTMLButtonElement>('.class-analysis__preview-button')!.click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('完整原题与全部条件'))
    expect(apiMock.getQuestionPreview).toHaveBeenCalledWith(7, '12(3)', expect.any(AbortSignal))
    document.querySelector<HTMLButtonElement>('[aria-label="关闭原题预览"]')!.click()
    await settle()
    host.querySelector<HTMLButtonElement>('.class-analysis__preview-button')!.click()
    await vi.waitFor(() => expect(document.body.textContent).toContain('完整原题与全部条件'))
    expect(apiMock.getQuestionPreview).toHaveBeenCalledTimes(1)
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
    expect(host.textContent).toContain('全部班级合并')
  })

  it('shows the empty state when the session has no graded results', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      status: 'no_data',
      data: null,
      narrative: null,
      generated_at: null,
    }))

    const { host } = await mountPanel()

    expect(host.textContent).toContain('当前范围尚无已批改成绩')
  })

  it('shows the generating state and reloads when the tracked job finishes', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      status: 'generating',
      data: null,
      narrative: null,
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

  it('keeps only statistics and diagnostics on the page and navigates to the report for AI analysis', async () => {
    const { host, router } = await mountPanel()
    await vi.waitFor(() => expect(host.textContent).toContain('数学阶段测试'))
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
    expect(apiMock.regenerate).not.toHaveBeenCalled()

    host.querySelector<HTMLButtonElement>('[data-testid="ai-analysis-open"]')!.click()
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/class-report'))
    expect(router.currentRoute.value.query).toMatchObject({ session: '7' })
  })

  it('requires confirmation before submitting cause grouping and tracks the new job', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      cause_analysis: { status: 'partial', pending_questions: 1, total_questions: 2,
        failed_questions: 0, stale: false, generated_at: null, origin: 'assistant' },
    }))
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(host.textContent).toContain('数学阶段测试'))

    host.querySelector<HTMLButtonElement>('[data-testid="group-causes-open"]')!.click()
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="regenerate-confirm-dialog"]'),
    ).not.toBeNull())
    await vi.waitFor(() => expect(modelProfilesMock.getState).toHaveBeenCalled())

    expect(host.textContent).toContain('默认内容服务')
    expect(host.textContent).toContain('qwen-plus')
    expect(apiMock.regenerate).not.toHaveBeenCalled()

    host.querySelector<HTMLButtonElement>('[data-testid="regenerate-confirm"]')!.click()
    await vi.waitFor(() => expect(apiMock.regenerate).toHaveBeenCalledWith(7, undefined, 'causes'))
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="regenerate-confirm-dialog"]'),
    ).toBeNull())
    expect(useJobStore().jobs[92]).toBeDefined()
  })

  it('blocks confirmation when no content generation model is configured', async () => {
    modelProfilesMock.getState.mockResolvedValue({
      profiles: [],
      active_profile_name: null,
      active_profile: null,
      task_bindings: {
        content_generation: { profile_name: null, model: '' },
        grading: { profile_name: '默认内容服务', model: 'qwen-plus' },
        class_teacher: { profile_name: '默认内容服务', model: 'qwen-plus' },
      },
    })
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      cause_analysis: { status: 'partial', pending_questions: 1, total_questions: 2,
        failed_questions: 0, stale: false, generated_at: null, origin: 'assistant' },
    }))
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(host.textContent).toContain('数学阶段测试'))

    host.querySelector<HTMLButtonElement>('[data-testid="group-causes-open"]')!.click()
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="regenerate-not-configured"]'),
    ).not.toBeNull())

    const confirm = host.querySelector<HTMLButtonElement>(
      '[data-testid="regenerate-confirm"]',
    )!
    expect(confirm.disabled).toBe(true)
    confirm.click()
    await settle()
    expect(apiMock.regenerate).not.toHaveBeenCalled()
  })

  it('explains the missing model configuration on a 422 regenerate response', async () => {
    apiMock.regenerate.mockRejectedValue(new ApiError({
      kind: 'validation',
      status: 422,
      code: 'content_generation_model_not_configured',
      message: '内容生成模型未配置',
      details: {},
      requestId: 'req-1',
      retryable: false,
    }))
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      cause_analysis: { status: 'partial', pending_questions: 1, total_questions: 2,
        failed_questions: 0, stale: false, generated_at: null, origin: 'assistant' },
    }))
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(host.textContent).toContain('数学阶段测试'))

    host.querySelector<HTMLButtonElement>('[data-testid="group-causes-open"]')!.click()
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="regenerate-confirm"]'),
    ).not.toBeNull())
    host.querySelector<HTMLButtonElement>('[data-testid="regenerate-confirm"]')!.click()

    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="regenerate-error"]')?.textContent,
    ).toContain('未配置内容生成模型'))
    expect(apiMock.regenerate).toHaveBeenCalledTimes(1)
  })

  it('edits a cause name and category via the optional 修改 dialog', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      cause_analysis: { status: 'ready', pending_questions: 0, total_questions: 1,
        failed_questions: 0, stale: false, generated_at: '2026-08-30T10:00:00Z', origin: 'assistant' },
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
    await vi.waitFor(() => expect(host.textContent).toContain('数学阶段测试'))

    // 大类统计行按人数渲染。
    expect(host.querySelector('[data-testid="cause-category-chips"]')!.textContent)
      .toContain('概念理解 2人')
    expect(host.querySelector('[data-testid="cause-category-chips"]')!.textContent)
      .toContain('审题与条件 1人')
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
})
