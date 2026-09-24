import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { ApiError } from '../api/errors'
import type { ReviewItem, ReviewQuestionSummary } from '../api/review'
import { REVIEW_PAGE_SIZE, useReviewQueueStore } from '../stores/review-queue'

const media = {
  crop_url: '/api/crop',
  original_front_url: '/api/front',
  original_back_url: '/api/back',
  annotated_front_url: '/api/front?variant=annotated',
  annotated_back_url: '/api/back?variant=annotated',
}

const item = (index: number, overrides: Partial<ReviewItem> = {}): ReviewItem => ({
  session_id: 7,
  result_id: index,
  detail_id: index,
  question_id: 'Q1',
  student_code: `S${String(index).padStart(4, '0')}`,
  student_name: `学生${index}`,
  class_name: index % 2 ? '七年级一班' : '七年级二班',
  score_awarded: 3,
  max_score: 5,
  deduction_reason: null,
  error_category: null,
  error_summary: null,
  confidence_score: 90,
  needs_review: true,
  candidate_scores: [],
  metadata: {},
  media,
  ...overrides,
})

const question = (
  questionId: string,
  overrides: Partial<ReviewQuestionSummary> = {},
): ReviewQuestionSummary => ({
  question_id: questionId,
  question_type: null,
  total_count: 2,
  needs_review_count: 1,
  max_score: 5,
  ...overrides,
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

describe('review queue store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('defaults to the complete answer queue in pages of 24 records', () => {
    const store = useReviewQueueStore()
    expect(REVIEW_PAGE_SIZE).toBe(24)
    expect(store.scope).toBe('all')
  })

  it('sorts risk deterministically and searches only identity fields', () => {
    const store = useReviewQueueStore()
    store.setScope('all')
    store.replaceItems([
      item(3, { needs_review: false }),
      item(2, { needs_review: true, confidence_score: null }),
      item(1, {
        needs_review: true,
        confidence_score: 65,
        deduction_reason: '搜索不应命中这段原因',
      }),
    ])
    expect(store.filteredItems.map((entry) => entry.detail_id)).toEqual([1, 2, 3])
    store.setSearch('二班')
    expect(store.filteredItems.map((entry) => entry.detail_id)).toEqual([2])
    store.setSearch('搜索不应命中')
    expect(store.filteredItems).toEqual([])
  })

  it('filters the complete local queue and reconciles selection', () => {
    const store = useReviewQueueStore()
    store.replaceItems([item(1, { needs_review: false }), item(2)], 1)
    expect(store.selectedDetailId).toBe(1)
    store.setScope('teacher_pending')
    expect(store.selectedDetailId).toBe(2)
  })

  it('patches multiple confirmed items with teacher-owned status', () => {
    const store = useReviewQueueStore()
    store.replaceItems([item(1), item(2), item(3)], 1)
    store.markItemsConfirmed([
      { identity: store.items[0]!, scoreAwarded: 4.5, deductionReason: '教师调整' },
      { identity: store.items[1]!, scoreAwarded: 3, deductionReason: '人工复核已确认' },
    ])

    expect(store.items[0]).toMatchObject({
      detail_id: 1,
      score_awarded: 4.5,
      deduction_reason: '教师调整',
      error_category: '已复核',
      error_summary: 'manual_review_confirmed',
      needs_review: false,
    })
    expect(store.items[1]).toMatchObject({
      detail_id: 2,
      deduction_reason: '人工复核已确认',
      error_category: '已复核',
      error_summary: 'manual_review_confirmed',
      needs_review: false,
    })
    expect(store.items[2]).toMatchObject({ detail_id: 3, needs_review: true })
  })

  it('keeps the original reason when a partial confirmation has no note', () => {
    const store = useReviewQueueStore()
    store.replaceItems([
      item(1, { deduction_reason: 'AI 原扣分理由', error_summary: 'ai_summary' }),
      item(2),
      item(3),
    ], 1)
    store.markItemsConfirmed([
      { identity: store.items[0]!, scoreAwarded: 4, deductionReason: '' },
      { identity: store.items[1]!, scoreAwarded: 5, deductionReason: '' },
      { identity: store.items[2]!, scoreAwarded: 3, deductionReason: '' },
    ])

    // 无批语且仍有扣分：保留原理由与原概要。
    expect(store.items[0]).toMatchObject({
      detail_id: 1,
      score_awarded: 4,
      deduction_reason: 'AI 原扣分理由',
      error_category: '已复核',
      error_summary: 'ai_summary',
      needs_review: false,
    })
    // 无批语且确认满分：写占位值。
    expect(store.items[1]).toMatchObject({
      detail_id: 2,
      score_awarded: 5,
      deduction_reason: '人工复核已确认',
      error_category: '已复核',
      error_summary: 'manual_review_confirmed',
      needs_review: false,
    })
    // 无批语、原理由为空且仍有扣分：写占位值。
    expect(store.items[2]).toMatchObject({
      detail_id: 3,
      deduction_reason: '人工复核已确认',
      error_summary: 'manual_review_confirmed',
    })
  })

  it('adjusts only the matching question pending count and clamps it safely', async () => {
    const store = useReviewQueueStore()
    await store.loadQuestions(7, async () => [
      question('Q1', { total_count: 3, needs_review_count: 2 }),
      question('Q2', { total_count: 2, needs_review_count: 1 }),
    ])

    store.adjustQuestionPendingCount('Q1', -2)
    store.adjustQuestionPendingCount('Q2', 5)

    expect(store.questions).toEqual([
      question('Q1', { total_count: 3, needs_review_count: 0 }),
      question('Q2', { total_count: 2, needs_review_count: 2 }),
    ])
  })

  it('sorts by student code or name with deterministic identity tie-breakers', () => {
    const store = useReviewQueueStore()
    store.replaceItems([
      item(4, { student_code: 'S10', student_name: 'Bob', class_name: 'B' }),
      item(3, { student_code: 'S2', student_name: 'Alice', class_name: 'B' }),
      item(2, { student_code: 'S2', student_name: 'Bob', class_name: 'A' }),
      item(1, { student_code: 'S10', student_name: 'Bob', class_name: 'A' }),
    ])

    store.setSort('student_code')
    expect(store.filteredItems.map((entry) => entry.detail_id)).toEqual([2, 1, 3, 4])
    store.setSort('student_name')
    expect(store.filteredItems.map((entry) => entry.detail_id)).toEqual([3, 2, 1, 4])
  })

  it('paginates 1000 rows and moves across the 24/25 boundary', () => {
    const store = useReviewQueueStore()
    store.setSort('student_code')
    store.replaceItems(
      Array.from({ length: 1000 }, (_, offset) =>
        item(offset + 1, { class_name: '七年级一班' }),
      ),
      24,
    )
    expect(REVIEW_PAGE_SIZE).toBe(24)
    expect(store.totalPages).toBe(42)
    store.moveSelection(1)
    expect(store.selectedDetailId).toBe(25)
    expect(store.page).toBe(2)
    store.moveSelection(-1)
    expect(store.selectedDetailId).toBe(24)
    expect(store.page).toBe(1)
  })

  it('clamps pages and exposes selection navigation state', () => {
    const store = useReviewQueueStore()
    store.setSort('student_code')
    store.replaceItems(Array.from({ length: 101 }, (_, offset) => item(offset + 1)), 101)

    expect(store.currentItem?.detail_id).toBe(101)
    expect(store.currentIndex).toBe(100)
    expect(store.canMovePrevious).toBe(true)
    expect(store.canMoveNext).toBe(false)
    expect(store.pageItems.map((entry) => entry.detail_id)).toContain(101)
    expect(store.pageItems.length).toBeLessThanOrEqual(REVIEW_PAGE_SIZE)

    store.setPage(-1)
    expect(store.page).toBe(1)
    store.setPage(99)
    expect(store.page).toBe(5)
    store.selectDetail(1)
    expect(store.page).toBe(
      Math.floor(store.filteredItems.findIndex((entry) => entry.detail_id === 1) / REVIEW_PAGE_SIZE) + 1,
    )
  })

  it('keeps the last successful queue when refresh fails', async () => {
    const store = useReviewQueueStore()
    store.replaceItems([item(1)])
    await store.loadItems(7, 'Q1', async () => {
      throw new Error('private failure')
    })
    expect(store.items).toHaveLength(1)
    expect(store.itemLoadState).toBe('error')
    expect(store.errorMessage).toBe('复核队列暂时无法读取，已保留上次内容。')
  })

  it('loads questions and reports stable first-load errors without private details', async () => {
    const store = useReviewQueueStore()
    await store.loadQuestions(7, async () => [question('Q1'), question('Q2')])
    expect(store.questions.map((entry) => entry.question_id)).toEqual(['Q1', 'Q2'])
    expect(store.questionLoadState).toBe('ready')

    store.reset()
    await store.loadQuestions(7, async () => {
      throw new Error('private failure')
    })
    expect(store.questionLoadState).toBe('error')
    expect(store.errorMessage).toBe('复核题目暂时无法读取。')
  })

  it('lets only the latest item request update state and aborts its predecessor', async () => {
    const store = useReviewQueueStore()
    const first = deferred<ReviewItem[]>()
    const second = deferred<ReviewItem[]>()
    let firstSignal: AbortSignal | undefined

    const firstLoad = store.loadItems(7, 'Q1', (_sessionId, _questionId, options) => {
      firstSignal = options.signal
      return first.promise
    })
    const secondLoad = store.loadItems(7, 'Q1', () => second.promise)

    expect(firstSignal?.aborted).toBe(true)
    second.resolve([item(2)])
    await secondLoad
    first.resolve([item(1)])
    await firstLoad

    expect(store.items.map((entry) => entry.detail_id)).toEqual([2])
    expect(store.itemLoadState).toBe('ready')
    expect(store.errorMessage).toBe('')
  })

  it('lets only the latest question request update state and aborts its predecessor', async () => {
    const store = useReviewQueueStore()
    const first = deferred<ReviewQuestionSummary[]>()
    const second = deferred<ReviewQuestionSummary[]>()
    let firstSignal: AbortSignal | undefined

    const firstLoad = store.loadQuestions(7, (_sessionId, signal) => {
      firstSignal = signal
      return first.promise
    })
    const secondLoad = store.loadQuestions(7, () => second.promise)

    expect(firstSignal?.aborted).toBe(true)
    second.resolve([question('Q2')])
    await secondLoad
    first.resolve([question('Q1')])
    await firstLoad

    expect(store.questions.map((entry) => entry.question_id)).toEqual(['Q2'])
    expect(store.questionLoadState).toBe('ready')
    expect(store.errorMessage).toBe('')
  })

  it('does not expose cancellation as a queue error', async () => {
    const store = useReviewQueueStore()
    await store.loadItems(7, 'Q1', async () => {
      throw new DOMException('aborted', 'AbortError')
    })
    expect(store.itemLoadState).not.toBe('error')
    expect(store.errorMessage).toBe('')

    await store.loadItems(7, 'Q1', async () => {
      throw new ApiError({
        kind: 'cancelled',
        status: null,
        code: 'request_cancelled',
        message: 'private cancellation detail',
        details: {},
        requestId: 'request-1',
        retryable: false,
      })
    })
    expect(store.itemLoadState).not.toBe('error')
    expect(store.errorMessage).toBe('')
  })

  it('resets state and invalidates in-flight work', async () => {
    const store = useReviewQueueStore()
    const pending = deferred<ReviewItem[]>()
    let signal: AbortSignal | undefined
    const load = store.loadItems(7, 'Q1', (_sessionId, _questionId, options) => {
      signal = options.signal
      return pending.promise
    })
    store.selectQuestion('Q1')
    store.setSearch('学生')
    store.setScope('needs_review')
    store.setSort('student_name')

    store.reset()
    expect(signal?.aborted).toBe(true)
    expect(store.$state).toEqual({
      questions: [],
      items: [],
      questionLoadState: 'idle',
      itemLoadState: 'idle',
      errorMessage: '',
      selectedQuestionId: null,
      selectedDetailId: null,
      selectedReviewItemId: null,
      search: '',
      scope: 'all',
      sort: 'risk',
      page: 1,
    })

    pending.resolve([item(1)])
    await load
    expect(store.items).toEqual([])
  })
})
