<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useResizeObserver } from '@vueuse/core'

import {
  resolveReviewItem,
  type ReviewConfirmInput,
  type ReviewItemLike,
  type ReviewQuestionSummary,
  type ReviewRubricSection,
} from '../../api/review'
import {
  reviewDraftKey,
  reviewDraftRevision,
  reviewDraftIssue,
  submittedSteps,
  useReviewDraftStore,
} from '../../stores/review-drafts'
import type { ReviewScope, ReviewSort } from '../../stores/review-queue'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import ReviewAnswerSheet from './ReviewAnswerSheet.vue'
import ReviewAnswerPanel from './ReviewAnswerPanel.vue'
import AppIcon from '../shell/AppIcon.vue'
import { loadReviewRubric } from './review-rubric-cache'
import { pendingCount, questionStatus, reviewStatus } from './review-status'

const props = defineProps<{
  questions: ReviewQuestionSummary[]
  selectedQuestionId: string
  items: ReviewItemLike[]
  queueItems: ReviewItemLike[]
  search: string
  scope: ReviewScope
  sort: ReviewSort
  page: number
  totalPages: number
  filteredTotal: number
  loading: boolean
  submitting: boolean
  answerPanelOpen?: boolean
}>()

const emit = defineEmits<{
  selectQuestion: [questionId: string]
  updateSearch: [value: string]
  updateScope: [value: ReviewScope]
  updateSort: [value: ReviewSort]
  updatePage: [value: number]
  openItem: [reviewItemId: string]
  focusScore: [reviewItemId: string]
  confirmBatch: [inputs: ReviewConfirmInput[], draftKeys: string[], items: ReviewItemLike[]]
  toggleAnswerPanel: []
}>()

const draftStore = useReviewDraftStore()
const root = ref<HTMLElement | null>(null)
const rubric = ref<ReviewRubricSection | null>(null)
const rubricState = ref<'loading' | 'ready' | 'error'>('loading')
let rubricGeneration = 0
const answerPanelWidth = computed(() => {
  try { return Math.max(300, Number(localStorage.getItem('ai-grading:review-answer-panel-width:v1')) || 420) }
  catch { return 420 }
})
const currentQuestion = computed(() => props.questions.find((entry) => entry.question_id === props.selectedQuestionId))
const sessionId = computed(() => props.queueItems[0]?.session_id ?? props.items[0]?.session_id)
const selectedQuestionLayout = computed<'compact' | 'expanded'>(() => {
  const questionType = props.questions
    .find((question) => question.question_id === props.selectedQuestionId)
    ?.question_type
    ?.trim()
    .toLocaleLowerCase()
  return questionType === 'choice' || questionType === 'fill_blank'
    ? 'compact'
    : 'expanded'
})
const orderedQuestions = computed(() => [...props.questions].sort((left, right) =>
  left.question_id.localeCompare(right.question_id, 'zh-CN', { numeric: true })))
function nextPendingQuestion(): void {
  const entries = orderedQuestions.value
  const start = entries.findIndex((entry) => entry.question_id === props.selectedQuestionId)
  for (let offset = 1; offset <= entries.length; offset += 1) {
    const entry = entries[(start + offset) % entries.length]!
    if (pendingCount(entry) > 0) { emit('selectQuestion', entry.question_id); return }
  }
}
function questionTitle(question: ReviewQuestionSummary): string {
  return `${question.question_id}，待人工 ${question.ungraded_count ?? 0}，处理失败 ${question.failed_count ?? 0}，待复核 ${question.needs_review_count}，AI 已评 ${question.ai_ready_count ?? 0}，教师已确认 ${question.teacher_confirmed_count ?? 0}，总计 ${question.total_count}，满分 ${question.max_score}`
}
const actionableItems = computed(() =>
  props.queueItems.filter((item) => {
    const resolved = resolveReviewItem(item)
    if (resolved.teacher_locked) return false
    const draft = draftStore.drafts[reviewDraftKey(item)] ?? draftStore.ensureDraft(item)
    return resolved.score_status !== 'ai_ready' || draft.dirty
  }),
)
const invalidItems = computed(() => actionableItems.value.filter((item) => {
  const resolved = resolveReviewItem(item)
  const draft = draftStore.drafts[reviewDraftKey(item)] ?? draftStore.ensureDraft(item)
  return reviewDraftIssue(draft, resolved.max_score) !== null
}))
const submitDisabled = computed(() =>
  props.loading
  || rubricState.value === 'loading'
  || props.submitting
  || actionableItems.value.length === 0
  || invalidItems.value.length > 0,
)

function onSearch(event: Event): void {
  emit('updateSearch', (event.currentTarget as HTMLInputElement).value)
}

function onScope(event: Event): void {
  emit('updateScope', (event.currentTarget as HTMLSelectElement).value as ReviewScope)
}

function onSort(event: Event): void {
  emit('updateSort', (event.currentTarget as HTMLSelectElement).value as ReviewSort)
}

function itemKey(item: ReviewItemLike): string {
  return resolveReviewItem(item).review_item_id
}

async function focusScore(reviewItemId: string): Promise<void> {
  await nextTick()
  const card = [...(root.value?.querySelectorAll<HTMLElement>(
    '[data-review-item-id]',
  ) ?? [])].find((entry) => entry.dataset.reviewItemId === reviewItemId)
  const input = card?.querySelector<HTMLInputElement>(
    '[data-score-position][aria-invalid="true"]:not(:disabled)',
  )
    ?? card?.querySelector<HTMLInputElement>('[data-score-position]:not(:disabled)')
  if (input) {
    input.focus()
    return
  }
  emit('focusScore', reviewItemId)
}

function submitBatch(focusInvalid = false): void {
  if (
    props.loading
    || rubricState.value === 'loading'
    || props.submitting
    || actionableItems.value.length === 0
  ) return
  const firstInvalid = invalidItems.value[0]
  if (firstInvalid) {
    if (focusInvalid) {
      void focusScore(resolveReviewItem(firstInvalid).review_item_id)
    }
    return
  }
  const inputs: ReviewConfirmInput[] = []
  const draftKeys: string[] = []
  const submittedItems: ReviewItemLike[] = []

  for (const item of actionableItems.value) {
    const resolved = resolveReviewItem(item)
    const draft = draftStore.drafts[reviewDraftKey(item)] ?? draftStore.ensureDraft(item)
    const issue = reviewDraftIssue(draft, resolved.max_score)
    if (issue !== null) return
    const note = draft.note.trim()
    inputs.push({
      review_item_id: resolved.review_item_id,
      expected_revision: reviewDraftRevision(draft),
      student_id: resolved.student_id,
      result_id: resolved.result_id,
      detail_id: resolved.detail_id,
      score_awarded: Number(draft.scoreText.trim()),
      ...(note ? { deduction_reason: note } : {}),
      ...(draft.stepScores ? { step_scores: submittedSteps(draft) } : {}),
    })
    draftKeys.push(draft.key)
    submittedItems.push(item)
  }

  emit('confirmBatch', inputs, draftKeys, submittedItems)
}

function onScoreKeydown(event: KeyboardEvent): void {
  if (props.loading || props.submitting || event.repeat) {
    event.preventDefault()
    return
  }
  const direction = event.key === 'Tab' && event.shiftKey ? -1 : 1
  const inputs = [...(root.value?.querySelectorAll<HTMLInputElement>('[data-score-position]:not(:disabled)') ?? [])]
  const currentIndex = inputs.indexOf(event.target as HTMLInputElement)
  const target = direction > 0 ? inputs.slice(currentIndex + 1).find((input) => input.dataset.reviewRed === 'true')
    : inputs.slice(0, currentIndex).reverse().find((input) => input.dataset.reviewRed === 'true')
  event.preventDefault()
  if (target) {
    target.focus()
    target.select()
    return
  }
  if (event.key === 'Enter' && direction > 0) {
    event.preventDefault()
    submitBatch(true)
  }
}

watch(
  [() => props.queueItems, rubric],
  () => props.queueItems.forEach((item) => { draftStore.ensureDraft(item); draftStore.ensureSteps(item, rubric.value) }),
  { immediate: true },
)
function scrollToSelectedQuestion(): void {
  const strip = root.value?.querySelector<HTMLElement>('.review-question-strip')
  const selected = strip?.querySelector<HTMLElement>('[aria-current="true"]')
  if (!strip || !selected) return
  const visible = strip.getBoundingClientRect()
  const chip = selected.getBoundingClientRect()
  if (chip.right > visible.right) strip.scrollLeft += chip.right - visible.right
  else if (chip.left < visible.left) strip.scrollLeft += chip.left - visible.left
}
useResizeObserver(root, scrollToSelectedQuestion)
watch([() => props.selectedQuestionId, () => props.questions], async () => {
  await nextTick()
  scrollToSelectedQuestion()
}, { immediate: true })
watch([sessionId, () => props.selectedQuestionId], async ([session, question]) => {
  const generation = ++rubricGeneration
  rubric.value = null
  if (!session || !question || selectedQuestionLayout.value === 'compact') { rubricState.value = 'ready'; return }
  rubricState.value = 'loading'
  try {
    const loaded = await loadReviewRubric(session, question)
    if (generation !== rubricGeneration) return
    rubric.value = loaded
    rubricState.value = 'ready'
  } catch { if (generation === rubricGeneration) rubricState.value = 'error' }
}, { immediate: true })
</script>

<template>
  <section
    ref="root"
    class="review-batch-workspace"
    data-testid="review-batch-workspace"
    :aria-busy="loading ? 'true' : 'false'"
  >
    <div class="review-question-strip-wrap">
      <nav class="review-question-strip" data-testid="question-strip" aria-label="按题号选择复核内容">
        <button v-for="question in orderedQuestions" :key="question.question_id" type="button"
          class="review-question-chip" :data-question-id="question.question_id" :data-status="questionStatus(question)"
          :style="{ '--review-status-color': reviewStatus[questionStatus(question)].color }"
          :title="questionTitle(question)" :aria-label="questionTitle(question)"
          :aria-current="question.question_id === selectedQuestionId ? 'true' : undefined"
          @click="emit('selectQuestion', question.question_id)">
          <strong>{{ question.question_id }}</strong><span>{{ pendingCount(question) ? `剩 ${pendingCount(question)}` : '✓' }}</span>
          <i class="review-question-chip__progress"><i :style="{ width: `${(question.teacher_confirmed_count ?? 0) / (question.total_count || 1) * 100}%` }" /></i>
        </button>
      </nav>
      <button type="button" class="review-question-next" :disabled="!questions.some(q => pendingCount(q) > 0)" @click="nextPendingQuestion">下一道待处理 ›</button>
    </div>

    <div class="review-batch-toolbar" aria-label="批量复核筛选">
      <label class="review-batch-toolbar__search" for="review-search">
        <span>搜索学生</span>
        <input
          id="review-search"
          data-testid="review-search"
          type="search"
          :value="search"
          placeholder="姓名、学号或班级"
          @input="onSearch"
        >
      </label>
      <label for="review-scope">
        <span>显示范围</span>
        <select id="review-scope" :value="scope" @change="onScope">
          <option value="all">全部答卷</option>
          <option value="teacher_pending">教师待处理</option>
          <option value="ungraded">仅未批</option>
          <option value="ai_review">仅 AI 待复核</option>
          <option value="teacher_final">仅教师已确认</option>
        </select>
      </label>
      <label for="review-sort">
        <span>排序</span>
        <select id="review-sort" :value="sort" @change="onSort">
          <option value="risk">复核风险优先</option>
          <option value="student_code">按学号</option>
          <option value="student_name">按姓名</option>
        </select>
      </label>
      <div v-if="currentQuestion" class="review-batch-progress">
        <span>本题已确认 {{ currentQuestion.teacher_confirmed_count ?? 0 }}/{{ currentQuestion.total_count }} · 待处理 {{ pendingCount(currentQuestion) }}</span>
        <progress :value="currentQuestion.teacher_confirmed_count ?? 0" :max="currentQuestion.total_count || 1" aria-label="本题确认进度" />
      </div>
      <button type="button" class="review-deep-workspace__panel-toggle" :aria-pressed="answerPanelOpen === true" @click="emit('toggleAnswerPanel')"><AppIcon name="book-open" :size="14" />题目与答案</button>
      <span class="review-batch-legend">红框：AI 拿不准或需要人工评分，Tab 只在红框间跳</span>
    </div>

    <p v-if="rubricState === 'error'" class="review-field-error review-batch-rubric-notice">评分标准暂时无法读取，不影响查看和编辑当前分数。</p>
    <div class="review-batch-body" :class="{ 'review-batch-body--answers': answerPanelOpen }" :style="{ '--review-answer-panel-width': `${answerPanelWidth}px` }">
    <div class="review-batch-grid-wrap">
    <StatePanel
      v-if="items.length === 0 && loading"
      kind="loading"
      title="正在读取本题答卷"
      description="可以继续切换题号，旧请求会安全取消。"
    />
    <StatePanel
      v-else-if="items.length === 0"
      kind="empty"
      title="当前范围没有答卷"
      description="可以更换题号、搜索条件或显示范围。"
    />
    <div
      v-else
      class="review-contact-sheet"
      data-testid="review-contact-sheet"
      :data-question-layout="selectedQuestionLayout"
    >
      <ReviewAnswerSheet
        v-for="(item, index) in items"
        :key="itemKey(item)"
        :item="item"
        :position="index"
        :submitting="submitting || rubricState === 'loading'"
        :rubric="rubric"
        @open-item="emit('openItem', $event)"
        @score-keydown="onScoreKeydown"
      />
    </div>

    </div>
    <ReviewAnswerPanel v-if="answerPanelOpen && sessionId" :session-id="sessionId" :question-id="selectedQuestionId" @close="emit('toggleAnswerPanel')" />
    </div>

    <footer class="review-batch-actions">
      <div>
        <strong>本题需要教师处理 {{ actionableItems.length }} 份</strong>
        <span v-if="invalidItems.length > 0">其中 {{ invalidItems.length }} 份分数需要修正</span>
        <span v-else>{{ draftStore.dirtyCount }} 条草稿未确认</span>
      </div>
    <nav class="review-batch-pagination" aria-label="答卷批次">
      <AppButton type="button" :disabled="page <= 1" variant="secondary" size="small" @click="emit('updatePage', page - 1)">
        ‹ 上一批
      </AppButton>
      <span>第 {{ page }} / {{ totalPages }} 批</span>
      <AppButton type="button" :disabled="page >= totalPages" variant="secondary" size="small" @click="emit('updatePage', page + 1)">
        下一批 ›
      </AppButton>
    </nav>

      <AppButton
        type="button"
        data-testid="confirm-batch"
        :disabled="submitDisabled"
        variant="primary" @click="submitBatch()"
      >
        {{ submitting ? '正在确认本题…' : `确认本题处理结果（${actionableItems.length}）` }}
      </AppButton>
      <p class="review-batch-shortcuts"><kbd>Tab</kbd> / <kbd>Shift+Tab</kbd> 下一个 / 上一个红框 · <kbd>Enter</kbd> 下一个，最后一个确认本题 · <kbd>J</kbd> / <kbd>K</kbd> 切换选中 · <kbd>/</kbd> 搜索</p>
    </footer>
  </section>
</template>
