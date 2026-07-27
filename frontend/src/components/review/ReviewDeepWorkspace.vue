<script setup lang="ts">
import type {
  ReviewConfirmInput,
  ReviewItemLike,
} from '../../api/review'
import ReviewEvidenceViewer from './ReviewEvidenceViewer.vue'
import ReviewScoringInspector from './ReviewScoringInspector.vue'

defineProps<{
  item: ReviewItemLike
  previousItem: ReviewItemLike | null
  nextItem: ReviewItemLike | null
  registerAnnotationRetry: (entry: {
    input: ReviewConfirmInput
    item: ReviewItemLike
  }) => void
}>()

const emit = defineEmits<{
  back: []
  confirmed: [payload: {
    reviewItemId: string
    annotationRetry: boolean
  }]
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
          :register-annotation-retry="registerAnnotationRetry"
          @confirmed="emit('confirmed', $event)"
        />
      </div>
    </div>
  </section>
</template>
