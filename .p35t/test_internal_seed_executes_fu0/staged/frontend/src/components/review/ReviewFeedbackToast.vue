<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue'

const props = defineProps<{
  message: string
  tone: 'success' | 'warning' | 'error'
}>()
const emit = defineEmits<{ dismiss: [] }>()

let dismissTimer: ReturnType<typeof setTimeout> | null = null

function clearDismissTimer(): void {
  if (dismissTimer === null) return
  clearTimeout(dismissTimer)
  dismissTimer = null
}

function dismiss(): void {
  clearDismissTimer()
  emit('dismiss')
}

watch(
  () => [props.message, props.tone] as const,
  ([message, tone]) => {
    clearDismissTimer()
    if (message && tone === 'success') dismissTimer = setTimeout(dismiss, 4000)
  },
  { immediate: true },
)

onBeforeUnmount(clearDismissTimer)
</script>

<template>
  <Teleport to="body">
    <div
      v-if="message"
      class="review-feedback-toast"
      :class="`review-feedback-toast--${tone}`"
      data-testid="review-feedback-toast"
      :role="tone === 'success' ? 'status' : 'alert'"
      :aria-live="tone === 'success' ? 'polite' : 'assertive'"
      aria-atomic="true"
    >
      <p>{{ message }}</p>
      <button type="button" aria-label="关闭通知" @click="dismiss">×</button>
    </div>
  </Teleport>
</template>
