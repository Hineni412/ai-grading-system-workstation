import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ConfigEditorResponse, ConfigSource } from '../api/config-workspace'
import type { JobResponse } from '../api/jobs'
import { ApiError } from '../api/errors'
import {
  CONFIG_WORKSPACE_STORAGE_KEY,
  useConfigWorkspaceStore,
} from '../stores/config-workspace'
import { useJobStore } from '../stores/jobs'

function editor(answer: string): ConfigEditorResponse {
  return {
    session_id: 7,
    configured: true,
    revision: 'a'.repeat(64),
    rows: [{
      row_id: 'row-1', question_id: 'Q1', part_id: 'Q1', step_id: 's1',
      part_label: '第 1 题', question_type: 'calculation', core_goal: '计算', score: 5,
      standard_answer: answer, accepted_answers: [], match_rule: 'exact',
      answer_only_max_score: null, require_final_answer: null, required_elements: [],
      deduction_rules: [], part_deduction_rules: [], final_answer_rule: '',
    }],
    total_score: 100,
    issues: [],
    source: null,
  }
}

function source(id: string): ConfigSource {
  return {
    session_id: 7,
    source_id: id,
    source_revision: 'b'.repeat(64),
    safe_filename: '测试卷.pdf', suffix: '.pdf', size_bytes: 10,
    sha256_prefix: 'c'.repeat(12), parse_state: 'ready', questions: [],
  }
}

function job(overrides: Partial<JobResponse> = {}): JobResponse {
  return {
    id: 31, job_type: 'config_generation',
    payload: {
      session_id: 7,
      mode: 'generate',
      generation_mode: 'batched',
      source_id: 'd'.repeat(32),
      source_revision: 'b'.repeat(64),
    },
    result: {}, status: 'queued', progress: 0, stage: 'queued', detail: '', error: null,
    cancel_requested: false, created_at: '2026-07-15T00:00:00Z', started_at: null,
    updated_at: '2026-07-15T00:00:00Z', finished_at: null,
    ...overrides,
  }
}

function notFound(): ApiError {
  return new ApiError({
    kind: 'not_found', status: 404, code: 'not_found', message: 'not found',
    details: {}, requestId: 'test', retryable: false,
  })
}

function sourceChanged(): ApiError {
  return new ApiError({
    kind: 'conflict', status: 409, code: 'config_source_changed',
    message: 'source changed', details: {}, requestId: 'test', retryable: false,
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('configuration workspace Store', () => {
  it('restores a partial generation from the server without a browser job index', async () => {
    const store = useConfigWorkspaceStore()
    const partial = job({ status: 'succeeded', result: {
      outcome: 'partial', exam_intake_complete: false, score_allocation_pending: true,
    } })
    const loadLatestJob = vi.fn().mockResolvedValue(partial)
    await store.hydrateSafeIndex([7], 7, {
      loadActiveSource: async () => source('d'.repeat(32)),
      loadEditor: async () => editor('2'),
      loadLatestJob,
    })
    expect(loadLatestJob).toHaveBeenCalledWith(7, expect.objectContaining({
      source_id: 'd'.repeat(32), source_revision: 'b'.repeat(64),
    }))
    expect(store.jobId).toBe(31)
    expect(useJobStore().jobs[31]?.result.exam_intake_complete).toBe(false)
    expect(JSON.parse(localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)!).jobId).toBe(31)
  })

  it('ignores a restored generation when the teacher switches exams during lookup', async () => {
    const store = useConfigWorkspaceStore()
    let resolveJob!: (value: JobResponse) => void
    const lookup = new Promise<JobResponse>((resolve) => { resolveJob = resolve })
    const loadLatestJob = vi.fn().mockReturnValue(lookup)
    store.selectSession(7)
    const loading = store.loadSelectedSessionWorkspace(7, {
      loadActiveSource: async () => source('d'.repeat(32)),
      loadEditor: async () => editor('2'),
      loadLatestJob,
    })
    await vi.waitFor(() => expect(loadLatestJob).toHaveBeenCalled())
    store.selectSession(9)
    resolveJob(job({ status: 'succeeded' }))
    await loading
    expect(store.sessionId).toBe(9)
    expect(store.jobId).toBeNull()
  })

  it('replaces a remembered terminal job with its newer retry on reopening', async () => {
    localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, JSON.stringify({
      sessionId: 7, phase: 'generation', sourceId: 'd'.repeat(32),
      sourceRevision: 'b'.repeat(64), jobId: 31, decisions: [],
    }))
    const store = useConfigWorkspaceStore()
    const newer = job({ id: 32, status: 'succeeded', result: { generated_questions: 14 } })
    await store.hydrateSafeIndex([7], 7, {
      loadSource: async () => source('d'.repeat(32)),
      loadEditor: async () => editor('2'),
      loadJob: async () => job({ status: 'succeeded' }),
      loadLatestJob: async () => newer,
    })
    expect(store.jobId).toBe(32)
    expect(useJobStore().jobs[32]?.result.generated_questions).toBe(14)
  })

  it('builds generation requests from current source facts and invalidates old view updates', () => {
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    store.setSource(source('d'.repeat(32)))
    store.source!.questions = [
      { question_id: 'Q1', question_type: 'calculation', question_preview: '', answer_preview: '',
        answer_present: false, needs_review: false, local_answer_trusted: false,
        has_question_asset: false, has_answer_asset: false },
    ]
    store.updateDecisions([{ question_id: 'Q1', question_type: 'proof', excluded: false }])
    const generation = store.captureGenerationContext()

    expect(store.canGenerate).toBe(true)
    expect(store.sourceRequest('batched')).toEqual({
      source_id: 'd'.repeat(32), source_revision: 'b'.repeat(64),
      generation_mode: 'batched',
      decisions: [{ question_id: 'Q1', question_type: 'proof', excluded: false }],
      sync_to_question_bank: false,
    })
    store.acceptUploadedSource({ ...source('e'.repeat(32)), source_revision: 'f'.repeat(64) })
    expect(store.attachJob(31, generation)).toBe(false)
    expect(store.jobId).toBeNull()
  })

  it('keeps legacy answer fields transient and strips them from the safe index', () => {
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    store.setSource({
      ...source('d'.repeat(32)),
      questions: [{
        question_id: 'Q5', question_type: 'fill_blank', question_preview: '',
        answer_preview: '70°', answer_present: true, needs_review: true,
        local_answer_trusted: false, has_question_asset: false, has_answer_asset: false,
      }],
    })

    store.updateDecisions([{
      question_id: 'Q5',
      question_type: 'fill_blank',
      excluded: false,
      answer_confirmed: true,
      answer_override: '72°',
    }])

    expect(store.sourceRequest('batched').decisions).toEqual([{
      question_id: 'Q5',
      question_type: 'fill_blank',
      excluded: false,
      answer_confirmed: true,
      answer_override: '72°',
    }])
    const persisted = localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)!
    expect(JSON.parse(persisted).decisions).toEqual([{
      question_id: 'Q5',
      excluded: false,
    }])
    expect(persisted).not.toContain('72°')
  })

  it('requires every uncertain image to be resolved before the whole paper can generate', () => {
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    store.setSource({
      ...source('d'.repeat(32)),
      ambiguous_assets: [
        {
          candidate_id: 'A1',
          previous_question_id: 'Q1',
          next_question_id: 'Q2',
          source_section: 'question',
          asset_url: `/api/sessions/7/config/sources/${'d'.repeat(32)}/ambiguous-assets/A1`,
        },
        {
          candidate_id: 'A2',
          previous_question_id: 'Q2',
          next_question_id: 'Q3',
          source_section: 'answer',
          asset_url: `/api/sessions/7/config/sources/${'d'.repeat(32)}/ambiguous-assets/A2`,
        },
      ],
      questions: [
        {
          question_id: 'Q1', question_type: 'comprehensive', question_preview: '第一题',
          answer_preview: '', answer_present: false, needs_review: false,
          local_answer_trusted: false, has_question_asset: false, has_answer_asset: false,
        },
        {
          question_id: 'Q2', question_type: 'comprehensive', question_preview: '第二题',
          answer_preview: '', answer_present: false, needs_review: false,
          local_answer_trusted: false, has_question_asset: false, has_answer_asset: false,
        },
        {
          question_id: 'Q3', question_type: 'comprehensive', question_preview: '第三题',
          answer_preview: '', answer_present: false, needs_review: false,
          local_answer_trusted: false, has_question_asset: false, has_answer_asset: false,
        },
      ],
    })

    expect(store.canGenerate).toBe(false)
    store.updateAssetDecisions([{
      candidate_id: 'A1', action: 'bind', question_id: 'Q2', asset_kind: 'question',
    }])

    expect(store.canGenerate).toBe(false)
    store.updateAssetDecisions([
      { candidate_id: 'A1', action: 'bind', question_id: 'Q2', asset_kind: 'question' },
      { candidate_id: 'A2', action: 'bind', question_id: 'Q3', asset_kind: 'answer' },
    ])

    expect(store.canGenerate).toBe(true)
    expect(store.sourceRequest('batched').asset_decisions).toEqual([{
      candidate_id: 'A1', action: 'bind', question_id: 'Q2', asset_kind: 'question',
    }, {
      candidate_id: 'A2', action: 'bind', question_id: 'Q3', asset_kind: 'answer',
    }])
    expect(JSON.parse(localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)!).assetDecisions)
      .toEqual([
        { candidate_id: 'A1', action: 'bind', question_id: 'Q2', asset_kind: 'question' },
        { candidate_id: 'A2', action: 'bind', question_id: 'Q3', asset_kind: 'answer' },
      ])
  })

  it('restores a stored Job into JobStore and keeps process-restart failure detail', async () => {
    localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, JSON.stringify({
      sessionId: 7, phase: 'generation', sourceId: 'd'.repeat(32),
      sourceRevision: 'b'.repeat(64), jobId: 31, decisions: [],
    }))
    const recovered = job({
      status: 'failed', detail: '应用重启后，本次生成已停止。',
      finished_at: '2026-07-15T00:01:00Z',
    })
    const store = useConfigWorkspaceStore()
    await store.hydrateSafeIndex([7], 7, {
      loadSource: async () => source('d'.repeat(32)),
      loadJob: async () => recovered,
      loadEditor: async () => ({ ...editor(''), configured: false, rows: [] }),
    })

    expect(store.jobId).toBe(31)
    expect(useJobStore().jobs[31]?.detail).toBe('应用重启后，本次生成已停止。')
  })

  it('persists and restores only safe retry counts without retry relationship ids', async () => {
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    store.setSource(source('d'.repeat(32)))
    const context = store.captureGenerationContext()
    expect(store.attachJob(32, context, {
      totalQuestions: 5, succeededQuestions: 3, failedQuestions: 2,
    })).toBe(true)

    const saved = localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)!
    expect(JSON.parse(saved).generationSummary).toEqual({
      totalQuestions: 5, succeededQuestions: 3, failedQuestions: 2,
    })
    expect(saved).not.toMatch(/source_job_id|retry_question_ids|Q2|Q5/)

    setActivePinia(createPinia())
    localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, saved)
    const restored = useConfigWorkspaceStore()
    await restored.hydrateSafeIndex([7], 7, {
      loadSource: async () => source('d'.repeat(32)),
      loadJob: async () => job({ id: 32, status: 'failed' }),
      loadEditor: async () => ({ ...editor(''), configured: false, rows: [] }),
    })
    expect(restored.generationSummary).toEqual({
      totalQuestions: 5, succeededQuestions: 3, failedQuestions: 2,
    })
  })

  it('restores exact pending upload and job request tokens after a refresh', async () => {
    const uploadToken = '1'.repeat(32)
    const jobToken = '2'.repeat(32)
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    store.setSource(source('d'.repeat(32)))
    store.attachJob(31, store.captureGenerationContext())
    store.markUploadSubmissionPending(uploadToken)

    const uploadSaved = localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)!
    expect(JSON.parse(uploadSaved)).toMatchObject({
      pendingUploadRequestToken: uploadToken,
    })

    const jobSaved = {
      ...JSON.parse(uploadSaved),
      pendingJobRequestToken: jobToken,
      pendingJobRequestKind: 'retry',
      generationSummary: { totalQuestions: 5, succeededQuestions: 3, failedQuestions: 2 },
    }
    delete jobSaved.pendingUploadRequestToken

    setActivePinia(createPinia())
    localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, JSON.stringify(jobSaved))
    const restored = useConfigWorkspaceStore()
    await restored.hydrateSafeIndex([7], 7, {
      loadSource: async () => source('d'.repeat(32)),
      loadJob: async () => job(),
      loadEditor: async () => ({ ...editor(''), configured: false, rows: [] }),
    })

    expect(restored.pendingUploadRequestToken).toBeNull()
    expect(restored.pendingJobRequestToken).toBe(jobToken)
    expect(restored.pendingJobRequestKind).toBe('retry')
    expect(restored.generationSummary).toEqual({
      totalQuestions: 5, succeededQuestions: 3, failedQuestions: 2,
    })
  })

  it('loads active source and editor for a selected exam when no workspace cache exists', async () => {
    const store = useConfigWorkspaceStore()

    await store.hydrateSafeIndex([7], 7, {
      loadActiveSource: async () => source('d'.repeat(32)),
      loadEditor: async () => editor('42'),
    })

    expect(store.sessionId).toBe(7)
    expect(store.sourceId).toBe('d'.repeat(32))
    expect(store.editor?.rows[0]?.standard_answer).toBe('42')
    expect(store.phase).toBe('editor')
  })

  it('replaces a stale cached source after 409 without applying its decisions to the active source', async () => {
    const oldSourceId = 'd'.repeat(32)
    const activeSourceId = 'e'.repeat(32)
    localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, JSON.stringify({
      sessionId: 7, phase: 'generation', sourceId: oldSourceId,
      sourceRevision: 'b'.repeat(64), jobId: 31,
      decisions: [{ question_id: 'Q1', question_type: 'proof', excluded: true }],
    }))
    const loadSource = vi.fn(async () => { throw sourceChanged() })
    const loadActiveSource = vi.fn(async () => ({
      ...source(activeSourceId), source_revision: 'f'.repeat(64),
      questions: [{
        question_id: 'Q1', question_type: 'calculation', question_preview: '',
        answer_preview: '', answer_present: false, needs_review: false,
        local_answer_trusted: false, has_question_asset: false, has_answer_asset: false,
      }],
    }))
    const store = useConfigWorkspaceStore()

    await store.hydrateSafeIndex([7], 7, {
      loadSource,
      loadActiveSource,
      loadJob: async () => job(),
      loadEditor: async () => editor('current answer'),
    })

    expect(loadSource).toHaveBeenCalledExactlyOnceWith(7, oldSourceId)
    expect(loadActiveSource).toHaveBeenCalledExactlyOnceWith(7)
    expect(store.sourceId).toBe(activeSourceId)
    expect(store.sourceRevision).toBe('f'.repeat(64))
    expect(store.decisions).toEqual([])
    expect(store.jobId).toBeNull()
    expect(store.editor?.rows[0]?.standard_answer).toBe('current answer')
    expect(JSON.parse(localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)!)).toMatchObject({
      sessionId: 7,
      sourceId: activeSourceId,
      sourceRevision: 'f'.repeat(64),
      jobId: null,
      decisions: [],
    })
  })

  it('does not retry a replaced cached source id when the active-source lookup is temporarily unavailable', async () => {
    const oldSourceId = 'd'.repeat(32)
    localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, JSON.stringify({
      sessionId: 7, phase: 'source', sourceId: oldSourceId,
      sourceRevision: 'b'.repeat(64), jobId: null,
      decisions: [{ question_id: 'Q1', question_type: 'proof', excluded: true }],
    }))
    const firstStore = useConfigWorkspaceStore()
    await firstStore.hydrateSafeIndex([7], 7, {
      loadSource: async () => { throw sourceChanged() },
      loadActiveSource: async () => { throw new Error('offline') },
      loadEditor: async () => ({ ...editor(''), configured: false, rows: [] }),
    })

    expect(JSON.parse(localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)!)).toMatchObject({
      sourceId: null, sourceRevision: null, decisions: [],
    })

    setActivePinia(createPinia())
    const loadSource = vi.fn(async () => source(oldSourceId))
    const active = { ...source('e'.repeat(32)), source_revision: 'f'.repeat(64) }
    const loadActiveSource = vi.fn(async () => active)
    const restored = useConfigWorkspaceStore()
    await restored.hydrateSafeIndex([7], 7, {
      loadSource,
      loadActiveSource,
      loadEditor: async () => ({ ...editor(''), configured: false, rows: [] }),
    })

    expect(loadSource).not.toHaveBeenCalled()
    expect(loadActiveSource).toHaveBeenCalledExactlyOnceWith(7)
    expect(restored.sourceId).toBe(active.source_id)
  })

  it('recovers the active source when cleanup has removed an older cached source', async () => {
    const oldSourceId = 'd'.repeat(32)
    const activeSourceId = 'e'.repeat(32)
    localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, JSON.stringify({
      sessionId: 7, phase: 'generation', sourceId: oldSourceId,
      sourceRevision: 'b'.repeat(64), jobId: 31,
      decisions: [{ question_id: 'Q1', question_type: 'proof', excluded: true }],
    }))
    const active = { ...source(activeSourceId), source_revision: 'f'.repeat(64) }
    const loadActiveSource = vi.fn(async () => active)
    const store = useConfigWorkspaceStore()

    await store.hydrateSafeIndex([7], 7, {
      loadSource: async () => { throw notFound() },
      loadActiveSource,
      loadJob: async () => job(),
      loadEditor: async () => editor('current answer'),
    })

    expect(loadActiveSource).toHaveBeenCalledExactlyOnceWith(7)
    expect(store.sourceId).toBe(activeSourceId)
    expect(store.sourceRevision).toBe('f'.repeat(64))
    expect(store.decisions).toEqual([])
    expect(store.jobId).toBeNull()
    expect(JSON.parse(localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)!)).toMatchObject({
      sourceId: activeSourceId, sourceRevision: 'f'.repeat(64), jobId: null, decisions: [],
    })
  })

  it('keeps an editor usable when the selected exam has no P2 source', async () => {
    const store = useConfigWorkspaceStore()

    await store.hydrateSafeIndex([7], 7, {
      loadActiveSource: async () => { throw notFound() },
      loadEditor: async () => editor('legacy answer'),
    })
    const context = store.captureGenerationContext()
    expect(store.markJobSubmissionPending('3'.repeat(32), 'refine')).toBe(true)

    expect(store.attachJob(45, context)).toBe(true)
    expect(store.sourceId).toBeNull()
    expect(store.jobId).toBe(45)
  })

  it('persists only the safe workspace index and never rubric rows or answer text', () => {
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    store.setSource(source('d'.repeat(32)))
    store.setEditor(editor('标准答案正文'))
    store.updateEditor({ row_id: 'row-1', standard_answer: '本地修改答案' })
    store.persistSafeIndex()

    expect(JSON.parse(localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)!)).toEqual({
      sessionId: 7,
      phase: 'editor',
      sourceId: 'd'.repeat(32),
      sourceRevision: 'b'.repeat(64),
      jobId: null,
      decisions: [],
    })
    expect(localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)).not.toContain('标准答案正文')
    expect(localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)).not.toContain('本地修改答案')
  })

  it('rejects unsafe cached fields and freshly loads the selected exam', async () => {
    localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, JSON.stringify({
      sessionId: 99,
      phase: 'editor',
      sourceId: 'd'.repeat(32),
      sourceRevision: 'e'.repeat(64),
      jobId: 31,
      decisions: [],
      rows: [{ standard_answer: 'must not restore' }],
    }))
    const store = useConfigWorkspaceStore()
    await store.hydrateSafeIndex([7, 9], 7, {
      loadActiveSource: async () => { throw notFound() },
      loadSource: async () => source('d'.repeat(32)),
      loadJob: async () => job(),
      loadEditor: async () => editor('42'),
    })

    expect(store.sessionId).toBe(7)
    expect(store.sourceId).toBeNull()
    expect(store.editor?.rows[0]?.standard_answer).toBe('42')
    expect(localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)).not.toContain('must not restore')
  })

  it('keeps persisted source, job and phase as candidates until every fact is reloaded', async () => {
    localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, JSON.stringify({
      sessionId: 7, phase: 'editor', sourceId: 'd'.repeat(32),
      sourceRevision: 'b'.repeat(64), jobId: 31, decisions: [],
    }))
    const store = useConfigWorkspaceStore()
    let resolveSource!: (value: ConfigSource) => void
    let resolveJob!: (value: JobResponse) => void
    let resolveEditor!: (value: ConfigEditorResponse) => void
    const hydration = store.hydrateSafeIndex([7], 7, {
      loadSource: () => new Promise((resolve) => { resolveSource = resolve }),
      loadJob: () => new Promise((resolve) => { resolveJob = resolve }),
      loadEditor: () => new Promise((resolve) => { resolveEditor = resolve }),
    })

    expect(store.sessionId).toBe(7)
    expect(store.sourceId).toBeNull()
    expect(store.jobId).toBeNull()
    expect(store.phase).toBe('draft')

    resolveSource(source('d'.repeat(32)))
    resolveJob(job())
    resolveEditor({ ...editor(''), configured: false, rows: [] })
    await hydration

    expect(store.sourceId).toBe('d'.repeat(32))
    expect(store.jobId).toBe(31)
    expect(store.phase).toBe('generation')
  })

  it('clears not-found or foreign source/job candidates but retains transient candidates', async () => {
    const saved = {
      sessionId: 7, phase: 'generation' as const, sourceId: 'd'.repeat(32),
      sourceRevision: 'b'.repeat(64), jobId: 31, decisions: [],
    }
    localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, JSON.stringify(saved))
    const store = useConfigWorkspaceStore()
    await store.hydrateSafeIndex([7], 7, {
      loadSource: async () => { throw notFound() },
      loadJob: async () => job({ payload: { session_id: 9 } }),
      loadEditor: async () => ({ ...editor(''), configured: false, rows: [] }),
    })
    expect(JSON.parse(localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)!)).toMatchObject({
      sourceId: null, sourceRevision: null, jobId: null,
    })

    localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, JSON.stringify(saved))
    const retryStore = useConfigWorkspaceStore()
    await retryStore.hydrateSafeIndex([7], 7, {
      loadSource: async () => { throw new Error('offline') },
      loadJob: async () => { throw new Error('offline') },
      loadEditor: async () => { throw new Error('offline') },
    })
    expect(JSON.parse(localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)!)).toEqual(saved)
    expect(retryStore.sourceId).toBeNull()
    expect(retryStore.jobId).toBeNull()
    expect(retryStore.phase).toBe('draft')
  })

  it('ignores all hydration responses after a later session selection', async () => {
    localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, JSON.stringify({
      sessionId: 7, phase: 'editor', sourceId: 'd'.repeat(32),
      sourceRevision: 'b'.repeat(64), jobId: 31, decisions: [],
    }))
    const store = useConfigWorkspaceStore()
    let resolveEditor!: (value: ConfigEditorResponse) => void
    const hydration = store.hydrateSafeIndex([7, 9], 7, {
      loadSource: async () => source('d'.repeat(32)),
      loadJob: async () => job(),
      loadEditor: () => new Promise((resolve) => { resolveEditor = resolve }),
    })
    store.selectSession(9)
    resolveEditor(editor('old answer'))
    await hydration

    expect(store.sessionId).toBe(9)
    expect(store.editor).toBeNull()
    expect(store.phase).toBe('draft')
  })

  it('keeps editor dirty until an authoritative reload or save succeeds', () => {
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    store.setEditor(editor('42'))
    expect(store.hasDirtyEditor).toBe(false)

    store.updateEditor({ row_id: 'row-1', standard_answer: '43' })
    expect(store.hasDirtyEditor).toBe(true)
    store.noteSaveFailed()
    expect(store.hasDirtyEditor).toBe(true)

    store.selectSource('f'.repeat(32))
    expect(store.hasDirtyEditor).toBe(true)

    store.setEditor(editor('43'))
    expect(store.hasDirtyEditor).toBe(false)
  })

  it('invalidates one editor context token after every draft or authority change', () => {
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    store.setSource(source('d'.repeat(32)))
    store.setEditor(editor('42'))

    let token = store.captureEditorContext()
    expect(store.isEditorContextCurrent(token)).toBe(true)
    store.updateEditor({ row_id: 'row-1', standard_answer: '43' })
    expect(store.isEditorContextCurrent(token)).toBe(false)

    token = store.captureEditorContext()
    store.addEditorCommand({ kind: 'split', question_id: 'Q1', count: 2, style: 'blank' })
    expect(store.isEditorContextCurrent(token)).toBe(false)

    token = store.captureEditorContext()
    store.setEditor({ ...editor('server reload'), revision: 'c'.repeat(64) })
    expect(store.isEditorContextCurrent(token)).toBe(false)

    token = store.captureEditorContext()
    store.acceptUploadedSource({ ...source('e'.repeat(32)), source_revision: 'f'.repeat(64) })
    expect(store.isEditorContextCurrent(token)).toBe(false)
  })

  it('keeps 422 issue rows inside the current editor and rejects non-editable fields', () => {
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    store.setEditor(editor('42'))
    const maliciousRow = 'row-1\"] [data-edit-field=\"standard_answer'
    const rowIssue = new ApiError({
      kind: 'validation', status: 422, code: 'invalid_config_editor', message: 'invalid',
      details: { issues: [
        { code: 'invalid_score', severity: 'error', row_id: 'row-1', field: 'score', message: 'known' },
        { code: 'invalid_score', severity: 'error', row_id: maliciousRow, field: 'score', message: 'unknown row' },
      ] }, requestId: 'safe', retryable: false,
    })

    expect(store.recordServerIssues(rowIssue)).toBe(true)
    expect(store.serverIssues).toEqual([
      { code: 'invalid_score', severity: 'error', row_id: 'row-1', field: 'score', message: 'known' },
      { code: 'invalid_score', severity: 'error', row_id: null, field: 'score', message: 'unknown row' },
    ])

    const unknownField = new ApiError({
      kind: 'validation', status: 422, code: 'invalid_config_editor', message: 'invalid',
      details: { issues: [{
        code: 'invalid_field', severity: 'error', row_id: 'row-1',
        field: 'score\"] [data-edit-field="standard_answer', message: 'unknown field',
      }] }, requestId: 'safe', retryable: false,
    })
    expect(store.recordServerIssues(unknownField)).toBe(false)
    expect(store.serverIssues).toEqual([])
  })

  it('retains safe global 422 messages and clears stale validation when the draft changes', () => {
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    store.setEditor(editor('旧答案'))
    const invalid = new ApiError({ kind: 'validation', status: 422,
      code: 'invalid_config_editor', message: 'invalid', requestId: 'safe', retryable: false,
      details: { issues: [
        { code: 'invalid_score', severity: 'error', row_id: 'row-1', field: 'score', message: '核对分值' },
        { code: 'future_policy', severity: 'error', row_id: 'row-1', field: 'future_policy', message: '核对新策略' },
      ] } })

    expect(store.recordServerIssues(invalid)).toBe(true)
    expect(store.serverIssues).toEqual([
      { code: 'invalid_score', severity: 'error', row_id: 'row-1', field: 'score', message: '核对分值' },
      { code: 'future_policy', severity: 'error', row_id: null, field: 'future_policy', message: '核对新策略' },
    ])
    store.updateEditor({ row_id: 'row-1', score: 6 })
    expect(store.serverIssues).toEqual([])
  })

  it('builds a single save request from the loaded revision and returns effective rows', () => {
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    store.setEditor(editor('42'))
    store.updateEditor({ row_id: 'row-1', score: 100, standard_answer: '43' })
    store.addEditorCommand({ kind: 'split', question_id: 'Q1', count: 2, style: 'blank' })

    expect(store.effectiveEditorRows[0]).toMatchObject({ score: 100, standard_answer: '43' })
    expect(store.effectiveTotalScore).toBe(100)
    expect(store.buildSaveRequest()).toEqual({
      revision: 'a'.repeat(64),
      edits: [{ row_id: 'row-1', score: 100, standard_answer: '43' }],
      commands: [{ kind: 'split', question_id: 'Q1', count: 2, style: 'blank' }],
    })
  })

  it('keeps an explicit answer-only policy clear in the draft and save request', () => {
    const store = useConfigWorkspaceStore()
    const loaded = editor('42')
    loaded.rows[0]!.answer_only_max_score = 2
    store.selectSession(7)
    store.setEditor(loaded)

    store.updateEditor({ row_id: 'row-1', answer_only_max_score: null })

    expect(store.effectiveEditorRows[0]?.answer_only_max_score).toBeNull()
    expect(store.buildSaveRequest().edits).toEqual([
      { row_id: 'row-1', answer_only_max_score: null },
    ])
  })

  it('refuses destructive session/source clearing until discard is explicit', () => {
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    store.setEditor(editor('42'))
    store.updateEditor({ row_id: 'row-1', standard_answer: '43' })
    store.addEditorCommand({ kind: 'split', question_id: 'Q1', count: 2, style: 'subquestion' })

    expect(store.selectSession(9)).toBe(false)
    expect(store.selectSource('f'.repeat(32))).toBe(false)
    expect(store.clearWorkspace()).toBe(false)
    expect(store.sessionId).toBe(7)
    expect(store.editor?.rows[0]?.standard_answer).toBe('42')
    expect(store.editorEdits).toHaveLength(1)
    expect(store.editorCommands).toHaveLength(1)

    store.discardEditorDraft()
    expect(store.selectSession(9)).toBe(true)
    expect(store.sessionId).toBe(9)
    expect(store.hasDirtyEditor).toBe(false)
  })

  it('refuses session changes and token replacement while a write result is unknown', () => {
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    expect(store.markJobSubmissionPending('1'.repeat(32), 'refine')).toBe(true)

    expect(store.markJobSubmissionPending('2'.repeat(32), 'generate', 'batched')).toBe(false)
    expect(store.markUploadSubmissionPending('3'.repeat(32))).toBe(false)
    expect(store.selectSession(9, true)).toBe(false)
    expect(store.selectSource('f'.repeat(32), true)).toBe(false)
    expect(store.clearWorkspace(true)).toBe(false)
    expect(store.sessionId).toBe(7)
    expect(store.pendingJobRequestToken).toBe('1'.repeat(32))
  })

  it('includes an unknown upload in the same session-switch protection', () => {
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    expect(store.markUploadSubmissionPending('4'.repeat(32))).toBe(true)

    expect(store.selectSession(9, true)).toBe(false)
    expect(store.sessionId).toBe(7)
    expect(store.pendingUploadRequestToken).toBe('4'.repeat(32))
  })

  it('ignores an old source response after the user selects another source', async () => {
    const store = useConfigWorkspaceStore()
    const firstId = '1'.repeat(32)
    const secondId = '2'.repeat(32)
    store.selectSession(7)
    store.selectSource(firstId)

    let resolveFirst!: (value: ConfigSource) => void
    const first = store.loadSource(7, firstId, () => new Promise((resolve) => {
      resolveFirst = resolve
    }))
    store.selectSource(secondId)
    resolveFirst(source(firstId))
    await first

    expect(store.sourceId).toBeNull()
    expect(store.source).toBeNull()
  })

  it('rejects an accepted upload from an old session without changing current context', () => {
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    store.setSource(source('d'.repeat(32)))

    expect(() => store.acceptUploadedSource({
      ...source('e'.repeat(32)), session_id: 8, source_revision: 'f'.repeat(64),
    })).toThrow('Source session does not match')
    expect(store.sessionId).toBe(7)
    expect(store.sourceId).toBe('d'.repeat(32))
    expect(store.sourceRevision).toBe('b'.repeat(64))
  })
})
