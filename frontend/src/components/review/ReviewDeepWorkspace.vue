<script setup lang="ts">
import type { ReviewConfirmInput, ReviewItem } from '../../api/review'
import ReviewEvidenceViewer from './ReviewEvidenceViewer.vue'
import ReviewScoringInspector from './ReviewScoringInspector.vue'

defineProps<{
  item: ReviewItem
  previousItem: ReviewItem | null
  nextItem: ReviewItem | null
}>()

const emit = defineEmits<{
  back: []
  confirmed: [payload: {
    detailId: number
    annotationRetry: boolean
  }]
  annotationRetry: [entry: { input: ReviewConfirmInput; item: ReviewItem }]
}>()
</script>

<template>
  <section
    class="review-deep-workspace"
    data-testid="review-deep-workspace"
    aria-labelledby="review-deep-title"
  >
    <header class="review-deep-workspace__header">
      <button type="button" data-testid="back-to-batch" @click="emit('back')">
        返回 {{ item.question_id }} 批量复核
      </button>
      <div>
        <p>单份深查 · 返回后保留批次、筛选和未确认草稿</p>
        <h2 id="review-deep-title">{{ item.student_name }}</h2>
        <span>{{ item.student_code || '学号未提供' }} · {{ item.class_name || '班级未提供' }}</span>
      </div>
    </header>

    <div class="review-deep-workspace__body">
      <div class="review-deep-workspace__evidence">
        <ReviewEvidenceViewer
          :item="item"
          :previous-item="previousItem"
          :next-item="nextItem"
        />
      </div>
      <div class="review-deep-workspace__scoring">
        <ReviewScoringInspector
          @confirmed="emit('confirmed', $event)"
          @annotation-retry="emit('annotationRetry', $event)"
        />
      </div>
    </div>
  </section>
</template>
