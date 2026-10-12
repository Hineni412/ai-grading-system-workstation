import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { type QuestionBankDetail, type QuestionBankListResponse } from '../api/question-bank';
import { assemblyApi, type AssemblyDraft } from '../api/assembly';
import { ApiError } from '../api/errors';

function page(
  keyword: string,
  id: number,
): QuestionBankListResponse {
  return {
    items: [{
      id,
      revision: String(id).padStart(64, 'a').slice(-64),
      paper_id: 1,
      question_number: String(id),
      question_type: '解答题',
      question_text: `${keyword}题干`,
      answer_text: null,
      difficulty: '5',
      typicality: null,
      reason: null,
      needs_review: false,
      criteria_needs_review: false,
      has_images: false,
      needs_image_review: false,
      created_at: '2026-07-18T08:00:00Z',
      updated_at: '2026-07-18T08:00:00Z',
      paper_title: '匿名试卷',
      year: '2025',
      province: null,
      city: null,
      district: null,
      exam_type: '期末',
      grade: '九年级',
      semester: null,
      textbook_version: null,
      tags: [],
      asset_urls: [],
    }],
    total: 1,
    page: 1,
    page_size: 20,
    total_pages: 1,
  }
}

function detail(id: number, text: string): QuestionBankDetail {
  return {
    ...page(text, id).items[0]!,
    page_range: null,
    assets: [],
    rich_content: {
      available: true,
      question_block_count: 1,
      answer_block_count: 0,
      question_blocks: [{
        text,
        asset_indexes: [],
        asset_urls: [],
      }],
      answer_blocks: [],
    },
    previews: [],
  }
}

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => vi.restoreAllMocks())

describe('question bank store', () => {

  it('keeps the teacher tag draft when a revision conflict rejects the save', async () => {
    const { useQuestionBankStore } = await import('../stores/question-bank')
    const store = useQuestionBankStore()
    await store.selectQuestion(1, async () => detail(1, '待标注题目'))
    store.replaceTagDraft([{
      tag_type: 'knowledge_point',
      tag_value: '一次函数',
      confidence: null,
    }])

    const saved = await store.saveTags({
      async replaceTags() {
        throw new ApiError({
          kind: 'conflict',
          status: 409,
          code: 'question_write_conflict',
          message: 'conflict',
          details: {},
          requestId: 'req-conflict',
          retryable: false,
        })
      },
      async softDelete() {
        throw new Error('unused')
      },
      async restore() {
        throw new Error('unused')
      },
    })

    expect(saved).toBe(false)
    expect(store.writeState).toBe('conflict')
    expect(store.tagDraft[0]?.tag_value).toBe('一次函数')
    expect(store.detail?.tags).toEqual([])
  })

})

describe('assembly draft save queue', () => {

  function serverDraft(revision: string, order: number[]): AssemblyDraft {
    return {
      basket_ids: [...order],
      order_ids: [...order],
      sections: [],
      title: '',
      header_text: '',
      include_answer: true,
      layout_mode: 'sequential',
      preview_mode: 'teacher',
      revision,
    }
  }

  async function flush(): Promise<void> {
    for (let i = 0; i < 10; i += 1) await Promise.resolve()
  }

  it('applies edits immediately, coalesces background saves, and reloads the server draft on conflict', async () => {
    const { useAssemblyStore } = await import('../stores/assembly')
    const pending: Array<{
      call: { revision: string; order: number[] }
      resolve: (draft: AssemblyDraft) => void
      reject: (error: unknown) => void
    }> = []
    const getDraft = vi.fn(async () => serverDraft('rev-a', [1, 2, 3]))
    const api = {
      ...assemblyApi,
      getDraft,
      saveDraft: vi.fn((revision: string, draft: AssemblyDraft) =>
        new Promise<AssemblyDraft>((resolve, reject) => {
          pending.push({ call: { revision, order: [...draft.order_ids] }, resolve, reject })
        })),
      resolveQuestions: vi.fn(async () => ({ items: [], missing_question_ids: [] })),
      listRecords: vi.fn(async () => ({ items: [], total: 0 })),
    }
    const store = useAssemblyStore()
    await store.load({ api })

    // Three rapid moves apply to the local draft before any PUT resolves.
    const saves = [
      store.moveQuestion(1, 1),
      store.moveQuestion(3, -1),
      store.moveQuestion(2, 1),
    ]
    expect(store.draft.order_ids).toEqual([3, 2, 1])
    expect(pending).toHaveLength(1)
    expect(pending[0]!.call).toEqual({ revision: 'rev-a', order: [2, 1, 3] })

    let queueSettled = false
    const settled = store.whenSaved().then(() => { queueSettled = true })
    pending[0]!.resolve(serverDraft('rev-b', [2, 1, 3]))
    await flush()

    // The queued edits coalesce into one follow-up PUT that chains the
    // returned revision and carries the latest local order.
    expect(pending).toHaveLength(2)
    expect(pending[1]!.call).toEqual({ revision: 'rev-b', order: [3, 2, 1] })
    expect(queueSettled).toBe(false)

    pending[1]!.resolve(serverDraft('rev-c', [3, 2, 1]))
    await settled
    expect(await Promise.all(saves)).toEqual([true, true, true])
    expect(store.draft.revision).toBe('rev-c')
    expect(store.draft.order_ids).toEqual([3, 2, 1])
    expect(store.saveState).toBe('idle')

    // A rejected PUT reloads the authoritative draft and reports the conflict.
    getDraft.mockResolvedValueOnce(serverDraft('rev-server', [7, 8]))
    const failed = store.moveQuestion(3, 1)
    expect(store.draft.order_ids).toEqual([2, 3, 1])
    await flush()
    expect(pending).toHaveLength(3)
    pending[2]!.reject(new ApiError({
      kind: 'conflict',
      status: 409,
      code: 'assembly_draft_conflict',
      message: 'conflict',
      details: {},
      requestId: 'req-conflict',
      retryable: false,
    }))
    expect(await failed).toBe(false)
    expect(getDraft).toHaveBeenCalledTimes(2)
    expect(store.draft.revision).toBe('rev-server')
    expect(store.draft.order_ids).toEqual([7, 8])
    expect(store.saveState).toBe('conflict')
    expect(store.message).toBe('组卷草稿已经被另一个窗口更新，请重新加载后再保存。')
    await store.whenSaved()
  })

})
