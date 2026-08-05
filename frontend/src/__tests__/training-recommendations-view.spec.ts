import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { TrainingDiagnosis, TrainingPlanResponse } from '../api/training'
import { createAppRouter } from '../router'
import { useJobStore } from '../stores/jobs'
import { useSessionStore } from '../stores/session'
import TrainingRecommendationsView from '../views/TrainingRecommendationsView.vue'

const trainingApiMock = vi.hoisted(() => ({
  diagnose: vi.fn(),
  preview: vi.fn(),
  confirm: vi.fn(),
}))

const exportsApiMock = vi.hoisted(() => ({
  listTrainingTasks: vi.fn(),
  getTrainingTask: vi.fn(),
  submitTrainingExport: vi.fn(),
  retryTrainingExport: vi.fn(),
  downloadJobFile: vi.fn(),
}))

const fetchStudentsMock = vi.hoisted(() => vi.fn())

vi.mock('../api/training', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/training')>(),
  trainingApi: trainingApiMock,
}))

vi.mock('../api/exports', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/exports')>(),
  exportsApi: exportsApiMock,
}))

vi.mock('../api/students', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/students')>(),
  fetchStudents: fetchStudentsMock,
}))

const diagnosis = {
  scope: { mode: 'student', student_ids: ['12'] },
  exam_scope: {
    mode: 'current',
    session_ids: [7],
    sessions: [{ session_id: 7, session_name: '匿名阶段测验' }],
  },
  students: [{
    student_id: '12',
    student_code: 'S012',
    student_name: '匿名学生甲',
    class_id: '七年级一班',
    score_rate: 55,
    weak_points: [{
      knowledge_key: 'knowledge_point:三角形全等',
      knowledge_point: '三角形全等',
      mastery: 0.55,
      score_sum: 11,
      full_score_sum: 20,
      deduction_count: 2,
      evidence_count: 1,
      exam_count: 1,
      source_question_refs: [{
        session_id: 7,
        session_name: '匿名阶段测验',
        question_id: 'Q1',
        bank_question_id: 101,
        score_awarded: 6,
        full_score: 10,
        score_rate: 0.6,
      }],
      actionable_reasons: ['证明步骤缺少依据'],
      tag_context: { knowledge_point: ['三角形全等'] },
      error_counts: { primary: { 逻辑断裂: 1 } },
    }],
  }],
  group_weak_points: [{
    knowledge_key: 'knowledge_point:三角形全等',
    knowledge_point: '三角形全等',
    mastery: 0.55,
    score_sum: 0,
    full_score_sum: 0,
    deduction_count: 0,
    evidence_count: 1,
    effective_weight: 1,
    exam_count: 1,
    source_question_refs: [],
    actionable_reasons: [],
    tag_context: {},
    error_counts: { primary: {}, secondary: {} },
    hierarchy_kind: 'root',
  }],
  knowledge_catalog: [{
    knowledge_key: 'knowledge_point:三角形全等',
    knowledge_point: '三角形全等',
  }],
  coverage: {
    covered_items: 1,
    total_items: 2,
    missing_items: { Q2: '题目尚未关联题库来源' },
  },
  confirmed_concept_ids: [],
  suggested_terms: ['三角形全等'],
  unmapped_terms: [],
  warnings: ['仍有 1 题缺少精确标签证据。'],
  diagnosis_identity: 'question_tag',
} satisfies TrainingDiagnosis

const plan = {
  plan_revision: 'a'.repeat(64),
  plan: {
    scope_snapshot: diagnosis.scope,
    exam_scope: diagnosis.exam_scope,
    diagnosis_snapshot: diagnosis,
    generation_config: {},
    variant_mode: 'individual',
    variants: [{
      variant_key: 'student-12',
      variant_type: 'individual',
      student_ids: ['12'],
      grouping_reason: { rule: 'individual' },
      diagnosis_snapshot: diagnosis,
      items: [{
        question_id: 201,
        item_order: 1,
        stage: 'direct',
        knowledge_key: 'knowledge_point:三角形全等',
        knowledge_point: '三角形全等',
        match_kind: 'exact',
        reason: '与薄弱知识点标签完全相同',
        recommend_score: 0.91,
        score_components: {},
        tag_matches: {},
        tags: { knowledge_point: ['三角形全等'] },
        warnings: [],
        question_fingerprint: 'fixture-201',
        question_text: '利用边角关系证明两个三角形全等',
        question_number: '11',
        difficulty: 5,
        source_paper: '合成练习',
        frequency: {},
      }],
      stage_counts: { direct: 1, prerequisite: 0, transfer: 0 },
      shortages: [{
        stage: 'transfer',
        requested_count: 1,
        selected_count: 0,
        missing_count: 1,
      }],
      warnings: ['transfer 阶段缺少 1 道精确标签候选题。'],
      dedupe_summary: { removed_count: 0, reason_counts: {} },
      generation_config: {},
    }],
    warnings: ['transfer 阶段缺少 1 道精确标签候选题。'],
    ungrouped_students: [],
    teacher_override: {
      allowed: true,
      applied: false,
      assignments: {},
    },
  },
} satisfies TrainingPlanResponse

const task = {
  id: 31,
  task_code: 'TRN-CFM-12345678123456781234567812345678',
  created_by: 'teacher',
  scope_snapshot: diagnosis.scope,
  exam_scope: diagnosis.exam_scope,
  generation_config: { plan_revision: plan.plan_revision },
  warnings: [],
  status: 'ready' as const,
  created_at: '2026-07-19T01:30:00Z',
  updated_at: '2026-07-19T01:30:00Z',
  diagnosis_snapshot: diagnosis,
  variants: [{
    id: 301,
    variant_key: 'student-12',
    variant_type: 'individual',
    students: [{ student_id: '12', student_name: '匿名学生甲' }],
    items: plan.plan.variants[0]!.items,
  }],
  exports: [],
}

const mounted: App[] = []

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore(pinia).$patch({
    sessions: [{
      id: 7,
      name: '匿名阶段测验',
      status: 'completed',
      is_deleted: false,
      deleted_at: null,
      created_at: null,
      updated_at: null,
    }],
    selectedSessionId: 7,
    loadState: 'ready',
  })
  const router = createAppRouter(createMemoryHistory())
  await router.push('/training')
  await router.isReady()
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(TrainingRecommendationsView)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  mounted.push(app)
  await settle()
  return { host, router }
}

function selectValue(element: HTMLSelectElement, value: string): void {
  element.value = value
  element.dispatchEvent(new Event('change', { bubbles: true }))
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  sessionStorage.clear()
  fetchStudentsMock.mockResolvedValue([
    {
      id: 12,
      student_code: 'S012',
      name: '匿名学生甲',
      class_name: '七年级一班',
      created_at: null,
    },
  ])
  trainingApiMock.diagnose.mockResolvedValue(diagnosis)
  trainingApiMock.preview.mockResolvedValue(plan)
  trainingApiMock.confirm.mockResolvedValue(task)
  exportsApiMock.listTrainingTasks.mockResolvedValue({
    items: [task],
    total: 1,
    page: 1,
    page_size: 20,
    total_pages: 1,
  })
  exportsApiMock.getTrainingTask.mockResolvedValue(task)
  exportsApiMock.submitTrainingExport.mockResolvedValue({
    id: 51,
    job_type: 'training_export',
    payload: { task_id: 31, format: 'docx' },
    result: {},
    status: 'queued',
    progress: 0,
    stage: 'queued',
    detail: '',
    error: null,
    cancel_requested: false,
    created_at: '2026-07-19T01:31:00Z',
    started_at: null,
    updated_at: '2026-07-19T01:31:00Z',
    finished_at: null,
  })
})

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('training recommendations view', () => {
  it('keeps the scope explicit and explains the tag-only recommendation basis', async () => {
    const { host } = await mountView()
    await vi.waitFor(() => expect(fetchStudentsMock).toHaveBeenCalled())

    expect(host.querySelector('h1')?.textContent).toBe('训练推荐')
    expect(host.textContent).toContain('精确知识标签')
    expect(host.textContent).toContain('当前证据范围')
    expect(host.textContent).toContain('缺考参考历史')
    await vi.waitFor(() => expect(trainingApiMock.diagnose).toHaveBeenCalledOnce())
  })

  it('cancels an in-flight diagnosis and publishes only the latest class scope', async () => {
    let resolveFirst!: (value: TrainingDiagnosis) => void
    const first = new Promise<TrainingDiagnosis>((resolve) => { resolveFirst = resolve })
    const secondDiagnosis: TrainingDiagnosis = {
      ...diagnosis,
      scope: { mode: 'class', class_id: '七年级二班', student_ids: ['22'] },
      students: [{
        ...diagnosis.students[0]!,
        student_id: '22',
        student_code: 'S022',
        student_name: '匿名学生乙',
        class_id: '七年级二班',
      }],
    }
    fetchStudentsMock.mockResolvedValue([
      {
        id: 12,
        student_code: 'S012',
        name: '匿名学生甲',
        class_name: '七年级一班',
        created_at: null,
      },
      {
        id: 22,
        student_code: 'S022',
        name: '匿名学生乙',
        class_name: '七年级二班',
        created_at: null,
      },
    ])
    trainingApiMock.diagnose
      .mockImplementationOnce(() => first)
      .mockResolvedValueOnce(secondDiagnosis)

    const { host } = await mountView()
    await vi.waitFor(() => expect(trainingApiMock.diagnose).toHaveBeenCalledTimes(1))
    ;[...host.querySelectorAll<HTMLLabelElement>('.evidence-scope__classes label')]
      .find((label) => label.textContent?.includes('七年级二班'))!
      .querySelector<HTMLInputElement>('input')!.click()
    await settle()
    host.querySelector<HTMLButtonElement>('[data-testid="apply-evidence-scope"]')!.click()
    await vi.waitFor(() => expect(trainingApiMock.diagnose).toHaveBeenCalledTimes(2))
    resolveFirst(diagnosis)
    await vi.waitFor(() => expect(
      [...host.querySelectorAll<HTMLButtonElement>('.training-group-summary button')]
        .some((button) => button.textContent?.includes('三角形全等')),
    ).toBe(true))
    ;[...host.querySelectorAll<HTMLButtonElement>('.training-group-summary button')]
      .find((button) => button.textContent?.includes('三角形全等'))!.click()
    await vi.waitFor(() => expect(host.textContent).toContain('匿名学生乙'))

    expect(host.textContent).not.toContain('匿名学生甲55%')
    expect(trainingApiMock.diagnose.mock.calls[1]?.[0]).toMatchObject({
      scope: { mode: 'class', class_ids: ['七年级二班'] },
    })
  })

  it('keeps selected students across multiple searches in the same class', async () => {
    fetchStudentsMock.mockResolvedValue([
      {
        id: 12,
        student_code: 'S012',
        name: '匿名学生甲',
        class_name: '七年级一班',
        created_at: null,
      },
      {
        id: 22,
        student_code: 'S022',
        name: '匿名学生乙',
        class_name: '七年级一班',
        created_at: null,
      },
    ])
    trainingApiMock.diagnose
      .mockResolvedValueOnce(diagnosis)
      .mockResolvedValueOnce({
        ...diagnosis,
        scope: {
          mode: 'all',
          student_ids: ['12', '22'],
          matched_student_count: 2,
        },
        students: [
          diagnosis.students[0]!,
          {
            ...diagnosis.students[0]!,
            student_id: '22',
            student_code: 'S022',
            student_name: '匿名学生乙',
          },
        ],
      })
    const { host } = await mountView()
    await vi.waitFor(() => expect(trainingApiMock.diagnose).toHaveBeenCalledTimes(1))

    const adjust = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((item) => item.textContent?.trim() === '精细调整学生')!
    adjust.click()
    await settle()
    const search = host.querySelector<HTMLInputElement>('input[aria-label="搜索学生"]')!

    search.value = '甲'
    search.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    selectValue(host.querySelector<HTMLSelectElement>('select[aria-label="匿名学生甲的范围决定"]')!, 'include')
    await settle()

    search.value = '乙'
    search.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    selectValue(host.querySelector<HTMLSelectElement>('select[aria-label="匿名学生乙的范围决定"]')!, 'include')
    await settle()
    host.querySelector<HTMLButtonElement>('[data-testid="apply-evidence-scope"]')!.click()
    await vi.waitFor(() => expect(trainingApiMock.diagnose).toHaveBeenCalledTimes(2))

    expect(trainingApiMock.diagnose.mock.calls[1]?.[0]).toMatchObject({
      scope: { mode: 'all', include_student_ids: ['12', '22'] },
    })
    await vi.waitFor(() => expect(host.textContent).toContain('当前范围 2 人'))
  })

  it('connects diagnosis evidence to the recommendation path and teacher confirmation', async () => {
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('三角形全等'))
    ;[...host.querySelectorAll<HTMLButtonElement>('.training-group-summary button')]
      .find((button) => button.textContent?.includes('三角形全等'))!.click()
    await vi.waitFor(() => expect(host.textContent).toContain('匿名学生甲'))
    await vi.waitFor(() => expect(host.textContent).toContain('55%'))
    expect(host.textContent).toContain('1 条证据')
    expect(host.textContent).toContain('Q2：题目尚未关联题库来源')
    host.querySelector<HTMLButtonElement>('[data-testid="weak-point-12-knowledge_point:三角形全等"]')!.click()
    await settle()
    expect(host.textContent).toContain('证明步骤缺少依据')
    expect(host.textContent).toContain('匿名阶段测验 · Q1 · 6 / 10')

    host.querySelector<HTMLButtonElement>('[data-testid="preview-training"]')!.click()
    await vi.waitFor(() => expect(host.textContent).toContain('与薄弱知识点标签完全相同'))
    expect(host.textContent).toContain('提升应用阶段缺少 1 道精确标签候选题')
    expect(host.querySelector('[data-testid="training-ratio-direct"]')).not.toBeNull()

    selectValue(
      host.querySelector<HTMLSelectElement>('[data-testid="training-question-count"]')!,
      '8',
    )
    await settle()
    expect(host.textContent).not.toContain('与薄弱知识点标签完全相同')
    expect(host.querySelector<HTMLButtonElement>('[data-testid="confirm-training"]')).toBeNull()

    host.querySelector<HTMLButtonElement>('[data-testid="preview-training"]')!.click()
    await vi.waitFor(() => expect(host.textContent).toContain('与薄弱知识点标签完全相同'))
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-training"]')!.click()
    await vi.waitFor(() => expect(trainingApiMock.confirm).toHaveBeenCalled())
    await vi.waitFor(() => expect(host.textContent).toContain('训练任务已保存'))
  })

  it('exports a saved task and tracks the queued job', async () => {
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain(task.task_code))
    host.querySelector<HTMLButtonElement>('[data-testid="task-31"]')!.click()
    await vi.waitFor(() => expect(exportsApiMock.getTrainingTask).toHaveBeenCalled())
    await vi.waitFor(() => expect(
      host.querySelector('[data-testid="export-training-task"]'),
    ).not.toBeNull())
    expect(host.textContent).toContain('匿名学生甲')
    expect(host.textContent).toContain('题 11 · 三角形全等')

    selectValue(
      host.querySelector<HTMLSelectElement>('[data-testid="training-export-mode"]')!,
      'variant',
    )
    host.querySelector<HTMLButtonElement>('[data-testid="export-training-task"]')!.click()
    await vi.waitFor(() => expect(exportsApiMock.submitTrainingExport).toHaveBeenCalledWith(
      31,
      {
        variant_id: 301,
        format: 'docx',
        audience: 'student',
      },
    ))
    await vi.waitFor(() => expect(host.textContent).toContain('训练材料已加入生成队列'))
    await vi.waitFor(() => expect(host.textContent).toContain('排队中'))
  })

  it('shows a recovery action when a completed export can no longer be downloaded', async () => {
    exportsApiMock.downloadJobFile.mockRejectedValueOnce(new Error('gone'))
    const { host } = await mountView()
    const jobStore = useJobStore()
    jobStore.track({
      id: 52,
      job_type: 'training_export',
      payload: { task_id: 31, format: 'docx' },
      result: { download_url: '/api/jobs/52/download' },
      status: 'succeeded',
      progress: 1,
      stage: 'completed',
      detail: '',
      error: null,
      cancel_requested: false,
      created_at: '2026-07-19T01:31:00Z',
      started_at: '2026-07-19T01:31:00Z',
      updated_at: '2026-07-19T01:32:00Z',
      finished_at: '2026-07-19T01:32:00Z',
    })
    await settle()

    const download = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '下载')
    download!.click()

    await vi.waitFor(() => expect(host.textContent).toContain('请重新生成'))
  })
})
