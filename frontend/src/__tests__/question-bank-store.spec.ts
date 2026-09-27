import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it } from 'vitest';

import type { QuestionBankDetail, QuestionBankFilters, QuestionBankListResponse } from '../api/question-bank';
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

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
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

describe('question bank store', () => {
  it('never lets an older filter response replace the latest applied range', async () => {
    const { useQuestionBankStore } = await import('../stores/question-bank')
    const store = useQuestionBankStore()
    const old = deferred<QuestionBankListResponse>()
    let oldSignal: AbortSignal | undefined
    const oldFilters: QuestionBankFilters = { keyword: '旧范围' }
    const newFilters: QuestionBankFilters = { keyword: '新范围' }

    const oldLoad = store.loadQuestions(oldFilters, (_filters, signal) => {
      oldSignal = signal
      return old.promise
    })
    await store.loadQuestions(newFilters, async () => page('新范围', 2))
    old.resolve(page('旧范围', 1))
    await oldLoad

    expect(oldSignal?.aborted).toBe(true)
    expect(store.appliedFilters.keyword).toBe('新范围')
    expect(store.questions.map((item) => item.id)).toEqual([2])
    expect(store.listState).toBe('ready')
  })

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
