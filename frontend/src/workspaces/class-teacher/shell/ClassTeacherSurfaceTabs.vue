<script setup lang="ts">
import type { ClassTeacherSurface } from './useClassTeacherRouteState'

defineProps<{ active: ClassTeacherSurface }>()
const emit = defineEmits<{ select: [surface: ClassTeacherSurface] }>()

const tabs: Array<{ id: ClassTeacherSurface; label: string }> = [
  { id: 'home', label: '首页' },
  { id: 'calendar', label: '日历' },
  { id: 'affairs', label: '事务' },
  { id: 'students', label: '学生' },
]
</script>

<template>
  <nav class="surface-tabs" aria-label="班主任工作台">
    <button
      v-for="tab in tabs"
      :key="tab.id"
      type="button"
      :class="['surface-tabs__tab', { 'is-active': active === tab.id }]"
      :aria-current="active === tab.id ? 'page' : undefined"
      @click="emit('select', tab.id)"
    >
      {{ tab.label }}
    </button>
  </nav>
</template>

<style scoped>
.surface-tabs {
  display: flex;
  gap: var(--space-1);
  min-height: 48px;
  padding: 0 var(--space-6);
  border-bottom: var(--border-width) solid var(--color-border-default);
  background: var(--color-bg-surface);
}

.surface-tabs__tab {
  position: relative;
  min-height: 48px;
  padding: 0 var(--space-4);
  border: 0;
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  font-weight: var(--font-weight-medium);
  cursor: pointer;
}

.surface-tabs__tab::after {
  position: absolute;
  right: var(--space-3);
  bottom: -1px;
  left: var(--space-3);
  height: 3px;
  border-radius: 3px 3px 0 0;
  background: transparent;
  content: '';
}

.surface-tabs__tab.is-active {
  color: var(--color-accent-active);
}

.surface-tabs__tab.is-active::after {
  background: var(--color-accent);
}

@media (max-width: 760px) {
  .surface-tabs {
    overflow-x: auto;
    padding: 0 var(--space-3);
  }

  .surface-tabs__tab {
    flex: 0 0 auto;
    padding-inline: var(--space-3);
  }
}
</style>
