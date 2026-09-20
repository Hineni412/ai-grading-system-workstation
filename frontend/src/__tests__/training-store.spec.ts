import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/errors'
import type {
  TrainingDiagnosis,
  TrainingPlanResponse,
  TrainingTaskConfirmRequest,
} from '../api/training'
import type {
  TrainingTaskDetail,
  TrainingTaskList,
} from '../api/exports'
import type { JobResponse } from '../api/jobs'
import { useJobStore } from '../stores/jobs'
import { useTrainingStore } from '../stores/training'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

const diagnosis = {
  scope: { mode: 'selected', student_ids: ['12', '13'] },
  exam_scope: {
    mode: 'manual',
    session_ids: [14],
    sessions: [{ session_id: 14, session_name: '七年级阶段测验' }],
  },
  students: [{
    student_id: '12',
    student_code: '20260012',
    student_name: '张同学',
    class_id: '七年级一班',
    score_rate: 55,
    weak_points: [{
      knowledge_key: 'knowledge_point:三角形全等',
      knowledge_point: '三角形全等',
      mastery: 0.55,
      score_sum: 11,
      full_score_sum: 20,
      deduction_count: 2,
      evidence_count: 2,
      exam_count: 1,
      source_question_refs: [],
      actionable_reasons: ['辅助线思路缺失'],
      tag_context: { knowledge_point: ['三角形全等'] },
      error_counts: { primary: { 逻辑断裂: 1 } },
    }],
  }],
  coverage: {
    covered_items: 1,
    total_items: 2,
    missing_items: { Q2: '题目尚未关联题库来源' },
  },
  confirmed_concept_ids: [],
  suggested_terms: ['三角形全等'],
  unmapped_terms: [],
  warnings: [],
  diagnosis_identity: 'question_tag',
} satisfies TrainingDiagnosis

const planResponse = {
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
      stage_counts: { direct: 6, prerequisite: 3, transfer: 1 },
      shortages: [],
      warnings: [],
      dedupe_summary: { removed_count: 0, reason_counts: {} },
      generation_config: {},
    }],
    warnings: [],
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
  generation_config: { plan_revision: planResponse.plan_revision },
  warnings: [],
  status: 'ready',
  created_at: '2026-07-19T01:30:00Z',
  updated_at: '2026-07-19T01:30:00Z',
  diagnosis_snapshot: diagnosis,
  variants: [],
  exports: [],
} satisfies TrainingTaskDetail

const taskList = {
  items: [task],
  total: 1,
  page: 1,
  page_size: 20,
  total_pages: 1,
} satisfies TrainingTaskList

function makeTrainingApi(overrides: Record<string, unknown> = {}) {
  return {
    diagnose: vi.fn(async () => diagnosis),
    preview: vi.fn(async () => planResponse),
    confirm: vi.fn(async () => task),
    ...overrides,
  }
}

function makeHistoryApi(overrides: Record<string, unknown> = {}) {
  return {
    listTrainingTasks: vi.fn(async () => taskList),
    getTrainingTask: vi.fn(async () => task),
    submitTrainingExport: vi.fn(async () => makeJob()),
    retryTrainingExport: vi.fn(async () => makeJob({ id: 52 })),
    downloadJobFile: vi.fn(),
    ...overrides,
  }
}

function makeJob(overrides: Partial<JobResponse> = {}): JobResponse {
  return {
    id: 51,
    job_type: 'training_export',
    payload: { task_id: 31, format: 'docx' },
    status: 'queued',
    progress: 0,
    stage: 'queued',
    detail: '',
    result: {},
    error: null,
    cancel_requested: false,
    created_at: '2026-07-19T01:31:00Z',
    started_at: null,
    updated_at: '2026-07-19T01:31:00Z',
    finished_at: null,
    ...overrides,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.restoreAllMocks()
  vi.stubGlobal('crypto', {
    randomUUID: vi.fn(() => '12345678-1234-5678-1234-567812345678'),
  })
})

describe('training store', () => {
  it('reuses chapter previews by evidence content until the source, settings or workspace changes', () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000)
    const store = useTrainingStore()
    store.rememberGroupDiagnosis('chapter-a', diagnosis, diagnosis)
    expect(store.cachedGroupDiagnosis('chapter-a', diagnosis)).toEqual(diagnosis)
    expect(store.cachedGroupDiagnosis('chapter-b', diagnosis)).toBeNull()
    expect(store.cachedGroupDiagnosis('chapter-a', { ...diagnosis })).toEqual(diagnosis)
    const changed = structuredClone(diagnosis)
    changed.students[0]!.weak_points[0]!.score_sum = 12
    expect(store.cachedGroupDiagnosis('chapter-a', changed)).toBeNull()
    now.mockReturnValue(121_000)
    expect(store.cachedGroupDiagnosis('chapter-a', diagnosis)).toEqual(diagnosis)
    store.rememberGroupDiagnosis('chapter-a', diagnosis, diagnosis)
    store.reset()
    expect(store.cachedGroupDiagnosis('chapter-a', diagnosis)).toBeNull()
  })
  it('normalizes explicit scope and ignores a diagnosis from the old selection', async () => {
    const first = deferred<TrainingDiagnosis>()
    const second = deferred<TrainingDiagnosis>()
    const api = makeTrainingApi({
      diagnose: vi.fn()
        .mockReturnValueOnce(first.promise)
        .mockReturnValueOnce(second.promise),
    })
    const store = useTrainingStore()

    store.setStudentScope({
      mode: 'selected',
      studentIds: ['13', '12', '12'],
      classId: '',
    })
    store.setExamScope({
      mode: 'manual',
      sessionIds: [14, 14],
    })
    const oldRequest = store.analyze(api)

    store.setStudentScope({
      mode: 'student',
      studentIds: ['12'],
      classId: '',
    })
    const currentRequest = store.analyze(api)
    first.resolve(diagnosis)
    await oldRequest

    expect(store.diagnosis).toBeNull()
    expect(store.hasCurrentDiagnosis).toBe(false)
    second.resolve({
      ...diagnosis,
      scope: { mode: 'student', student_ids: ['12'] },
    })
    await currentRequest

    expect(api.diagnose).toHaveBeenNthCalledWith(1, {
      scope: { mode: 'selected', student_ids: ['12', '13'] },
      exam_scope: { mode: 'manual', session_ids: [14] },
    }, expect.any(AbortSignal))
    expect(store.diagnosis?.scope).toEqual({
      mode: 'student',
      student_ids: ['12'],
    })
  })

  it('invalidates the old preview when the scope changes', async () => {
    const api = makeTrainingApi()
    const store = useTrainingStore()
    store.setStudentScope({
      mode: 'selected',
      studentIds: ['12', '13'],
      classId: '',
    })
    store.setExamScope({ mode: 'manual', sessionIds: [14] })

    await store.analyze(api)
    await store.previewPlan({
      variantMode: 'individual',
      questionCount: 10,
      stageRatios: { direct: 0.6, prerequisite: 0.3, transfer: 0.1 },
      excludeCurrentExamOriginals: true,
    }, api)
    expect(store.plan?.plan_revision).toBe('a'.repeat(64))

    store.setExamScope({ mode: 'current', sessionIds: [15] })

    expect(store.diagnosis).not.toBeNull()
    expect(store.hasCurrentDiagnosis).toBe(false)
    expect(store.plan).toBeNull()
    expect(store.confirmationId).toBe('')
  })

  it('reuses one confirmation id for an ambiguous manual retry', async () => {
    const ambiguous = new ApiError({
      kind: 'network',
      status: null,
      code: 'network_error',
      message: 'Network request failed',
      details: {},
      requestId: 'req-1',
      retryable: true,
    })
    const confirm = vi.fn()
      .mockRejectedValueOnce(ambiguous)
      .mockResolvedValueOnce(task)
    const api = makeTrainingApi({ confirm })
    const store = useTrainingStore()
    store.setStudentScope({
      mode: 'selected',
      studentIds: ['12', '13'],
      classId: '',
    })
    store.setExamScope({ mode: 'manual', sessionIds: [14] })
    await store.analyze(api)
    await store.previewPlan({
      variantMode: 'individual',
      questionCount: 10,
      stageRatios: { direct: 0.6, prerequisite: 0.3, transfer: 0.1 },
      excludeCurrentExamOriginals: true,
    }, api)

    await expect(store.confirmPlan(api)).rejects.toBe(ambiguous)
    await store.confirmPlan(api)

    const bodies = confirm.mock.calls.map(([body]) => (
      body as TrainingTaskConfirmRequest
    ))
    expect(bodies[0]?.confirmation_id).toBe(
      '12345678-1234-5678-1234-567812345678',
    )
    expect(bodies[1]?.confirmation_id).toBe(bodies[0]?.confirmation_id)
    expect(store.confirmedTask?.id).toBe(31)
  })

  it('keeps the preview visible but blocks confirmation after a revision conflict', async () => {
    const conflict = new ApiError({
      kind: 'conflict',
      status: 409,
      code: 'training_confirmation_conflict',
      message: 'Training plan changed; refresh and confirm again',
      details: { current_plan_revision: 'b'.repeat(64) },
      requestId: 'req-conflict',
      retryable: false,
    })
    const api = makeTrainingApi({
      confirm: vi.fn(async () => {
        throw conflict
      }),
    })
    const store = useTrainingStore()
    store.setStudentScope({
      mode: 'selected',
      studentIds: ['12', '13'],
      classId: '',
    })
    store.setExamScope({ mode: 'manual', sessionIds: [14] })
    await store.analyze(api)
    await store.previewPlan({
      variantMode: 'individual',
      questionCount: 10,
      stageRatios: { direct: 0.6, prerequisite: 0.3, transfer: 0.1 },
      excludeCurrentExamOriginals: true,
    }, api)

    await expect(store.confirmPlan(api)).rejects.toBe(conflict)

    expect(store.plan).not.toBeNull()
    expect(store.confirmState).toBe('conflict')
    await expect(store.confirmPlan(api)).rejects.toThrow('请重新生成训练计划')
  })

  it('does not let an aborted confirmation overwrite the new scope state', async () => {
    const pending = deferred<TrainingTaskDetail>()
    const api = makeTrainingApi({
      confirm: vi.fn(() => pending.promise),
    })
    const store = useTrainingStore()
    store.setStudentScope({
      mode: 'selected',
      studentIds: ['12', '13'],
      classId: '',
    })
    store.setExamScope({ mode: 'manual', sessionIds: [14] })
    await store.analyze(api)
    await store.previewPlan({
      variantMode: 'individual',
      questionCount: 10,
      stageRatios: { direct: 0.6, prerequisite: 0.3, transfer: 0.1 },
      excludeCurrentExamOriginals: true,
    }, api)

    const confirmation = store.confirmPlan(api)
    store.setStudentScope({
      mode: 'student',
      studentIds: ['99'],
      classId: '',
    })
    pending.resolve(task)
    await expect(confirmation).rejects.toThrow()

    expect(store.confirmState).toBe('idle')
    expect(store.errorMessage).toBe('')
    expect(store.confirmedTask).toBeNull()
  })

  it('reuses the same confirmation id after returning to an unchanged preview', async () => {
    const ambiguous = new ApiError({
      kind: 'network',
      status: null,
      code: 'network_error',
      message: 'Network request failed',
      details: {},
      requestId: 'req-ambiguous',
      retryable: true,
    })
    const confirm = vi.fn()
      .mockRejectedValueOnce(ambiguous)
      .mockResolvedValueOnce(task)
    const api = makeTrainingApi({ confirm })
    vi.mocked(crypto.randomUUID)
      .mockReturnValueOnce('11111111-1111-4111-8111-111111111111')
      .mockReturnValueOnce('22222222-2222-4222-8222-222222222222')
    const store = useTrainingStore()
    const firstScope = {
      mode: 'selected' as const,
      studentIds: ['12', '13'],
      classId: '',
    }
    store.setStudentScope(firstScope)
    store.setExamScope({ mode: 'manual', sessionIds: [14] })
    await store.analyze(api)
    await store.previewPlan({
      variantMode: 'individual',
      questionCount: 10,
      stageRatios: { direct: 0.6, prerequisite: 0.3, transfer: 0.1 },
      excludeCurrentExamOriginals: true,
    }, api)
    await expect(store.confirmPlan(api)).rejects.toBe(ambiguous)

    store.setStudentScope({ mode: 'student', studentIds: ['99'], classId: '' })
    store.setStudentScope(firstScope)
    await store.analyze(api)
    await store.previewPlan({
      variantMode: 'individual',
      questionCount: 10,
      stageRatios: { direct: 0.6, prerequisite: 0.3, transfer: 0.1 },
      excludeCurrentExamOriginals: true,
    }, api)
    await store.confirmPlan(api)

    const confirmationIds = confirm.mock.calls.map(
      ([body]) => (body as TrainingTaskConfirmRequest).confirmation_id,
    )
    expect(confirmationIds).toEqual([
      '11111111-1111-4111-8111-111111111111',
      '11111111-1111-4111-8111-111111111111',
    ])
  })

  it('invalidates a preview when its visible plan configuration changes', async () => {
    const api = makeTrainingApi()
    const store = useTrainingStore()
    store.setStudentScope({
      mode: 'selected',
      studentIds: ['12', '13'],
      classId: '',
    })
    store.setExamScope({ mode: 'manual', sessionIds: [14] })
    await store.analyze(api)
    await store.previewPlan({
      variantMode: 'individual',
      questionCount: 10,
      stageRatios: { direct: 0.6, prerequisite: 0.3, transfer: 0.1 },
      excludeCurrentExamOriginals: true,
    }, api)

    store.invalidatePlan()

    expect(store.plan).toBeNull()
    expect(store.confirmationId).toBe('')
    expect(store.planState).toBe('idle')
    await expect(store.confirmPlan(api)).rejects.toThrow('请先生成当前训练计划')
  })

  it('loads history and tracks a submitted export job for refresh recovery', async () => {
    const historyApi = makeHistoryApi()
    const jobStore = useJobStore()
    const store = useTrainingStore()

    await store.loadTasks(historyApi)
    await store.selectTask(31, historyApi)
    const job = await store.submitExport({
      taskId: 31,
      format: 'docx',
    }, historyApi)

    expect(store.tasks.map(({ id }) => id)).toEqual([31])
    expect(store.selectedTask?.id).toBe(31)
    expect(jobStore.jobs[job.id]?.job_type).toBe('training_export')
    expect(JSON.parse(localStorage.getItem('ai-grading:tracked-jobs:v1')!))
      .toEqual([{
        id: 51,
        jobType: 'training_export',
        trackedAt: expect.any(String),
      }])
  })

  it('explains how to recover when a completed export file is unavailable', async () => {
    const historyApi = makeHistoryApi({
      downloadJobFile: vi.fn(async () => {
        throw new Error('gone')
      }),
    })
    const store = useTrainingStore()

    await expect(store.download(51, historyApi)).rejects.toThrow('gone')

    expect(store.errorMessage).toContain('重新生成')
  })
})
