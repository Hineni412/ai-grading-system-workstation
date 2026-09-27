import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { AssemblyDraft, AssemblyQuestion, AssemblyRecordList } from '../api/assembly';
import { ApiError } from '../api/errors';

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

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('assembly store', () => {

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

  it('moves a question across sections and lands on both orders', async () => {
    const { useAssemblyStore } = await import('../stores/assembly')
    const store = useAssemblyStore()
    const api = {
      getDraft: vi.fn(async () => draft({
        basket_ids: [7, 8, 9, 10],
        order_ids: [7, 8, 9, 10],
        layout_mode: 'sections',
        sections: [
          { id: 'section-a', title: '第一部分', question_ids: [7, 8] },
          { id: 'section-b', title: '第二部分', question_ids: [9, 10] },
        ],
      })),
      saveDraft: vi.fn(async (_revision: string, nextDraft: AssemblyDraft) => ({ ...nextDraft, revision: revisionB })),
      resolveQuestions: vi.fn(async () => ({
        items: [question(7), question(8), question(9), question(10)],
        missing_question_ids: [],
      })),
      listRecords: vi.fn(async (): Promise<AssemblyRecordList> => ({ items: [], total: 0 })),
      deleteRecord: vi.fn(),
      restoreRecord: vi.fn(),
      submitExport: vi.fn(),
      retryExport: vi.fn(),
    }

    await store.load({ api })
    await store.moveQuestionToSlot(7, 'section-b', 9, 'after')

    expect(api.saveDraft).toHaveBeenLastCalledWith(revisionA, expect.objectContaining({
      order_ids: [8, 9, 7, 10],
      sections: [
        expect.objectContaining({ id: 'section-a', question_ids: [8] }),
        expect.objectContaining({ id: 'section-b', question_ids: [9, 7, 10] }),
      ],
    }))
  })

})
