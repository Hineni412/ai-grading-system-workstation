<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'

import { ApiError } from '../api/errors'
import {
  confirmReviewItems,
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
import { useReviewAnnotationRetry } from '../components/review/useReviewAnnotationRetry'
import { useReviewKeyboard } from '../components/review/useReviewKeyboard'
import { useReviewStudentNav } from '../components/review/useReviewStudentNav'
import '../styles/review-queue.css'
import '../styles/review-evidence.css'
import '../styles/review-scoring.css'
import { useReviewDraftStore } from '../stores/review-drafts'
import {
  useReviewQueueStore,
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
const { mode, deepSwitching } = storeToRefs(reviewStore)
const { selectQuestionFromRoute: selectQuestion, updateScope, refreshServerState, openStudentQuestion, syncValidatedQuery } = reviewStore
const batchScrollTop = ref(0)
const ANSWER_PANEL_STORAGE_KEY = 'ai-grading:review-answer-panel:v1'
const answerPanelOpen = ref(false)

let unmounting = false

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
  await reviewStore.waitForRouteSync()
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
  const submittedContextGeneration = reviewStore.contextGeneration

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
      && reviewStore.contextGeneration === submittedContextGeneration
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

async function openItem(reviewItemId: string): Promise<void> {
  const scrollingElement = reviewPage.value?.parentElement
  batchScrollTop.value = scrollingElement?.scrollTop ?? 0
  reviewStore.selectItem(reviewItemId)
  mode.value = 'deep'
  syncValidatedQuery()
  await reviewStore.waitForRouteSync()
}

async function closeDeepReview(): Promise<void> {
  if (resultsReturnPath.value) {
    await returnToResults()
    return
  }
  mode.value = 'batch'
  syncValidatedQuery()
  await reviewStore.waitForRouteSync()
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

const stopRouteSync = reviewStore.connectRoute(router, sessionStore, activeStudentId)

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
  stopRouteSync()
  stopStudentStripWatch()
  stopAnswerPanelWatch()
  window.removeEventListener('keydown', onKeydown)
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
