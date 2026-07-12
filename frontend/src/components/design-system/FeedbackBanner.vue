<script lang="ts">
export type FeedbackTone = 'info' | 'success' | 'warning' | 'error'
</script>

<script setup lang="ts">
defineProps<{
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
</script>

<template>
  <aside
    class="feedback-banner"
    data-testid="feedback-banner"
    :data-tone="tone"
    :role="tone === 'warning' || tone === 'error' ? 'alert' : 'status'"
  >
    <div class="feedback-banner__marker" aria-hidden="true" />
    <div class="feedback-banner__copy">
      <strong>{{ title }}</strong>
      <p>{{ description }}</p>
    </div>
    <div v-if="actionLabel || dismissible" class="feedback-banner__actions">
      <button v-if="actionLabel" type="button" @click="$emit('action')">
        {{ actionLabel }}
      </button>
      <button v-if="dismissible" type="button" @click="$emit('dismiss')">关闭提示</button>
    </div>
  </aside>
</template>

<style scoped>
.feedback-banner {
  display: grid;
  min-width: 0;
  grid-template-columns: var(--space-1) minmax(0, 1fr) auto;
  align-items: start;
  gap: var(--space-3);
  padding: var(--space-4);
  border: var(--border-width) solid var(--color-border-subtle);
  border-radius: var(--radius-panel);
  background: var(--color-info-subtle);
  color: var(--color-text-primary);
}

.feedback-banner__marker {
  align-self: stretch;
  border-radius: var(--radius-tag);
  background: var(--color-info);
}

.feedback-banner[data-tone='success'] {
  background: var(--color-success-subtle);
}

.feedback-banner[data-tone='success'] .feedback-banner__marker {
  background: var(--color-success);
}

.feedback-banner[data-tone='warning'] {
  background: var(--color-warning-subtle);
}

.feedback-banner[data-tone='warning'] .feedback-banner__marker {
  background: var(--color-warning);
}

.feedback-banner[data-tone='error'] {
  background: var(--color-danger-subtle);
}

.feedback-banner[data-tone='error'] .feedback-banner__marker {
  background: var(--color-danger);
}

.feedback-banner__copy {
  display: grid;
  min-width: 0;
  gap: var(--space-1);
}

.feedback-banner strong {
  font-weight: var(--font-weight-semibold);
}

.feedback-banner p {
  margin: 0;
  color: var(--color-text-secondary);
}

.feedback-banner__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.feedback-banner button {
  padding: var(--space-1) var(--space-2);
  border: 0;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--color-accent);
  font-weight: var(--font-weight-medium);
  cursor: pointer;
}

@media (max-width: 640px) {
  .feedback-banner {
    grid-template-columns: var(--space-1) minmax(0, 1fr);
  }

  .feedback-banner__actions {
    grid-column: 2;
  }
}
</style>
