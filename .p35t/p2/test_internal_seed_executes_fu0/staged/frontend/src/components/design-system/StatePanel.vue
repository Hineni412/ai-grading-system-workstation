<script lang="ts">
export type StateKind = 'empty' | 'loading' | 'error'
</script>

<script setup lang="ts">
defineProps<{
  kind: StateKind
  title: string
  description: string
  detail?: string
  retryLabel?: string
}>()

defineEmits<{
  retry: []
}>()
</script>

<template>
  <section
    class="state-panel"
    data-testid="state-panel"
    :data-kind="kind"
    :role="kind === 'error' ? 'alert' : 'status'"
    :aria-busy="kind === 'loading' ? 'true' : undefined"
  >
    <div class="state-panel__marker" aria-hidden="true" />
    <div class="state-panel__content">
      <h3>{{ title }}</h3>
      <p>{{ description }}</p>
      <p v-if="detail" class="state-panel__detail">{{ detail }}</p>
      <div v-if="kind === 'loading'" class="state-panel__skeletons" aria-hidden="true">
        <span v-for="line in 3" :key="line" class="state-panel__skeleton" />
      </div>
      <button
        v-if="kind === 'error' && retryLabel"
        class="state-panel__action"
        type="button"
        @click="$emit('retry')"
      >
        {{ retryLabel }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.state-panel {
  display: grid;
  min-width: 0;
  grid-template-columns: var(--space-1) minmax(0, 1fr);
  gap: var(--space-4);
  padding: var(--space-5);
  border: var(--border-width) solid var(--color-border-subtle);
  border-radius: var(--radius-panel);
  background: var(--color-bg-subtle);
}

.state-panel__marker {
  border-radius: var(--radius-tag);
  background: var(--color-info);
}

.state-panel[data-kind='loading'] .state-panel__marker {
  background: var(--color-accent);
}

.state-panel[data-kind='error'] .state-panel__marker {
  background: var(--color-danger);
}

.state-panel__content {
  display: grid;
  min-width: 0;
  gap: var(--space-2);
}

.state-panel h3,
.state-panel p {
  margin: 0;
}

.state-panel h3 {
  font-size: var(--font-size-h3);
  font-weight: var(--font-weight-semibold);
}

.state-panel p {
  color: var(--color-text-secondary);
}

.state-panel__detail {
  color: var(--color-text-primary);
  font-size: var(--font-size-dense);
}

.state-panel__skeletons {
  display: grid;
  gap: var(--space-2);
  padding-block: var(--space-2);
}

.state-panel__skeleton {
  display: block;
  height: var(--space-3);
  border-radius: var(--radius-tag);
  background: var(--color-bg-selected);
}

.state-panel__skeleton:last-child {
  width: 62%;
}

.state-panel__action {
  justify-self: start;
  padding: var(--space-2) var(--space-3);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  cursor: pointer;
}
</style>
