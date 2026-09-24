<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

const route = useRoute()

const current = computed<'overview' | 'structure' | 'chapter' | 'student' | 'paper'>(() => {
  if (route.name === 'knowledge-overview') return 'overview'
  if (route.name !== 'training') return 'structure'
  if (route.query.mode === 'paper') return 'paper'
  return route.query.mode === 'student' ? 'student' : 'chapter'
})

const tabs = [
  {
    id: 'overview',
    label: '学情总览',
    to: { name: 'knowledge-overview' },
  },
  {
    id: 'structure',
    label: '知识结构',
    to: { name: 'knowledge-graph' },
  },
  {
    id: 'chapter',
    label: '按章节训练',
    to: { name: 'training', query: { mode: 'chapter' } },
  },
  {
    id: 'student',
    label: '按学生训练',
    to: { name: 'training', query: { mode: 'student' } },
  },
  {
    id: 'paper',
    label: '生成试卷',
    to: { name: 'training', query: { mode: 'paper' } },
  },
] as const
</script>

<template>
  <nav class="knowledge-training-tabs" aria-label="知识与训练页面">
    <RouterLink
      v-for="tab in tabs"
      :key="tab.id"
      :to="tab.to"
      :aria-current="current === tab.id ? 'page' : undefined"
    >
      {{ tab.label }}
    </RouterLink>
    <span>同一证据范围 · 页面之间自动保留</span>
  </nav>
</template>

<style scoped>
.knowledge-training-tabs {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  min-width: 0;
  padding: var(--space-1);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
}

.knowledge-training-tabs a {
  display: inline-flex;
  min-height: var(--control-height-default);
  align-items: center;
  padding-inline: var(--space-3);
  border-radius: calc(var(--radius) - 2px);
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
  text-decoration: none;
}

.knowledge-training-tabs a[aria-current='page'] {
  background: var(--color-accent-subtle);
  color: var(--color-accent-active);
  font-weight: var(--font-weight-semibold);
}

.knowledge-training-tabs a:focus-visible {
  outline: var(--border-width) solid var(--color-accent);
  outline-offset: var(--focus-offset);
  box-shadow: var(--focus-ring);
}

.knowledge-training-tabs > span {
  margin-inline-start: auto;
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}

@media (max-width: 760px) {
  .knowledge-training-tabs {
    flex-wrap: wrap;
  }

  .knowledge-training-tabs > span {
    width: 100%;
    margin-inline-start: 0;
    padding-inline: var(--space-2);
  }
}
</style>
