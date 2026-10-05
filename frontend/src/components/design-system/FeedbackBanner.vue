<script lang="ts">
export type FeedbackTone = 'info' | 'success' | 'warning' | 'error'
</script>

<script setup lang="ts">
import AppIconButton from './AppIconButton.vue'
import { computed } from 'vue'

import { cn } from '@/lib/utils'
import AppButton from './AppButton.vue'

defineOptions({ inheritAttrs: false })

const props = defineProps<{
  tone: FeedbackTone
  title?: string
  description?: string
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
    'feedback-banner fx-enter grid min-w-0 grid-cols-[var(--space-1)_minmax(0,1fr)_auto] items-start gap-3 rounded-(--radius-panel) border border-border text-foreground max-sm:grid-cols-[var(--space-1)_minmax(0,1fr)]',
    props.title ? 'p-4' : 'px-3 py-2',
    toneSurface[props.tone],
  ),
)

const computedRole = computed(() =>
  props.tone === 'warning' || props.tone === 'error' ? 'alert' : 'status',
)

</script>

<template>
  <aside
    v-bind="$attrs"
    :class="bannerClass"
    data-testid="feedback-banner"
    :data-tone="tone"
    :role="($attrs.role as string | undefined) ?? computedRole"
  >
    <div
      class="feedback-banner__marker self-stretch rounded-(--radius-tag)"
      :class="toneMarker[tone]"
      aria-hidden="true"
    />
    <div class="feedback-banner__copy grid min-w-0 gap-1">
      <strong v-if="title" class="font-semibold">{{ title }}</strong>
      <p v-if="description" class="m-0" :class="title ? 'text-(--color-text-secondary)' : undefined">{{ description }}</p>
      <slot />
    </div>
    <div v-if="actionLabel || dismissible" class="feedback-banner__actions flex flex-wrap gap-2 max-sm:col-start-2">
      <AppButton v-if="actionLabel" type="button" variant="ghost" size="small" @click="$emit('action')">
        {{ actionLabel }}
      </AppButton>
      <AppIconButton v-if="dismissible" label="关闭提示" @click="$emit('dismiss')" icon="close" />
    </div>
  </aside>
</template>
