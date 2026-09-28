import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createMemoryHistory } from 'vue-router'

import type {
  PersonalizedRecommendationDraft,
  TrainingDiagnosis,
} from '../api/training'
import { savePaperSelectionSession } from '../features/training/paper-selection-session'
import { createAppRouter } from '../router'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import { useSessionStore } from '../stores/session'
import { useTrainingStore } from '../stores/training'
import TrainingRecommendationsView from '../views/TrainingRecommendationsView.vue'

const trainingApiMock = vi.hoisted(() => ({
  diagnose: vi.fn(),
  createPersonalizedDraft: vi.fn(),
  getPersonalizedDraft: vi.fn(),
  editPersonalizedDraft: vi.fn(),
  listPaperInstances: vi.fn(),
  listPaperBatches: vi.fn(),
}))

const fetchStudentsMock = vi.hoisted(() => vi.fn())

vi.mock('../api/training', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/training')>(),
  trainingApi: trainingApiMock,
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

const paperDraft = {
  draft_id: 'd'.repeat(64),
  status: 'draft',
  revision: 1,
  result_version: 'a'.repeat(64),
  engine_version: 'personalized-recommendation-v1',
  source_version: 'b'.repeat(64),
  config: {},
  students: [{
    student_id: '12',
    student_code: 'S012',
    student_name: '匿名学生甲',
    class_id: '七年级一班',
    selection_mode: 'mastery_targeted',
    targets: [{
      stable_key: 'knowledge_point:三角形全等',
      display_name: '三角形全等',
    }],
    items: [{
      item_id: 'item-1',
      item_order: 1,
      slot: 1,
      question_id: 201,
      question_number: '11',
      question_text: '利用边角关系证明两个三角形全等',
      stage: 'direct',
      target: { stable_key: 'knowledge_point:三角形全等' },
      matched_key: 'knowledge_point:三角形全等',
      matched_name: '第四章 三角形｜全等三角形｜三角形全等',
      relation: null,
      criterion_version_id: 'c'.repeat(64),
      criterion_point_count: 3,
      difficulty: 5,
      estimated_minutes: 6,
      source_paper: '合成题源',
      reason: '直接巩固三角形全等。',
      locked: false,
      replacement_history: [],
    }],
    shortages: [{
      stage: 'prerequisite',
      requested_count: 3,
      selected_count: 1,
      missing_count: 2,
      reason_code: 'stage_targets_empty',
    }],
    warnings: ['先修补强少配 2 题：当前知识标准中没有这些细点已确认的先修关系，无法推导先修补强目标。'],
    estimated_minutes: 6,
  }],
  warnings: ['先修补强少配 2 题：当前知识标准中没有这些细点已确认的先修关系，无法推导先修补强目标。'],
  history: [],
} satisfies PersonalizedRecommendationDraft

const mounted: App[] = []

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountView(path = '/training', pinia = createPinia()) {
  setActivePinia(pinia)
  useCurriculumScopeStore(pinia).$patch({ loadState: 'ready', selectedVolumeId: 'bnu24-math-g8-upper' })
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
  return { app, host, router, pinia }
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
  trainingApiMock.createPersonalizedDraft.mockResolvedValue(paperDraft)
  trainingApiMock.listPaperInstances.mockResolvedValue([])
  trainingApiMock.listPaperBatches.mockResolvedValue([])
})

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('training recommendations view', () => {
  it('sends the student score floor in diagnosis and restores it after leaving and returning', async () => {
    const first = await mountView('/training?mode=student')
    await vi.waitFor(() => expect(trainingApiMock.diagnose).toHaveBeenCalledOnce())
    const floor = first.host.querySelector<HTMLInputElement>('input[aria-label="最低得分率"]')!
    expect(floor).toBeTruthy()
    floor.value = '20'; floor.dispatchEvent(new Event('input', { bubbles: true }))
    first.host.querySelector<HTMLButtonElement>('[data-testid="apply-evidence-scope"]')!.click()
    await vi.waitFor(() => expect(trainingApiMock.diagnose).toHaveBeenCalledTimes(2))
    expect(trainingApiMock.diagnose.mock.calls[1]?.[0].scope.score_rate_min).toBe(.2)
    mounted.splice(mounted.indexOf(first.app), 1); first.app.unmount(); first.host.remove()
    const second = await mountView('/training?mode=student')
    await vi.waitFor(() => expect(trainingApiMock.diagnose).toHaveBeenCalledTimes(3))
    expect(second.host.querySelector<HTMLInputElement>('input[aria-label="最低得分率"]')!.value).toBe('20')
    expect(trainingApiMock.diagnose.mock.calls[2]?.[0].scope.score_rate_min).toBe(.2)
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

  it('checks seventeen adopted members and generates one shared draft without resetting it on diagnosis refresh', async () => {
    const ids = Array.from({ length: 17 }, (_, index) => `SYN-${index}`)
    const key = 'kp_synthetic_target'
    const data: TrainingDiagnosis = {
      ...diagnosis,
      students: ids.map(id => ({ ...diagnosis.students[0]!, student_id: id,
        weak_points: [{ ...diagnosis.students[0]!.weak_points[0]!, knowledge_key: key }] })),
      knowledge_catalog: [{ knowledge_key: key, knowledge_point: '合成训练目标' }],
      grouping: { version: 'v1', scope_keys: ['kp_chapter'], groups: [], unassigned: [], warnings: [],
        selection: { group_id: 'group-17', source_version: 'b'.repeat(64), ready: true, issues: [], warnings: [],
          members: [], targets: [], compatibility: 1, available_question_count: 10, recent_excluded_count: 0, reason: '' } },
    }
    trainingApiMock.diagnose.mockImplementation(async () => JSON.parse(JSON.stringify(data)))
    savePaperSelectionSession({ targetKeys: [key], rangeKeys: [], questionCount: 10, difficultyMax: 7,
      excludeCurrentOriginals: true, paperMode: 'shared', chapterKey: 'kp_chapter',
      adoptedGroup: { groupId: 'group-17', memberIds: ids, targetKeys: [key], scopeKeys: ['kp_chapter'], sourceVersion: 'a'.repeat(64) } })
    const { host, pinia } = await mountView('/training?mode=paper')
    await vi.waitFor(() => expect(host.querySelector<HTMLButtonElement>('[data-testid="generate-paper-draft"]')?.disabled).toBe(false))
    const generate = host.querySelector<HTMLButtonElement>('[data-testid="generate-paper-draft"]')!
    expect(generate.textContent).toContain('生成 1 份草稿')
    expect(host.querySelector('.paper-review-bar')?.textContent).toContain('17 名学生共同练习')
    generate.click()
    await vi.waitFor(() => expect(trainingApiMock.createPersonalizedDraft).toHaveBeenCalledOnce())
    expect(trainingApiMock.createPersonalizedDraft).toHaveBeenCalledWith(expect.objectContaining({
      paper_mode: 'shared', scope: expect.objectContaining({ student_ids: ids }), group_source_version: 'b'.repeat(64),
    }))
    await vi.waitFor(() => expect(host.textContent).toContain('04 · 草稿审核与匹配预览'))
    useTrainingStore(pinia).diagnosis = JSON.parse(JSON.stringify(data))
    await settle()
    expect(host.textContent).toContain('04 · 草稿审核与匹配预览')
    expect(trainingApiMock.getPersonalizedDraft).not.toHaveBeenCalled()
  })

  it('restores selections and the draft after leaving the page and returning', async () => {
    trainingApiMock.getPersonalizedDraft.mockResolvedValue(paperDraft)
    const first = await mountView('/training?mode=chapter')
    await vi.waitFor(() => expect(first.host.textContent).toContain('学生 × 知识点'))
    expect(first.host.querySelector('[aria-label="训练强度"]')).toBeNull()
    expect(first.host.textContent).not.toContain('预计用时')
    first.host.querySelector<HTMLInputElement>('.chapter-training thead input[type="checkbox"]')!.click()
    await nextTick()
    first.host.querySelector<HTMLButtonElement>('[data-testid="go-paper"]')!.click()
    await vi.waitFor(() => expect(first.router.currentRoute.value.query.mode).toBe('paper'))
    first.host.querySelector<HTMLButtonElement>('[data-testid="generate-paper-draft"]')!.click()
    await vi.waitFor(() => expect(first.host.textContent).toContain('04 · 草稿审核与匹配预览'))
    const mountedIndex = mounted.indexOf(first.app)
    if (mountedIndex >= 0) mounted.splice(mountedIndex, 1)
    first.app.unmount()

    // 模拟切到其他页签再回来：全新挂载，只剩会话暂存的勾选与草稿记录。
    const second = await mountView('/training?mode=paper')
    await vi.waitFor(() => expect(second.host.textContent).toContain('三角形全等'))
    await vi.waitFor(() => expect(second.host.textContent).toContain('已恢复上次生成的草稿'))
    await vi.waitFor(() => expect(second.host.textContent).toContain('04 · 草稿审核与匹配预览'))
    expect(trainingApiMock.getPersonalizedDraft).toHaveBeenCalledWith(paperDraft.draft_id)
    expect(trainingApiMock.createPersonalizedDraft).toHaveBeenCalledTimes(1)
    expect(trainingApiMock.createPersonalizedDraft).toHaveBeenCalledWith(expect.objectContaining({ difficulty_max: 8 }))
    expect(second.host.querySelector('[aria-label="训练强度"]')).toBeNull()
    expect(second.host.querySelector('.personalized-draft-toolbar')?.textContent)
      .toContain('草稿已自动暂存')
  })

})
