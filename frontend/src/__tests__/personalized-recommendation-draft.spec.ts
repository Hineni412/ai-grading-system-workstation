import { createApp, nextTick, type App } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { PersonalizedPaperBatch, PersonalizedPaperInstance, PersonalizedRecommendationDraft, TrainingDiagnosis } from '../api/training';
import PersonalizedRecommendationDraftView from '../components/training/PersonalizedRecommendationDraft.vue'
import { ApiError } from '../api/errors';

const trainingApiMock = vi.hoisted(() => ({
  createPersonalizedDraft: vi.fn(),
  exportHandout: vi.fn(),
  getHandoutExportByRequest: vi.fn(),
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

const jobApiMock = vi.hoisted(() => ({ getJob: vi.fn() }))
vi.mock('../api/jobs', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/jobs')>(), jobApi: jobApiMock,
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
  it('opens a fixed class draft by its id and continues printing without automatic reselection', async () => {
    const fixed = {...draft,config:{...draft.config,paper_mode:'shared',assembly_source:{source:'班级组卷 · 全班',title:'TEST-班级练习'}},
      students:draft.students.map(s=>({...s,items:s.items.map(i=>({...i,locked:true}))}))}
    trainingApiMock.getPersonalizedDraft.mockResolvedValue(fixed)
    const host=document.createElement('div');document.body.append(host)
    const app=createApp(PersonalizedRecommendationDraftView,{initialDraftId:fixed.draft_id,diagnosis:null,
      scope:{mode:'class',class_ids:['SYN-C01']},examScope:{mode:'manual',session_ids:[7]}})
    app.mount(host);mounted.push(app)
    await vi.waitFor(()=>expect(host.textContent).toContain('班级组卷 · 全班'))
    expect(host.textContent).toContain('TEST-班级练习')
    expect(host.querySelector('.personalized-student-list')?.textContent).toContain('合成学生')
    expect(trainingApiMock.createPersonalizedDraft).not.toHaveBeenCalled()
    expect(trainingApiMock.getPersonalizedDraft).toHaveBeenCalledWith(fixed.draft_id)
    expect([...host.querySelectorAll('button')].some(b=>['替换','移出','解锁'].includes(b.textContent?.trim()??''))).toBe(false)
  })
  it.each([
    { response: 'success', paperMode: 'individual' as const, skipped: 1 },
    { response: 'uncertain response', paperMode: 'individual' as const, skipped: 1 },
    { response: 'success', paperMode: 'shared' as const, skipped: 0 },
  ])('exports a 100 question $paperMode handout and restores it after $response without creating a training paper', async ({ response, paperMode, skipped }) => {
    const emptyStudent = { ...draft.students[0]!, student_id: 'SYN-S02', student_name: '合成空卷', items: [] }
    const handout = { ...draft, config: { purpose: 'handout', question_count: 100, paper_mode: paperMode, remediation_only: paperMode === 'individual' },
      students: (skipped ? [...draft.students, emptyStudent] : draft.students).map(student => ({ ...student,
        items: student.items.map((item, index) => ({ ...item, practice_purpose: index ? 'consolidation' as const : 'new' as const,
          knowledge_section: { id: 'kp_section', title: '合成章 · 合成节' }, primary_skill_name: '合成技能' })) })) }
    const job = { id: 81, status: 'succeeded', progress: 1, result: { download_url: '/api/jobs/81/download', skipped_student_count: skipped } }
    trainingApiMock.createPersonalizedDraft.mockResolvedValue(handout)
    trainingApiMock.getPersonalizedDraft.mockResolvedValue(handout)
    trainingApiMock.exportHandout.mockResolvedValue(job)
    if (response === 'uncertain response') trainingApiMock.exportHandout.mockRejectedValueOnce(new ApiError({
      kind: 'contract', status: 202, code: 'invalid_response', message: '合成异常响应',
      details: {}, requestId: 'synthetic', retryable: false,
    }))
    trainingApiMock.getHandoutExportByRequest.mockResolvedValue(job)
    const props = { diagnosis, scope: diagnosis.scope, examScope: { mode: 'current' as const, session_ids: [7] },
      questionCount: 100, purpose: 'handout', maxQuestionsPerSkill: 20, maxWrittenQuestions: 20,
      recentActivityCount: 0, difficultyMax: 10, excludeCurrentExamOriginals: true,
      targetKeys: ['kp_alg_linear_equation'], paperMode }
    const host = document.createElement('div'); document.body.append(host)
    const app = createApp(PersonalizedRecommendationDraftView, props)
    const view = app.mount(host) as unknown as { generate: () => Promise<void> }
    mounted.push(app)
    await settle(); await view.generate(); await settle()
    expect(host.querySelectorAll('.personalized-section-heading')).toHaveLength(1)
    expect(host.querySelector('.personalized-section-heading')?.textContent).toBe('合成章 · 合成节')
    expect(host.querySelector('.personalized-skill-note')?.textContent).toContain('技能：合成技能')
    if (paperMode === 'individual') expect(host.querySelector('.personalized-composition')?.textContent).toContain('补弱 0 题')
    expect(trainingApiMock.createPersonalizedDraft).toHaveBeenCalledWith(expect.objectContaining({
      purpose: 'handout', question_count: 100, max_questions_per_skill: 20,
      max_written_questions: 20, recent_activity_count: 0, difficulty_max: 10,
      paper_mode: paperMode, remediation_only: paperMode === 'individual',
      max_unmeasured_questions: paperMode === 'individual' ? 6 : 0, max_consolidation_questions: paperMode === 'individual' ? 6 : 0,
    }))
    expect(host.textContent).toContain('讲义只打印，不回收、不更新掌握度')
    expect(host.textContent).not.toContain('生成全部 PDF')
    expect(host.querySelector('.personalized-paper-panel')).toBeNull()
    host.querySelector<HTMLButtonElement>('[data-testid="export-handout"]')!.click()
    await vi.waitFor(() => expect(trainingApiMock.exportHandout).toHaveBeenCalledOnce())
    await settle()
    expect(trainingApiMock.exportHandout).toHaveBeenCalledExactlyOnceWith(draft.draft_id, expect.objectContaining({ expected_revision: 1 }))
    expect(trainingApiMock.createPaperBatch).not.toHaveBeenCalled()
    const token = trainingApiMock.exportHandout.mock.calls[0]![1].request_token
    mounted.splice(mounted.indexOf(app), 1); app.unmount()
    const returning = createApp(PersonalizedRecommendationDraftView, props)
    mounted.push(returning); returning.mount(host)
    await vi.waitFor(() => expect(trainingApiMock.getHandoutExportByRequest).toHaveBeenCalledWith(draft.draft_id, token))
    if (skipped) await vi.waitFor(() => expect(host.textContent).toContain('1 名学生暂无可配补弱题，已跳过空卷'))
    else expect(host.textContent).not.toContain('已跳过空卷')
    expect(trainingApiMock.exportHandout).toHaveBeenCalledExactlyOnceWith(draft.draft_id, expect.objectContaining({ expected_revision: 1 }))
  })

  it('lets the teacher select a target, generate, explain and lock an item', async () => {
    const workspaceChange = vi.fn()
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(PersonalizedRecommendationDraftView, {
      diagnosis,
      scope: { mode: 'student', student_ids: ['SYN-S01'] },
      examScope: { mode: 'current', session_ids: [7] },
      questionCount: 8,
      trainingIntent: 'challenge',
      onWorkspaceChange: workspaceChange,
      teachingProgressChapterId: 'bnu24-math-g8-upper-c02',
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
        remediation_only: true,
        max_unmeasured_questions: 4,
        question_count: 8,
        difficulty_max: 8,
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
    expect(host.textContent).toContain('2 页')
    expect(host.textContent).toContain('可打印')
    expect(host.textContent).toContain('下载 PDF 试卷')
    expect(workspaceChange).toHaveBeenLastCalledWith(expect.objectContaining({ current: 'print', revision: 2 }))
    expect(host.querySelector<HTMLDivElement>('.draft-review')?.style.display).toBe('none')
  })

  const externalProps = {
    diagnosis,
    externalSetup: true,
    scope: { mode: 'student', student_ids: ['SYN-S01'] },
    examScope: { mode: 'current', session_ids: [7] },
    questionCount: 8,
    excludeCurrentExamOriginals: true,
    targetKeys: ['knowledge_point:一元一次方程'],
  }

  it('remembers an uncertain request on a return visit and only checks its saved result', async () => {
    trainingApiMock.createPersonalizedDraft.mockRejectedValueOnce(new ApiError({ kind: 'timeout', status: null,
      code: 'request_timeout', message: 'timeout', details: {}, requestId: 'synthetic', retryable: false }))
    const recoveredDraft = { ...draft, config: { paper_mode: 'individual', question_count: 8 } }
    trainingApiMock.getPersonalizedDraftByRequest.mockRejectedValueOnce(new Error('not saved yet')).mockResolvedValueOnce(recoveredDraft)
    const host = document.createElement('div'); document.body.append(host)
    const first = createApp(PersonalizedRecommendationDraftView, externalProps)
    const view = first.mount(host) as unknown as { generate: () => Promise<void> }
    await settle(); await view.generate(); await settle()
    expect(host.textContent).toContain('后台可能仍在处理')
    first.unmount()
    const contextChange = vi.fn()
    const second = createApp(PersonalizedRecommendationDraftView, { ...externalProps, onContextChange: contextChange })
    mounted.push(second); second.mount(host)
    await vi.waitFor(() => expect(host.textContent).toContain('已取回上次请求生成的草稿'))
    expect(trainingApiMock.createPersonalizedDraft).toHaveBeenCalledOnce()
    expect(trainingApiMock.getPersonalizedDraftByRequest).toHaveBeenCalledTimes(2)
    expect(contextChange).toHaveBeenCalledWith(expect.objectContaining({ mode: 'individual', studentCount: 1, questionCount: 8 }))
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

})
