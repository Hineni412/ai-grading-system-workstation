<script lang="ts">
export type StateKind = 'empty' | 'loading' | 'error'
</script>

<script setup lang="ts">
import { computed } from 'vue'

import { cn } from '@/lib/utils'

const props = defineProps<{
  kind: StateKind
  title: string
  description: string
  detail?: string
  retryLabel?: string
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

const markerClass = computed(() =>
  cn('state-panel__marker rounded-(--radius-tag)', markerClasses[props.kind]),
)
</script>

<template>
  <section
    class="state-panel grid min-w-0 grid-cols-[var(--space-1)_minmax(0,1fr)] gap-4 rounded-xl border border-(--color-border-subtle) bg-secondary p-5"
    data-testid="state-panel"
    :data-kind="kind"
    :role="kind === 'error' ? 'alert' : 'status'"
    :aria-busy="kind === 'loading' ? 'true' : undefined"
  >
    <div :class="markerClass" aria-hidden="true" />
    <div class="state-panel__content grid min-w-0 gap-2">
      <h3 class="m-0 text-base font-semibold">{{ title }}</h3>
      <p class="m-0 text-(--color-text-secondary)">{{ description }}</p>
      <p v-if="detail" class="state-panel__detail m-0 text-[13px] text-foreground">{{ detail }}</p>
      <div v-if="kind === 'loading'" class="state-panel__skeletons grid gap-2 py-2" aria-hidden="true">
        <span
          v-for="line in 3"
          :key="line"
          class="state-panel__skeleton block h-3 rounded-(--radius-tag) bg-(--color-bg-selected) last:w-[62%]"
        />
      </div>
      <button
        v-if="kind === 'error' && retryLabel"
        class="state-panel__action cursor-pointer justify-self-start rounded-md border border-border bg-card px-3 py-2 text-foreground hover:bg-secondary"
        type="button"
        @click="$emit('retry')"
      >
        {{ retryLabel }}
      </button>
    </div>
  </section>
</template>
