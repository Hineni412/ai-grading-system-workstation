import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '../api/errors'
import {
  fetchReviewItems,
  fetchReviewQuestions,
  type ReviewItem,
  type ReviewQuestionSummary,
} from '../api/review'

export const REVIEW_PAGE_SIZE = 100
export type ReviewScope = 'all' | 'needs_review'
export type ReviewSort = 'risk' | 'student_code' | 'student_name'
export type ReviewLoadState = 'idle' | 'loading' | 'ready' | 'error'

const text = (value: string | null) =>
  (value ?? '').trim().toLocaleLowerCase('zh-CN')
const identityKey = (entry: ReviewItem) => [
  entry.class_name ?? '',
  entry.student_code ?? '',
  entry.student_name,
  entry.detail_id,
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

function riskKey(entry: ReviewItem): (string | number)[] {
  return [
    entry.needs_review ? 0 : 1,
    entry.confidence_score === null ? 1 : 0,
    entry.confidence_score ?? 0,
    ...identityKey(entry),
  ]
}

function isCancelled(error: unknown): boolean {
  return (
    (typeof error === 'object' &&
      error !== null &&
      'name' in error &&
      error.name === 'AbortError') ||
    (error instanceof ApiError && error.kind === 'cancelled')
  )
}

function loadError(subject: '题目' | '队列', hasContent: boolean): string {
  return `复核${subject}暂时无法读取${hasContent ? '，已保留上次内容。' : '。'}`
}

export const useReviewQueueStore = defineStore('review-queue', () => {
  const questions = ref<ReviewQuestionSummary[]>([])
  const items = ref<ReviewItem[]>([])
  const questionLoadState = ref<ReviewLoadState>('idle')
  const itemLoadState = ref<ReviewLoadState>('idle')
  const errorMessage = ref('')
  const selectedQuestionId = ref<string | null>(null)
  const selectedDetailId = ref<number | null>(null)
  const search = ref('')
  const scope = ref<ReviewScope>('all')
  const sort = ref<ReviewSort>('risk')
  const page = ref(1)

  let questionController: AbortController | null = null
  let itemController: AbortController | null = null
  let questionGeneration = 0
  let itemGeneration = 0

  const filteredItems = computed(() => {
    const query = text(search.value)
    const visible = items.value.filter((entry) => {
      if (scope.value === 'needs_review' && !entry.needs_review) return false
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
          [left.student_name, left.class_name ?? '', left.student_code ?? '', left.detail_id],
          [right.student_name, right.class_name ?? '', right.student_code ?? '', right.detail_id],
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
    filteredItems.value.findIndex((entry) => entry.detail_id === selectedDetailId.value),
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

  function reconcileSelection(preferredDetailId?: number): void {
    const visible = filteredItems.value
    const selectedIsVisible = visible.some(
      (entry) => entry.detail_id === selectedDetailId.value,
    )
    if (!selectedIsVisible) {
      const preferredIsVisible = visible.some(
        (entry) => entry.detail_id === preferredDetailId,
      )
      selectedDetailId.value = preferredIsVisible
        ? (preferredDetailId ?? null)
        : (visible[0]?.detail_id ?? null)
    }
    syncPageToSelection()
  }

  function replaceItems(nextItems: ReviewItem[], preferredDetailId?: number): void {
    items.value = [...nextItems]
    reconcileSelection(preferredDetailId)
  }

  function markItemConfirmed(
    detailId: number,
    scoreAwarded: number,
    deductionReason: string,
  ): void {
    items.value = items.value.map((entry) =>
      entry.detail_id === detailId
        ? {
            ...entry,
            score_awarded: scoreAwarded,
            deduction_reason: deductionReason,
            error_category: '已复核',
            error_summary: 'manual_review_confirmed',
            needs_review: false,
          }
        : entry,
    )
  }

  function reconcileAfterConfirmation(preferredDetailId?: number): void {
    if (
      preferredDetailId !== undefined &&
      filteredItems.value.some((entry) => entry.detail_id === preferredDetailId)
    ) {
      selectedDetailId.value = preferredDetailId
      syncPageToSelection()
      return
    }
    reconcileSelection(preferredDetailId)
  }

  function selectQuestion(questionId: string): void {
    selectedQuestionId.value = questionId
  }

  function selectDetail(detailId: number): void {
    if (!filteredItems.value.some((entry) => entry.detail_id === detailId)) return
    selectedDetailId.value = detailId
    syncPageToSelection()
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
    selectedDetailId.value = filteredItems.value[targetIndex]?.detail_id ?? null
    syncPageToSelection()
  }

  async function loadQuestions(
    sessionId: number,
    loader: typeof fetchReviewQuestions = fetchReviewQuestions,
  ): Promise<void> {
    questionController?.abort()
    const controller = new AbortController()
    questionController = controller
    const generation = ++questionGeneration
    questionLoadState.value = 'loading'
    errorMessage.value = ''

    try {
      const loadedQuestions = await loader(sessionId, controller.signal)
      if (generation !== questionGeneration) return
      questions.value = [...loadedQuestions]
      if (
        selectedQuestionId.value !== null &&
        !questions.value.some(
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
    loader: typeof fetchReviewItems = fetchReviewItems,
  ): Promise<void> {
    itemController?.abort()
    const controller = new AbortController()
    itemController = controller
    const generation = ++itemGeneration
    selectedQuestionId.value = questionId
    itemLoadState.value = 'loading'
    errorMessage.value = ''

    try {
      const loadedItems = await loader(sessionId, questionId, controller.signal)
      if (generation !== itemGeneration) return
      replaceItems(loadedItems, selectedDetailId.value ?? undefined)
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

  function reset(): void {
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
    selectedDetailId.value = null
    search.value = ''
    scope.value = 'all'
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
    markItemConfirmed,
    reconcileAfterConfirmation,
    selectQuestion,
    selectDetail,
    setSearch,
    setScope,
    setSort,
    setPage,
    moveSelection,
    reset,
  }
})
