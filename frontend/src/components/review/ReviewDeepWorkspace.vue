<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import {
  resolveReviewItem,
  type ReviewMediaLinks,
  type ReviewConfirmInput,
  type ReviewItemLike,
} from '../../api/review'
import type { EvidenceSource } from '../../composables/use-evidence-viewer'
import { ImageCompare } from '../ui/image-compare'
import ReviewEvidenceViewer from './ReviewEvidenceViewer.vue'
import ReviewScoringInspector from './ReviewScoringInspector.vue'

const props = defineProps<{
  item: ReviewItemLike
  previousItem: ReviewItemLike | null
  nextItem: ReviewItemLike | null
  registerAnnotationRetry: (entry: {
    input: ReviewConfirmInput
    item: ReviewItemLike
  }) => void
}>()

const sourceOptions: readonly {
  value: EvidenceSource
  label: string
  mediaKey: keyof ReviewMediaLinks
}[] = [
  { value: 'crop', label: '裁剪证据', mediaKey: 'crop_url' },
  { value: 'original_front', label: '原卷正面', mediaKey: 'original_front_url' },
  { value: 'original_back', label: '原卷反面', mediaKey: 'original_back_url' },
  { value: 'annotated_front', label: '标注正面', mediaKey: 'annotated_front_url' },
  { value: 'annotated_back', label: '标注反面', mediaKey: 'annotated_back_url' },
]
const selectedSource = ref<EvidenceSource>('crop')
const compareMode = ref(false)
const resolvedItem = computed(() => resolveReviewItem(props.item))
const availableSources = computed(() =>
  sourceOptions.filter((option) => Boolean(resolvedItem.value.media[option.mediaKey])),
)
const canCompareFront = computed(() => Boolean(
  resolvedItem.value.media.original_front_url && resolvedItem.value.media.annotated_front_url,
))

function selectSource(source: EvidenceSource): void {
  selectedSource.value = source
  compareMode.value = false
}

watch(
  () => resolvedItem.value.review_item_id,
  () => {
    selectedSource.value = 'crop'
    compareMode.value = false
  },
  { immediate: true, flush: 'sync' },
)

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
      <div class="review-deep-workspace__identity">
        <p>单份深查 · 返回后保留批次、筛选和未确认草稿</p>
        <h2 id="review-deep-title">{{ item.student_name }}</h2>
        <span>{{ item.student_code || '学号未提供' }} · {{ item.class_name || '班级未提供' }}</span>
      </div>
      <div
        class="review-deep-workspace__sources"
        role="group"
        aria-label="证据来源"
      >
        <button
          v-for="option in availableSources"
          :key="option.value"
          type="button"
          :aria-pressed="!compareMode && selectedSource === option.value"
          @click="selectSource(option.value)"
        >
          {{ option.label }}
        </button>
        <button
          v-if="canCompareFront"
          type="button"
          :aria-pressed="compareMode"
          @click="compareMode = !compareMode"
        >
          对比
        </button>
      </div>
    </header>

    <div class="review-deep-workspace__body">
      <div class="review-deep-workspace__evidence">
        <ImageCompare
          v-if="compareMode"
          class="review-deep-workspace__compare"
          :first-image="resolvedItem.media.original_front_url ?? ''"
          first-image-alt="原卷正面"
          :second-image="resolvedItem.media.annotated_front_url ?? ''"
          second-image-alt="标注正面"
        />
        <ReviewEvidenceViewer
          v-else
          :item="item"
          :previous-item="previousItem"
          :next-item="nextItem"
          :source="selectedSource"
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
