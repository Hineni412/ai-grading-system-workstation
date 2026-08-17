import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import type {
  QuestionBankDetail,
  QuestionBankFilters,
  QuestionBankListResponse,
  QuestionBankPaper,
} from '../api/question-bank'
import { ApiError } from '../api/errors'

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

function paper(overrides: Partial<QuestionBankPaper> = {}): QuestionBankPaper {
  return {
    id: 4,
    title: 'source',
    year: '2026',
    province: null,
    city: null,
    district: null,
    exam_type: '阶段练习',
    grade: null,
    semester: null,
    folder_name: null,
    textbook_version: null,
    curriculum_volume_id: null,
    import_status: 'imported',
    created_at: '2026-07-29 10:00:00',
    updated_at: '2026-07-29 10:00:00',
    question_count: 12,
    tagged_question_count: 10,
    tagged_any_question_count: 12,
    evidence_question_count: 9,
    criteria_question_count: 8,
    criteria_needs_review_count: 0,
    complete_analysis_count: 8,
    source_type: 'docx',
    ...overrides,
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

  it('rejects a response that belongs to a different server page', async () => {
    const { useQuestionBankStore } = await import('../stores/question-bank')
    const store = useQuestionBankStore()
    await store.loadQuestions({ page: 1, pageSize: 20 }, async () => page('第一页', 1))
    await store.loadQuestions({ page: 2, pageSize: 20 }, async () => ({
      ...page('错误页', 2),
      page: 1,
    }))

    expect(store.questions.map((item) => item.id)).toEqual([1])
    expect(store.listState).toBe('stale-error')
  })

  it('keeps cross-page selection stable and enforces the 500-question ceiling', async () => {
    const { useQuestionBankStore } = await import('../stores/question-bank')
    const store = useQuestionBankStore()
    await store.loadQuestions({ page: 1 }, async () => page('第一页', 1))
    store.selectCurrentPage(true)
    await store.loadQuestions({ page: 2 }, async () => ({
      ...page('第二页', 2),
      page: 2,
      total_pages: 2,
    }))
    store.selectCurrentPage(true)

    for (let id = 3; id <= 500; id += 1) {
      expect(store.toggleQuestionSelection(id, true)).toBe(true)
    }

    expect(store.selectedCount).toBe(500)
    expect(store.selectionIsFull).toBe(true)
    expect(store.hiddenSelectionCount).toBe(499)
    expect(store.toggleQuestionSelection(501, true)).toBe(false)
    expect(store.selectedQuestionIds).not.toContain(501)
  })

  it('ignores a late detail response after the user opens another question', async () => {
    const { useQuestionBankStore } = await import('../stores/question-bank')
    const store = useQuestionBankStore()
    const old = deferred<QuestionBankDetail>()
    let oldSignal: AbortSignal | undefined

    const oldLoad = store.selectQuestion(1, async (_id, signal) => {
      oldSignal = signal
      return old.promise
    })
    await store.selectQuestion(2, async () => detail(2, '新题目'))
    old.resolve(detail(1, '旧题目'))
    await oldLoad

    expect(oldSignal?.aborted).toBe(true)
    expect(store.selectedQuestionId).toBe(2)
    expect(store.detail?.id).toBe(2)
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

  it('keeps a restorable plain-data snapshot when deleting a reactive detail', async () => {
    const { useQuestionBankStore } = await import('../stores/question-bank')
    const store = useQuestionBankStore()
    await store.selectQuestion(1, async () => detail(1, '可恢复题目'))
    const deletedRevision = 'b'.repeat(64)

    await expect(store.deleteCurrent({
      async replaceTags() {
        throw new Error('unused')
      },
      async softDelete(questionId) {
        return {
          question_id: questionId,
          revision: deletedRevision,
          deleted: true,
          tags: [],
        }
      },
      async restore(questionId) {
        return {
          question_id: questionId,
          revision: 'c'.repeat(64),
          deleted: false,
          tags: [],
        }
      },
    })).resolves.toBe(true)

    expect(store.lastDeleted?.question.question_text).toBe('可恢复题目题干')
    expect(store.lastDeleted?.revision).toBe(deletedRevision)
    expect(store.selectedQuestionId).toBeNull()
  })

  it('updates only paper metadata while preserving the card counters', async () => {
    const { useQuestionBankStore } = await import('../stores/question-bank')
    const store = useQuestionBankStore()
    await store.loadPapers(async () => ({ items: [paper()], total: 1 }))
    let receivedVersion = ''

    const saved = await store.updatePaperMetadata(4, {
      title: '0526test2',
      year: '2026',
      province: null,
      city: null,
      district: null,
      exam_type: '阶段练习',
      grade: '七年级',
      semester: '下学期',
      folder_name: '中考专题',
      textbook_version: null,
    }, {
      async updatePaperMetadata(_paperId, expectedUpdatedAt) {
        receivedVersion = expectedUpdatedAt
        return {
          id: 4,
          title: '0526test2',
          year: '2026',
          province: null,
          city: null,
          district: null,
          exam_type: '阶段练习',
          grade: '七年级',
          semester: '下学期',
          folder_name: '中考专题',
          textbook_version: null,
          updated_at: '2026-07-29 10:01:00.123456',
        }
      },
    })

    expect(saved).toBe(true)
    expect(receivedVersion).toBe('2026-07-29 10:00:00')
    expect(store.papers[0]).toMatchObject({
      title: '0526test2',
      grade: '七年级',
      updated_at: '2026-07-29 10:01:00.123456',
      question_count: 12,
      tagged_question_count: 10,
    })
  })

  it('refreshes a conflicting paper card without replacing the teacher draft', async () => {
    const { useQuestionBankStore } = await import('../stores/question-bank')
    const store = useQuestionBankStore()
    await store.loadPapers(async () => ({ items: [paper()], total: 1 }))
    const draft = {
      title: '老师正在输入的名称',
      year: '2026',
      province: null,
      city: null,
      district: null,
      exam_type: '阶段练习',
      grade: null,
      semester: null,
      folder_name: null,
      textbook_version: null,
    }

    const saved = await store.updatePaperMetadata(4, draft, {
      async updatePaperMetadata() {
        throw new ApiError({
          kind: 'conflict',
          status: 409,
          code: 'paper_metadata_conflict',
          message: 'conflict',
          details: { current_updated_at: '2026-07-29 10:02:00' },
          requestId: 'req-paper-conflict',
          retryable: false,
        })
      },
    }, async () => ({
      items: [paper({
        title: '另一处刚保存的名称',
        updated_at: '2026-07-29 10:02:00',
      })],
      total: 1,
    }))

    expect(saved).toBe(false)
    expect(store.paperWriteState).toBe('conflict')
    expect(store.papers[0]?.title).toBe('另一处刚保存的名称')
    expect(draft.title).toBe('老师正在输入的名称')
  })

})
