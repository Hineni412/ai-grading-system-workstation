<script setup lang="ts">
import type { ClassTeacherSurface } from './useClassTeacherRouteState'

defineProps<{ active: ClassTeacherSurface; locked: boolean }>()
const emit = defineEmits<{ select: [surface: ClassTeacherSurface] }>()

const tabs: Array<{ id: ClassTeacherSurface; label: string; protected: boolean }> = [
  { id: 'today', label: '今日', protected: false },
  { id: 'calendar', label: '日历与工作图', protected: false },
  { id: 'affairs', label: '事务', protected: true },
  { id: 'students', label: '学生', protected: true },
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
      <span v-if="tab.protected && locked" class="surface-tabs__lock" aria-label="需要解锁">锁</span>
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

.surface-tabs__lock {
  margin-left: var(--space-1);
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
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
