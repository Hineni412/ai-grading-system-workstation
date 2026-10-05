<script lang="ts">
export interface StepProgressStep {
  id: string
  label: string
  status: 'done' | 'in_progress' | 'todo'
  available: boolean
  hint?: string
}
</script>

<script setup lang="ts">
import { Check } from '@lucide/vue'

withDefaults(defineProps<{
  steps: StepProgressStep[]
  current: string
  orientation?: 'horizontal' | 'vertical'
}>(), { orientation: 'horizontal' })

const emit = defineEmits<{
  select: [id: string]
}>()
</script>

<template>
  <ol class="step-progress" :class="{ 'step-progress--vertical': orientation === 'vertical' }">
    <template v-for="(step, index) in steps" :key="step.id">
      <li
        v-if="index > 0"
        class="step-progress__connector"
        :class="{ 'is-done': steps[index - 1]?.status === 'done' }"
        aria-hidden="true"
      />
      <li
        class="step-progress__item"
        :class="[`is-${step.status}`, { 'is-current': step.id === current }]"
      >
        <button
          type="button"
          :disabled="!step.available"
          :aria-current="step.id === current ? 'step' : undefined"
          :aria-description="step.hint"
          :title="step.hint"
          :data-step-id="step.id"
          @click="emit('select', step.id)"
        >
          <span class="step-progress__node" aria-hidden="true">
            <Check v-if="step.status === 'done'" :size="11" :stroke-width="3" />
            <span
              v-else-if="step.status === 'in_progress'"
              class="step-progress__node-dot"
            />
            <template v-else>{{ index + 1 }}</template>
          </span>
          <span class="step-progress__label">{{ step.label }}</span>
          <span v-if="orientation === 'vertical' && step.hint" class="step-progress__hint">{{ step.hint }}</span>
        </button>
      </li>
    </template>
  </ol>
</template>

<style scoped>
.step-progress {
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.step-progress__item {
  display: flex;
  flex: 0 0 auto;
}

.step-progress__connector {
  flex: 0 0 16px;
  align-self: center;
  height: 2px;
  margin-inline: var(--space-2);
  border-radius: 1px;
  background: var(--color-border-default);
}

.step-progress__connector.is-done {
  background: var(--color-accent);
}

.step-progress button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: var(--control-height-small);
  margin: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
  font-family: inherit;
  white-space: nowrap;
  cursor: pointer;
}

.step-progress button:disabled {
  cursor: not-allowed;
  opacity: var(--opacity-disabled);
}

.step-progress button:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.step-progress__label {
  min-width: 0;
}

.step-progress__node {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 18px;
  height: 18px;
  border: 1.5px solid var(--color-border-strong);
  border-radius: var(--radius-circle);
  box-sizing: border-box;
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  font-variant-numeric: tabular-nums;
}

.is-done .step-progress__node {
  border-color: var(--color-accent);
  background: var(--color-accent);
  color: var(--color-bg-surface);
}

.is-in_progress .step-progress__node {
  border-color: var(--color-accent);
}

.step-progress__node-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-circle);
  background: var(--color-accent);
}

.is-current .step-progress__label {
  color: var(--color-text-primary);
  font-weight: var(--font-weight-semibold);
  box-shadow: inset 0 -2px 0 var(--color-accent);
}

@media (prefers-reduced-motion: reduce) {
  .step-progress button {
    transition: none;
  }
}

.step-progress--vertical { flex-direction: column; align-items: stretch; gap: 0; }
.step-progress--vertical .step-progress__item { min-width: 0; }
.step-progress--vertical button {
  display: grid; grid-template-columns: 18px minmax(0, 1fr) auto;
  gap: var(--space-2); width: 100%; min-height: var(--control-height-default); text-align: left;
  border-radius: var(--radius-control); white-space: normal;
}
.step-progress--vertical button:hover:not(:disabled) { background: var(--color-bg-subtle); }
.step-progress--vertical .step-progress__connector {
  flex: 0 0 8px; width: 2px; height: 8px; align-self: flex-start; margin: -2px 0 -2px 8px;
}
.step-progress--vertical .step-progress__hint {
  max-width: 140px; font-size: var(--font-size-caption); color: var(--color-text-secondary); text-align: right;
}
.step-progress--vertical .is-current .step-progress__label { box-shadow: none; }
</style>
