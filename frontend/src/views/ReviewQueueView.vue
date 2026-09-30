<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '../api/errors'
import {
  confirmReviewItems,
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
import ReviewStudentStrip from '../components/review/ReviewStudentStrip.vue'
import {
  entryQuery,
  positiveIntegerQuery,
  scopeQuery,
  stringQuery,
} from '../components/review/review-route'
import { useReviewAnnotationRetry } from '../components/review/useReviewAnnotationRetry'
import { useReviewKeyboard } from '../components/review/useReviewKeyboard'
import { useReviewStudentNav, type ReviewMode } from '../components/review/useReviewStudentNav'
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
import { useResultsCenterStore } from '../stores/results-center'

const route = useRoute()
const router = useRouter()
const sessionStore = useSessionStore()
const reviewStore = useReviewQueueStore()
const draftStore = useReviewDraftStore()
const resultsStore = useResultsCenterStore()
const reviewPage = ref<HTMLElement | null>(null)
const batchSubmitting = ref(false)
const feedback = ref('')
const feedbackTone = ref<'success' | 'warning' | 'error'>('success')
const mode = ref<ReviewMode>('batch')
// 跨题深查切换期间继续展示上一份答卷，等目标题数据到达后再替换。
const deepSwitching = ref(false)
const batchScrollTop = ref(0)
const ANSWER_PANEL_STORAGE_KEY = 'ai-grading:review-answer-panel:v1'
const answerPanelOpen = ref(false)

let contextGeneration = 0
let itemGeneration = 0
let unmounting = false
let initialRouteSessionHandled = false
let querySync = Promise.resolve()

const {
  annotationRetryEntries,
  annotationRetryTitle,
  mergeAnnotationRetryEntries,
  retryEntriesFromResponse,
  retryAnnotations,
  handleDeepAnnotationRetry,
} = useReviewAnnotationRetry({ batchSubmitting, feedback, feedbackTone })

const studentNavContext = useReviewStudentNav({ mode, openStudentQuestion })
const {
  deepItem,
  previousDeepItem,
  nextDeepItem,
  studentNav,
  activeStudentId,
  stripStudent,
  studentStripEntries,
  showStudentStrip,
  studentSearchOptions,
  studentSearchNotice,
  openStudentPick,
  openQuestionNav,
  openStudentNav,
} = studentNavContext

const { onKeydown, focusBatchScore } = useReviewKeyboard({
  mode,
  reviewPage,
  onQuestionNav: openQuestionNav,
  onStudentNav: openStudentNav,
})

const resultsReturnPath = computed(() => {
  if (route.query.entry !== 'results') return null
  const saved = resultsStore.viewState
  if (saved && saved.sessionId === sessionStore.selectedSessionId) return saved.fullPath
  return router.resolve({
    path: '/results',
    query: { tab: 'details', ...(sessionStore.selectedSessionId === null ? {} : { session: String(sessionStore.selectedSessionId) }) },
  }).fullPath
})
const resultsReturnLabel = computed(() => {
  if (!resultsReturnPath.value) return undefined
  return '返回成绩明细'
})

async function returnToResults(): Promise<void> {
  const target = resultsReturnPath.value
  if (!target) return
  // 先结束当前页排队中的路由同步，避免离开后又写回复核页面的查询条件。
  await querySync
  if (router.options.history.state.back === target) router.back()
  else await router.replace(target)
}

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
  const entry = entryQuery(route.query)
  if (entry !== null) query.entry = entry
  if (activeStudentId.value !== null) query.student = String(activeStudentId.value)
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
  // Refresh counts and the visible question together, preserving the selected
  // student's draft until fresh results arrive.
  const itemRefresh = loadQuestion(
    sessionId, submittedQuestionId, preferredReviewItemId, null, false, generation,
  )
  await Promise.all([reviewStore.loadQuestions(sessionId), itemRefresh])
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
  if (questionId === submittedQuestionId) return
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

async function openStudentQuestion(
  questionId: string,
  reviewItemId: string,
): Promise<void> {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId === null) return
  if (questionId === reviewStore.selectedQuestionId) {
    reviewStore.selectItem(reviewItemId)
    mode.value = 'deep'
    syncValidatedQuery()
    await querySync
    return
  }
  // 不清空当前列表：深查工作区继续显示旧答卷直到新题数据到达，避免闪回批量页。
  deepSwitching.value = true
  try {
    await loadQuestion(
      sessionId,
      questionId,
      reviewItemId,
      null,
      false,
      contextGeneration,
    )
  } finally {
    deepSwitching.value = false
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
  if (resultsReturnPath.value) {
    await returnToResults()
    return
  }
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
  // 成绩明细入口确认后留在深查页继续复核；其他入口维持返回行为。
  if (!resultsReturnPath.value && !selectionChanged) {
    await closeDeepReview()
    return
  }
  if (resultsReturnPath.value && !selectionChanged) {
    await nextTick()
    reviewPage.value
      ?.querySelector<HTMLInputElement>('.review-quick-score input')
      ?.focus()
  }
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

// 从成绩页进入且带学生参数时，复用成绩快照生成该生各题横条；快照缺失时补一次读取。
const stopStudentStripWatch = watch(
  [activeStudentId, () => sessionStore.selectedSessionId],
  ([studentId, sessionId]) => {
    if (studentId === null || sessionId === null) return
    if (resultsStore.state === 'loading') return
    if (resultsStore.sessionId === sessionId && resultsStore.results !== null) return
    void resultsStore.load(sessionId)
  },
  { immediate: true },
)

const stopAnswerPanelWatch = watch(
  answerPanelOpen,
  (open) => {
    try {
      localStorage.setItem(ANSWER_PANEL_STORAGE_KEY, open ? '1' : '0')
    } catch {
      // 隐私模式等场景下写入失败时仅本次会话生效
    }
  },
)

onMounted(() => {
  answerPanelOpen.value = localStorage.getItem(ANSWER_PANEL_STORAGE_KEY) === '1'
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  unmounting = true
  contextGeneration += 1
  itemGeneration += 1
  stopSessionWatch()
  stopSelectionWatch()
  stopStudentStripWatch()
  stopAnswerPanelWatch()
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
        <h1 id="review-page-title" tabindex="-1">{{ resultsReturnPath ? '学生作答' : '人工干预工作台' }}</h1>
        <p v-if="!resultsReturnPath">需要教师处理的答卷优先显示；高置信 AI 结果保留在队列中，也可以随时修改。</p>
      </div>
      <AppButton
        v-if="!resultsReturnPath"
        class="review-page__run-switch"
        :disabled="sessionStore.selectedSessionId === null"
        :title="sessionStore.selectedSessionId === null ? '请先选择考试' : '进入当前考试的批改执行'"
        @click="openGradingRun"
      >
        批改执行
      </AppButton>
      <AppButton v-else-if="!deepItem" @click="returnToResults">{{ resultsReturnLabel }}</AppButton>
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
      :back-label="resultsReturnLabel"
      :session-id="sessionStore.selectedSessionId"
      :question-id="reviewStore.selectedQuestionId"
      :student-nav="studentNav"
      :student-options="studentSearchOptions"
      :active-student-id="activeStudentId"
      :student-search-notice="studentSearchNotice"
      :answer-panel-open="answerPanelOpen"
      :switching="deepSwitching"
      :stay-after-confirm="resultsReturnPath !== null"
      :register-annotation-retry="handleDeepAnnotationRetry"
      @back="closeDeepReview"
      @student-nav="openStudentNav"
      @student-pick="openStudentPick"
      @toggle-answer-panel="answerPanelOpen = !answerPanelOpen"
      @confirmed="handleDeepConfirmed"
    >
      <template #strip>
        <ReviewStudentStrip
          v-if="showStudentStrip"
          :entries="studentStripEntries"
          :selected-question-id="reviewStore.selectedQuestionId"
          :switching="deepSwitching"
          :student-name="stripStudent!.student_name"
          @question-nav="openQuestionNav"
          @open-question="openStudentQuestion"
        />
      </template>
    </ReviewDeepWorkspace>

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
