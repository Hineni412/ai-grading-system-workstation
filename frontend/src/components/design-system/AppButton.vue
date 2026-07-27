<script setup lang="ts">
defineOptions({ inheritAttrs: false })

withDefaults(defineProps<{
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  type?: 'button' | 'submit' | 'reset'
  disabled?: boolean
  loading?: boolean
  loadingLabel?: string
  block?: boolean
}>(), {
  variant: 'secondary',
  type: 'button',
  disabled: false,
  loading: false,
  loadingLabel: '正在处理',
  block: false,
})
</script>

<template>
  <button
    v-bind="$attrs"
    class="app-button"
    :class="{ 'is-loading': loading, 'is-block': block }"
    :data-variant="variant"
    :type="type"
    :disabled="disabled || loading"
    :aria-busy="loading || undefined"
  >
    <span v-if="$slots.leading" class="app-button__icon" aria-hidden="true">
      <slot name="leading" />
    </span>
    <span v-if="loading" class="app-button__spinner" aria-hidden="true" />
    <span class="app-button__label"><slot /></span>
    <span v-if="loading" class="app-button__loading-label">{{ loadingLabel }}</span>
  </button>
</template>

<style scoped>
.app-button {
  position: relative;
  display: inline-flex;
  min-height: var(--control-height-default);
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: 0 14px;
  border: var(--border-width) solid transparent;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--color-text-primary);
  font: inherit;
  font-size: var(--font-size-dense);
  font-weight: var(--font-weight-semibold);
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
  transition:
    background-color var(--duration-fast),
    border-color var(--duration-fast),
    color var(--duration-fast),
    transform var(--duration-fast);
}

.app-button[data-variant='primary'] {
  border-color: var(--color-accent);
  background: var(--color-accent);
  color: var(--color-bg-surface);
}

.app-button[data-variant='primary']:hover:not(:disabled) {
  border-color: var(--color-accent-hover);
  background: var(--color-accent-hover);
}

.app-button[data-variant='secondary'] {
  border-color: var(--color-border-default);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}

.app-button[data-variant='secondary']:hover:not(:disabled) {
  border-color: var(--color-border-strong);
  background: var(--color-bg-subtle);
}

.app-button[data-variant='ghost'] {
  color: var(--color-accent);
}

.app-button[data-variant='ghost']:hover:not(:disabled) {
  background: var(--color-accent-subtle);
  color: var(--color-accent-hover);
}

.app-button[data-variant='danger'] {
  border-color: var(--color-danger);
  background: var(--color-danger);
  color: var(--color-bg-surface);
}

.app-button[data-variant='danger']:hover:not(:disabled) {
  border-color: var(--color-danger);
  background: var(--color-danger-subtle);
  color: var(--color-danger);
}

.app-button:active:not(:disabled) {
  transform: translateY(1px);
}

.app-button:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.app-button:disabled {
  cursor: not-allowed;
  opacity: var(--opacity-disabled);
}

.app-button.is-block {
  width: 100%;
}

.app-button__icon,
.app-button__label {
  display: inline-flex;
  align-items: center;
}

.app-button__spinner {
  width: 14px;
  height: 14px;
  border: 2px solid currentColor;
  border-inline-end-color: transparent;
  border-radius: var(--radius-circle);
  animation: app-button-spin 700ms linear infinite;
}

.app-button__loading-label {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  white-space: nowrap;
}

@keyframes app-button-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
