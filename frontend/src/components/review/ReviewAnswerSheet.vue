<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { resolveReviewItem, type ReviewItemLike } from '../../api/review'
import { reviewDraftKey, scoreIssue, useReviewDraftStore } from '../../stores/review-drafts'

const props = defineProps<{
  item: ReviewItemLike
  position: number
}>()

const emit = defineEmits<{
  openItem: [reviewItemId: string]
  focusNextScore: [position: number]
}>()

const draftStore = useReviewDraftStore()
const imageFailed = ref(false)
const imageKey = ref(0)
const reviewItem = computed(() => resolveReviewItem(props.item))
const draft = computed(() => draftStore.drafts[reviewDraftKey(props.item)]!)
const issue = computed(() => scoreIssue(draft.value.scoreText, reviewItem.value.max_score))
const riskReason = computed(() =>
  reviewItem.value.score_status === 'ungraded'
    ? '等待教师评分'
    :
  reviewItem.value.error_summary?.trim() ||
  reviewItem.value.error_category?.trim() ||
  reviewItem.value.deduction_reason?.trim() ||
  '等待教师确认'
)

const statusLabel = computed(() => ({
  ungraded: '未批',
  ai_ready: 'AI 已完成',
  ai_review: '待复核',
  teacher_final: '教师已确认',
  failed: '处理失败',
})[reviewItem.value.score_status])

function formatScore(value: number | null): string {
  return value !== null && Number.isFinite(value) ? String(value) : '—'
}

function formatConfidence(value: number | null): string {
  if (reviewItem.value.score_source !== 'ai') return '人工评分'
  if (value === null) return 'AI 置信度未提供'
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
    :data-review-item-id="reviewItem.review_item_id"
    :data-detail-id="reviewItem.detail_id ?? undefined"
    :aria-labelledby="`review-answer-name-${position}`"
  >
    <header class="review-answer-sheet__identity">
      <div>
        <h2 :id="`review-answer-name-${position}`">{{ reviewItem.student_name }}</h2>
        <p>{{ reviewItem.student_code || '学号未提供' }} · {{ reviewItem.class_name || '班级未提供' }}</p>
      </div>
      <span
        class="review-answer-sheet__status"
        :class="`is-${reviewItem.score_status}`"
      >
        {{ statusLabel }}
      </span>
    </header>

    <div class="review-answer-sheet__risk">
      <span>{{ riskReason }}</span>
      <span>{{ formatConfidence(reviewItem.confidence_score) }}</span>
    </div>

    <div class="review-answer-sheet__image-frame">
      <img
        v-if="!imageFailed"
        :key="`${reviewItem.review_item_id}:${imageKey}`"
        :src="reviewItem.media.crop_url"
        :alt="`${reviewItem.student_name} 的 ${reviewItem.question_id} 答卷裁剪`"
        :data-testid="`answer-crop-${position}`"
        @error="imageFailed = true"
      >
      <div v-else class="review-answer-sheet__image-error" role="status">
        <span>答卷图片暂时无法显示</span>
        <button type="button" @click="retryImage">重新加载答卷图片</button>
      </div>
    </div>

    <div class="review-answer-sheet__decision">
      <label :for="`batch-score-${position}`">教师最终分</label>
      <div class="review-answer-sheet__score">
        <input
          :id="`batch-score-${position}`"
          :value="draft.scoreText"
          inputmode="decimal"
          :disabled="reviewItem.teacher_locked"
          :aria-invalid="issue !== null"
          :aria-describedby="issue ? `batch-score-error-${position}` : undefined"
          :data-testid="`teacher-score-${position}`"
          :data-score-position="position"
          @input="updateScore"
          @focus="selectScore"
          @keydown="onScoreKeydown"
        >
        <span>/ {{ formatScore(reviewItem.max_score) }}</span>
      </div>
      <p v-if="issue && !reviewItem.teacher_locked" :id="`batch-score-error-${position}`" class="review-field-error">
        {{ issue }}
      </p>
    </div>

    <button
      type="button"
      class="review-answer-sheet__deep"
      @click="emit('openItem', reviewItem.review_item_id)"
    >
      深查此份答卷
    </button>
  </article>
</template>
