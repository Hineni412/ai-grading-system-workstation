import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  AiAssemblyPreflight,
  AiAssemblySelectResult,
  AiAssemblySpec,
} from '../api/ai-assembly'
import type { AssemblyDraft, AssemblyQuestion } from '../api/assembly'
import type { JobResponse } from '../api/jobs'
import { useAiAssemblyStore } from '../stores/ai-assembly'
import { useAssemblyStore } from '../stores/assembly'

const revisionA = 'a'.repeat(64)
const revisionB = 'b'.repeat(64)

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
    question_type: '选择题',
    question_text: `第 ${id} 题题干`,
    answer_text: '解析',
    difficulty: '5',
    paper_title: '匿名试卷',
    tags: [],
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

interface Scheduled {
  callback: () => void
  delay: number
}

function makeScheduler() {
  const scheduled: Scheduled[] = []
  return {
    scheduled,
    schedule: (callback: () => void, delay: number) => {
      scheduled.push({ callback, delay })
      return scheduled.length as unknown as ReturnType<typeof setTimeout>
    },
    cancelScheduled: vi.fn(),
    runNext(): void {
      const next = scheduled.shift()
      if (!next) throw new Error('no scheduled poll')
      next.callback()
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
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('ai-assembly store', () => {
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
})
