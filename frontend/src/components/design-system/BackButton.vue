<script setup lang="ts">
import { RouterLink, type RouteLocationRaw } from 'vue-router'

defineOptions({ inheritAttrs: false })

defineProps<{
  label: string
  to?: RouteLocationRaw
}>()

const emit = defineEmits<{ click: [event: MouseEvent] }>()
</script>

<template>
  <RouterLink v-if="to" v-bind="$attrs" class="app-back-button" :to="to">
    <span aria-hidden="true">←</span> 返回{{ label }}
  </RouterLink>
  <button v-else v-bind="$attrs" type="button" class="app-back-button" @click="emit('click', $event)">
    <span aria-hidden="true">←</span> 返回{{ label }}
  </button>
</template>

<style scoped>
.app-back-button {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  flex-shrink: 0;
  min-height: var(--control-height-small);
  padding-inline: var(--space-3);
  border: var(--border-width) solid var(--color-border-strong);
  border-radius: var(--radius-control);
  background: var(--card);
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
  font-weight: var(--font-weight-medium);
  white-space: nowrap;
  text-decoration: none;
  cursor: pointer;
}

.app-back-button:hover {
  border-color: var(--color-accent-hover);
  background: var(--color-accent-subtle);
  color: var(--color-accent-hover);
}
</style>
