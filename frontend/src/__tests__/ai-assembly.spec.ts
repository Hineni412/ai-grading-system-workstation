import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  AiAssemblyPreflight,
  AiAssemblySelectResult,
  AiAssemblySession,
  AiAssemblySessionWrite,
  AiAssemblySpec,
} from '../api/ai-assembly'
import type { AssemblyDraft, AssemblyQuestion } from '../api/assembly'
import { ApiError } from '../api/errors'
import type { JobResponse } from '../api/jobs'
import { useAiAssemblyStore } from '../stores/ai-assembly'
import { useAssemblyStore } from '../stores/assembly'

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

  it('fills only vacancies and remaps compressed rows around fully locked rows', async () => {
    const dependencies = makeDependencies(makeScheduler())
    const store = useAiAssemblyStore()
    store.configure(dependencies)
    store.spec = spec()
    store.selections = { 0: [11, 12], 1: [] }
    store.detailsById = { 11: question(11), 12: question(12) }
    store.toggleLock(11)
    store.toggleLock(12)
    dependencies.aiApi.select.mockResolvedValueOnce({ rows: [{ row_index: 0, question_ids: [13] }], gaps: [] })
    expect(await store.runSelect({ preserveExisting: true })).toBe(true)
    expect(dependencies.aiApi.select).toHaveBeenCalledWith(expect.objectContaining({ rows: [spec().rows[1]] }), expect.objectContaining({ exclude_ids: [11, 12] }))
    expect(store.selections).toEqual({ 0: [11, 12], 1: [13] })
    expect(store.lockedRowById).toEqual({ 11: 0, 12: 0 })
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

  it('commits a difficulty relaxation with partial fill without losing other rows', async () => {
    const dependencies = makeDependencies(makeScheduler())
    const store = useAiAssemblyStore()
    store.configure(dependencies)
    store.spec = spec()
    store.selections = { 0: [11], 1: [13] }
    store.detailsById = { 11: question(11), 13: question(13) }
    dependencies.aiApi.select.mockResolvedValueOnce({ rows: [{ row_index: 0, question_ids: [] }], gaps: [{ row_index: 0, missing: 1, candidates: 1, excluded_by_dedupe: 0, suggestions: [] }] })
    expect(await store.runSelect({ preserveExisting: true, rowIndex: 0, relaxation: 'difficulty' })).toBe(true)
    expect(store.spec?.rows[0]?.difficulty).toBeNull()
    expect(store.spec?.scope_knowledge_points).toEqual(spec().scope_knowledge_points)
    expect(store.selections).toEqual({ 0: [11], 1: [13] })
    expect(store.gaps[0]?.missing).toBe(1)
  })

  it('rejects repeated requests and ignores a response for an obsolete configuration', async () => {
    const dependencies = makeDependencies(makeScheduler())
    const store = useAiAssemblyStore()
    store.configure(dependencies)
    store.spec = spec()
    store.selections = { 0: [7] }
    let finish!: (result: AiAssemblySelectResult) => void
    dependencies.aiApi.select.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    const pending = store.runSelect()
    expect(await store.runSelect()).toBe(false)
    store.spec.rows[0]!.count = 3
    finish({ rows: [{ row_index: 0, question_ids: [11, 12] }], gaps: [] })
    expect(await pending).toBe(false)
    expect(store.selections).toEqual({ 0: [7] })
    expect(store.spec.rows[0]!.count).toBe(3)
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

  it('gets replacement candidates through assembly constraints and rejects an incompatible replacement', async () => {
    const dependencies = makeDependencies(makeScheduler())
    const store = useAiAssemblyStore()
    store.configure(dependencies)
    store.spec = spec()
    store.selections = { 0: [11], 1: [13] }
    dependencies.aiApi.select.mockResolvedValueOnce({ rows: [{ row_index: 0, question_ids: [21, 22] }], gaps: [] })
    dependencies.assemblyApi.resolveQuestions.mockImplementation(async (ids) => ({ items: ids.map((id) => id === 22 ? { ...question(id), question_type: '填空题' } : question(id)), missing_question_ids: [] }))
    expect((await store.listReplacements(0, 11)).map((item) => item.id)).toEqual([21])
    expect(dependencies.aiApi.select).toHaveBeenCalledWith(expect.objectContaining({ scope_knowledge_points: spec().scope_knowledge_points }), expect.objectContaining({ exclude_ids: [11, 13] }))
    expect(await store.replaceQuestion(0, 11, 22)).toBe(false)
    expect(store.selections[0]).toEqual([11])
  })

  it('keeps the previous selection when new question details fail to load', async () => {
    const dependencies = makeDependencies(makeScheduler())
    const store = useAiAssemblyStore()
    store.configure(dependencies)
    store.spec = spec()
    store.selections = { 0: [7] }
    store.detailsById = { 7: question(7) }
    dependencies.assemblyApi.resolveQuestions.mockRejectedValueOnce(new Error('offline'))
    expect(await store.runSelect()).toBe(false)
    expect(store.selections).toEqual({ 0: [7] })
    expect(store.detailsById[7]).toEqual(question(7))
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

  it('regenerates without the original free text, passing only the new instruction', async () => {
    const scheduler = makeScheduler()
    const dependencies = makeDependencies(scheduler)
    const store = useAiAssemblyStore()
    store.configure(dependencies)

    // 首次生成携带口语描述。
    store.params.freeText = '重点考勾股定理的应用'
    dependencies.aiApi.submitSpecJob.mockResolvedValueOnce(job(43, {
      status: 'succeeded',
      progress: 1,
      result: { spec: spec(), model_name: 'qwen-plus' },
      finished_at: '2026-09-01T10:01:00Z',
    }))
    await store.confirmAndSubmitSpec()
    expect(dependencies.aiApi.submitSpecJob).toHaveBeenLastCalledWith(
      expect.objectContaining({ free_text: '重点考勾股定理的应用', new_instruction: '' }),
    )
    expect(store.phase).toBe('spec')

    // 重生成：free_text 置空，只通过 new_instruction 传新指令。
    dependencies.aiApi.submitSpecJob.mockResolvedValueOnce(job(44, {
      status: 'succeeded',
      progress: 1,
      result: { spec: spec(), model_name: 'qwen-plus' },
      finished_at: '2026-09-01T10:02:00Z',
    }))
    await store.confirmAndSubmitSpec('加一道几何题')
    expect(dependencies.aiApi.submitSpecJob).toHaveBeenLastCalledWith(
      expect.objectContaining({
        free_text: '',
        new_instruction: '加一道几何题',
        current_spec: expect.objectContaining({ title: '勾股定理小测' }),
      }),
    )
  })

  it('leaves difficulty ratio empty in template mode and restores defaults in free mode', () => {
    const store = useAiAssemblyStore()
    store.configure(makeDependencies(makeScheduler()))

    expect(store.params.difficultyRatio).toEqual({ easy: 30, medium: 50, hard: 20 })

    // 选择模板：比例清空，提交时不发送 difficulty_ratio。
    store.setTemplatePaper(7)
    expect(store.params.difficultyRatio).toEqual({ easy: null, medium: null, hard: null })
    expect(store.buildRequest().difficulty_ratio).toEqual({})

    // 用户主动填了才发送比例。
    store.params.difficultyRatio = { ...store.params.difficultyRatio, medium: 60 }
    expect(store.buildRequest().difficulty_ratio).toEqual({ medium: 60 })

    // 回到自由组卷：恢复默认预填。
    store.setTemplatePaper(null)
    expect(store.params.difficultyRatio).toEqual({ easy: 30, medium: 50, hard: 20 })
  })

  it('cascades scope checks to descendants and allows individual removal', () => {
    const store = useAiAssemblyStore()
    store.configure(makeDependencies(makeScheduler()))

    store.setScopeChecked('chapter-1', true, ['section-1', 'kp-1', 'kp-2'])
    expect(store.params.scopeKeys).toEqual(['chapter-1', 'section-1', 'kp-1', 'kp-2'])

    // 展开后单独取消一个知识点，兄弟与父节点保留。
    store.setScopeChecked('kp-1', false)
    expect(store.params.scopeKeys).toEqual(['chapter-1', 'section-1', 'kp-2'])

    // 取消章节连带取消全部子孙。
    store.setScopeChecked('chapter-1', false, ['section-1', 'kp-1', 'kp-2'])
    expect(store.params.scopeKeys).toEqual([])
  })

  it('exposes gap rows with relaxation suggestions after select', async () => {
    const scheduler = makeScheduler()
    const dependencies = makeDependencies(scheduler)
    const store = useAiAssemblyStore()
    store.configure(dependencies)

    dependencies.aiApi.submitSpecJob.mockResolvedValueOnce(job(43, {
      status: 'succeeded',
      progress: 1,
      result: { spec: spec(), model_name: 'qwen-plus' },
      finished_at: '2026-09-01T10:01:00Z',
    }))
    // 提交即终态的 job 不需要轮询。
    await store.confirmAndSubmitSpec()
    expect(store.phase).toBe('spec')

    dependencies.aiApi.select.mockResolvedValueOnce({
      rows: [{ row_index: 0, question_ids: [11, 12] }],
      gaps: [{
        row_index: 1,
        missing: 1,
        candidates: 0,
        excluded_by_dedupe: 2,
        suggestions: [{
          step: 'disable_dedupe',
          title: '关闭去重',
          sacrifice: '最近组卷已用过的题目可能再次出现。',
          knowledge_points: [],
        }],
      }],
    })
    await store.runSelect()

    const gap = store.gapByRow.get(1)
    expect(gap?.missing).toBe(1)
    expect(gap?.suggestions[0]?.step).toBe('disable_dedupe')
  })

  it('keeps the unconfigured preflight for the guidance copy', async () => {
    const scheduler = makeScheduler()
    const dependencies = makeDependencies(scheduler)
    dependencies.aiApi.getPreflight.mockResolvedValueOnce(preflight({
      configured: false,
      service_name: null,
      model_name: null,
    }))
    const store = useAiAssemblyStore()
    store.configure(dependencies)

    await store.loadPreflight()

    expect(store.preflight?.configured).toBe(false)
    expect(store.preflight?.model_name).toBeNull()
    expect(store.preflightState).toBe('ready')
  })

  it('surfaces job failure without retrying the model call', async () => {
    const scheduler = makeScheduler()
    const dependencies = makeDependencies(scheduler)
    dependencies.jobApi.getJob.mockResolvedValueOnce(job(41, {
      status: 'failed',
      error: '细目表解析失败',
      finished_at: '2026-09-01T10:01:00Z',
    }))
    const store = useAiAssemblyStore()
    store.configure(dependencies)

    await store.confirmAndSubmitSpec()
    scheduler.runNext()
    await vi.waitFor(() => expect(store.phase).toBe('params'))

    expect(store.error).toContain('细目表解析失败')
    expect(dependencies.aiApi.submitSpecJob).toHaveBeenCalledTimes(1)
    expect(scheduler.scheduled).toHaveLength(0)
  })

  it('shows a dedicated message when select exceeds the wait limit', async () => {
    const scheduler = makeScheduler()
    const dependencies = makeDependencies(scheduler)
    dependencies.aiApi.select.mockRejectedValueOnce(new ApiError({
      kind: 'timeout',
      status: null,
      code: 'request_timeout',
      message: '请求超时',
      details: {},
      requestId: 'req-timeout',
      retryable: false,
    }))
    const store = useAiAssemblyStore()
    store.configure(dependencies)
    store.spec = spec()

    expect(await store.runSelect()).toBe(false)
    expect(store.error).toBe('选题耗时过长（已超过等待上限），请重试。')
  })

  it('passes through the backend validation reason for invalid specs', async () => {
    const scheduler = makeScheduler()
    const dependencies = makeDependencies(scheduler)
    dependencies.aiApi.select.mockRejectedValueOnce(new ApiError({
      kind: 'validation',
      status: 422,
      code: 'ai_assembly_spec_invalid',
      message: 'Assembly spec is invalid',
      details: { reason: '第 2 行题型为空' },
      requestId: 'req-invalid',
      retryable: false,
    }))
    const store = useAiAssemblyStore()
    store.configure(dependencies)
    store.spec = spec()

    expect(await store.runSelect()).toBe(false)
    expect(store.error).toBe('第 2 行题型为空')
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

  it('saves the session after a debounce once state changes', async () => {
    const scheduler = makeScheduler()
    const dependencies = makeDependencies(scheduler)
    const store = useAiAssemblyStore()
    store.configure(dependencies)

    await store.restoreSession()
    expect(dependencies.aiApi.getSession).toHaveBeenCalledTimes(1)
    // 空会话不覆盖内存默认状态。
    expect(store.params.difficultyRatio).toEqual({ easy: 30, medium: 50, hard: 20 })

    store.setScopeChecked('chapter-1', true, ['kp-1'])
    expect(scheduler.scheduled).toHaveLength(1)
    expect(scheduler.scheduled[0]?.delay).toBe(800)

    scheduler.runNext()
    await vi.waitFor(() => expect(dependencies.aiApi.saveSession).toHaveBeenCalledTimes(1))
    const [revision, payload] = dependencies.aiApi.saveSession.mock.calls[0]!
    expect(revision).toBe(revisionA)
    expect(payload.params.scope_keys).toEqual(['chapter-1', 'kp-1'])
    expect(payload.spec).toBeNull()
    expect(payload.spec_job_id).toBeNull()

    // 连续变更只保留最后一次防抖（前一次被取消）。
    dependencies.aiApi.saveSession.mockClear()
    store.setScopeChecked('chapter-2', true)
    store.params.freeText = '重点考应用'
    const pending = scheduler.scheduled.filter((entry) => !entry.canceled)
    expect(pending).toHaveLength(1)
    while (scheduler.scheduled.length) scheduler.runNext()
    await vi.waitFor(() => expect(dependencies.aiApi.saveSession).toHaveBeenCalledTimes(1))
    expect(dependencies.aiApi.saveSession.mock.calls[0]![1].params.free_text).toBe('重点考应用')
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

  it('resumes polling the spec job when the session has no spec yet', async () => {
    const scheduler = makeScheduler()
    const dependencies = makeDependencies(scheduler)
    dependencies.aiApi.getSession.mockResolvedValueOnce({
      ...emptySession(revisionA),
      spec_job_id: 41,
    })
    const store = useAiAssemblyStore()
    store.configure(dependencies)

    await store.restoreSession()
    expect(store.phase).toBe('generating')
    expect(store.jobId).toBe(41)
    expect(scheduler.scheduled).toHaveLength(1)

    scheduler.runNext()
    await vi.waitFor(() => expect(store.phase).toBe('spec'))
    expect(store.spec?.title).toBe('勾股定理小测')
    expect(store.specModelName).toBe('qwen-plus')
  })

  it('clears the persisted session on reset and keeps saving afterwards', async () => {
    const scheduler = makeScheduler()
    const dependencies = makeDependencies(scheduler)
    dependencies.aiApi.getSession.mockResolvedValueOnce({
      ...emptySession(revisionA),
      spec: spec(),
    })
    const store = useAiAssemblyStore()
    store.configure(dependencies)
    await store.restoreSession()
    expect(store.spec).not.toBeNull()

    store.reset()
    await vi.waitFor(() => expect(dependencies.aiApi.clearSession).toHaveBeenCalledTimes(1))
    expect(store.spec).toBeNull()
    expect(store.phase).toBe('params')
    expect(store.params.scopeKeys).toEqual([])
    // 复位本身不触发防抖保存。
    expect(scheduler.scheduled).toHaveLength(0)

    // 等 clearSession 回写新版本号（跨事件循环，等同真实用户操作的时机）。
    await new Promise((resolve) => setTimeout(resolve, 0))

    // 复位后的新进度从空会话版本继续保存。
    store.setScopeChecked('chapter-2', true)
    expect(scheduler.scheduled).toHaveLength(1)
    scheduler.runNext()
    await vi.waitFor(() => expect(dependencies.aiApi.saveSession).toHaveBeenCalledTimes(1))
    expect(dependencies.aiApi.saveSession.mock.calls[0]![0]).toBe(revisionC)
  })

  it('reloads the server session on a 409 conflict', async () => {
    const scheduler = makeScheduler()
    const dependencies = makeDependencies(scheduler)
    const store = useAiAssemblyStore()
    store.configure(dependencies)
    await store.restoreSession()

    dependencies.aiApi.saveSession.mockRejectedValueOnce(new ApiError({
      kind: 'conflict',
      status: 409,
      code: 'ai_assembly_session_conflict',
      message: 'AI assembly session has changed',
      details: { current_revision: revisionB },
      requestId: 'req-conflict',
      retryable: false,
    }))
    dependencies.aiApi.getSession.mockResolvedValueOnce({
      ...emptySession(revisionB),
      params: { ...emptySession().params, scope_keys: ['chapter-9'] },
    })

    store.setScopeChecked('chapter-1', true)
    scheduler.runNext()
    await vi.waitFor(() => expect(dependencies.aiApi.getSession).toHaveBeenCalledTimes(2))
    await vi.waitFor(() => expect(store.params.scopeKeys).toEqual(['chapter-9']))
    // 跨事件循环，等重新加载完全结束（等同真实用户操作的时机）。
    await new Promise((resolve) => setTimeout(resolve, 0))

    // 重新加载后以服务端版本继续保存。
    store.setScopeChecked('chapter-10', true)
    scheduler.runNext()
    await vi.waitFor(() => expect(dependencies.aiApi.saveSession).toHaveBeenCalledTimes(2))
    expect(dependencies.aiApi.saveSession.mock.calls[1]![0]).toBe(revisionB)
  })

  it('does not save before the session has been restored', async () => {
    const scheduler = makeScheduler()
    const dependencies = makeDependencies(scheduler)
    const store = useAiAssemblyStore()
    store.configure(dependencies)

    store.setScopeChecked('chapter-1', true)
    expect(scheduler.scheduled).toHaveLength(0)
    expect(dependencies.aiApi.saveSession).not.toHaveBeenCalled()
  })
})
