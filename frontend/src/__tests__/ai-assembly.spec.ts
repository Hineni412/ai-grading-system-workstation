import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { AiAssemblyPreflight, AiAssemblySelectResult, AiAssemblySession, AiAssemblySessionWrite, AiAssemblySpec } from '../api/ai-assembly';
import type { AssemblyDraft, AssemblyQuestion } from '../api/assembly';

import type { JobResponse } from '../api/jobs';
import { useAiAssemblyStore } from '../stores/ai-assembly';
import { useAssemblyStore } from '../stores/assembly';

const revisionA = 'a'.repeat(64)
const revisionB = 'b'.repeat(64)
const revisionC = 'c'.repeat(64)

function draft(patch: Partial<AssemblyDraft> = {}): AssemblyDraft {
  return {
    basket_ids: [],
    order_ids: [],
    sections: [],
    title: '',
    header_text: '',
    include_answer: true,
    layout_mode: 'sequential',
    preview_mode: 'teacher',
    revision: revisionA,
    ...patch,
  }
}

function question(id: number): AssemblyQuestion {
  return {
    id,
    revision: revisionA,
    question_number: String(id),
    question_type: id === 13 ? '填空题' : '选择题',
    question_text: `第 ${id} 题题干`,
    answer_text: '解析',
    difficulty: '5',
    paper_title: '匿名试卷',
    tags: [{ tag_type: 'knowledge_point', tag_value: id === 13 ? '实数' : '勾股定理', confidence: 1 }],
    asset_urls: [],
    score_value: 5,
  }
}

function spec(patch: Partial<AiAssemblySpec> = {}): AiAssemblySpec {
  return {
    title: '勾股定理小测',
    rows: [
      { question_type: '选择题', count: 2, knowledge_points: ['勾股定理'], difficulty: 4, score: 5 },
      { question_type: '填空题', count: 1, knowledge_points: ['实数'], difficulty: null, score: null },
    ],
    scope_knowledge_points: ['勾股定理', '实数'],
    ...patch,
  }
}

function job(id: number, patch: Partial<JobResponse> = {}): JobResponse {
  return {
    id,
    job_type: 'ai_assembly_spec',
    payload: {},
    result: {},
    status: 'running',
    progress: 0.5,
    stage: '生成细目表',
    detail: '正在整理需求',
    error: null,
    cancel_requested: false,
    created_at: '2026-09-01T10:00:00Z',
    started_at: '2026-09-01T10:00:01Z',
    updated_at: '2026-09-01T10:00:02Z',
    finished_at: null,
    ...patch,
  }
}

function preflight(patch: Partial<AiAssemblyPreflight> = {}): AiAssemblyPreflight {
  return {
    configured: true,
    service_name: '校内服务',
    model_name: 'qwen-plus',
    call_count: 1,
    estimated_total_tokens: 12000,
    ...patch,
  }
}

function emptySession(revision = revisionA): AiAssemblySession {
  return {
    params: {
      template_paper_id: null,
      scope_keys: [],
      difficulty_ratio: { easy: null, medium: null, hard: null },
      type_counts: {},
      exam_types: [],
      years: [],
      free_text: '',
      essay_subtype: null,
    },
    spec: null,
    spec_model_name: '',
    selections: {},
    locked_question_ids: [],
    locked_row_by_id: {},
    gaps: [],
    dedupe_enabled: true,
    title: '',
    spec_job_id: null,
    updated_at: '',
    revision,
  }
}

interface Scheduled {
  id: number
  callback: () => void
  delay: number
  canceled: boolean
}

function makeScheduler() {
  const scheduled: Scheduled[] = []
  let nextId = 0
  return {
    scheduled,
    schedule: (callback: () => void, delay: number) => {
      nextId += 1
      scheduled.push({ id: nextId, callback, delay, canceled: false })
      return nextId as unknown as ReturnType<typeof setTimeout>
    },
    cancelScheduled: vi.fn((handle: ReturnType<typeof setTimeout>) => {
      const entry = scheduled.find((item) => item.id === Number(handle))
      if (entry) entry.canceled = true
    }),
    runNext(): void {
      const next = scheduled.shift()
      if (!next) throw new Error('no scheduled poll')
      if (!next.canceled) next.callback()
    },
  }
}

function makeDependencies(scheduler: ReturnType<typeof makeScheduler>) {
  return {
    aiApi: {
      getPreflight: vi.fn(async (): Promise<AiAssemblyPreflight> => preflight()),
      submitSpecJob: vi.fn(async (): Promise<JobResponse> => job(41)),
      select: vi.fn(async (): Promise<AiAssemblySelectResult> => ({
        rows: [
          { row_index: 0, question_ids: [11, 12] },
          { row_index: 1, question_ids: [13] },
        ],
        gaps: [],
      })),
      getTemplateStructure: vi.fn(),
      getSession: vi.fn(async (): Promise<AiAssemblySession> => emptySession()),
      saveSession: vi.fn(
        async (_revision: string, session: AiAssemblySessionWrite): Promise<AiAssemblySession> => ({
          ...session,
          updated_at: '2026-09-01 10:03:00',
          revision: revisionB,
        }),
      ),
      clearSession: vi.fn(async (): Promise<AiAssemblySession> => emptySession(revisionC)),
    },
    jobApi: {
      getJob: vi.fn(async (): Promise<JobResponse> => job(41, {
        status: 'succeeded',
        progress: 1,
        result: { spec: spec(), model_name: 'qwen-plus' },
        finished_at: '2026-09-01T10:01:00Z',
      })),
      cancelJob: vi.fn(),
      getJobStatusBatch: vi.fn(),
    },
    assemblyApi: {
      resolveQuestions: vi.fn(async (ids: readonly number[]) => ({
        items: ids.map(question),
        missing_question_ids: [],
      })),
    },
    listSimilar: vi.fn(),
    schedule: scheduler.schedule,
    cancelScheduled: scheduler.cancelScheduled,
    pollIntervalMs: 1,
    maxBackoffMs: 10,
    sessionSaveDelayMs: 800,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('ai-assembly store', () => {
  it('asks before replacing a nonempty basket, then saves exactly the preview order', async () => {
    const store = useAiAssemblyStore()
    store.spec = spec()
    store.selections = { 0: [11, 12], 1: [13] }
    const assembly = useAssemblyStore()
    const saveDraft = vi.fn(async (_revision: string, next: AssemblyDraft) => ({ ...next, revision: revisionB }))
    await assembly.load({ api: {
      getDraft: vi.fn(async () => draft({ basket_ids: [13, 99], order_ids: [13, 99] })),
      saveDraft, resolveQuestions: vi.fn(async (ids: readonly number[]) => ({ items: ids.map(question), missing_question_ids: [] })),
      listRecords: vi.fn(async () => ({ items: [], total: 0 })), deleteRecord: vi.fn(), restoreRecord: vi.fn(), submitExport: vi.fn(), retryExport: vi.fn(),
    } })
    expect(await store.settle()).toBe(false)
    expect(saveDraft).not.toHaveBeenCalled()
    expect(assembly.draft.order_ids).toEqual([13, 99])
    expect(store.basketConflict).toBe(true)
    expect(await store.settle(true)).toBe(true)
    expect(assembly.draft.order_ids).toEqual([11, 12, 13])
    expect(assembly.draft.basket_ids).toEqual([11, 12, 13])
    expect(assembly.draft.title).toBe(spec().title)
    expect(store.basketConflict).toBe(false)
  })

  it('keeps all existing questions and scope when an expansion fails, then adds scope on success', async () => {
    const dependencies = makeDependencies(makeScheduler())
    const store = useAiAssemblyStore()
    store.configure(dependencies)
    store.spec = spec()
    store.selections = { 0: [11], 1: [13] }
    store.detailsById = { 11: question(11), 13: question(13) }
    const options = { preserveExisting: true, additionalKnowledgePoints: ['新章节'], additionalScopeKeys: ['chapter-new'] }
    dependencies.aiApi.select.mockRejectedValueOnce(new Error('offline'))
    expect(await store.runSelect(options)).toBe(false)
    expect(store.spec).toEqual(spec())
    expect(store.selections).toEqual({ 0: [11], 1: [13] })
    expect(store.params.scopeKeys).toEqual([])
    dependencies.aiApi.select.mockResolvedValueOnce({ rows: [{ row_index: 0, question_ids: [12] }], gaps: [] })
    expect(await store.runSelect(options)).toBe(true)
    expect(store.spec?.scope_knowledge_points).toEqual(['勾股定理', '实数', '新章节'])
    expect(store.selections).toEqual({ 0: [11, 12], 1: [13] })
    expect(store.params.scopeKeys).toEqual(['chapter-new'])
  })

  it('protects locked questions and template quantities during edits', () => {
    const store = useAiAssemblyStore()
    store.spec = spec()
    store.selections = { 0: [11, 12] }
    store.detailsById = { 11: question(11), 12: question(12) }
    store.toggleLock(11)
    store.toggleLock(12)
    store.applySpecEdit(0, { count: 1 })
    expect(store.spec.rows[0]!.count).toBe(2)
    store.applySpecEdit(0, { difficulty: 9 })
    expect(store.spec.rows[0]!.difficulty).toBe(4)
    expect(store.selections[0]).toEqual([11, 12])
    store.params.templatePaperId = 1
    store.applySpecEdit(0, { count: 3 })
    expect(store.spec.rows[0]!.count).toBe(2)
  })

  it('runs the full state machine: preflight → spec job → select → lock → regenerate → settle', async () => {
    const scheduler = makeScheduler()
    const dependencies = makeDependencies(scheduler)
    const store = useAiAssemblyStore()
    store.configure(dependencies)

    await store.loadPreflight()
    expect(store.preflight?.model_name).toBe('qwen-plus')
    expect(store.preflight?.configured).toBe(true)

    const submitted = await store.confirmAndSubmitSpec()
    expect(submitted).toBe(true)
    expect(store.phase).toBe('generating')
    expect(store.jobId).toBe(41)
    expect(dependencies.aiApi.submitSpecJob).toHaveBeenCalledWith(
      expect.objectContaining({ new_instruction: '', locked_question_ids: [] }),
    )
    expect(scheduler.scheduled).toHaveLength(1)

    scheduler.runNext()
    await vi.waitFor(() => expect(store.phase).toBe('spec'))
    expect(store.spec?.title).toBe('勾股定理小测')
    expect(store.specModelName).toBe('qwen-plus')

    expect(await store.runSelect()).toBe(true)
    expect(store.selections[0]).toEqual([11, 12])
    expect(store.selections[1]).toEqual([13])
    expect(store.detailsById[11]?.question_text).toContain('第 11 题')

    store.toggleLock(11)
    expect(store.lockedQuestionIds).toEqual([11])

    // 补充指令重生成：同一路径，携带当前细目表、锁定题与新指令。
    dependencies.aiApi.submitSpecJob.mockResolvedValueOnce(job(42))
    dependencies.jobApi.getJob.mockResolvedValueOnce(job(42, {
      status: 'succeeded',
      progress: 1,
      result: {
        spec: spec({ title: '勾股定理小测（修订）' }),
        model_name: 'qwen-plus',
      },
      finished_at: '2026-09-01T10:02:00Z',
    }))
    await store.confirmAndSubmitSpec('加一道几何题')
    expect(dependencies.aiApi.submitSpecJob).toHaveBeenLastCalledWith(
      expect.objectContaining({
        new_instruction: '加一道几何题',
        free_text: '',
        locked_question_ids: [11],
        current_spec: expect.objectContaining({ title: '勾股定理小测' }),
      }),
    )
    scheduler.runNext()
    await vi.waitFor(() => expect(store.spec?.title).toBe('勾股定理小测（修订）'))

    // 重选：锁定题留在原行最前，其余由新结果补齐。
    dependencies.aiApi.select.mockResolvedValueOnce({
      rows: [
        { row_index: 0, question_ids: [21, 22] },
        { row_index: 1, question_ids: [13] },
      ],
      gaps: [],
    })
    await store.runSelect()
    expect(store.selections[0]).toEqual([11, 21])
    expect(store.selections[1]).toEqual([13])

    // 换一题：本地替换，锁随题走。
    expect(await store.replaceQuestion(0, 11, 99)).toBe(true)
    expect(store.selections[0]).toEqual([99, 21])
    expect(store.lockedQuestionIds).toEqual([99])

    // 落卷：写入试卷篮并标记导出来源。
    const assembly = useAssemblyStore()
    await assembly.load({
      api: {
        getDraft: vi.fn(async () => draft()),
        saveDraft: vi.fn(async (_revision: string, nextDraft: AssemblyDraft) => ({
          ...nextDraft,
          revision: revisionB,
        })),
        resolveQuestions: vi.fn(async () => ({ items: [], missing_question_ids: [] })),
        listRecords: vi.fn(async () => ({ items: [], total: 0 })),
        deleteRecord: vi.fn(),
        restoreRecord: vi.fn(),
        submitExport: vi.fn(),
        retryExport: vi.fn(),
      },
    })

    expect(await store.settle()).toBe(true)
    expect(assembly.draft.order_ids).toEqual([99, 21, 13])
    expect(assembly.exportSource).toBe('ai')
  })

})

describe('ai-assembly session persistence', () => {
  it('serializes slow saves so newer locks are saved with the returned revision', async () => {
    const scheduler = makeScheduler()
    const dependencies = makeDependencies(scheduler)
    const store = useAiAssemblyStore()
    store.configure(dependencies)
    await store.restoreSession()
    let finish!: (saved: AiAssemblySession) => void
    dependencies.aiApi.saveSession.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    store.spec = spec()
    store.selections = { 0: [11] }
    while (scheduler.scheduled.length) scheduler.runNext()
    expect(dependencies.aiApi.saveSession).toHaveBeenCalledTimes(1)
    store.toggleLock(11)
    while (scheduler.scheduled.length) scheduler.runNext()
    expect(dependencies.aiApi.saveSession).toHaveBeenCalledTimes(1)
    finish({ ...emptySession(revisionB), spec: spec(), selections: { 0: [11] } })
    await vi.waitFor(() => expect(dependencies.aiApi.saveSession).toHaveBeenCalledTimes(2))
    expect(dependencies.aiApi.saveSession).toHaveBeenLastCalledWith(revisionB, expect.objectContaining({ locked_question_ids: [11], selections: { 0: [11] } }))
    expect(dependencies.aiApi.getSession).toHaveBeenCalledTimes(1)
  })

  it('restores params, spec, selections and locks from the persisted session', async () => {
    const scheduler = makeScheduler()
    const dependencies = makeDependencies(scheduler)
    dependencies.aiApi.getSession.mockResolvedValueOnce({
      ...emptySession(revisionA),
      params: {
        template_paper_id: 7,
        scope_keys: ['chapter-1'],
        difficulty_ratio: { easy: null, medium: null, hard: null },
        type_counts: { 选择题: 2 },
        exam_types: ['期末'],
        years: [2024],
        free_text: '出一份小测',
        essay_subtype: '证明',
      },
      spec: spec(),
      spec_model_name: 'qwen-plus',
      selections: { 0: [11, 12], 1: [13] },
      locked_question_ids: [11],
      locked_row_by_id: { 11: 0 },
      dedupe_enabled: false,
      title: '勾股定理小测',
    })
    const store = useAiAssemblyStore()
    store.configure(dependencies)

    await store.restoreSession()

    expect(store.params.templatePaperId).toBe(7)
    expect(store.params.scopeKeys).toEqual(['chapter-1'])
    expect(store.params.difficultyRatio).toEqual({ easy: null, medium: null, hard: null })
    expect(store.params.essaySubtype).toBe('证明')
    expect(store.phase).toBe('spec')
    expect(store.spec?.title).toBe('勾股定理小测')
    expect(store.specModelName).toBe('qwen-plus')
    expect(store.selections[0]).toEqual([11, 12])
    expect(store.selections[1]).toEqual([13])
    expect(store.lockedQuestionIds).toEqual([11])
    expect(store.lockedRowById).toEqual({ 11: 0 })
    expect(store.dedupeEnabled).toBe(false)
    // 题目详情不持久化，恢复后按选题重新拉取。
    expect(dependencies.assemblyApi.resolveQuestions).toHaveBeenCalledWith([11, 12, 13])
    expect(store.detailsById[11]?.question_text).toContain('第 11 题')
    // spec 已恢复，不恢复轮询也不立即回写。
    expect(scheduler.scheduled).toHaveLength(0)
    expect(dependencies.aiApi.saveSession).not.toHaveBeenCalled()
  })

})
