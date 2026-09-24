import { createApp, nextTick, type App } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  PersonalizedPaperBatch,
  PersonalizedPaperInstance,
  PersonalizedRecommendationDraft,
  TrainingDiagnosis,
} from '../api/training'
import PersonalizedRecommendationDraftView from '../components/training/PersonalizedRecommendationDraft.vue'
import { ApiError } from '../api/errors'

const trainingApiMock = vi.hoisted(() => ({
  createPersonalizedDraft: vi.fn(),
  getPersonalizedDraft: vi.fn(),
  getPersonalizedDraftByRequest: vi.fn(),
  editPersonalizedDraft: vi.fn(),
  listPaperInstances: vi.fn(),
  listPaperBatches: vi.fn(),
  createPaperInstance: vi.fn(),
  createPaperBatch: vi.fn(),
  freezePaperInstance: vi.fn(),
  downloadPaperArtifact: vi.fn(),
}))

const questionBankApiMock = vi.hoisted(() => ({
  getQuestion: vi.fn(),
}))

vi.mock('../api/training', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/training')>(),
  trainingApi: trainingApiMock,
}))

vi.mock('../api/question-bank', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/question-bank')>(),
  questionBankApi: questionBankApiMock,
}))

const diagnosis = {
  scope: { mode: 'student', student_ids: ['SYN-S01'] },
  exam_scope: {
    mode: 'current',
    session_ids: [7],
    sessions: [{ session_id: 7, session_name: '合成考试' }],
  },
  students: [{
    student_id: 'SYN-S01',
    student_code: 'S01',
    student_name: '合成学生',
    class_id: 'SYN-C01',
    score_rate: 45,
    weak_points: [{
      knowledge_key: 'knowledge_point:一元一次方程',
      knowledge_point: '一元一次方程',
      mastery: 0.45,
      score_sum: 9,
      full_score_sum: 20,
      deduction_count: 1,
      evidence_count: 1,
      exam_count: 1,
      source_question_refs: [],
      actionable_reasons: ['方程变形需要巩固'],
      tag_context: {},
      error_counts: {},
    }],
  }],
  coverage: {
    covered_items: 1,
    total_items: 1,
    missing_items: {},
  },
  confirmed_concept_ids: [],
  suggested_terms: [],
  unmapped_terms: [],
  warnings: [],
  diagnosis_identity: 'question_tag',
} satisfies TrainingDiagnosis

const draft = {
  draft_id: 'd'.repeat(64),
  status: 'draft',
  revision: 1,
  result_version: 'a'.repeat(64),
  engine_version: 'personalized-recommendation-v1',
  source_version: 'b'.repeat(64),
  config: {},
  students: [{
    student_id: 'SYN-S01',
    student_code: 'S01',
    student_name: '合成学生',
    class_id: 'SYN-C01',
    selection_mode: 'mastery_targeted',
    targets: [],
    items: [{
      item_id: 'item-1',
      item_order: 1,
      slot: 1,
      question_id: 31,
      question_number: '3',
      stage: 'direct',
      target: {},
      matched_key: 'kp_alg_linear_equation',
      matched_name: '一元一次方程',
      relation: null,
      criterion_version_id: 'c'.repeat(64),
      criterion_point_count: 3,
      difficulty: 5,
      estimated_minutes: 6,
      source_paper: '合成题源',
      reason: '直接巩固一元一次方程。',
      locked: false,
      replacement_history: [],
    }],
    shortages: [],
    warnings: [],
    estimated_minutes: 6,
  }],
  warnings: [],
  history: [],
} satisfies PersonalizedRecommendationDraft

const paper = {
  paper_instance_id: 'e'.repeat(64),
  paper_batch_id: 'f'.repeat(64),
  draft_id: draft.draft_id,
  draft_revision: 1,
  student_id: 'SYN-S01',
  student_code: 'S01',
  student_name: '合成学生',
  class_id: 'SYN-C01',
  series_version: 1,
  status: 'frozen',
  revision: 2,
  layout_version: 'personalized-paper-school-a4-v1',
  budget: {
    version: 'whole-paper-context-budget-v1',
    status: 'ready',
    context_window_tokens: 32768,
    question_count: 1,
    criterion_point_count: 3,
    image_count: 0,
    page_count: 2,
    page_count_is_estimate: false,
    estimated_input_tokens: 3200,
    estimated_output_tokens: 1340,
    estimated_total_tokens: 4540,
    limits: { questions: 12, criterion_points: 120, images: 48, pages: 20 },
    blockers: [],
  },
  question_count: 1,
  criterion_point_count: 3,
  items: [],
  pages: [{ page_number: 1 }, { page_number: 2 }],
  review_docx_sha256: '1'.repeat(64),
  reviewed_docx_sha256: '1'.repeat(64),
  frozen_pdf_sha256: '2'.repeat(64),
  downloads: {
    review_docx: `/api/training/paper-instances/${'e'.repeat(64)}/files/review-docx`,
    reviewed_docx: null,
    frozen_pdf: `/api/training/paper-instances/${'e'.repeat(64)}/files/frozen-pdf`,
  },
  error_code: null,
  created_at: '2026-07-30 08:00:00',
  frozen_at: '2026-07-30 08:05:00',
} satisfies PersonalizedPaperInstance

const paperBatch = {
  batch_run_id: 'b'.repeat(64),
  paper_batch_id: 'f'.repeat(64),
  status: 'complete',
  requested_count: 1,
  succeeded_count: 1,
  failed_count: 0,
  items: [paper],
  failures: [],
  downloads: { bundle: null, manifest: null, frozen_bundle: '/api/training/paper-batches/' + 'b'.repeat(64) + '/files/frozen-bundle' },
} satisfies PersonalizedPaperBatch

const matchedDiagnosis = {
  ...diagnosis,
  students: [{
    ...diagnosis.students[0]!,
    weak_points: [{
      ...diagnosis.students[0]!.weak_points[0]!,
      source_question_refs: [{
        session_id: 7,
        session_name: '合成考试',
        question_id: '5',
        bank_question_id: 88,
        score_awarded: 3,
        full_score: 10,
        score_rate: 0.3,
      }],
    }],
  }],
} satisfies TrainingDiagnosis

const matchedDraft = {
  ...draft,
  students: [{
    ...draft.students[0]!,
    targets: [{
      stable_key: 'knowledge_point:一元一次方程',
      display_name: '一元一次方程',
    }],
    items: [{
      ...draft.students[0]!.items[0]!,
      question_text: '解方程 2x + 3 = 9。',
      matched_name: '第三章 方程｜一元一次方程',
      target: { stable_key: 'knowledge_point:一元一次方程' },
    }, {
      item_id: 'item-2',
      item_order: 2,
      slot: 2,
      question_id: 32,
      question_number: '4',
      stage: 'transfer',
      target: { stable_key: 'knowledge_point:二元一次方程' },
      matched_key: 'knowledge_point:二元一次方程',
      matched_name: '二元一次方程',
      relation: null,
      criterion_version_id: 'c'.repeat(64),
      criterion_point_count: 2,
      difficulty: 6,
      estimated_minutes: 5,
      source_paper: '合成题源',
      reason: '练习迁移。',
      locked: false,
      replacement_history: [],
    }],
    shortages: [{
      stage: 'prerequisite',
      requested_count: 2,
      selected_count: 0,
      missing_count: 2,
      reason_code: 'stage_targets_empty',
    }],
    warnings: ['先修补强少配 2 题：当前知识标准中没有这些细点已确认的先修关系，无法推导先修补强目标。'],
  }],
  warnings: ['先修补强少配 2 题：当前知识标准中没有这些细点已确认的先修关系，无法推导先修补强目标。'],
} satisfies PersonalizedRecommendationDraft

const mounted: App[] = []

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

beforeEach(() => {
  vi.clearAllMocks()
  globalThis.localStorage?.clear()
  trainingApiMock.createPersonalizedDraft.mockResolvedValue(draft)
  trainingApiMock.getPersonalizedDraft.mockResolvedValue(draft)
  trainingApiMock.listPaperInstances.mockResolvedValue([])
  trainingApiMock.listPaperBatches.mockResolvedValue([])
  trainingApiMock.editPersonalizedDraft.mockResolvedValue({
    ...draft,
    revision: 2,
    students: [{
      ...draft.students[0]!,
      items: [{ ...draft.students[0]!.items[0]!, locked: true }],
    }],
  })
  trainingApiMock.createPaperInstance.mockResolvedValue(paper)
  trainingApiMock.createPaperBatch.mockResolvedValue(paperBatch)
  questionBankApiMock.getQuestion.mockResolvedValue({
    id: 88,
    question_number: '5',
    paper_title: '合成题源',
    question_type: '计算题',
    difficulty: 5,
    question_text: '错题题干',
    answer_text: '错题答案',
    page_range: null,
    has_images: false,
    assets: [],
    rich_content: { question_blocks: [], answer_blocks: [] },
    previews: [],
    tags: [],
  })
})

afterEach(() => {
  mounted.splice(0).forEach((app) => app.unmount())
  document.body.innerHTML = ''
})

describe('personalized recommendation draft', () => {
  it('lets the teacher select a target, generate, explain and lock an item', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(PersonalizedRecommendationDraftView, {
      diagnosis,
      scope: { mode: 'student', student_ids: ['SYN-S01'] },
      examScope: { mode: 'current', session_ids: [7] },
      questionCount: 8,
      trainingIntent: 'challenge',
      teachingProgressChapterId: 'bnu24-math-g8-upper-c02',
      stageRatios: {
        direct: 0.6,
        prerequisite: 0.3,
        transfer: 0.1,
      },
      excludeCurrentExamOriginals: true,
    })
    app.mount(host)
    mounted.push(app)
    await settle()

    const generate = host.querySelector<HTMLButtonElement>(
      '[data-testid="generate-personalized-draft"]',
    )
    expect(generate?.disabled).toBe(true)
    host.querySelector<HTMLInputElement>('.personalized-targets input[type="checkbox"]')?.click()
    await settle()
    expect(generate?.disabled).toBe(false)
    generate?.click()
    await settle()

    expect(host.textContent).not.toContain('人工纳入')
    expect(host.textContent).not.toContain('保守复习卷')

    expect(trainingApiMock.createPersonalizedDraft).toHaveBeenCalledWith(
      expect.objectContaining({
        target_keys: [],
        target_names: ['一元一次方程'],
        paper_mode: 'individual',
        question_count: 8,
        difficulty_max: 7,
        teaching_progress_chapter_id: 'bnu24-math-g8-upper-c02',
      }),
    )
    expect(host.textContent).toContain('直接巩固一元一次方程')
    expect(host.textContent).toContain('3 个判定点')
    expect(host.textContent).toContain('尚未形成正式训练卷')

    const lock = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '锁定')
    lock?.click()
    await settle()

    expect(trainingApiMock.editPersonalizedDraft).toHaveBeenCalledWith(
      draft.draft_id,
      expect.objectContaining({
        expected_revision: 1,
        action: 'lock',
        student_id: 'SYN-S01',
        item_id: 'item-1',
      }),
    )
    expect(host.textContent).toContain('已锁定该题')
    expect(host.textContent).toContain('解锁')
    // 体积上限参数与单人生成入口对教师不可见，只保留批量生成。
    expect(host.textContent).not.toContain('容量')
    expect(host.textContent).not.toContain('只为该生生成审核稿')

    const createBatch = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '生成全部 PDF 试卷（可直接打印）')
    createBatch?.click()
    await settle()

    expect(trainingApiMock.createPaperBatch).toHaveBeenCalledWith(
      draft.draft_id,
      expect.objectContaining({
        expected_draft_revision: 2,
        student_ids: ['SYN-S01'],
        context_window_tokens: 128000,
        direct_freeze: true,
      }),
    )
    expect(host.textContent).toContain('已生成 1 份实名 PDF 试卷')
    expect(host.textContent).toContain('V1')
    expect(host.textContent).toContain('2 页冻结 PDF')
    expect(host.textContent).toContain('下载 PDF 试卷')
    expect(host.textContent).toContain('不会自动打印')
  })

  it('pairs each recommended question with its source evidence in external setup mode', async () => {
    trainingApiMock.createPersonalizedDraft.mockResolvedValue(matchedDraft)
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(PersonalizedRecommendationDraftView, {
      diagnosis: matchedDiagnosis,
      externalSetup: true,
      scope: { mode: 'student', student_ids: ['SYN-S01'] },
      examScope: { mode: 'current', session_ids: [7] },
      questionCount: 8,
      stageRatios: {
        direct: 0.6,
        prerequisite: 0.3,
        transfer: 0.1,
      },
      excludeCurrentExamOriginals: true,
      targetKeys: ['knowledge_point:一元一次方程'],
    })
    const view = app.mount(host) as unknown as { generate: () => Promise<void> }
    mounted.push(app)
    await settle()

    await view.generate()
    await settle()

    // 头部只保留人数与选稿方式，不再拼接全部细点路径。
    const studentCard = host.querySelector<HTMLElement>('.personalized-student')!
    expect(studentCard.querySelector('header')?.textContent).toContain('按掌握证据推荐')
    expect(studentCard.querySelector('header')?.textContent).not.toContain('细点')

    // 配对卡片：错题依据 → 推荐题。
    const firstPair = host.querySelector<HTMLElement>('.personalized-match')!
    expect(firstPair.querySelector('.personalized-match__evidence')?.textContent).toContain('错题依据')
    expect(firstPair.querySelector('.personalized-match__evidence')?.textContent)
      .toContain('合成考试 · 第 5 题 · 得 3/10 分')
    expect(firstPair.querySelector('.personalized-stage-badge')?.textContent).toContain('直接巩固')
    expect(firstPair.querySelector('.personalized-match__order')?.textContent).toContain('第 1 题')
    expect(firstPair.querySelector('.personalized-match__meta')?.textContent).toContain('原卷第 3 题')
    expect(firstPair.querySelector('.personalized-match__head strong')?.textContent).toBe('一元一次方程')
    expect(firstPair.querySelector('.personalized-match__stem')?.textContent).toContain('解方程 2x + 3 = 9。')
    expect(firstPair.textContent).toContain('来源：合成题源')
    // 推荐依据包含难度与覆盖说明，直接练习也要可见。
    expect(firstPair.querySelector('.personalized-match__reason')?.textContent).toContain('直接巩固一元一次方程。')

    // 旧草稿项没有题干时给出诚实提示；无匹配细点时给出空依据提示。
    const pairs = [...host.querySelectorAll<HTMLElement>('.personalized-match')]
    expect(pairs[1]!.querySelector('.personalized-match__stem')?.textContent)
      .toContain('旧草稿未包含题干，重新生成后可见')
    expect(pairs[1]!.querySelector('.personalized-match__evidence')?.textContent)
      .toContain('该细点在当前范围内暂无逐题失分记录')
    expect(pairs[1]!.querySelector('.personalized-stage-badge')?.textContent).toContain('迁移应用')
    // 非直接阶段卡显示推荐理由，教师可看到兜底来源说明。
    expect(pairs[1]!.querySelector('.personalized-match__reason')?.textContent).toContain('练习迁移。')

    // 缺题卡片沿用现有 warning 文案。
    const shortage = host.querySelector<HTMLElement>('.personalized-shortage')
    expect(shortage?.textContent).toContain('先修补强')
    expect(shortage?.textContent).toContain('待配 2 题')
    expect(host.textContent).toContain('无法推导先修补强目标')
  })

  const externalProps = {
    diagnosis,
    externalSetup: true,
    scope: { mode: 'student', student_ids: ['SYN-S01'] },
    examScope: { mode: 'current', session_ids: [7] },
    questionCount: 8,
    stageRatios: {
      direct: 0.6,
      prerequisite: 0.3,
      transfer: 0.1,
    },
    excludeCurrentExamOriginals: true,
    targetKeys: ['knowledge_point:一元一次方程'],
  }

  it('recovers a saved draft after the create request times out without posting again', async () => {
    trainingApiMock.createPersonalizedDraft.mockRejectedValueOnce(new ApiError({ kind: 'timeout', status: null,
      code: 'request_timeout', message: 'timeout', details: {}, requestId: 'synthetic', retryable: false }))
    trainingApiMock.getPersonalizedDraftByRequest.mockResolvedValueOnce(draft)
    const host = document.createElement('div'); document.body.append(host)
    const app = createApp(PersonalizedRecommendationDraftView, externalProps)
    mounted.push(app)
    const view = app.mount(host) as unknown as { generate: () => Promise<void> }
    await settle(); await view.generate(); await settle()
    expect(host.textContent).toContain('已取回上次请求生成的草稿')
    expect(trainingApiMock.createPersonalizedDraft).toHaveBeenCalledOnce()
    expect(trainingApiMock.getPersonalizedDraftByRequest).toHaveBeenCalledWith(
      trainingApiMock.createPersonalizedDraft.mock.calls[0]![0].request_token)
  })

  it('remembers an uncertain request on a return visit and only checks its saved result', async () => {
    trainingApiMock.createPersonalizedDraft.mockRejectedValueOnce(new ApiError({ kind: 'timeout', status: null,
      code: 'request_timeout', message: 'timeout', details: {}, requestId: 'synthetic', retryable: false }))
    trainingApiMock.getPersonalizedDraftByRequest.mockRejectedValueOnce(new Error('not saved yet')).mockResolvedValueOnce(draft)
    const host = document.createElement('div'); document.body.append(host)
    const first = createApp(PersonalizedRecommendationDraftView, externalProps)
    const view = first.mount(host) as unknown as { generate: () => Promise<void> }
    await settle(); await view.generate(); await settle()
    expect(host.textContent).toContain('后台可能仍在处理')
    first.unmount()
    const second = createApp(PersonalizedRecommendationDraftView, externalProps)
    mounted.push(second); second.mount(host)
    await vi.waitFor(() => expect(host.textContent).toContain('已取回上次请求生成的草稿'))
    expect(trainingApiMock.createPersonalizedDraft).toHaveBeenCalledOnce()
    expect(trainingApiMock.getPersonalizedDraftByRequest).toHaveBeenCalledTimes(2)
  })

  it('marks range supplements without pairing them with a student mistake', async () => {
    const supplemented: PersonalizedRecommendationDraft = structuredClone(matchedDraft)
    const item = supplemented.students[0]!.items[0]!
    item.selection_kind = 'supplement'
    item.reason = '补充练习：选定范围内的近似难度题，不作为此知识点薄弱的证据。'
    trainingApiMock.createPersonalizedDraft.mockResolvedValue(supplemented)
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(PersonalizedRecommendationDraftView, { ...externalProps, diagnosis: matchedDiagnosis })
    mounted.push(app)
    const view = app.mount(host) as unknown as { generate: () => Promise<void> }
    await settle()
    await view.generate()
    await settle()
    const pair = host.querySelector<HTMLElement>('.personalized-match')!
    expect(pair.querySelector('.personalized-stage-badge')?.textContent).toBe('补充练习')
    expect(pair.querySelector('.personalized-match__evidence')?.textContent).toContain('补充依据')
    expect(pair.querySelector('.personalized-match__evidence')?.textContent).not.toContain('得 3/10 分')
    expect(pair.querySelector('.personalized-match__reason')?.textContent).toContain('不作为此知识点薄弱的证据')
  })

  it('shows task matches without presenting them as same-skill matches', async () => {
    const taskDraft: PersonalizedRecommendationDraft = structuredClone(matchedDraft)
    const item = taskDraft.students[0]!.items[0]!
    item.selection_kind = 'task_matched'
    item.match_level = 3
    item.match_label = '原小问任务匹配（已有解题步骤）'
    item.reason = '原小问需要列式与推导，本题已有相应解题步骤。'
    trainingApiMock.createPersonalizedDraft.mockResolvedValue(taskDraft)
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(PersonalizedRecommendationDraftView, { ...externalProps, diagnosis: matchedDiagnosis })
    mounted.push(app)
    const view = app.mount(host) as unknown as { generate: () => Promise<void> }
    await settle()
    await view.generate()
    await settle()
    const badge = host.querySelector('.personalized-stage-badge')?.textContent
    expect(badge).toBe('原小问任务匹配（已有解题步骤）')
    expect(badge).not.toContain('3级')
  })

  it('restores the saved draft after returning with unchanged settings', async () => {
    const host1 = document.createElement('div')
    document.body.append(host1)
    const app1 = createApp(PersonalizedRecommendationDraftView, externalProps)
    const view1 = app1.mount(host1) as unknown as { generate: () => Promise<void> }
    mounted.push(app1)
    await settle()
    await view1.generate()
    await settle()
    expect(trainingApiMock.createPersonalizedDraft).toHaveBeenCalledTimes(1)
    expect(host1.textContent).toContain('尚未形成正式训练卷')

    const host2 = document.createElement('div')
    document.body.append(host2)
    const app2 = createApp(PersonalizedRecommendationDraftView, externalProps)
    app2.mount(host2)
    mounted.push(app2)

    await vi.waitFor(() => {
      expect(host2.textContent).toContain('已恢复上次生成的草稿')
    })
    expect(trainingApiMock.getPersonalizedDraft).toHaveBeenCalledWith(draft.draft_id)
    expect(host2.textContent).toContain('直接巩固')
    expect(trainingApiMock.createPersonalizedDraft).toHaveBeenCalledTimes(1)
  })

  it('ignores the saved draft when paper settings changed', async () => {
    const host1 = document.createElement('div')
    document.body.append(host1)
    const app1 = createApp(PersonalizedRecommendationDraftView, externalProps)
    const view1 = app1.mount(host1) as unknown as { generate: () => Promise<void> }
    mounted.push(app1)
    await settle()
    await view1.generate()
    await settle()

    const host2 = document.createElement('div')
    document.body.append(host2)
    const app2 = createApp(PersonalizedRecommendationDraftView, {
      ...externalProps,
      questionCount: 10,
    })
    app2.mount(host2)
    mounted.push(app2)
    await settle()

    expect(trainingApiMock.getPersonalizedDraft).not.toHaveBeenCalled()
    expect(host2.textContent).not.toContain('已恢复上次生成的草稿')
  })

  it('discards the draft on demand and stops restoring it', async () => {
    const host1 = document.createElement('div')
    document.body.append(host1)
    const app1 = createApp(PersonalizedRecommendationDraftView, externalProps)
    const view1 = app1.mount(host1) as unknown as { generate: () => Promise<void> }
    mounted.push(app1)
    await settle()
    await view1.generate()
    await settle()

    expect(host1.textContent).toContain('草稿已自动暂存')
    host1.querySelector<HTMLButtonElement>('[data-testid="discard-paper-draft"]')!.click()
    await settle()
    expect(host1.textContent).toContain('确认放弃')
    host1.querySelector<HTMLButtonElement>('[data-testid="confirm-discard-paper-draft"]')!.click()
    await settle()

    expect(host1.textContent).toContain('已放弃该草稿')
    expect(host1.querySelector('.personalized-workbench')).toBeNull()
    expect(globalThis.localStorage.getItem('ai-grading:personalized-paper-draft:v1')).toBeNull()

    // 放弃后重新进入页面：不再自动恢复，教师重新生成会得到新草稿。
    const host2 = document.createElement('div')
    document.body.append(host2)
    const app2 = createApp(PersonalizedRecommendationDraftView, externalProps)
    app2.mount(host2)
    mounted.push(app2)
    await settle()

    expect(trainingApiMock.getPersonalizedDraft).not.toHaveBeenCalled()
    expect(host2.textContent).not.toContain('已恢复上次生成的草稿')
  })

  it('shows one representative evidence and previews bank questions', async () => {
    trainingApiMock.createPersonalizedDraft.mockResolvedValue(matchedDraft)
    const multiRefDiagnosis = {
      ...matchedDiagnosis,
      students: [{
        ...matchedDiagnosis.students[0]!,
        weak_points: [{
          ...matchedDiagnosis.students[0]!.weak_points[0]!,
          source_question_refs: [
            {
              session_id: 8,
              session_name: '历史考试',
              question_id: '9',
              bank_question_id: 77,
              score_awarded: 0,
              full_score: 8,
              score_rate: 0,
              source_kind: 'historical_exam' as const,
            },
            {
              session_id: 7,
              session_name: '合成考试',
              question_id: '5',
              bank_question_id: 88,
              score_awarded: 3,
              full_score: 10,
              score_rate: 0.3,
              source_kind: 'current_exam' as const,
            },
          ],
        }],
      }],
    } satisfies TrainingDiagnosis
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(PersonalizedRecommendationDraftView, {
      ...externalProps,
      diagnosis: multiRefDiagnosis,
    })
    const view = app.mount(host) as unknown as { generate: () => Promise<void> }
    mounted.push(app)
    await settle()
    await view.generate()
    await settle()

    // 代表错题：本次考试优先，即使历史考试失分更重。
    const evidence = host.querySelector<HTMLElement>('.personalized-match__evidence')!
    expect(evidence.querySelector('span[title]')?.textContent)
      .toContain('合成考试 · 第 5 题 · 得 3/10 分')
    expect(evidence.querySelector('summary')?.textContent).toContain('全部 2 条依据')

    const evidencePreview = [...evidence.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '预览')
    evidencePreview?.click()
    await vi.waitFor(() => {
      expect(document.body.textContent).toContain('错题题干')
    })
    expect(questionBankApiMock.getQuestion).toHaveBeenCalledWith(88, expect.anything())
    document.querySelector<HTMLButtonElement>('.question-preview__close')?.click()
    await settle()

    const actions = host.querySelector<HTMLElement>(
      '.personalized-match .personalized-item-actions',
    )!
    const stemPreview = [...actions.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '预览')
    stemPreview?.click()
    await vi.waitFor(() => {
      expect(questionBankApiMock.getQuestion).toHaveBeenCalledWith(31, expect.anything())
    })
  })

  it('aggregates sub-question evidence to the question level', async () => {
    trainingApiMock.createPersonalizedDraft.mockResolvedValue(matchedDraft)
    const subQuestionDiagnosis = {
      ...matchedDiagnosis,
      students: [{
        ...matchedDiagnosis.students[0]!,
        weak_points: [{
          ...matchedDiagnosis.students[0]!.weak_points[0]!,
          source_question_refs: [
            {
              session_id: 7,
              session_name: '合成考试',
              question_id: 'Q10(P1)',
              bank_question_id: 91,
              score_awarded: 0,
              full_score: 8,
              score_rate: 0,
              source_kind: 'current_exam' as const,
            },
            {
              session_id: 7,
              session_name: '合成考试',
              question_id: 'Q10(P2)',
              bank_question_id: 92,
              score_awarded: 2,
              full_score: 8,
              score_rate: 0.25,
              source_kind: 'current_exam' as const,
            },
            {
              session_id: 7,
              session_name: '合成考试',
              question_id: 'Q7',
              bank_question_id: 93,
              score_awarded: 0,
              full_score: 6,
              score_rate: 0,
              source_kind: 'current_exam' as const,
            },
          ],
        }],
      }],
    } satisfies TrainingDiagnosis
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(PersonalizedRecommendationDraftView, {
      ...externalProps,
      diagnosis: subQuestionDiagnosis,
    })
    const view = app.mount(host) as unknown as { generate: () => Promise<void> }
    mounted.push(app)
    await settle()
    await view.generate()
    await settle()

    // Q10(P1)+Q10(P2) 合并为题目层级的一条依据，合计得分。
    const evidence = host.querySelector<HTMLElement>('.personalized-match__evidence')!
    expect(evidence.querySelector('span[title]')?.textContent)
      .toContain('合成考试 · 第 Q10 题 · 得 2/16 分')
    expect(evidence.querySelector('summary')?.textContent).toContain('全部 2 条依据')
    expect(evidence.textContent).not.toContain('P1')

    // 预览打开失分最重的 P1 小问对应的题库原题。
    const preview = [...evidence.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '预览')
    preview?.click()
    await vi.waitFor(() => {
      expect(questionBankApiMock.getQuestion).toHaveBeenCalledWith(91, expect.anything())
    })
  })
})
