import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it } from 'vitest';

import type { ConfigEditorResponse, ConfigSource } from '../api/config-workspace';
import type { JobResponse } from '../api/jobs';

import { CONFIG_WORKSPACE_STORAGE_KEY, useConfigWorkspaceStore } from '../stores/config-workspace';

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

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('configuration workspace Store', () => {

  it('persists and restores bank-match decisions across a reload', async () => {
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    store.setSource({
      ...source('d'.repeat(32)),
      questions: [{
        question_id: 'Q5', question_type: 'fill_blank', question_preview: '',
        answer_preview: '70°', answer_present: true, needs_review: false,
        local_answer_trusted: false, has_question_asset: false, has_answer_asset: false,
      }],
    })
    store.updateDecisions([{
      question_id: 'Q5', excluded: false, bank_match: 'same', bank_question_id: 1425,
    }])

    const saved = localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)!
    expect(JSON.parse(saved).decisions).toEqual([{
      question_id: 'Q5', excluded: false, bank_match: 'same', bank_question_id: 1425,
    }])

    setActivePinia(createPinia())
    localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, saved)
    const restored = useConfigWorkspaceStore()
    await restored.hydrateSafeIndex([7], 7, {
      loadSource: async () => source('d'.repeat(32)),
      loadEditor: async () => ({ ...editor(''), configured: false, rows: [] }),
    })

    expect(restored.decisions).toEqual([{
      question_id: 'Q5', excluded: false, bank_match: 'same', bank_question_id: 1425,
    }])
  })

  it('drops persisted bank-match decisions when the source revision changed', async () => {
    localStorage.setItem(CONFIG_WORKSPACE_STORAGE_KEY, JSON.stringify({
      sessionId: 7, phase: 'source', sourceId: 'd'.repeat(32),
      sourceRevision: 'b'.repeat(64), jobId: null,
      decisions: [{
        question_id: 'Q5', excluded: false, bank_match: 'same', bank_question_id: 1425,
      }],
    }))
    const store = useConfigWorkspaceStore()
    await store.hydrateSafeIndex([7], 7, {
      loadSource: async () => ({
        ...source('d'.repeat(32)), source_revision: 'e'.repeat(64),
      }),
      loadEditor: async () => ({ ...editor(''), configured: false, rows: [] }),
    })

    expect(store.decisions).toEqual([])
    expect(JSON.parse(localStorage.getItem(CONFIG_WORKSPACE_STORAGE_KEY)!).decisions)
      .toEqual([])
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

  it('refuses session changes and token replacement while a write result is unknown', () => {
    const store = useConfigWorkspaceStore()
    store.selectSession(7)
    expect(store.markJobSubmissionPending('1'.repeat(32), 'retry')).toBe(true)

    expect(store.markJobSubmissionPending('2'.repeat(32), 'generate', 'batched')).toBe(false)
    expect(store.markUploadSubmissionPending('3'.repeat(32))).toBe(false)
    expect(store.selectSession(9, true)).toBe(false)
    expect(store.selectSource('f'.repeat(32), true)).toBe(false)
    expect(store.clearWorkspace(true)).toBe(false)
    expect(store.sessionId).toBe(7)
    expect(store.pendingJobRequestToken).toBe('1'.repeat(32))
  })

})
