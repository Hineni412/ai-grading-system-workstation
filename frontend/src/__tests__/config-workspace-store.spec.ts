import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import type { ConfigEditorResponse, ConfigSource } from '../api/config-workspace'
import type { JobResponse } from '../api/jobs'
import { ApiError } from '../api/errors'
import {
  CONFIG_WORKSPACE_STORAGE_KEY,
  useConfigWorkspaceStore,
} from '../stores/config-workspace'

function editor(answer: string): ConfigEditorResponse {
  return {
    session_id: 7,
    configured: true,
    revision: 'a'.repeat(64),
    rows: [{
      row_id: 'row-1', question_id: 'Q1', part_id: 'Q1', step_id: 's1',
      part_label: '第 1 题', question_type: 'calculation', core_goal: '计算', score: 5,
      standard_answer: answer, accepted_answers: [], match_rule: 'exact', knowledge: '',
      answer_only_max_score: null, require_final_answer: null, required_elements: [],
      deduction_rules: [], final_answer_rule: '',
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
      generation_mode: 'per_question',
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

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('configuration workspace Store', () => {
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

  it('restores only exact safe fields and clears a stale session id', async () => {
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
      loadSource: async () => source('d'.repeat(32)),
      loadJob: async () => job(),
      loadEditor: async () => editor('42'),
    })

    expect(store.sessionId).toBeNull()
    expect(store.sourceId).toBeNull()
    expect(localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)).toBeNull()
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
})
