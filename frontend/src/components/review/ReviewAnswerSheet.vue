<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { ReviewItem } from '../../api/review'
import { reviewDraftKey, scoreIssue, useReviewDraftStore } from '../../stores/review-drafts'

const props = defineProps<{
  item: ReviewItem
  position: number
}>()

const emit = defineEmits<{
  openDetail: [detailId: number]
  focusNextScore: [position: number]
}>()

const draftStore = useReviewDraftStore()
const imageFailed = ref(false)
const imageKey = ref(0)
const draft = computed(() => draftStore.drafts[reviewDraftKey(props.item)]!)
const issue = computed(() => scoreIssue(draft.value.scoreText, props.item.max_score))
const riskReason = computed(() =>
  props.item.error_summary?.trim() ||
  props.item.error_category?.trim() ||
  props.item.deduction_reason?.trim() ||
  '等待教师确认',
)

function formatScore(value: number): string {
  return Number.isFinite(value) ? String(value) : '—'
}

function formatConfidence(value: number | null): string {
  if (value === null) return '置信度未提供'
  return `置信度 ${Math.round(value <= 1 ? value * 100 : value)}%`
}

function updateScore(event: Event): void {
  draftStore.updateScore(draft.value.key, (event.currentTarget as HTMLInputElement).value)
}

function selectScore(event: FocusEvent): void {
  ;(event.currentTarget as HTMLInputElement).select()
}

function onScoreKeydown(event: KeyboardEvent): void {
  if (
    event.key !== 'Enter' ||
    event.shiftKey ||
    event.altKey ||
    event.ctrlKey ||
    event.metaKey ||
    event.repeat
  ) return
  event.preventDefault()
  emit('focusNextScore', props.position + 1)
}

function retryImage(): void {
  imageFailed.value = false
  imageKey.value += 1
}

watch(
  () => props.item,
  (item) => {
    draftStore.ensureDraft(item)
    imageFailed.value = false
    imageKey.value += 1
  },
  { immediate: true },
)
</script>

<template>
  <article
    class="review-answer-sheet"
    data-testid="review-answer-sheet"
    :data-detail-id="item.detail_id"
    :aria-labelledby="`review-answer-name-${item.detail_id}`"
  >
    <header class="review-answer-sheet__identity">
      <div>
        <h2 :id="`review-answer-name-${item.detail_id}`">{{ item.student_name }}</h2>
        <p>{{ item.student_code || '学号未提供' }} · {{ item.class_name || '班级未提供' }}</p>
      </div>
      <span :class="item.needs_review ? 'review-answer-sheet__pending' : 'review-answer-sheet__confirmed'">
        {{ item.needs_review ? '待复核' : '已复核' }}
      </span>
    </header>

    <div class="review-answer-sheet__risk">
      <span>{{ riskReason }}</span>
      <span>{{ formatConfidence(item.confidence_score) }}</span>
    </div>

    <div class="review-answer-sheet__image-frame">
      <img
        v-if="!imageFailed"
        :key="`${item.detail_id}:${imageKey}`"
        :src="item.media.crop_url"
        :alt="`${item.student_name} 的 ${item.question_id} 答卷裁剪`"
        :data-testid="`answer-crop-${item.detail_id}`"
        @error="imageFailed = true"
      >
      <div v-else class="review-answer-sheet__image-error" role="status">
        <span>答卷图片暂时无法显示</span>
        <button type="button" @click="retryImage">重新加载答卷图片</button>
      </div>
    </div>

    <div class="review-answer-sheet__decision">
      <label :for="`batch-score-${item.detail_id}`">教师最终分</label>
      <div class="review-answer-sheet__score">
        <input
          :id="`batch-score-${item.detail_id}`"
          :value="draft.scoreText"
          inputmode="decimal"
          :disabled="!item.needs_review"
          :aria-invalid="issue !== null"
          :aria-describedby="issue ? `batch-score-error-${item.detail_id}` : undefined"
          :data-testid="`teacher-score-${item.detail_id}`"
          :data-score-position="position"
          @input="updateScore"
          @focus="selectScore"
          @keydown="onScoreKeydown"
        >
        <span>/ {{ formatScore(item.max_score) }}</span>
      </div>
      <p v-if="issue" :id="`batch-score-error-${item.detail_id}`" class="review-field-error">
        {{ issue }}
      </p>
    </div>

    <button
      type="button"
      class="review-answer-sheet__deep"
      @click="emit('openDetail', item.detail_id)"
    >
      深查此份答卷
    </button>
  </article>
</template>
