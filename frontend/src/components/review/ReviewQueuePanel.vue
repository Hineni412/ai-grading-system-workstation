<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

import type { ReviewItem, ReviewQuestionSummary } from '../../api/review'
import { reviewShortcutBus } from '../../composables/review-shortcuts'
import type { ReviewScope, ReviewSort } from '../../stores/review-queue'
import StatusBadge from '../design-system/StatusBadge.vue'

const props = defineProps<{
  questions: ReviewQuestionSummary[]
  selectedQuestionId: string | null
  pageItems: ReviewItem[]
  selectedDetailId: number | null
  search: string
  scope: ReviewScope
  sort: ReviewSort
  page: number
  totalPages: number
  filteredTotal: number
  loading: boolean
}>()

const emit = defineEmits<{
  selectQuestion: [questionId: string]
  selectDetail: [detailId: number]
  updateSearch: [value: string]
  updateScope: [value: ReviewScope]
  updateSort: [value: ReviewSort]
  updatePage: [page: number]
}>()

const searchInput = ref<HTMLInputElement | null>(null)
let stopShortcuts: () => void = () => undefined

onMounted(() => {
  stopShortcuts = reviewShortcutBus.subscribe((command) => {
    if (command !== 'focus-search') return
    searchInput.value?.focus()
    searchInput.value?.select()
  })
})

onBeforeUnmount(() => {
  stopShortcuts()
})

function questionLabel(question: ReviewQuestionSummary): string {
  return `${question.question_id} · ${question.total_count} 条 · 待复核 ${question.needs_review_count}`
}

function confidenceLabel(item: ReviewItem): string {
  return item.confidence_score === null
    ? '置信度未提供'
    : `置信度 ${Math.round(item.confidence_score)}%`
}

function reason(item: ReviewItem): string | null {
  return item.error_summary ?? item.error_category ?? item.deduction_reason
}

function isSelected(detailId: number): boolean {
  return detailId === props.selectedDetailId
}

function selectQuestion(event: Event): void {
  emit('selectQuestion', (event.target as HTMLSelectElement).value)
}

function updateSearch(event: Event): void {
  emit('updateSearch', (event.target as HTMLInputElement).value)
}

function updateScope(event: Event): void {
  emit('updateScope', (event.target as HTMLSelectElement).value as ReviewScope)
}

function updateSort(event: Event): void {
  emit('updateSort', (event.target as HTMLSelectElement).value as ReviewSort)
}
</script>

<template>
  <section class="review-queue-panel" aria-labelledby="review-queue-title" :aria-busy="loading">
    <header class="review-queue-panel__header">
      <h2 id="review-queue-title">复核记录</h2>
      <span>共 {{ filteredTotal }} 条</span>
    </header>

    <div class="review-queue-panel__controls">
      <div class="review-queue-control">
        <label for="review-question">题目</label>
        <select
          id="review-question"
          :value="selectedQuestionId ?? ''"
          @change="selectQuestion"
        >
          <option
            v-for="question in questions"
            :key="question.question_id"
            :value="question.question_id"
          >
            {{ questionLabel(question) }}
          </option>
        </select>
      </div>

      <div class="review-queue-control">
        <label for="review-search">搜索学生</label>
        <input
          id="review-search"
          ref="searchInput"
          type="search"
          :value="search"
          placeholder="姓名、学号或班级"
          @input="updateSearch"
        >
      </div>

      <div class="review-queue-control">
        <label for="review-scope">复核范围</label>
        <select id="review-scope" :value="scope" @change="updateScope">
          <option value="all">全部记录</option>
          <option value="needs_review">仅待复核</option>
        </select>
      </div>

      <div class="review-queue-control">
        <label for="review-sort">排序方式</label>
        <select id="review-sort" :value="sort" @change="updateSort">
          <option value="risk">风险优先</option>
          <option value="student_code">按学号</option>
          <option value="student_name">按姓名</option>
        </select>
      </div>
    </div>

    <ul class="review-queue-list" aria-label="复核记录列表">
      <li v-for="entry in pageItems" :key="entry.detail_id">
        <button
          class="review-queue-row"
          type="button"
          :aria-current="isSelected(entry.detail_id) ? 'true' : undefined"
          @click="emit('selectDetail', entry.detail_id)"
        >
          <strong class="review-queue-row__name">{{ entry.student_name }}</strong>
          <span>{{ entry.student_code ?? '未提供学号' }}</span>
          <span v-if="entry.class_name">{{ entry.class_name }}</span>
          <StatusBadge
            :tone="entry.needs_review ? 'warning' : 'success'"
            :label="entry.needs_review ? '待复核' : '已复核'"
          />
          <span>{{ confidenceLabel(entry) }}</span>
          <span v-if="reason(entry)" class="review-queue-row__reason">{{ reason(entry) }}</span>
        </button>
      </li>
    </ul>

    <nav class="review-queue-pagination" aria-label="复核记录分页">
      <button
        type="button"
        :disabled="page <= 1"
        @click="emit('updatePage', page - 1)"
      >
        上一页
      </button>
      <span>第 {{ page }} / {{ totalPages }} 页</span>
      <button
        type="button"
        :disabled="page >= totalPages"
        @click="emit('updatePage', page + 1)"
      >
        下一页
      </button>
    </nav>
  </section>
</template>
