<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import FeedbackBanner from '../components/design-system/FeedbackBanner.vue'
import StatePanel from '../components/design-system/StatePanel.vue'
import ReviewEvidenceViewer from '../components/review/ReviewEvidenceViewer.vue'
import ReviewQueuePanel from '../components/review/ReviewQueuePanel.vue'
import ReviewSelectionSummary from '../components/review/ReviewSelectionSummary.vue'
import ReviewShortcutGuide from '../components/review/ReviewShortcutGuide.vue'
import { reviewShortcutBus, type ReviewShortcutCommand } from '../composables/review-shortcuts'
import { useReviewQueueStore, type ReviewScope, type ReviewSort } from '../stores/review-queue'
import { useSessionStore } from '../stores/session'

const route = useRoute()
const router = useRouter()
const sessionStore = useSessionStore()
const reviewStore = useReviewQueueStore()
const reviewPage = ref<HTMLElement | null>(null)

let contextGeneration = 0
let itemGeneration = 0
let scrollGeneration = 0
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
const initialItemLoading = computed(() =>
  reviewStore.itemLoadState === 'loading' && reviewStore.items.length === 0,
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
const currentPosition = computed(() =>
  reviewStore.currentIndex >= 0 ? reviewStore.currentIndex + 1 : 0,
)
const previousItem = computed(() =>
  reviewStore.currentIndex > 0
    ? reviewStore.filteredItems[reviewStore.currentIndex - 1] ?? null
    : null,
)
const nextItem = computed(() =>
  reviewStore.currentIndex >= 0
    ? reviewStore.filteredItems[reviewStore.currentIndex + 1] ?? null
    : null,
)

function stringQuery(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function detailQuery(value: unknown): number | null {
  if (typeof value !== 'string' || !/^\d+$/.test(value)) return null
  const detailId = Number(value)
  return Number.isSafeInteger(detailId) && detailId > 0 ? detailId : null
}

function sameQuery(query: Record<string, string>): boolean {
  const keys = Object.keys(route.query)
  const expectedKeys = Object.keys(query)
  return keys.length === expectedKeys.length && expectedKeys.every(
    (key) => route.query[key] === query[key],
  )
}

function syncValidatedQuery(): void {
  querySync = querySync
    .then(async () => {
      if (unmounting) return
      const query: Record<string, string> = {}
      const questionId = reviewStore.selectedQuestionId
      const detailId = reviewStore.selectedDetailId
      const questionIsValid = questionId !== null && reviewStore.questions.some(
        (question) => question.question_id === questionId,
      )

      if (questionIsValid && questionId !== null) query.question = questionId
      if (
        questionIsValid &&
        detailId !== null &&
        reviewStore.items.some(
          (item) => item.question_id === questionId && item.detail_id === detailId,
        )
      ) {
        query.detail = String(detailId)
      }

      if (!sameQuery(query)) await router.replace({ query })
    })
    .catch(() => undefined)
}

async function scrollSelectedRowIntoView(): Promise<void> {
  const detailId = reviewStore.selectedDetailId
  const generation = ++scrollGeneration
  if (detailId === null) return

  await nextTick()
  if (
    unmounting ||
    generation !== scrollGeneration ||
    reviewStore.selectedDetailId !== detailId
  ) return
  reviewPage.value
    ?.querySelector<HTMLElement>('.review-queue-row[aria-current="true"]')
    ?.scrollIntoView({ block: 'nearest' })
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
  }
  syncValidatedQuery()
}

async function loadSession(sessionId: number | null): Promise<void> {
  const preferredQuestionId = stringQuery(route.query.question)
  const preferredDetailId = detailQuery(route.query.detail)
  const generation = ++contextGeneration
  itemGeneration += 1
  reviewStore.reset()

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
  await loadQuestion(sessionId, questionId, null, true, contextGeneration)
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

function moveSelection(delta: number): void {
  reviewStore.moveSelection(delta)
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
    event.defaultPrevented ||
    event.altKey ||
    event.ctrlKey ||
    event.metaKey ||
    isShortcutProtectedTarget(event.target)
  ) return

  const key = event.key.toLocaleLowerCase()
  const delta = key === 'j' ? 1 : key === 'k' ? -1 : 0
  if (delta !== 0) {
    if (event.shiftKey) return
    const previousDetailId = reviewStore.selectedDetailId
    reviewStore.moveSelection(delta)
    if (reviewStore.selectedDetailId !== previousDetailId) event.preventDefault()
    return
  }

  if (event.repeat && key === 'enter') return
  if (event.shiftKey && key !== 'enter' && key !== '+') return

  let command: ReviewShortcutCommand | null = null
  if (key === '/') command = 'focus-search'
  else if (key === 'z') command = 'fit-width'
  else if (key === '+' || key === '=') command = 'zoom-in'
  else if (key === '-') command = 'zoom-out'
  else if (key === 'enter') command = event.shiftKey ? 'confirm-next' : 'confirm-stay'
  if (command === null) return

  reviewShortcutBus.dispatch(command)
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
  ],
  syncValidatedQuery,
)

const stopScrollWatch = watch(
  () => reviewStore.selectedDetailId,
  () => void scrollSelectedRowIntoView(),
)

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  unmounting = true
  contextGeneration += 1
  itemGeneration += 1
  scrollGeneration += 1
  stopSessionWatch()
  stopSelectionWatch()
  stopScrollWatch()
  window.removeEventListener('keydown', onKeydown)
  reviewStore.reset()
})
</script>

<template>
  <section ref="reviewPage" class="review-page" aria-labelledby="review-page-title">
    <header class="review-page__header">
      <h1 id="review-page-title" tabindex="-1">复核队列</h1>
      <p>查看单题复核记录，并在不修改评分数据的前提下连续浏览。</p>
    </header>

    <ReviewShortcutGuide />

    <FeedbackBanner
      v-if="hasRetainedContentError"
      tone="warning"
      title="复核内容刷新失败"
      :description="reviewStore.errorMessage"
      action-label="重新加载"
      @action="retry"
    />

    <StatePanel
      v-if="sessionStore.selectedSessionId === null"
      kind="empty"
      title="请先选择考试"
      description="从顶部考试选择器选择一个考试后，可以查看复核队列。"
    />
    <StatePanel
      v-else-if="initialLoading"
      kind="loading"
      title="正在读取复核队列"
      description="正在校验题目和当前记录。"
    />
    <StatePanel
      v-else-if="firstLoadError"
      kind="error"
      title="复核队列加载失败"
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

    <div v-else-if="hasValidatedQuestion" class="review-workspace">
      <ReviewQueuePanel
        :questions="reviewStore.questions"
        :selected-question-id="reviewStore.selectedQuestionId"
        :page-items="reviewStore.pageItems"
        :selected-detail-id="reviewStore.selectedDetailId"
        :search="reviewStore.search"
        :scope="reviewStore.scope"
        :sort="reviewStore.sort"
        :page="reviewStore.page"
        :total-pages="reviewStore.totalPages"
        :filtered-total="reviewStore.filteredItems.length"
        :loading="reviewStore.itemLoadState === 'loading'"
        @select-question="selectQuestion"
        @select-detail="reviewStore.selectDetail"
        @update-search="reviewStore.setSearch"
        @update-scope="(value: ReviewScope) => reviewStore.setScope(value)"
        @update-sort="(value: ReviewSort) => reviewStore.setSort(value)"
        @update-page="reviewStore.setPage"
      />

      <section class="review-detail" aria-label="当前复核记录">
        <nav class="review-record-navigation" aria-label="连续浏览复核记录">
          <button
            type="button"
            :disabled="!reviewStore.canMovePrevious"
            @click="moveSelection(-1)"
          >
            上一条
          </button>
          <span>当前位置 {{ currentPosition }} / {{ reviewStore.filteredItems.length }}</span>
          <button
            type="button"
            :disabled="!reviewStore.canMoveNext"
            @click="moveSelection(1)"
          >
            下一条
          </button>
        </nav>
        <StatePanel
          v-if="initialItemLoading"
          kind="loading"
          title="正在读取复核记录"
          description="可以继续切换题目，当前请求会安全取消。"
        />
        <div v-else class="review-evidence-layout">
          <ReviewSelectionSummary
            :item="reviewStore.currentItem"
            :question-id="reviewStore.selectedQuestionId"
          />
          <ReviewEvidenceViewer
            v-if="reviewStore.currentItem"
            :item="reviewStore.currentItem"
            :previous-item="previousItem"
            :next-item="nextItem"
          />
        </div>
      </section>
    </div>
  </section>
</template>
