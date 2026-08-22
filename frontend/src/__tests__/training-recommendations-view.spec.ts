import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { TrainingDiagnosis, TrainingPlanResponse } from '../api/training'
import { createAppRouter } from '../router'
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
    score_rate: 0.55,
    weak_points: [{
      knowledge_key: 'knowledge_point:三角形全等',
      knowledge_point: '三角形全等',
      parent_knowledge_key: 'section:全等三角形',
      parent_knowledge_point: '全等三角形',
      hierarchy_kind: 'child',
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
    knowledge_key: 'chapter:三角形',
    knowledge_point: '第四章 三角形',
    mastery: 0.62,
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
  }, {
    knowledge_key: 'section:全等三角形',
    knowledge_point: '全等三角形',
    mastery: 0.58,
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
    hierarchy_kind: 'parent_summary',
    parent_knowledge_key: 'chapter:三角形',
    parent_knowledge_point: '第四章 三角形',
  }, {
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
    hierarchy_kind: 'child',
    parent_knowledge_key: 'section:全等三角形',
    parent_knowledge_point: '全等三角形',
  }],
  knowledge_catalog: [{
    knowledge_key: 'chapter:三角形',
    knowledge_point: '第四章 三角形',
  }, {
    knowledge_key: 'section:全等三角形',
    knowledge_point: '全等三角形',
    parent_knowledge_key: 'chapter:三角形',
    parent_knowledge_point: '第四章 三角形',
  }, {
    knowledge_key: 'knowledge_point:三角形全等',
    knowledge_point: '三角形全等',
    parent_knowledge_key: 'section:全等三角形',
    parent_knowledge_point: '全等三角形',
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

async function mountView(path = '/training') {
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
  await router.push(path)
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
  it('keeps the scope explicit and presents the chapter heat matrix', async () => {
    const { host } = await mountView()
    await vi.waitFor(() => expect(fetchStudentsMock).toHaveBeenCalled())

    expect(host.querySelector('h1')?.textContent).toBe('按章节训练')
    expect(host.textContent).toContain('章节学生热力图')
    expect(host.textContent).toContain('学生 × 知识点')
    expect(host.textContent).toContain('三角形全等')
    expect(host.textContent).toContain('当前证据范围')
    expect(host.textContent).toContain('缺考参考历史')
    ;[...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '群体加权')!
      .click()
    await nextTick()
    expect(host.textContent).toContain('群体加权 × 知识点')
    expect(host.textContent).toContain('全部班级 · 1 人')
    expect(host.textContent).toContain('1 / 1 人有效')
    expect(host.textContent).toContain('1 条证据')
    ;[...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '调整')!
      .click()
    await nextTick()
    expect(host.textContent).toContain('指定学生')
    await vi.waitFor(() => expect(trainingApiMock.diagnose).toHaveBeenCalledOnce())
  })

  it('hides students without evidence in the current section until the toggle is enabled', async () => {
    trainingApiMock.diagnose.mockResolvedValue({
      ...diagnosis,
      students: [
        diagnosis.students[0]!,
        {
          ...diagnosis.students[0]!,
          student_id: '22',
          student_code: 'S022',
          student_name: '匿名学生乙',
          weak_points: [],
        },
      ],
    })
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('学生 × 知识点'))

    expect(host.querySelectorAll('.chapter-training tbody tr')).toHaveLength(1)
    expect(host.textContent).toContain('显示无证据学生（1）')
    expect(host.textContent).toContain('1 名学生 · 1 个知识点')
    const toggle = [...host.querySelectorAll<HTMLLabelElement>('.chapter-training__toggle')]
      .find((label) => label.textContent?.includes('显示无证据学生'))!
      .querySelector<HTMLInputElement>('input')!
    toggle.click()
    await nextTick()
    expect(host.querySelectorAll('.chapter-training tbody tr')).toHaveLength(2)
    expect(host.textContent).toContain('2 名学生 · 1 个知识点')
  })

  it('shows an empty hint when no student has evidence in the current section', async () => {
    trainingApiMock.diagnose.mockResolvedValue({
      ...diagnosis,
      students: [{
        ...diagnosis.students[0]!,
        weak_points: [{ ...diagnosis.students[0]!.weak_points[0]!, evidence_count: 0 }],
      }],
    })
    const { host } = await mountView()
    await vi.waitFor(() => expect(host.textContent).toContain('章节学生热力图'))

    expect(host.textContent).toContain('当前范围所有学生都没有证据')
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
      .find((item) => item.textContent?.trim() === '更多筛选')!
    adjust.click()
    await settle()
    const search = host.querySelector<HTMLInputElement>('input[aria-label="搜索学生"]')!

    search.value = '甲'
    search.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    host.querySelector<HTMLInputElement>('input[aria-label="选择匿名学生甲"]')!.click()
    await settle()

    search.value = '乙'
    search.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    host.querySelector<HTMLInputElement>('input[aria-label="选择匿名学生乙"]')!.click()
    await settle()
    expect(host.textContent).toContain('队列 2 人')
    host.querySelector<HTMLButtonElement>('[data-testid="apply-evidence-scope"]')!.click()
    await vi.waitFor(() => expect(trainingApiMock.diagnose).toHaveBeenCalledTimes(2))

    const lastCall = trainingApiMock.diagnose.mock.calls[trainingApiMock.diagnose.mock.calls.length - 1]
    expect(lastCall?.[0]).toMatchObject({
      scope: { mode: 'selected', student_ids: ['12', '22'] },
    })
    await vi.waitFor(() => expect(host.textContent).toContain('所选 2 人'))
  })

  it('hides students without a score once a score range is set', async () => {
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
    trainingApiMock.diagnose.mockResolvedValue({
      ...diagnosis,
      students: [
        diagnosis.students[0]!,
        {
          ...diagnosis.students[0]!,
          student_id: '22',
          student_code: 'S022',
          student_name: '匿名学生乙',
          score_rate: null,
          score_rate_source: 'none' as const,
        },
      ],
    })
    const { host } = await mountView()
    await vi.waitFor(() => expect(trainingApiMock.diagnose).toHaveBeenCalledTimes(1))

    ;[...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((item) => item.textContent?.trim() === '更多筛选')!
      .click()
    await settle()
    expect(host.querySelectorAll('.evidence-scope__card')).toHaveLength(0)
    expect(host.textContent).toContain('输入姓名、学号，或设定班级、得分率后显示匹配学生')

    const search = host.querySelector<HTMLInputElement>('input[aria-label="搜索学生"]')!
    search.value = '匿名'
    search.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(host.querySelectorAll('.evidence-scope__card')).toHaveLength(2)

    const minimum = host.querySelector<HTMLInputElement>('input[aria-label="最低得分率"]')!
    minimum.value = '60'
    minimum.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(host.querySelectorAll('.evidence-scope__card')).toHaveLength(0)
    expect(host.textContent).toContain('当前条件下没有匹配的学生')

    minimum.value = '50'
    minimum.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    const cards = [...host.querySelectorAll<HTMLElement>('.evidence-scope__card')]
    expect(cards).toHaveLength(1)
    expect(cards[0]!.textContent).toContain('匿名学生甲')
    expect(cards[0]!.textContent).toContain('55%')
  })

  it('uses the shared scope filter cards and a weighted structure in student mode', async () => {
    const { host } = await mountView('/training?mode=student')
    await vi.waitFor(() => expect(host.textContent).toContain('群体知识结构'))

    expect(host.querySelector('h1')?.textContent).toBe('按学生训练')
    expect(host.querySelector('.student-filter-strip')).toBeNull()
    expect(host.textContent).not.toContain('多选学生')
    expect(host.textContent).toContain('所选学生的加权知识结构')
    expect(host.textContent).toContain('勾选章或小节作为训练范围')

    ;[...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((item) => item.textContent?.trim() === '更多筛选')!
      .click()
    await settle()
    const search = host.querySelector<HTMLInputElement>('input[aria-label="搜索学生"]')!
    search.value = 'S012'
    search.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(host.querySelectorAll('.evidence-scope__card')).toHaveLength(1)
    expect(host.querySelector('.evidence-scope__card-evidence')?.getAttribute('href'))
      .toContain('/training/evidence/12?from=student')
    expect(host.textContent).not.toContain('薄弱原因与评分证据')
  })

  it('keeps every card score rate through the merged profile cache under a selected scope', async () => {
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
      .mockResolvedValueOnce({
        ...diagnosis,
        scope: { mode: 'all', student_ids: ['12', '22'] },
        students: [
          diagnosis.students[0]!,
          {
            ...diagnosis.students[0]!,
            student_id: '22',
            student_code: 'S022',
            student_name: '匿名学生乙',
            score_rate: 0.9,
            weak_points: [],
          },
        ],
      })
      // 选定范围后诊断只回所选学生，乙的得分率只能来自合并缓存。
      .mockResolvedValue(diagnosis)
    const { host } = await mountView()
    await vi.waitFor(() => expect(trainingApiMock.diagnose).toHaveBeenCalledTimes(1))

    ;[...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((item) => item.textContent?.trim() === '更多筛选')!
      .click()
    await settle()
    const search = host.querySelector<HTMLInputElement>('input[aria-label="搜索学生"]')!
    search.value = '匿名'
    search.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    host.querySelector<HTMLInputElement>('input[aria-label="选择匿名学生甲"]')!.click()
    await nextTick()
    host.querySelector<HTMLButtonElement>('[data-testid="apply-evidence-scope"]')!.click()
    await vi.waitFor(() => expect(trainingApiMock.diagnose).toHaveBeenCalledTimes(2))

    const cards = [...host.querySelectorAll<HTMLElement>('.evidence-scope__card')]
    expect(cards).toHaveLength(2)
    expect(cards.find((card) => card.textContent?.includes('匿名学生甲'))!.textContent).toContain('55%')
    expect(cards.find((card) => card.textContent?.includes('匿名学生乙'))!.textContent).toContain('90%')
    expect(host.textContent).toContain('队列 1 人')
  })

  it('uses the compact A paper console and carries selected targets into its summary', async () => {
    const { host, router } = await mountView('/training?mode=chapter')
    await vi.waitFor(() => expect(host.textContent).toContain('学生 × 知识点'))
    host.querySelector<HTMLInputElement>('.chapter-training thead input[type="checkbox"]')!.click()
    await nextTick()
    await router.push('/training?mode=paper')
    await nextTick()
    await vi.waitFor(() => expect(host.textContent).toContain('出卷设置与草稿'))

    expect(host.querySelector('h1')?.textContent).toBe('生成试卷')
    expect(host.textContent).toContain('一人一卷')
    expect(host.textContent).toContain('多人同一套卷')
    expect(host.textContent).toContain('每卷题数')
    expect(host.textContent).toContain('本次出卷摘要')
    expect(host.textContent).toContain('预计试卷')
    expect(host.textContent).toContain('三角形全等')
    expect(host.querySelector<HTMLButtonElement>('[data-testid="generate-paper-draft"]')?.textContent).toContain('生成 1 份草稿')
    expect(host.querySelector<HTMLButtonElement>('[data-testid="generate-paper-draft"]')?.disabled).toBe(false)
    expect(host.textContent).not.toContain('章节学生热力图')
    expect(host.textContent).not.toContain('薄弱原因与评分证据')
  })

  it('lets student-mode range selection unlock one-paper-per-student drafts', async () => {
    const { host, router } = await mountView('/training?mode=student')
    await vi.waitFor(() => expect(host.textContent).toContain('群体知识结构'))
    const rangeCheckbox = host.querySelector<HTMLInputElement>('.structure-range-check input')
    expect(rangeCheckbox).not.toBeNull()
    rangeCheckbox!.click()
    await nextTick()
    await router.push('/training?mode=paper')
    await nextTick()
    await vi.waitFor(() => expect(host.textContent).toContain('出卷设置与草稿'))
    expect(host.textContent).toContain('训练范围')
    expect(host.querySelector<HTMLButtonElement>('[data-testid="generate-paper-draft"]')?.disabled).toBe(false)
  })

  it('keeps the knowledge structure visible while a new diagnosis is loading', async () => {
    let finishSecond: ((value: TrainingDiagnosis) => void) | undefined
    const second = new Promise<TrainingDiagnosis>((resolve) => {
      finishSecond = resolve
    })
    trainingApiMock.diagnose
      .mockResolvedValueOnce(diagnosis)
      .mockImplementationOnce(() => second)
    const { host } = await mountView('/training?mode=student')
    await vi.waitFor(() => expect(host.textContent).toContain('所选学生的加权知识结构'))

    ;[...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((item) => item.textContent?.trim() === '更多筛选')!
      .click()
    await settle()
    host.querySelector<HTMLInputElement>('.evidence-scope__history input')!.click()
    await vi.waitFor(() => expect(trainingApiMock.diagnose).toHaveBeenCalledTimes(2))

    expect(host.textContent).toContain('所选学生的加权知识结构')
    expect(host.textContent).not.toContain('正在汇总学生与知识点')
    expect(host.textContent).toContain('正在更新掌握汇总')
    finishSecond?.(diagnosis)
    await settle()
  })
})
