<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  confirmReviewItems,
  type ReviewConfirmInput,
  type ReviewItem,
} from '../api/review'
import FeedbackBanner from '../components/design-system/FeedbackBanner.vue'
import StatePanel from '../components/design-system/StatePanel.vue'
import ReviewBatchWorkspace from '../components/review/ReviewBatchWorkspace.vue'
import ReviewDeepWorkspace from '../components/review/ReviewDeepWorkspace.vue'
import ReviewFeedbackToast from '../components/review/ReviewFeedbackToast.vue'
import ReviewShortcutGuide from '../components/review/ReviewShortcutGuide.vue'
import { useReviewDraftStore } from '../stores/review-drafts'
import { useReviewQueueStore, type ReviewScope, type ReviewSort } from '../stores/review-queue'
import { useSessionStore } from '../stores/session'

interface AnnotationRetryEntry {
  input: ReviewConfirmInput
  item: ReviewItem
}

const route = useRoute()
const router = useRouter()
const sessionStore = useSessionStore()
const reviewStore = useReviewQueueStore()
const draftStore = useReviewDraftStore()
const reviewPage = ref<HTMLElement | null>(null)
const batchSubmitting = ref(false)
const feedback = ref('')
const feedbackTone = ref<'success' | 'warning' | 'error'>('success')
const annotationRetryEntries = ref<AnnotationRetryEntry[]>([])
const mode = ref<'batch' | 'deep'>('batch')
const batchScrollTop = ref(0)

let contextGeneration = 0
let itemGeneration = 0
let unmounting = false
let querySync = Promise.resolve()

const hasRetainedContentError = computed(() =>
  Boolean(
    reviewStore.errorMessage &&
    (
      (reviewStore.questionLoadState === 'error' && reviewStore.questions.length > 0) ||
      (reviewStore.itemLoadState === 'error' && reviewStore.items.length > 0)
    ),
  ),
)
const initialLoading = computed(() =>
  reviewStore.questionLoadState === 'loading' && reviewStore.questions.length === 0,
)
const firstLoadError = computed(() =>
  Boolean(
    reviewStore.errorMessage &&
    !hasRetainedContentError.value &&
    (
      (reviewStore.questionLoadState === 'error' && reviewStore.questions.length === 0) ||
      (reviewStore.itemLoadState === 'error' && reviewStore.items.length === 0)
    ),
  ),
)
const noQuestions = computed(() =>
  reviewStore.questionLoadState === 'ready' && reviewStore.questions.length === 0,
)
const hasValidatedQuestion = computed(() =>
  reviewStore.selectedQuestionId !== null &&
  reviewStore.questions.some(
    (question) => question.question_id === reviewStore.selectedQuestionId,
  ),
)
const selectedQuestionIdForView = computed(() => reviewStore.selectedQuestionId ?? '')
const deepItem = computed(() => mode.value === 'deep' ? reviewStore.currentItem : null)
const deepItemIndex = computed(() => {
  if (!deepItem.value) return -1
  return reviewStore.filteredItems.findIndex((entry) => entry.detail_id === deepItem.value?.detail_id)
})
const previousDeepItem = computed(() =>
  deepItemIndex.value > 0 ? reviewStore.filteredItems[deepItemIndex.value - 1] ?? null : null,
)
const nextDeepItem = computed(() =>
  deepItemIndex.value >= 0 ? reviewStore.filteredItems[deepItemIndex.value + 1] ?? null : null,
)
const annotationRetryTitle = computed(() =>
  `分数已保存，${annotationRetryEntries.value.length} 份标注图需要重试`,
)

function stringQuery(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function detailQuery(value: unknown): number | null {
  if (typeof value !== 'string' || !/^\d+$/.test(value)) return null
  const detailId = Number(value)
  return Number.isSafeInteger(detailId) && detailId > 0 ? detailId : null
}

function syncValidatedQuery(): void {
  const query: Record<string, string> = {}
  const questionId = reviewStore.selectedQuestionId
  const detailId = reviewStore.selectedDetailId
  const questionIsValid = questionId !== null && reviewStore.questions.some(
    (question) => question.question_id === questionId,
  )

  if (questionIsValid && questionId !== null) query.question = questionId
  if (
    mode.value === 'deep' &&
    questionIsValid &&
    detailId !== null &&
    reviewStore.items.some(
      (item) => item.question_id === questionId && item.detail_id === detailId,
    )
  ) {
    query.detail = String(detailId)
  }
  querySync = querySync
    .then(async () => {
      if (unmounting) return
      await router.replace({ query })
    })
    .catch(() => undefined)
}

async function loadQuestion(
  sessionId: number,
  questionId: string,
  preferredDetailId: number | null,
  clearPreviousItems: boolean,
  expectedContextGeneration: number,
): Promise<void> {
  const generation = ++itemGeneration
  if (clearPreviousItems) reviewStore.replaceItems([])
  reviewStore.selectQuestion(questionId)
  await reviewStore.loadItems(sessionId, questionId)
  if (
    unmounting ||
    generation !== itemGeneration ||
    expectedContextGeneration !== contextGeneration ||
    sessionStore.selectedSessionId !== sessionId ||
    reviewStore.selectedQuestionId !== questionId
  ) return

  if (
    preferredDetailId !== null &&
    reviewStore.items.some((entry) => entry.detail_id === preferredDetailId)
  ) {
    reviewStore.selectDetail(preferredDetailId)
    mode.value = 'deep'
  } else if (preferredDetailId !== null) {
    mode.value = 'batch'
  }
  syncValidatedQuery()
}

async function loadSession(sessionId: number | null): Promise<void> {
  const preferredQuestionId = stringQuery(route.query.question)
  const preferredDetailId = detailQuery(route.query.detail)
  const generation = ++contextGeneration
  itemGeneration += 1
  reviewStore.reset()
  mode.value = preferredDetailId === null ? 'batch' : 'deep'
  annotationRetryEntries.value = []

  if (sessionId === null || sessionId <= 0) {
    syncValidatedQuery()
    return
  }

  await reviewStore.loadQuestions(sessionId)
  if (
    unmounting ||
    generation !== contextGeneration ||
    sessionStore.selectedSessionId !== sessionId
  ) return

  const questionId = reviewStore.questions.some(
    (question) => question.question_id === preferredQuestionId,
  )
    ? preferredQuestionId
    : (reviewStore.questions[0]?.question_id ?? null)

  if (questionId === null) {
    syncValidatedQuery()
    return
  }

  await loadQuestion(sessionId, questionId, preferredDetailId, false, generation)
}

async function selectQuestion(questionId: string): Promise<void> {
  const sessionId = sessionStore.selectedSessionId
  if (
    sessionId === null ||
    questionId === reviewStore.selectedQuestionId ||
    !reviewStore.questions.some((question) => question.question_id === questionId)
  ) return
  mode.value = 'batch'
  await loadQuestion(sessionId, questionId, null, true, contextGeneration)
}

async function updateScope(scope: ReviewScope): Promise<void> {
  if (scope === reviewStore.scope) return
  const previousScope = reviewStore.scope
  reviewStore.setScope(scope)
  const sessionId = sessionStore.selectedSessionId
  const questionId = reviewStore.selectedQuestionId
  if (sessionId === null || questionId === null) return
  await loadQuestion(sessionId, questionId, null, false, contextGeneration)
  if (
    reviewStore.scope === scope &&
    reviewStore.itemLoadState === 'error' &&
    sessionStore.selectedSessionId === sessionId &&
    reviewStore.selectedQuestionId === questionId
  ) {
    reviewStore.setScope(previousScope)
  }
}

async function retry(): Promise<void> {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId === null) return

  const questionId = reviewStore.selectedQuestionId
  if (reviewStore.itemLoadState === 'error' && questionId !== null) {
    await loadQuestion(
      sessionId,
      questionId,
      reviewStore.selectedDetailId,
      false,
      contextGeneration,
    )
    return
  }

  const generation = contextGeneration
  await reviewStore.loadQuestions(sessionId)
  if (
    unmounting ||
    generation !== contextGeneration ||
    sessionStore.selectedSessionId !== sessionId
  ) return

  const nextQuestionId = reviewStore.questions.some(
    (question) => question.question_id === questionId,
  )
    ? questionId
    : (reviewStore.questions[0]?.question_id ?? null)
  if (nextQuestionId === null) {
    syncValidatedQuery()
    return
  }
  if (reviewStore.items.length === 0 || nextQuestionId !== questionId) {
    await loadQuestion(sessionId, nextQuestionId, null, true, generation)
  } else {
    syncValidatedQuery()
  }
}

function nextPendingQuestion(questionId: string): string | null {
  const currentIndex = reviewStore.questions.findIndex(
    (question) => question.question_id === questionId,
  )
  const ordered = [
    ...reviewStore.questions.slice(currentIndex + 1),
    ...reviewStore.questions.slice(0, Math.max(0, currentIndex)),
  ]
  return ordered.find((question) => question.needs_review_count > 0)?.question_id ?? null
}

async function confirmBatch(
  inputs: ReviewConfirmInput[],
  draftKeys: string[],
  submittedItems: ReviewItem[],
): Promise<void> {
  if (batchSubmitting.value || inputs.length === 0 || submittedItems.length !== inputs.length) return
  const sessionId = submittedItems[0]?.session_id
  const questionId = submittedItems[0]?.question_id
  if (!sessionId || !questionId) return
  const submittedContextGeneration = contextGeneration

  batchSubmitting.value = true
  feedback.value = ''
  try {
    const response = await confirmReviewItems(sessionId, questionId, inputs)
    const retryResultIds = new Set(
      response.annotation_outcomes
        .filter((outcome) => outcome.status === 'retry_required')
        .map((outcome) => outcome.result_id),
    )
    const nextRetryEntries = inputs.flatMap((input, index) => {
      const item = submittedItems[index]
      return item && retryResultIds.has(item.result_id) ? [{ input, item }] : []
    })
    draftStore.markConfirmedMany(draftKeys)

    const stillOnSubmittedContext =
      !unmounting &&
      contextGeneration === submittedContextGeneration &&
      sessionStore.selectedSessionId === sessionId &&
      reviewStore.selectedQuestionId === questionId
    if (!stillOnSubmittedContext) {
      annotationRetryEntries.value = nextRetryEntries
      feedbackTone.value = nextRetryEntries.length > 0 ? 'warning' : 'success'
      feedback.value = nextRetryEntries.length > 0
        ? '先前考试的本批分数已保存；部分标注图需要显式重试。当前考试未被旧响应更改。'
        : '先前考试的本批分数已确认；当前考试未被旧响应更改。'
      return
    }

    reviewStore.markItemsConfirmed(inputs.map((input, index) => ({
      identity: submittedItems[index]!,
      scoreAwarded: input.score_awarded,
      deductionReason: input.deduction_reason?.trim() || '人工复核已确认',
    })))
    reviewStore.adjustQuestionPendingCount(questionId, -inputs.length)
    reviewStore.reconcileAfterConfirmation()
    annotationRetryEntries.value = nextRetryEntries

    const currentQuestion = reviewStore.questions.find(
      (question) => question.question_id === questionId,
    )
    if (currentQuestion?.needs_review_count === 0) {
      const nextQuestionId = nextPendingQuestion(questionId)
      if (nextQuestionId !== null) {
        await loadQuestion(sessionId, nextQuestionId, null, true, contextGeneration)
      }
    }

    feedbackTone.value = annotationRetryEntries.value.length > 0 ? 'warning' : 'success'
    feedback.value = annotationRetryEntries.value.length > 0
      ? '本批分数已保存；部分标注图需要单独重试。'
      : `本批 ${inputs.length} 份评分已确认。`
  } catch {
    feedbackTone.value = 'error'
    feedback.value = '确认失败，整批草稿已保留。请检查网络后重试。'
  } finally {
    batchSubmitting.value = false
  }
}

async function retryAnnotations(): Promise<void> {
  if (batchSubmitting.value || annotationRetryEntries.value.length === 0) return
  const entries = [...annotationRetryEntries.value]
  const first = entries[0]!
  batchSubmitting.value = true
  try {
    const response = await confirmReviewItems(
      first.item.session_id,
      first.item.question_id,
      entries.map((entry) => entry.input),
    )
    const retryResultIds = new Set(
      response.annotation_outcomes
        .filter((outcome) => outcome.status === 'retry_required')
        .map((outcome) => outcome.result_id),
    )
    annotationRetryEntries.value = entries.filter((entry) => retryResultIds.has(entry.item.result_id))
    feedbackTone.value = annotationRetryEntries.value.length > 0 ? 'warning' : 'success'
    feedback.value = annotationRetryEntries.value.length > 0
      ? '分数保持已保存；仍有标注图需要再次重试。'
      : '标注图已重新生成。'
  } catch {
    feedbackTone.value = 'error'
    feedback.value = '标注图重试失败；分数仍已保存，可以稍后再次重试。'
  } finally {
    batchSubmitting.value = false
  }
}

async function openDetail(detailId: number): Promise<void> {
  const scrollingElement = reviewPage.value?.parentElement
  batchScrollTop.value = scrollingElement?.scrollTop ?? 0
  reviewStore.selectDetail(detailId)
  mode.value = 'deep'
  const questionId = reviewStore.selectedQuestionId
  if (questionId !== null) {
    querySync = querySync.then(async () => {
      await router.replace({ query: { question: questionId, detail: String(detailId) } })
    })
  }
  await querySync
}

async function closeDeepReview(): Promise<void> {
  mode.value = 'batch'
  const questionId = reviewStore.selectedQuestionId
  querySync = querySync.then(async () => {
    await router.replace({ query: questionId === null ? {} : { question: questionId } })
  })
  await querySync
  await nextTick()
  const scrollingElement = reviewPage.value?.parentElement
  if (scrollingElement) scrollingElement.scrollTop = batchScrollTop.value
}

function handleDeepConfirmed(payload: {
  detailId: number
  retryEntry: AnnotationRetryEntry | null
}): void {
  annotationRetryEntries.value = payload.retryEntry ? [payload.retryEntry] : []
  feedbackTone.value = payload.retryEntry ? 'warning' : 'success'
  feedback.value = payload.retryEntry
    ? '此份分数已保存；标注图需要显式重试。'
    : '此份评分已确认。'
  void closeDeepReview()
}

function isShortcutProtectedTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false
  if (target.closest('input, textarea, select, button')) return true
  if (target instanceof HTMLElement && target.isContentEditable) return true
  const editableRoot = target.closest<HTMLElement>('[contenteditable]')
  if (editableRoot === null) return false
  return editableRoot.getAttribute('contenteditable')?.trim().toLocaleLowerCase() !== 'false'
}

function onKeydown(event: KeyboardEvent): void {
  if (
    !event.defaultPrevented &&
    !event.altKey &&
    !event.ctrlKey &&
    !event.metaKey &&
    !event.shiftKey &&
    !isShortcutProtectedTarget(event.target) &&
    (event.key.toLocaleLowerCase() === 'j' || event.key.toLocaleLowerCase() === 'k')
  ) {
    reviewStore.moveSelection(event.key.toLocaleLowerCase() === 'j' ? 1 : -1)
    event.preventDefault()
    if (mode.value === 'batch') {
      void nextTick(() => reviewPage.value
        ?.querySelector<HTMLInputElement>(
          `[data-detail-id="${reviewStore.selectedDetailId}"] input:not(:disabled)`,
        )
        ?.focus())
    }
    return
  }
  if (
    event.defaultPrevented ||
    event.altKey ||
    event.ctrlKey ||
    event.metaKey ||
    event.shiftKey ||
    event.key !== '/' ||
    mode.value !== 'batch' ||
    isShortcutProtectedTarget(event.target)
  ) return
  reviewPage.value?.querySelector<HTMLInputElement>('#review-search')?.focus()
  event.preventDefault()
}

const stopSessionWatch = watch(
  [
    () => sessionStore.selectedSessionId,
    () => sessionStore.loadState,
  ],
  ([sessionId, loadState]) => {
    if (loadState !== 'ready') return
    void loadSession(sessionId)
  },
  { immediate: true },
)

const stopSelectionWatch = watch(
  [
    () => reviewStore.selectedQuestionId,
    () => reviewStore.selectedDetailId,
    () => mode.value,
  ],
  syncValidatedQuery,
)

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  unmounting = true
  contextGeneration += 1
  itemGeneration += 1
  stopSessionWatch()
  stopSelectionWatch()
  window.removeEventListener('keydown', onKeydown)
  reviewStore.reset()
})
</script>

<template>
  <section ref="reviewPage" class="review-page" aria-labelledby="review-page-title">
    <header class="review-page__header">
      <h1 id="review-page-title" tabindex="-1">评分复核</h1>
      <p>按题号批量比较答卷；遇到疑难记录时再进入单份深查。</p>
    </header>

    <ReviewShortcutGuide v-if="mode === 'batch'" />

    <FeedbackBanner
      v-if="hasRetainedContentError"
      tone="warning"
      title="复核内容刷新失败"
      :description="reviewStore.errorMessage"
      action-label="重新加载"
      @action="retry"
    />

    <FeedbackBanner
      v-if="annotationRetryEntries.length > 0"
      tone="warning"
      :title="annotationRetryTitle"
      description="评分结果已经保存；这里只重试受影响答卷的标注图。"
      action-label="重试标注图"
      @action="retryAnnotations"
    />

    <StatePanel
      v-if="sessionStore.selectedSessionId === null"
      kind="empty"
      title="请先选择考试"
      description="从顶部考试选择器选择一个考试后，可以按题号批量复核。"
    />
    <StatePanel
      v-else-if="initialLoading"
      kind="loading"
      title="正在读取复核题目"
      description="正在校验题号和待复核数量。"
    />
    <StatePanel
      v-else-if="firstLoadError"
      kind="error"
      title="复核内容加载失败"
      :description="reviewStore.errorMessage"
      retry-label="重新加载"
      @retry="retry"
    />
    <StatePanel
      v-else-if="noQuestions"
      kind="empty"
      title="当前考试没有复核题目"
      description="该考试暂时没有可浏览的复核记录。"
    />

    <ReviewDeepWorkspace
      v-else-if="hasValidatedQuestion && deepItem"
      :item="deepItem"
      :previous-item="previousDeepItem"
      :next-item="nextDeepItem"
      @back="closeDeepReview"
      @confirmed="handleDeepConfirmed"
    />

    <ReviewBatchWorkspace
      v-else-if="hasValidatedQuestion"
      :questions="reviewStore.questions"
      :selected-question-id="selectedQuestionIdForView"
      :items="reviewStore.pageItems"
      :search="reviewStore.search"
      :scope="reviewStore.scope"
      :sort="reviewStore.sort"
      :page="reviewStore.page"
      :total-pages="reviewStore.totalPages"
      :filtered-total="reviewStore.filteredItems.length"
      :loading="reviewStore.itemLoadState === 'loading'"
      :submitting="batchSubmitting"
      @select-question="selectQuestion"
      @update-search="reviewStore.setSearch"
      @update-scope="updateScope"
      @update-sort="(value: ReviewSort) => reviewStore.setSort(value)"
      @update-page="reviewStore.setPage"
      @open-detail="openDetail"
      @confirm-batch="confirmBatch"
    />

    <ReviewFeedbackToast
      :message="feedback"
      :tone="feedbackTone"
      @dismiss="feedback = ''"
    />
  </section>
</template>
