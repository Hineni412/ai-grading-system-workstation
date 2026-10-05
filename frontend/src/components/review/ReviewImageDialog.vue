<script setup lang="ts">
import AppIconButton from '../design-system/AppIconButton.vue'
import { onMounted, ref } from 'vue'
import type { ReviewItemLike } from '../../api/review'
import type { EvidenceSource } from '../../composables/use-evidence-viewer'
import ReviewEvidenceViewer from './ReviewEvidenceViewer.vue'

withDefaults(defineProps<{ item: ReviewItemLike; source?: EvidenceSource }>(), { source: 'crop' })
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement | null>(null)
onMounted(() => dialog.value?.showModal())
</script>

<template>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="review-image-dialog"
      aria-label="放大查看答卷"
      @close="emit('close')"
      @click.self="dialog?.close()"
    >
      <header>
        <strong>{{ item.student_name }} · {{ item.question_id }}</strong><AppIconButton label="关闭放大窗口（Esc）" autofocus @click="dialog?.close()" icon="close" />
      </header>
      <ReviewEvidenceViewer
        :item="item"
        :previous-item="null"
        :next-item="null"
        :source="source"
        :expandable="false"
      />
    </dialog>
  </Teleport>
</template>
