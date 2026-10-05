<script setup lang="ts">
import AppDialog from '../design-system/AppDialog.vue'
import type { ReviewItemLike } from '../../api/review'
import type { EvidenceSource } from '../../composables/use-evidence-viewer'
import ReviewEvidenceViewer from './ReviewEvidenceViewer.vue'

withDefaults(defineProps<{ item: ReviewItemLike; source?: EvidenceSource }>(), { source: 'crop' })
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <AppDialog
    :open="true"
    title="放大查看答卷"
    class="review-image-dialog"
    @update:open="(value: boolean) => { if (!value) emit('close') }"
  >
    <template #header-extra>
      <strong>{{ item.student_name }} · {{ item.question_id }}</strong>
    </template>
    <ReviewEvidenceViewer
      :item="item"
      :previous-item="null"
      :next-item="null"
      :source="source"
      :expandable="false"
    />
  </AppDialog>
</template>
