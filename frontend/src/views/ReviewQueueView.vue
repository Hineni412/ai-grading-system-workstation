<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '../api/errors'
import {
  confirmReviewItems,
  fetchReviewItems,
  resolveReviewItem,
  type ReviewConfirmInput,
  type ReviewItemLike,
} from '../api/review'
import AppButton from '../components/design-system/AppButton.vue'
import FeedbackBanner from '../components/design-system/FeedbackBanner.vue'
import StatePanel from '../components/design-system/StatePanel.vue'
import ReviewBatchWorkspace from '../components/review/ReviewBatchWorkspace.vue'
import ReviewDeepWorkspace from '../components/review/ReviewDeepWorkspace.vue'
import ReviewFeedbackToast from '../components/review/ReviewFeedbackToast.vue'
import ReviewShortcutGuide from '../components/review/ReviewShortcutGuide.vue'
import '../styles/review-queue.css'
import '../styles/review-evidence.css'
import '../styles/review-scoring.css'
import { useReviewDraftStore } from '../stores/review-drafts'
import {
  useReviewQueueStore,
  type ReviewScope,
  type ReviewSort,
} from '../stores/review-queue'
import { useSessionStore } from '../stores/session'

interface AnnotationRetryEntry {
  input: ReviewConfirmInput
  item: ReviewItemLike
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
let initialRouteSessionHandled = false
let querySync = Promise.resolve()

const hasRetainedContentError = computed(() =>
  Boolean(
    reviewStore.errorMessage
    && (
      (reviewStore.questionLoadState === 'error' && reviewStore.questions.length > 0)
      || (reviewStore.itemLoadState === 'error' && reviewStore.items.length > 0)
    ),
  ),
)
const initialLoading = computed(() =>
  reviewStore.questionLoadState === 'loading' && reviewStore.questions.length === 0,
)
const firstLoadError = computed(() =>
  Boolean(
    reviewStore.errorMessage
    && !hasRetainedContentError.value
    && (
      (reviewStore.questionLoadState === 'error' && reviewStore.questions.length === 0)
      || (reviewStore.itemLoadState === 'error' && reviewStore.items.length === 0)
    ),
  ),
)
const noQuestions = computed(() =>
  reviewStore.questionLoadState === 'ready' && reviewStore.questions.length === 0,
)
const hasValidatedQuestion = computed(() =>
  reviewStore.selectedQuestionId !== null
  && reviewStore.questions.some(
    (question) => question.question_id === reviewStore.selectedQuestionId,
  ),
)
const selectedQuestionIdForView = computed(() => reviewStore.selectedQuestionId ?? '')
const deepItem = computed(() => mode.value === 'deep' ? reviewStore.currentItem : null)
const deepItemIndex = computed(() => {
  if (!deepItem.value) return -1
  return reviewStore.filteredItems.findIndex(
    (entry) => entry.review_item_id === deepItem.value?.review_item_id,
  )
})
const previousDeepItem = computed(() =>
  deepItemIndex.value > 0
    ? reviewStore.filteredItems[deepItemIndex.value - 1] ?? null
    : null,
)
const nextDeepItem = computed(() =>
  deepItemIndex.value >= 0
    ? reviewStore.filteredItems[deepItemIndex.value + 1] ?? null
    : null,
)
const annotationRetryTitle = computed(() =>
  `分数已保存，${annotationRetryEntries.value.length} 份标注图需要重试`,
)

function annotationRetryKey(entry: AnnotationRetryEntry): string {
  return resolveReviewItem(entry.item).review_item_id
}

function mergeAnnotationRetryEntries(entries: AnnotationRetryEntry[]): void {
  const merged = new Map(
    annotationRetryEntries.value.map((entry) => [annotationRetryKey(entry), entry]),
  )
  for (const entry of entries) merged.set(annotationRetryKey(entry), entry)
  annotationRetryEntries.value = [...merged.values()]
}

function stringQuery(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function positiveIntegerQuery(value: unknown): number | null {
  if (typeof value !== 'string' || !/^\d+$/.test(value)) return null
  const parsed = Number(value)
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null
}

function scopeQuery(value: unknown): ReviewScope {
  return value === 'teacher_pending'
    || value === 'ungraded'
    || value === 'ai_review'
    || value === 'teacher_final'
    || value === 'all'
    ? value
    : 'all'
}

function entryQuery(): string | null {
  const entry = stringQuery(route.query.entry)
  return entry === 'manual' || entry === 'intervention' || entry === 'results'
    ? entry
    : null
}

function openGradingRun(): void {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId !== null) void router.push(`/sessions/${sessionId}/grading-run`)
}

function syncValidatedQuery(): void {
  const query: Record<string, string> = {
    scope: reviewStore.scope,
  }
  const sessionId = sessionStore.selectedSessionId
  const questionId = reviewStore.selectedQuestionId
  const reviewItemId = reviewStore.selectedReviewItemId
  const questionIsValid = questionId !== null && reviewStore.questions.some(
    (question) => question.question_id === questionId,
  )

  if (sessionId !== null) query.session = String(sessionId)
  const entry = entryQuery()
  if (entry !== null) query.entry = entry
  if (questionIsValid && questionId !== null) query.question = questionId
  if (
    mode.value === 'deep'
    && questionIsValid
    && reviewItemId !== null
    && reviewStore.items.some(
      (item) => {
        const resolved = resolveReviewItem(item)
        return resolved.question_id === questionId
          && resolved.review_item_id === reviewItemId
      },
    )
  ) {
    query.item = reviewItemId
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
  preferredReviewItemId: string | null,
  legacyDetailId: number | null,
  clearPreviousItems: boolean,
  expectedContextGeneration: number,
): Promise<void> {
  const generation = ++itemGeneration
  if (clearPreviousItems) reviewStore.replaceItems([])
  reviewStore.selectQuestion(questionId)
  await reviewStore.loadItems(sessionId, questionId)
  if (
    unmounting
    || generation !== itemGeneration
    || expectedContextGeneration !== contextGeneration
    || sessionStore.selectedSessionId !== sessionId
    || reviewStore.selectedQuestionId !== questionId
  ) return

  const requestedItem = preferredReviewItemId === null
    ? null
    : reviewStore.items
      .map(resolveReviewItem)
      .find((entry) => entry.review_item_id === preferredReviewItemId) ?? null
  const legacyItem = requestedItem === null && legacyDetailId !== null
    ? reviewStore.items
      .map(resolveReviewItem)
      .find((entry) => entry.detail_id === legacyDetailId) ?? null
    : null
  const resolvedItem = requestedItem ?? legacyItem
  if (resolvedItem !== null) {
    reviewStore.selectItem(resolvedItem.review_item_id)
    mode.value = 'deep'
  } else if (preferredReviewItemId !== null || legacyDetailId !== null) {
    mode.value = 'batch'
  }
  syncValidatedQuery()
}

async function loadSession(sessionId: number | null): Promise<void> {
  const preferredQuestionId = stringQuery(route.query.question)
  const preferredReviewItemId = stringQuery(route.query.item)
  const legacyDetailId = positiveIntegerQuery(route.query.detail)
  const initialScope = scopeQuery(route.query.scope)
  const generation = ++contextGeneration
  itemGeneration += 1
  reviewStore.reset(initialScope)
  mode.value =
    preferredReviewItemId === null && legacyDetailId === null ? 'batch' : 'deep'

  if (sessionId === null || sessionId <= 0) {
    syncValidatedQuery()
    return
  }

  await reviewStore.loadQuestions(sessionId)
  if (
    unmounting
    || generation !== contextGeneration
    || sessionStore.selectedSessionId !== sessionId
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

  await loadQuestion(
    sessionId,
    questionId,
    preferredReviewItemId,
    legacyDetailId,
    false,
    generation,
  )
}

async function selectQuestion(questionId: string): Promise<void> {
  const sessionId = sessionStore.selectedSessionId
  if (
    sessionId === null
    || questionId === reviewStore.selectedQuestionId
    || !reviewStore.questions.some((question) => question.question_id === questionId)
  ) return
  mode.value = 'batch'
  await loadQuestion(
    sessionId,
    questionId,
    null,
    null,
    true,
    contextGeneration,
  )
}

function updateScope(scope: ReviewScope): void {
  if (scope === reviewStore.scope) return
  reviewStore.setScope(scope)
  mode.value = 'batch'
  syncValidatedQuery()
}

async function refreshServerState(
  sessionId: number,
  submittedQuestionId: string,
  preferredReviewItemId: string | null,
): Promise<void> {
  const generation = contextGeneration
  await reviewStore.loadQuestions(sessionId)
  if (
    unmounting
    || generation !== contextGeneration
    || sessionStore.selectedSessionId !== sessionId
  ) return

  const questionId = reviewStore.questions.some(
    (question) => question.question_id === submittedQuestionId,
  )
    ? submittedQuestionId
    : (reviewStore.questions[0]?.question_id ?? null)
  if (questionId === null) {
    reviewStore.replaceItems([])
    reviewStore.selectQuestion(null)
    syncValidatedQuery()
    return
  }
  await loadQuestion(
    sessionId,
    questionId,
    questionId === submittedQuestionId ? preferredReviewItemId : null,
    null,
    false,
    generation,
  )
}

async function retry(): Promise<void> {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId === null) return
  await refreshServerState(
    sessionId,
    reviewStore.selectedQuestionId ?? '',
    reviewStore.selectedReviewItemId,
  )
}

function retryEntriesFromResponse(
  response: Awaited<ReturnType<typeof confirmReviewItems>>,
  inputs: ReviewConfirmInput[],
  items: ReviewItemLike[],
): AnnotationRetryEntry[] {
  const retryResultIds = new Set(
    response.annotation_outcomes
      .filter((outcome) => outcome.status === 'retry_required')
      .map((outcome) => outcome.result_id),
  )
  return inputs.flatMap((input, index) => {
    const item = items[index]
    if (
      item === undefined
      || item.result_id === null
      || !retryResultIds.has(item.result_id)
    ) return []
    return [{ input, item }]
  })
}

async function confirmBatch(
  inputs: ReviewConfirmInput[],
  draftKeys: string[],
  submittedItems: ReviewItemLike[],
): Promise<void> {
  if (
    batchSubmitting.value
    || inputs.length === 0
    || submittedItems.length !== inputs.length
  ) return
  const sessionId = submittedItems[0]?.session_id
  const questionId = submittedItems[0]?.question_id
  if (!sessionId || !questionId) return
  const submittedContextGeneration = contextGeneration

  batchSubmitting.value = true
  feedback.value = ''
  try {
    const response = await confirmReviewItems(sessionId, questionId, inputs)
    const nextRetryEntries = retryEntriesFromResponse(
      response,
      inputs,
      submittedItems,
    )
    draftStore.markConfirmedMany(draftKeys)
    mergeAnnotationRetryEntries(nextRetryEntries)

    const stillOnSubmittedContext =
      !unmounting
      && contextGeneration === submittedContextGeneration
      && sessionStore.selectedSessionId === sessionId
      && reviewStore.selectedQuestionId === questionId
    if (stillOnSubmittedContext) {
      await refreshServerState(sessionId, questionId, null)
    }

    const refreshFailed = stillOnSubmittedContext
      && (
        reviewStore.questionLoadState === 'error'
        || reviewStore.itemLoadState === 'error'
      )
    feedbackTone.value =
      nextRetryEntries.length > 0 || refreshFailed ? 'warning' : 'success'
    feedback.value = refreshFailed
      ? `本批 ${inputs.length} 份评分已保存，但页面刷新失败；可安全重新加载。`
      : nextRetryEntries.length > 0
        ? '本批分数已保存；部分标注图需要单独重试。'
        : `本批 ${inputs.length} 份评分已确认。`
  } catch (error) {
    feedbackTone.value = 'error'
    feedback.value = error instanceof ApiError && error.kind === 'conflict'
      ? '本批中有答卷已被更新，整批草稿仍保留。请重新加载后核对再确认。'
      : '确认失败，整批草稿已保留。请检查网络后重试。'
  } finally {
    batchSubmitting.value = false
  }
}

async function retryAnnotations(): Promise<void> {
  if (batchSubmitting.value || annotationRetryEntries.value.length === 0) return
  const entries = [...annotationRetryEntries.value]
  const snapshotByKey = new Map(
    entries.map((entry) => [annotationRetryKey(entry), entry]),
  )
  batchSubmitting.value = true
  let hadRequestFailure = false
  const remainingKeys = new Set<string>()

  try {
    const groups = new Map<string, AnnotationRetryEntry[]>()
    for (const entry of entries) {
      const key = `${entry.item.session_id}:${entry.item.question_id}`
      groups.set(key, [...(groups.get(key) ?? []), entry])
    }

    for (const group of groups.values()) {
      const first = group[0]!
      try {
        const currentItems = await fetchReviewItems(
          first.item.session_id,
          first.item.question_id,
          { scope: 'all' },
        )
        const currentById = new Map(
          currentItems.map(resolveReviewItem).map(
            (item) => [item.review_item_id, item],
          ),
        )
        const refreshedEntries = group.flatMap((entry) => {
          const current = currentById.get(annotationRetryKey(entry))
          if (!current) {
            remainingKeys.add(annotationRetryKey(entry))
            hadRequestFailure = true
            return []
          }
          return [{
            item: current,
            input: {
              ...entry.input,
              review_item_id: current.review_item_id,
              expected_revision: current.revision,
              student_id: current.student_id,
              result_id: current.result_id,
              detail_id: current.detail_id,
            },
          }]
        })
        if (refreshedEntries.length === 0) continue

        const response = await confirmReviewItems(
          first.item.session_id,
          first.item.question_id,
          refreshedEntries.map((entry) => entry.input),
        )
        const retryResultIds = new Set(
          response.annotation_outcomes
            .filter((outcome) => outcome.status === 'retry_required')
            .map((outcome) => outcome.result_id),
        )
        for (const entry of refreshedEntries) {
          if (
            entry.item.result_id !== null
            && retryResultIds.has(entry.item.result_id)
          ) {
            remainingKeys.add(entry.item.review_item_id)
          }
        }
      } catch {
        hadRequestFailure = true
        for (const entry of group) remainingKeys.add(annotationRetryKey(entry))
      }
    }

    annotationRetryEntries.value = annotationRetryEntries.value.filter((entry) => {
      const key = annotationRetryKey(entry)
      const snapshotEntry = snapshotByKey.get(key)
      return snapshotEntry !== entry || remainingKeys.has(key)
    })
    const remainingCount = annotationRetryEntries.value.length
    feedbackTone.value =
      hadRequestFailure ? 'error' : remainingCount > 0 ? 'warning' : 'success'
    feedback.value = hadRequestFailure
      ? '部分标注图重试失败；分数仍已保存，可以稍后再次重试。'
      : remainingCount > 0
        ? '分数保持已保存；仍有标注图需要再次重试。'
        : '标注图已重新生成。'
  } finally {
    batchSubmitting.value = false
  }
}

async function openItem(reviewItemId: string): Promise<void> {
  const scrollingElement = reviewPage.value?.parentElement
  batchScrollTop.value = scrollingElement?.scrollTop ?? 0
  reviewStore.selectItem(reviewItemId)
  mode.value = 'deep'
  syncValidatedQuery()
  await querySync
}

async function closeDeepReview(): Promise<void> {
  mode.value = 'batch'
  syncValidatedQuery()
  await querySync
  await nextTick()
  const scrollingElement = reviewPage.value?.parentElement
  if (scrollingElement) scrollingElement.scrollTop = batchScrollTop.value
}

async function handleDeepConfirmed(payload: {
  reviewItemId: string
  annotationRetry: boolean
}): Promise<void> {
  const sessionId = sessionStore.selectedSessionId
  const questionId = reviewStore.selectedQuestionId
  const selectedReviewItemId = reviewStore.selectedReviewItemId
  const selectionChanged =
    selectedReviewItemId !== null
    && selectedReviewItemId !== payload.reviewItemId
  if (sessionId !== null && questionId !== null) {
    await refreshServerState(
      sessionId,
      questionId,
      selectionChanged ? selectedReviewItemId : payload.reviewItemId,
    )
  }
  feedbackTone.value = payload.annotationRetry ? 'warning' : 'success'
  feedback.value = payload.annotationRetry
    ? '此份分数已保存；标注图需要显式重试。'
    : reviewStore.itemLoadState === 'error'
      ? '此份评分已确认，但页面刷新失败；可安全重新加载。'
      : '此份评分已确认。'
  if (!selectionChanged) await closeDeepReview()
}

function handleDeepAnnotationRetry(entry: AnnotationRetryEntry): void {
  mergeAnnotationRetryEntries([entry])
  feedbackTone.value = 'warning'
  feedback.value = '分数已保存；标注图需要显式重试。'
}

function isShortcutProtectedTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false
  if (target.closest('input, textarea, select, button')) return true
  if (target instanceof HTMLElement && target.isContentEditable) return true
  const editableRoot = target.closest<HTMLElement>('[contenteditable]')
  if (editableRoot === null) return false
  return editableRoot.getAttribute('contenteditable')?.trim().toLocaleLowerCase() !== 'false'
}

function focusSelectedBatchScore(): void {
  const reviewItemId = reviewStore.selectedReviewItemId
  if (reviewItemId === null) return
  const card = [...(reviewPage.value?.querySelectorAll<HTMLElement>(
    '[data-review-item-id]',
  ) ?? [])].find((entry) => entry.dataset.reviewItemId === reviewItemId)
  card?.querySelector<HTMLInputElement>('input:not(:disabled)')?.focus()
}

async function focusBatchScore(reviewItemId: string): Promise<void> {
  reviewStore.selectItem(reviewItemId)
  await nextTick()
  focusSelectedBatchScore()
}

function onKeydown(event: KeyboardEvent): void {
  if (
    !event.defaultPrevented
    && !event.altKey
    && !event.ctrlKey
    && !event.metaKey
    && !event.shiftKey
    && !isShortcutProtectedTarget(event.target)
    && (
      event.key.toLocaleLowerCase() === 'j'
      || event.key.toLocaleLowerCase() === 'k'
    )
  ) {
    reviewStore.moveSelection(event.key.toLocaleLowerCase() === 'j' ? 1 : -1)
    event.preventDefault()
    if (mode.value === 'batch') void nextTick(focusSelectedBatchScore)
    return
  }
  if (
    event.defaultPrevented
    || event.altKey
    || event.ctrlKey
    || event.metaKey
    || event.shiftKey
    || event.key !== '/'
    || mode.value !== 'batch'
    || isShortcutProtectedTarget(event.target)
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
    if (!initialRouteSessionHandled) {
      initialRouteSessionHandled = true
      const requestedSessionId = positiveIntegerQuery(route.query.session)
      if (
        requestedSessionId !== null
        && requestedSessionId !== sessionId
        && sessionStore.sessions.some((session) => session.id === requestedSessionId)
      ) {
        sessionStore.selectSession(requestedSessionId)
        return
      }
    }
    void loadSession(sessionId)
  },
  { immediate: true },
)

const stopSelectionWatch = watch(
  [
    () => reviewStore.selectedQuestionId,
    () => reviewStore.selectedReviewItemId,
    () => reviewStore.scope,
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
  <section
    ref="reviewPage"
    class="review-page"
    :class="{ 'review-page--deep': mode === 'deep' }"
    aria-labelledby="review-page-title"
  >
    <header class="review-page__header">
      <div class="review-page__header-copy">
        <h1 id="review-page-title" tabindex="-1">人工干预工作台</h1>
        <p>需要教师处理的答卷优先显示；高置信 AI 结果保留在队列中，也可以随时修改。</p>
      </div>
      <AppButton
        class="review-page__run-switch"
        :disabled="sessionStore.selectedSessionId === null"
        :title="sessionStore.selectedSessionId === null ? '请先选择考试' : '进入当前考试的批改执行'"
        @click="openGradingRun"
      >
        批改执行
      </AppButton>
    </header>

    <ReviewShortcutGuide v-if="mode === 'batch'" />

    <FeedbackBanner
      v-if="hasRetainedContentError"
      tone="warning"
      title="评分内容刷新失败"
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
      description="从顶部考试选择器选择一个考试后，可以按题集中评分。"
    />
    <StatePanel
      v-else-if="initialLoading"
      kind="loading"
      title="正在读取人工干预队列"
      description="正在核对每道题的未评分、AI 结果和教师确认状态。"
    />
    <StatePanel
      v-else-if="firstLoadError"
      kind="error"
      title="评分内容加载失败"
      :description="reviewStore.errorMessage"
      retry-label="重新加载"
      @retry="retry"
    />
    <StatePanel
      v-else-if="noQuestions"
      kind="empty"
      title="当前考试还没有可干预的评分题目"
      description="完成答卷预检后，可以直接人工评分；AI 批改后也会在这里显示复核结果。"
    />

    <ReviewDeepWorkspace
      v-else-if="hasValidatedQuestion && deepItem"
      :item="deepItem"
      :previous-item="previousDeepItem"
      :next-item="nextDeepItem"
      :register-annotation-retry="handleDeepAnnotationRetry"
      @back="closeDeepReview"
      @confirmed="handleDeepConfirmed"
    />

    <ReviewBatchWorkspace
      v-else-if="hasValidatedQuestion"
      :questions="reviewStore.questions"
      :selected-question-id="selectedQuestionIdForView"
      :items="reviewStore.pageItems"
      :queue-items="reviewStore.filteredItems"
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
      @open-item="openItem"
      @focus-score="focusBatchScore"
      @confirm-batch="confirmBatch"
    />

    <ReviewFeedbackToast
      :message="feedback"
      :tone="feedbackTone"
      @dismiss="feedback = ''"
    />
  </section>
</template>
