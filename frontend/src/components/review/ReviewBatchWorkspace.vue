<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import type { ReviewConfirmInput, ReviewItem, ReviewQuestionSummary } from '../../api/review'
import { reviewDraftKey, scoreIssue, useReviewDraftStore } from '../../stores/review-drafts'
import type { ReviewScope, ReviewSort } from '../../stores/review-queue'
import StatePanel from '../design-system/StatePanel.vue'
import ReviewAnswerSheet from './ReviewAnswerSheet.vue'

const props = defineProps<{
  questions: ReviewQuestionSummary[]
  selectedQuestionId: string
  items: ReviewItem[]
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
  openDetail: [detailId: number]
  confirmBatch: [inputs: ReviewConfirmInput[], draftKeys: string[], items: ReviewItem[]]
}>()

const draftStore = useReviewDraftStore()
const root = ref<HTMLElement | null>(null)
const pendingItems = computed(() => props.items.filter((item) => item.needs_review))
const invalidItems = computed(() => pendingItems.value.filter((item) => {
  const draft = draftStore.drafts[reviewDraftKey(item)] ?? draftStore.ensureDraft(item)
  return scoreIssue(draft.scoreText, item.max_score) !== null
}))
const submitDisabled = computed(() =>
  props.loading || props.submitting || pendingItems.value.length === 0 || invalidItems.value.length > 0,
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

function submitBatch(): void {
  if (submitDisabled.value) return
  const inputs: ReviewConfirmInput[] = []
  const draftKeys: string[] = []
  const submittedItems: ReviewItem[] = []

  for (const item of pendingItems.value) {
    const draft = draftStore.drafts[reviewDraftKey(item)] ?? draftStore.ensureDraft(item)
    const issue = scoreIssue(draft.scoreText, item.max_score)
    if (issue !== null) return
    const note = draft.note.trim()
    inputs.push({
      result_id: item.result_id,
      detail_id: item.detail_id,
      score_awarded: Number(draft.scoreText.trim()),
      ...(note ? { deduction_reason: note } : {}),
    })
    draftKeys.push(draft.key)
    submittedItems.push(item)
  }

  emit('confirmBatch', inputs, draftKeys, submittedItems)
}

async function focusNextScore(position: number): Promise<void> {
  await nextTick()
  root.value
    ?.querySelector<HTMLInputElement>(`[data-score-position="${position}"]:not(:disabled)`)
    ?.focus()
}

watch(
  () => props.items,
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
        <span>待复核 {{ question.needs_review_count }} / {{ question.total_count }}</span>
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
          <option value="needs_review">仅待复核</option>
          <option value="all">本题全部</option>
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
    <div v-else class="review-contact-sheet" data-testid="review-contact-sheet">
      <ReviewAnswerSheet
        v-for="(item, index) in items"
        :key="`${item.session_id}:${item.question_id}:${item.detail_id}`"
        :item="item"
        :position="index"
        @open-detail="emit('openDetail', $event)"
        @focus-next-score="focusNextScore"
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
        <strong>本批待确认 {{ pendingItems.length }} 份</strong>
        <span v-if="invalidItems.length > 0">其中 {{ invalidItems.length }} 份分数需要修正</span>
        <span v-else-if="pendingItems.length > 0">可以保留 AI 原分直接确认</span>
        <span v-else>本批没有待复核答卷</span>
      </div>
      <button
        type="button"
        data-testid="confirm-batch"
        :disabled="submitDisabled"
        @click="submitBatch"
      >
        {{ submitting ? '正在确认本批' : '确认本批并继续' }}
      </button>
    </footer>
  </section>
</template>
