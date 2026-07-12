<script lang="ts">
export type StatusTone =
  | 'neutral'
  | 'info'
  | 'success'
  | 'warning'
  | 'danger'
  | 'ai'
  | 'teacher'
</script>

<script setup lang="ts">
defineProps<{
  tone: StatusTone
  label: string
}>()
</script>

<template>
  <span
    class="status-badge"
    data-testid="status-badge"
    :data-tone="tone"
    :aria-label="`状态：${label}`"
  >
    {{ label }}
  </span>
</template>

<style scoped>
.status-badge {
  display: inline-flex;
  max-width: 100%;
  align-items: center;
  padding: var(--space-1) var(--space-2);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-tag);
  background: var(--color-bg-selected);
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
  font-weight: var(--font-weight-medium);
  line-height: var(--line-height-tight);
  overflow-wrap: anywhere;
}

.status-badge[data-tone='info'] {
  border-color: var(--color-info);
  background: var(--color-info-subtle);
  color: var(--color-info);
}

.status-badge[data-tone='success'] {
  border-color: var(--color-success);
  background: var(--color-success-subtle);
  color: var(--color-success);
}

.status-badge[data-tone='warning'] {
  border-color: var(--color-warning);
  background: var(--color-warning-subtle);
  color: var(--color-text-primary);
}

.status-badge[data-tone='danger'] {
  border-color: var(--color-danger);
  background: var(--color-danger-subtle);
  color: var(--color-danger);
}

.status-badge[data-tone='ai'] {
  border-color: var(--color-ai);
  background: var(--color-ai-subtle);
  color: var(--color-ai);
}

.status-badge[data-tone='teacher'] {
  border-color: var(--color-teacher);
  background: var(--color-teacher-subtle);
  color: var(--color-teacher);
}
</style>
