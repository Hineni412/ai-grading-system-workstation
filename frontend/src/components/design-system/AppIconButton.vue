<script setup lang="ts">
import type { AppIconName } from '../../navigation'
import AppIcon from '../shell/AppIcon.vue'

defineOptions({ inheritAttrs: false })

withDefaults(defineProps<{
  label: string
  icon: AppIconName
  type?: 'button' | 'submit' | 'reset'
  variant?: 'ghost' | 'secondary'
  disabled?: boolean
}>(), {
  type: 'button',
  variant: 'ghost',
  disabled: false,
})
</script>

<template>
  <button
    v-bind="$attrs"
    class="app-icon-button"
    :data-variant="variant"
    :type="type"
    :disabled="disabled"
    :aria-label="label"
    :title="label"
  >
    <AppIcon :name="icon" />
  </button>
</template>

<style scoped>
.app-icon-button {
  display: inline-grid;
  width: var(--control-height-default);
  height: var(--control-height-default);
  flex: none;
  place-items: center;
  padding: 0;
  border: var(--border-width) solid transparent;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  cursor: pointer;
  transition:
    background-color var(--duration-fast),
    border-color var(--duration-fast),
    color var(--duration-fast);
}

.app-icon-button[data-variant='ghost']:hover:not(:disabled) {
  background: var(--color-accent-subtle);
  color: var(--color-accent);
}

.app-icon-button[data-variant='secondary'] {
  border-color: var(--color-border-default);
  background: var(--color-bg-surface);
}

.app-icon-button[data-variant='secondary']:hover:not(:disabled) {
  border-color: var(--color-border-strong);
  background: var(--color-bg-subtle);
  color: var(--color-text-primary);
}

.app-icon-button:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.app-icon-button:disabled {
  cursor: not-allowed;
  opacity: var(--opacity-disabled);
}
</style>
