<script lang="ts">
export type FeedbackTone = 'info' | 'success' | 'warning' | 'error'
</script>

<script setup lang="ts">
import { computed } from 'vue'

import { cn } from '@/lib/utils'
import AppButton from './AppButton.vue'

const props = defineProps<{
  tone: FeedbackTone
  title: string
  description: string
  actionLabel?: string
  dismissible?: boolean
}>()

defineEmits<{
  action: []
  dismiss: []
}>()

/* 颜色走 tokens.css 令牌（经 Tailwind 任意值引用） */
const toneSurface: Record<FeedbackTone, string> = {
  info: 'bg-(--color-info-subtle)',
  success: 'bg-(--color-success-subtle)',
  warning: 'bg-(--color-warning-subtle)',
  error: 'bg-(--color-danger-subtle)',
}

const toneMarker: Record<FeedbackTone, string> = {
  info: 'bg-(--color-info)',
  success: 'bg-(--color-success)',
  warning: 'bg-(--color-warning)',
  error: 'bg-(--color-danger)',
}

const bannerClass = computed(() =>
  cn(
    'feedback-banner fx-enter grid min-w-0 grid-cols-[var(--space-1)_minmax(0,1fr)_auto] items-start gap-3 rounded-(--radius-panel) border border-border p-4 text-foreground max-sm:grid-cols-[var(--space-1)_minmax(0,1fr)]',
    toneSurface[props.tone],
  ),
)

</script>

<template>
  <aside
    :class="bannerClass"
    data-testid="feedback-banner"
    :data-tone="tone"
    :role="tone === 'warning' || tone === 'error' ? 'alert' : 'status'"
  >
    <div
      class="feedback-banner__marker self-stretch rounded-(--radius-tag)"
      :class="toneMarker[tone]"
      aria-hidden="true"
    />
    <div class="feedback-banner__copy grid min-w-0 gap-1">
      <strong class="font-semibold">{{ title }}</strong>
      <p v-if="description" class="m-0 text-(--color-text-secondary)">{{ description }}</p>
    </div>
    <div v-if="actionLabel || dismissible" class="feedback-banner__actions flex flex-wrap gap-2 max-sm:col-start-2">
      <AppButton v-if="actionLabel" type="button" variant="ghost" size="sm" @click="$emit('action')">
        {{ actionLabel }}
      </AppButton>
      <AppButton v-if="dismissible" type="button" variant="ghost" size="sm" @click="$emit('dismiss')">关闭提示</AppButton>
    </div>
  </aside>
</template>
