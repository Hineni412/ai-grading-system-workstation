<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { resolveReviewItem, type ReviewItemLike } from '../../api/review'
import { reviewDraftKey, scoreIssue, useReviewDraftStore } from '../../stores/review-drafts'

const props = defineProps<{
  item: ReviewItemLike
  position: number
  submitting: boolean
}>()

const emit = defineEmits<{
  openItem: [reviewItemId: string]
  scoreKeydown: [event: KeyboardEvent, reviewItemId: string]
}>()

const draftStore = useReviewDraftStore()
const imageFailed = ref(false)
const imageKey = ref(0)
const reviewItem = computed(() => resolveReviewItem(props.item))
const draft = computed(() => draftStore.drafts[reviewDraftKey(props.item)]!)
const issue = computed(() => scoreIssue(draft.value.scoreText, reviewItem.value.max_score))
const riskReason = computed(() => {
  const item = reviewItem.value
  if (item.score_status === 'ungraded') return '等待教师评分'
  if (item.score_status === 'failed') return '自动处理失败，请教师直接评分'
  const reason = item.error_summary?.trim()
    || item.error_category?.trim()
    || item.deduction_reason?.trim()
  if (reason) return reason
  if (item.score_status === 'ai_review') return 'AI 结果需要教师复核'
  if (item.score_status === 'ai_ready') return '高置信 AI 结果'
  return '教师已确认'
})

const statusLabel = computed(() => ({
  ungraded: '待人工评分',
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
  const isForwardEnter =
    event.key === 'Enter'
    && !event.shiftKey
    && !event.altKey
    && !event.ctrlKey
    && !event.metaKey
  const isScoreTab =
    event.key === 'Tab'
    && !event.altKey
    && !event.ctrlKey
    && !event.metaKey
  if ((!isForwardEnter && !isScoreTab) || event.isComposing) return
  emit('scoreKeydown', event, reviewItem.value.review_item_id)
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
    :data-score-status="reviewItem.score_status"
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
      <label :for="`batch-score-${position}`">
        {{ reviewItem.score_source === 'ai' ? '当前 AI 得分（可修改）' : '教师最终分' }}
      </label>
      <div class="review-answer-sheet__score">
        <input
          :id="`batch-score-${position}`"
          :value="draft.scoreText"
          inputmode="decimal"
          :disabled="reviewItem.teacher_locked || submitting"
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
