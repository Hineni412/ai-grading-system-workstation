<script lang="ts">
export type StateKind = 'empty' | 'loading' | 'error'
</script>

<script setup lang="ts">
import { computed } from 'vue'
import { Inbox, LoaderCircle, TriangleAlert, type LucideIcon } from '@lucide/vue'

import { cn } from '@/lib/utils'
import AppButton from './AppButton.vue'

const props = defineProps<{
  kind: StateKind
  title: string
  description?: string
  detail?: string
  retryLabel?: string
  compact?: boolean
}>()

defineEmits<{
  retry: []
}>()

/* 颜色走 tokens.css 令牌（经 Tailwind 任意值引用） */
const markerClasses: Record<StateKind, string> = {
  empty: 'bg-(--color-info)',
  loading: 'bg-primary',
  error: 'bg-destructive',
}

const kindIcons: Record<StateKind, LucideIcon> = {
  empty: Inbox,
  loading: LoaderCircle,
  error: TriangleAlert,
}

const iconClasses: Record<StateKind, string> = {
  empty: 'text-(--color-info)',
  loading: 'text-primary',
  error: 'text-destructive',
}

const markerClass = computed(() =>
  cn('state-panel__marker rounded-(--radius-tag)', markerClasses[props.kind]),
)
</script>

<template>
  <section
    class="state-panel fx-enter min-w-0"
    :class="kind === 'empty'
      ? cn('flex flex-col items-center text-center', compact ? 'p-4' : 'px-6 py-10')
      : 'grid grid-cols-[var(--space-1)_minmax(0,1fr)] gap-4 rounded-(--radius-panel) border border-border bg-card p-5 shadow-[var(--shadow-raised)]'"
    data-testid="state-panel"
    :data-kind="kind"
    :role="kind === 'error' ? 'alert' : 'status'"
    :aria-busy="kind === 'loading' ? 'true' : undefined"
  >
    <div v-if="kind !== 'empty'" :class="markerClass" aria-hidden="true" />
    <div class="state-panel__content grid min-w-0 gap-2">
      <div class="flex items-center gap-2" :class="kind === 'empty' && 'justify-center'">
        <component
          v-if="kind !== 'empty'"
          :is="kindIcons[kind]"
          :class="cn('state-panel__icon size-5', iconClasses[kind], kind === 'loading' && 'animate-spin')"
          aria-hidden="true"
        />
        <h3
          class="m-0 text-base"
          :class="compact && kind === 'empty' ? 'font-medium text-(--color-text-secondary)' : 'font-semibold'"
        >{{ title }}</h3>
      </div>
      <p v-if="description" class="m-0 text-xs leading-relaxed text-(--color-text-secondary)">{{ description }}</p>
      <p v-if="detail" class="state-panel__detail m-0 text-xs text-foreground">{{ detail }}</p>
      <div v-if="kind === 'loading'" class="state-panel__skeletons grid gap-2 py-2" aria-hidden="true">
        <span
          v-for="line in 3"
          :key="line"
          class="state-panel__skeleton block h-3 rounded-(--radius-tag) bg-(--color-bg-selected) last:w-[62%]"
        />
      </div>
      <AppButton
        v-if="kind === 'error' && retryLabel"
        class="state-panel__action justify-self-start"
        variant="secondary"
        type="button"
        @click="$emit('retry')"
      >
        {{ retryLabel }}
      </AppButton>
      <div v-if="$slots.actions" class="state-panel__actions flex gap-2" :class="{ 'justify-center': kind === 'empty' }">
        <slot name="actions" />
      </div>
    </div>
  </section>
</template>

<style scoped>
/* 加载骨架：白纹流光扫过，表示正在读取（有实际进度语义，非装饰闪烁） */
.state-panel__skeleton {
  background-image: linear-gradient(
    90deg,
    transparent,
    var(--fx-shimmer-stripe),
    transparent
  );
  background-repeat: no-repeat;
  background-size: 220% 100%;
  animation: state-panel-skeleton 1.4s linear infinite;
}

@keyframes state-panel-skeleton {
  from { background-position: 120% 0; }
  to { background-position: -120% 0; }
}
</style>
