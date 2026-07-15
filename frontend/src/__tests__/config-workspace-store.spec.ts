import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import type { ConfigEditorResponse, ConfigSource } from '../api/config-workspace'
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

  it('restores only exact safe fields and clears stale session/source ids', () => {
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
    store.restoreSafeIndex([7, 9])

    expect(store.sessionId).toBeNull()
    expect(store.sourceId).toBeNull()
    expect(localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)).toBeNull()
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

    expect(store.sourceId).toBe(secondId)
    expect(store.source).toBeNull()
  })
})
