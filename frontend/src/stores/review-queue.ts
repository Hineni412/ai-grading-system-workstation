import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '../api/errors'
import {
  fetchReviewItems,
  fetchReviewQuestions,
  resolveReviewItem,
  type FetchReviewItemsOptions,
  type FetchReviewQuestionsOptions,
  type ResolvedReviewItem,
  type ReviewItemLike,
  type ReviewQuestionSummary,
  type ReviewScope as ApiReviewScope,
} from '../api/review'

export const REVIEW_PAGE_SIZE = 24
export type ReviewScope = ApiReviewScope | 'needs_review'
export type ReviewSort = 'risk' | 'student_code' | 'student_name'
export type ReviewLoadState = 'idle' | 'loading' | 'ready' | 'error'

type ReviewItemsLoader = (
  sessionId: number,
  questionId: string,
  options: FetchReviewItemsOptions,
) => Promise<ReviewItemLike[]>
type ReviewQuestionsLoader = (
  sessionId: number,
  signal?: AbortSignal,
  options?: FetchReviewQuestionsOptions,
) => Promise<ReviewQuestionSummary[]>

export interface ConfirmedReviewPatch {
  identity: Pick<ReviewItemLike, 'session_id' | 'question_id' | 'detail_id'>
  scoreAwarded: number
  deductionReason: string
}

const text = (value: string | null) =>
  (value ?? '').trim().toLocaleLowerCase('zh-CN')
const identityKey = (entry: ResolvedReviewItem) => [
  entry.class_name ?? '',
  entry.student_code ?? '',
  entry.student_name,
  entry.review_item_id,
]
const compareText = (left: string | number, right: string | number) =>
  String(left).localeCompare(String(right), 'zh-CN', { numeric: true })

function compareTuple(left: (string | number)[], right: (string | number)[]): number {
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    const compared = compareText(left[index] ?? '', right[index] ?? '')
    if (compared !== 0) return compared
  }
  return 0
}

const statusOrder: Record<ResolvedReviewItem['score_status'], number> = {
  ungraded: 0,
  failed: 1,
  ai_review: 2,
  ai_ready: 3,
  teacher_final: 4,
}

function riskKey(entry: ResolvedReviewItem): (string | number)[] {
  return [
    statusOrder[entry.score_status],
    entry.confidence_score === null ? 1 : 0,
    entry.confidence_score ?? 0,
    ...identityKey(entry),
  ]
}

function matchesScope(entry: ResolvedReviewItem, scope: ReviewScope): boolean {
  if (scope === 'all') return true
  if (scope === 'needs_review') return entry.needs_review
  if (scope === 'teacher_pending') {
    return entry.score_status === 'ungraded'
      || entry.score_status === 'ai_review'
      || entry.score_status === 'failed'
  }
  return entry.score_status === scope
}

function isCancelled(error: unknown): boolean {
  return (
    (typeof error === 'object'
      && error !== null
      && 'name' in error
      && error.name === 'AbortError')
    || (error instanceof ApiError && error.kind === 'cancelled')
  )
}

function loadError(subject: '题目' | '队列', hasContent: boolean): string {
  return `复核${subject}暂时无法读取${hasContent ? '，已保留上次内容。' : '。'}`
}

export const useReviewQueueStore = defineStore('review-queue', () => {
  const questions = ref<ReviewQuestionSummary[]>([])
  const items = ref<ReviewItemLike[]>([])
  const questionLoadState = ref<ReviewLoadState>('idle')
  const itemLoadState = ref<ReviewLoadState>('idle')
  const errorMessage = ref('')
  const selectedQuestionId = ref<string | null>(null)
  const selectedReviewItemId = ref<string | null>(null)
  const selectedDetailId = ref<number | null>(null)
  const search = ref('')
  const scope = ref<ReviewScope>('all')
  const sort = ref<ReviewSort>('risk')
  const page = ref(1)

  let questionController: AbortController | null = null
  let itemController: AbortController | null = null
  let questionGeneration = 0
  let itemGeneration = 0

  const resolvedItems = computed(() => items.value.map(resolveReviewItem))
  const filteredItems = computed(() => {
    const query = text(search.value)
    const visible = resolvedItems.value.filter((entry) => {
      if (!matchesScope(entry, scope.value)) return false
      if (!query) return true
      return [entry.student_name, entry.student_code, entry.class_name]
        .map((value) => text(value))
        .some((value) => value.includes(query))
    })

    return [...visible].sort((left, right) => {
      if (sort.value === 'student_code') {
        return compareTuple(identityKey(left), identityKey(right))
      }
      if (sort.value === 'student_name') {
        return compareTuple(
          [
            left.student_name,
            left.class_name ?? '',
            left.student_code ?? '',
            left.review_item_id,
          ],
          [
            right.student_name,
            right.class_name ?? '',
            right.student_code ?? '',
            right.review_item_id,
          ],
        )
      }
      return compareTuple(riskKey(left), riskKey(right))
    })
  })

  const totalPages = computed(() =>
    Math.max(1, Math.ceil(filteredItems.value.length / REVIEW_PAGE_SIZE)),
  )
  const pageItems = computed(() => {
    const start = (page.value - 1) * REVIEW_PAGE_SIZE
    return filteredItems.value.slice(start, start + REVIEW_PAGE_SIZE)
  })
  const currentIndex = computed(() =>
    filteredItems.value.findIndex(
      (entry) => entry.review_item_id === selectedReviewItemId.value,
    ),
  )
  const currentItem = computed(() => filteredItems.value[currentIndex.value] ?? null)
  const canMovePrevious = computed(() => currentIndex.value > 0)
  const canMoveNext = computed(
    () => currentIndex.value >= 0 && currentIndex.value < filteredItems.value.length - 1,
  )

  function syncPageToSelection(): void {
    page.value =
      currentIndex.value >= 0
        ? Math.floor(currentIndex.value / REVIEW_PAGE_SIZE) + 1
        : 1
  }

  function assignSelection(item: ResolvedReviewItem | null): void {
    selectedReviewItemId.value = item?.review_item_id ?? null
    selectedDetailId.value = item?.detail_id ?? null
  }

  function reconcileSelection(preferredIdentity?: string | number): void {
    const visible = filteredItems.value
    const selected = visible.find(
      (entry) => entry.review_item_id === selectedReviewItemId.value,
    )
    if (selected) {
      assignSelection(selected)
    } else {
      const preferred = visible.find(
        (entry) => typeof preferredIdentity === 'number'
          ? entry.detail_id === preferredIdentity
          : entry.review_item_id === preferredIdentity,
      )
      assignSelection(preferred ?? visible[0] ?? null)
    }
    syncPageToSelection()
  }

  function replaceItems(
    nextItems: ReviewItemLike[],
    preferredIdentity?: string | number | null,
  ): void {
    items.value = nextItems.map(resolveReviewItem)
    reconcileSelection(preferredIdentity ?? undefined)
  }

  function markItemsConfirmed(patches: readonly ConfirmedReviewPatch[]): void {
    items.value = items.value.map((rawEntry) => {
      const entry = resolveReviewItem(rawEntry)
      const patch = patches.find(
        ({ identity }) =>
          entry.session_id === identity.session_id
          && entry.question_id === identity.question_id
          && entry.detail_id === identity.detail_id,
      )
      if (!patch) return entry
      return {
        ...entry,
        score_awarded: patch.scoreAwarded,
        deduction_reason: patch.deductionReason,
        error_category: '已复核',
        error_summary: 'manual_review_confirmed',
        needs_review: false,
        score_status: 'teacher_final',
        score_source: 'teacher',
        teacher_locked: true,
      }
    })
    reconcileSelection()
  }

  function markItemConfirmed(
    identity: ConfirmedReviewPatch['identity'],
    scoreAwarded: number,
    deductionReason: string,
  ): void {
    markItemsConfirmed([{ identity, scoreAwarded, deductionReason }])
  }

  function adjustQuestionPendingCount(questionId: string, delta: number): void {
    questions.value = questions.value.map((entry) =>
      entry.question_id === questionId
        ? {
            ...entry,
            needs_review_count: Math.min(
              entry.total_count,
              Math.max(0, entry.needs_review_count + Math.trunc(delta)),
            ),
          }
        : entry,
    )
  }

  function reconcileAfterConfirmation(preferredDetailId?: number): void {
    reconcileSelection(preferredDetailId)
  }

  function selectQuestion(questionId: string | null): void {
    selectedQuestionId.value = questionId
  }

  function selectItem(reviewItemId: string | number): void {
    const item = filteredItems.value.find((entry) =>
      typeof reviewItemId === 'number'
        ? entry.detail_id === reviewItemId
        : entry.review_item_id === reviewItemId,
    )
    if (!item) return
    assignSelection(item)
    syncPageToSelection()
  }

  function selectDetail(detailId: number): void {
    selectItem(detailId)
  }

  function setSearch(value: string): void {
    search.value = value
    reconcileSelection()
  }

  function setScope(value: ReviewScope): void {
    scope.value = value
    reconcileSelection()
  }

  function setSort(value: ReviewSort): void {
    sort.value = value
    reconcileSelection()
  }

  function setPage(value: number): void {
    const requested = Number.isFinite(value) ? Math.trunc(value) : 1
    page.value = Math.min(Math.max(requested, 1), totalPages.value)
  }

  function moveSelection(delta: number): void {
    if (currentIndex.value < 0 || filteredItems.value.length === 0) return
    const targetIndex = Math.min(
      Math.max(currentIndex.value + Math.trunc(delta), 0),
      filteredItems.value.length - 1,
    )
    assignSelection(filteredItems.value[targetIndex] ?? null)
    syncPageToSelection()
  }

  async function loadQuestions(
    sessionId: number,
    loader: ReviewQuestionsLoader = fetchReviewQuestions,
  ): Promise<void> {
    questionController?.abort()
    const controller = new AbortController()
    questionController = controller
    const generation = ++questionGeneration
    questionLoadState.value = 'loading'
    errorMessage.value = ''

    try {
      const options: FetchReviewQuestionsOptions = {
        signal: controller.signal,
        scope: 'all',
      }
      const loadedQuestions = await loader(
        sessionId,
        controller.signal,
        options,
      )
      if (generation !== questionGeneration) return
      questions.value = [...loadedQuestions]
      if (
        selectedQuestionId.value !== null
        && !questions.value.some(
          (entry) => entry.question_id === selectedQuestionId.value,
        )
      ) {
        selectedQuestionId.value = null
      }
      questionLoadState.value = 'ready'
    } catch (error) {
      if (generation !== questionGeneration) return
      if (isCancelled(error)) {
        questionLoadState.value = questions.value.length > 0 ? 'ready' : 'idle'
        return
      }
      questionLoadState.value = 'error'
      errorMessage.value = loadError('题目', questions.value.length > 0)
    } finally {
      if (questionController === controller) questionController = null
    }
  }

  async function loadItems(
    sessionId: number,
    questionId: string,
    loader: ReviewItemsLoader = fetchReviewItems,
    needsReviewOnly?: boolean,
  ): Promise<void> {
    // Keep the legacy argument for existing callers; the intervention
    // workspace now always loads the complete question queue.
    void needsReviewOnly
    itemController?.abort()
    const controller = new AbortController()
    itemController = controller
    const generation = ++itemGeneration
    selectedQuestionId.value = questionId
    itemLoadState.value = 'loading'
    errorMessage.value = ''

    try {
      const options: FetchReviewItemsOptions = {
        signal: controller.signal,
        scope: 'all',
      }
      const loadedItems = await loader(sessionId, questionId, options)
      if (generation !== itemGeneration) return
      replaceItems(
        loadedItems,
        selectedReviewItemId.value ?? selectedDetailId.value ?? undefined,
      )
      itemLoadState.value = 'ready'
    } catch (error) {
      if (generation !== itemGeneration) return
      if (isCancelled(error)) {
        itemLoadState.value = items.value.length > 0 ? 'ready' : 'idle'
        return
      }
      itemLoadState.value = 'error'
      errorMessage.value = loadError('队列', items.value.length > 0)
    } finally {
      if (itemController === controller) itemController = null
    }
  }

  function reset(nextScope: ReviewScope = 'all'): void {
    questionController?.abort()
    itemController?.abort()
    questionController = null
    itemController = null
    questionGeneration += 1
    itemGeneration += 1

    questions.value = []
    items.value = []
    questionLoadState.value = 'idle'
    itemLoadState.value = 'idle'
    errorMessage.value = ''
    selectedQuestionId.value = null
    selectedReviewItemId.value = null
    selectedDetailId.value = null
    search.value = ''
    scope.value = nextScope
    sort.value = 'risk'
    page.value = 1
  }

  return {
    questions,
    items,
    questionLoadState,
    itemLoadState,
    errorMessage,
    selectedQuestionId,
    selectedReviewItemId,
    selectedDetailId,
    search,
    scope,
    sort,
    page,
    filteredItems,
    pageItems,
    totalPages,
    currentItem,
    currentIndex,
    canMovePrevious,
    canMoveNext,
    loadQuestions,
    loadItems,
    replaceItems,
    markItemsConfirmed,
    markItemConfirmed,
    adjustQuestionPendingCount,
    reconcileAfterConfirmation,
    selectQuestion,
    selectItem,
    selectDetail,
    setSearch,
    setScope,
    setSort,
    setPage,
    moveSelection,
    reset,
  }
})
