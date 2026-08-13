<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import {
  resolveReviewItem,
  type ReviewConfirmInput,
  type ReviewItemLike,
  type ReviewQuestionSummary,
} from '../../api/review'
import {
  reviewDraftKey,
  reviewDraftRevision,
  scoreIssue,
  useReviewDraftStore,
} from '../../stores/review-drafts'
import type { ReviewScope, ReviewSort } from '../../stores/review-queue'
import StatePanel from '../design-system/StatePanel.vue'
import ReviewAnswerSheet from './ReviewAnswerSheet.vue'

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
}>()

const draftStore = useReviewDraftStore()
const root = ref<HTMLElement | null>(null)
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
const editableItems = computed(() =>
  props.queueItems.filter((item) => !resolveReviewItem(item).teacher_locked),
)
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
  return scoreIssue(draft.scoreText, resolved.max_score) !== null
}))
const submitDisabled = computed(() =>
  props.loading
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
    '[data-score-position]:not(:disabled)',
  )
  if (input) {
    input.focus()
    return
  }
  emit('focusScore', reviewItemId)
}

function submitBatch(focusInvalid = false): void {
  if (
    props.loading
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
    const issue = scoreIssue(draft.scoreText, resolved.max_score)
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
    })
    draftKeys.push(draft.key)
    submittedItems.push(item)
  }

  emit('confirmBatch', inputs, draftKeys, submittedItems)
}

function onScoreKeydown(event: KeyboardEvent, reviewItemId: string): void {
  if (props.loading || props.submitting || event.repeat) {
    event.preventDefault()
    return
  }
  const direction = event.key === 'Tab' && event.shiftKey ? -1 : 1
  const currentIndex = editableItems.value.findIndex(
    (item) => resolveReviewItem(item).review_item_id === reviewItemId,
  )
  if (currentIndex < 0) return
  const target = editableItems.value[currentIndex + direction]
  if (target) {
    event.preventDefault()
    void focusScore(resolveReviewItem(target).review_item_id)
    return
  }
  if (event.key === 'Enter' && direction > 0) {
    event.preventDefault()
    submitBatch(true)
  }
}

watch(
  () => props.queueItems,
  (items) => items.forEach((item) => draftStore.ensureDraft(item)),
  { immediate: true },
)
</script>

<template>
  <section
    ref="root"
    class="review-batch-workspace"
    data-testid="review-batch-workspace"
    :aria-busy="loading ? 'true' : 'false'"
  >
    <nav class="review-question-strip" data-testid="question-strip" aria-label="按题号选择复核内容">
      <button
        v-for="question in questions"
        :key="question.question_id"
        type="button"
        :data-question-id="question.question_id"
        :aria-current="question.question_id === selectedQuestionId ? 'true' : undefined"
        @click="emit('selectQuestion', question.question_id)"
      >
        <strong>{{ question.question_id }}</strong>
        <span>
          待人工 {{ (question.ungraded_count ?? 0) + (question.failed_count ?? 0) }} ·
          待复核 {{ question.needs_review_count }} ·
          AI 已评 {{ question.ai_ready_count ?? 0 }}
        </span>
        <span>教师确认 {{ question.teacher_confirmed_count ?? 0 }} / 总计 {{ question.total_count }}</span>
        <span>满分 {{ question.max_score }}</span>
      </button>
    </nav>

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
      <span class="review-batch-toolbar__count">当前结果 {{ filteredTotal }} 份</span>
    </div>

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
        :submitting="submitting"
        @open-item="emit('openItem', $event)"
        @score-keydown="onScoreKeydown"
      />
    </div>

    <nav class="review-batch-pagination" aria-label="答卷批次">
      <button type="button" :disabled="page <= 1" @click="emit('updatePage', page - 1)">
        上一批
      </button>
      <span>第 {{ page }} / {{ totalPages }} 批</span>
      <button type="button" :disabled="page >= totalPages" @click="emit('updatePage', page + 1)">
        下一批
      </button>
    </nav>

    <footer class="review-batch-actions">
      <div>
        <strong>本题需要教师处理 {{ actionableItems.length }} 份</strong>
        <span v-if="invalidItems.length > 0">其中 {{ invalidItems.length }} 份分数需要修正</span>
        <span v-else-if="actionableItems.length > 0">未批答卷需填写分数；待复核 AI 分可直接确认或修改</span>
        <span v-else>本批只有高置信 AI 结果，无需逐份确认；修改后才会进入保存范围</span>
      </div>
      <button
        type="button"
        data-testid="confirm-batch"
        :disabled="submitDisabled"
        @click="submitBatch()"
      >
        {{ submitting ? '正在保存本题' : '保存本题处理结果' }}
      </button>
    </footer>
  </section>
</template>
