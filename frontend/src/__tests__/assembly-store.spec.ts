import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AssemblyDraft, AssemblyQuestion, AssemblyRecordList } from '../api/assembly'
import { ApiError } from '../api/errors'
import type { JobResponse } from '../api/jobs'

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
    answer_text: 'A',
    difficulty: '5',
    paper_title: '匿名试卷',
    tags: [],
    asset_urls: [],
    score_value: 5,
  }
}

function job(id = 31): JobResponse {
  return {
    id,
    job_type: 'assembly_export',
    payload: { draft_revision: revisionB, format: 'docx', question_count: 2 },
    result: {},
    status: 'queued',
    progress: 0,
    stage: '',
    detail: '',
    error: null,
    cancel_requested: false,
    created_at: '2026-07-18T10:00:00Z',
    started_at: null,
    updated_at: '2026-07-18T10:00:00Z',
    finished_at: null,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('assembly store', () => {
  it('adds selected questions once, resolves them and tracks export jobs', async () => {
    const { useAssemblyStore } = await import('../stores/assembly')
    const { useJobStore } = await import('../stores/jobs')
    const store = useAssemblyStore()
    const savedDraft = draft({ basket_ids: [7, 8], order_ids: [7, 8], revision: revisionB })
    const api = {
      getDraft: vi.fn(async () => draft()),
      saveDraft: vi.fn(async () => savedDraft),
      resolveQuestions: vi.fn(async () => ({
        items: [question(7), question(8)],
        missing_question_ids: [],
      })),
      listRecords: vi.fn(async (): Promise<AssemblyRecordList> => ({ items: [], total: 0 })),
      deleteRecord: vi.fn(),
      restoreRecord: vi.fn(),
      submitExport: vi.fn(async () => job()),
      retryExport: vi.fn(),
    }

    await store.load({ api })
    await store.addQuestions([7, 7, 8])
    const submitted = await store.submitExport('docx')

    expect(api.saveDraft).toHaveBeenCalledWith(revisionA, expect.objectContaining({
      basket_ids: [7, 8],
      order_ids: [7, 8],
    }))
    expect(api.resolveQuestions).toHaveBeenLastCalledWith([7, 8])
    expect(store.orderedQuestions.map((item) => item.id)).toEqual([7, 8])
    expect(store.totalScore).toBe(10)
    expect(submitted?.id).toBe(31)
    expect(useJobStore().jobs[31]?.job_type).toBe('assembly_export')
  })

  it('keeps the draft visible when a revision conflict rejects save', async () => {
    const { useAssemblyStore } = await import('../stores/assembly')
    const store = useAssemblyStore()
    const api = {
      getDraft: vi.fn(async () => draft({ basket_ids: [7], order_ids: [7] })),
      saveDraft: vi.fn(async () => {
        throw new ApiError({
          kind: 'conflict',
          status: 409,
          code: 'assembly_draft_conflict',
          message: 'changed',
          details: { current_revision: revisionB },
          requestId: 'req-conflict',
          retryable: false,
        })
      }),
      resolveQuestions: vi.fn(async () => ({ items: [question(7)], missing_question_ids: [] })),
      listRecords: vi.fn(async (): Promise<AssemblyRecordList> => ({ items: [], total: 0 })),
      deleteRecord: vi.fn(),
      restoreRecord: vi.fn(),
      submitExport: vi.fn(),
      retryExport: vi.fn(),
    }

    await store.load({ api })
    const saved = await store.addQuestions([8])

    expect(saved).toBe(false)
    expect(store.saveState).toBe('conflict')
    expect(store.draft.order_ids).toEqual([7])
    expect(store.message).toContain('另一个窗口')
  })

  it('ignores late question responses from an older draft', async () => {
    const { useAssemblyStore } = await import('../stores/assembly')
    const store = useAssemblyStore()
    let releaseOld!: (value: { items: AssemblyQuestion[]; missing_question_ids: number[] }) => void
    const oldQuestions = new Promise<{ items: AssemblyQuestion[]; missing_question_ids: number[] }>((resolve) => {
      releaseOld = resolve
    })
    const api = {
      getDraft: vi.fn(async () => draft({ basket_ids: [7], order_ids: [7] })),
      saveDraft: vi.fn(async () => draft({ basket_ids: [8], order_ids: [8], revision: revisionB })),
      resolveQuestions: vi.fn((ids: readonly number[]) => (
        ids[0] === 7
          ? oldQuestions
          : Promise.resolve({ items: [question(8)], missing_question_ids: [] })
      )),
      listRecords: vi.fn(async (): Promise<AssemblyRecordList> => ({ items: [], total: 0 })),
      deleteRecord: vi.fn(),
      restoreRecord: vi.fn(),
      submitExport: vi.fn(),
      retryExport: vi.fn(),
    }

    const loading = store.load({ api })
    await vi.waitFor(() => expect(store.draft.order_ids).toEqual([7]))
    await store.addQuestions([8])
    releaseOld({ items: [question(7)], missing_question_ids: [] })
    await loading

    expect(store.draft.order_ids).toEqual([8])
    expect(store.orderedQuestions.map((item) => item.id)).toEqual([8])
  })
})
