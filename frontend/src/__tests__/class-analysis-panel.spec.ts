import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
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

async function mountPanel(sessionId: number | null = 7) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(ClassAnalysisPanel, { sessionId })
  app.use(pinia)
  app.mount(host)
  mounted.push(app)
  await settle()
  return { host }
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
      teaching_prep: { profile_name: '默认内容服务', model: 'qwen-plus' },
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
  it('shows the empty state when the session has no graded results', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      status: 'no_data',
      data: null,
      narrative: null,
      generated_at: null,
    }))

    const { host } = await mountPanel()

    expect(host.textContent).toContain('本场次尚无已批改成绩')
    expect(host.querySelector('[data-testid="auto-generate-toggle"]')).not.toBeNull()
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

  it('renders ready data and maps narrative aliases back to student names', async () => {
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(host.textContent).toContain('数学阶段测试'))

    expect(host.textContent).toContain('平均分')
    expect(host.textContent).toContain('及格率')
    expect(host.textContent).toContain('12(3)')
    expect(host.textContent).toContain('钱肖白：未作答')
    expect(host.textContent).toContain('陈维懋')
    expect(host.textContent).toContain('两极分化严重')
    expect(host.textContent).toContain('用第 12 题整题做板书示范')
    expect(host.textContent).toContain('高分组布置压轴变式')
    expect(host.textContent).toContain('AI 分析 · 仅供参考')
    expect(host.textContent).toContain('含学生姓名，请勿直接外发')
    // S1 映射回 data.students 第一位学生姓名，不直接暴露别名。
    expect(host.textContent).not.toContain('S1')
    expect(host.textContent).toContain('保持优势')
  })

  it('shows the stale banner and the small-sample caution', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      stale: true,
      small_sample: true,
    }))

    const { host } = await mountPanel()
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="stale-banner"]'),
    ).not.toBeNull())

    expect(host.textContent).toContain('成绩已更新，分析可能不是最新')
    expect(host.querySelector('[data-testid="small-sample-note"]')).not.toBeNull()
  })

  it('keeps data sections and degrades the AI block when the narrative failed', async () => {
    apiMock.getClassAnalysis.mockResolvedValue(makeAnalysis({
      narrative: null,
      narrative_failed: true,
    }))

    const { host } = await mountPanel()
    await vi.waitFor(() => expect(host.textContent).toContain('数学阶段测试'))

    expect(host.textContent).toContain('陈维懋')
    expect(host.querySelector('[data-testid="narrative-missing"]')?.textContent)
      .toContain('AI 分析生成失败，可重新生成')
    expect(host.querySelector('[data-testid="narrative-regenerate"]')).not.toBeNull()
  })

  it('persists the auto-generate toggle immediately and rolls back on failure', async () => {
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="auto-generate-toggle"]'),
    ).not.toBeNull())

    const toggle = host.querySelector<HTMLInputElement>(
      '[data-testid="auto-generate-toggle"]',
    )!
    expect(toggle.checked).toBe(true)

    toggle.click()
    await vi.waitFor(() => expect(apiMock.updateSettings).toHaveBeenCalledWith(7, false))
    // 等第一次保存落库、绑定值刷新后再触发第二次，避免与进行中状态竞争。
    await vi.waitFor(() => expect(toggle.checked).toBe(false))

    apiMock.updateSettings.mockRejectedValueOnce(new Error('save failed'))
    toggle.click()
    await vi.waitFor(() => expect(host.textContent).toContain('自动生成设置未能保存'))
    expect(toggle.checked).toBe(false)
  })

  it('requires confirmation before regenerating and tracks the new job', async () => {
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(host.textContent).toContain('数学阶段测试'))

    host.querySelector<HTMLButtonElement>('[data-testid="regenerate-open"]')!.click()
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="regenerate-confirm-dialog"]'),
    ).not.toBeNull())
    await vi.waitFor(() => expect(modelProfilesMock.getState).toHaveBeenCalled())

    expect(host.textContent).toContain('默认内容服务')
    expect(host.textContent).toContain('qwen-plus')
    expect(apiMock.regenerate).not.toHaveBeenCalled()

    host.querySelector<HTMLButtonElement>('[data-testid="regenerate-confirm"]')!.click()
    await vi.waitFor(() => expect(apiMock.regenerate).toHaveBeenCalledWith(7))
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
        teaching_prep: { profile_name: '默认内容服务', model: 'qwen-plus' },
        class_teacher: { profile_name: '默认内容服务', model: 'qwen-plus' },
      },
    })
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(host.textContent).toContain('数学阶段测试'))

    host.querySelector<HTMLButtonElement>('[data-testid="regenerate-open"]')!.click()
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
    const { host } = await mountPanel()
    await vi.waitFor(() => expect(host.textContent).toContain('数学阶段测试'))

    host.querySelector<HTMLButtonElement>('[data-testid="regenerate-open"]')!.click()
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="regenerate-confirm"]'),
    ).not.toBeNull())
    host.querySelector<HTMLButtonElement>('[data-testid="regenerate-confirm"]')!.click()

    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="regenerate-error"]')?.textContent,
    ).toContain('未配置内容生成模型'))
    expect(apiMock.regenerate).toHaveBeenCalledTimes(1)
  })
})
