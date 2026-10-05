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
  <nav class="page-tabs knowledge-training-tabs" aria-label="知识与训练页面">
    <RouterLink
      v-for="tab in tabs"
      :key="tab.id"
      :to="tab.to"
      :class="{ 'is-active': current === tab.id }"
      :aria-current="current === tab.id ? 'page' : undefined"
    >
      {{ tab.label }}
    </RouterLink>
  </nav>
</template>

<style scoped>
.knowledge-training-tabs {
  display: flex;
  align-items: stretch;
  align-self: stretch;
  gap: 2px;
  min-width: 0;
  overflow-x: auto;
}

.knowledge-training-tabs a {
  position: relative;
  display: inline-flex;
  flex-shrink: 0;
  min-height: 56px;
  align-items: center;
  padding-inline: var(--space-3);
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
  font-weight: var(--font-weight-medium);
  text-decoration: none;
  white-space: nowrap;
}

.knowledge-training-tabs a:hover {
  color: var(--color-text-primary);
}

.knowledge-training-tabs a[aria-current='page'] {
  color: var(--color-accent);
}

.knowledge-training-tabs a[aria-current='page']::after {
  content: '';
  position: absolute;
  inset-inline: 10px;
  bottom: 0;
  height: 2px;
  border-radius: 1px;
  background: var(--color-accent);
}

.knowledge-training-tabs a:focus-visible {
  outline: var(--border-width) solid var(--color-accent);
  outline-offset: -3px;
  border-radius: var(--radius-control);
  box-shadow: var(--focus-ring);
}
</style>

<style>
.knowledge-training-page {
  --knowledge-training-inset: var(--page-inset-x);
}

.knowledge-training-page > .knowledge-training-header {
  flex: none;
  min-width: 0;
  min-height: 56px;
  gap: var(--space-4);
  border-bottom-color: var(--color-border-subtle);
}

.knowledge-training-header .page-header__title {
  font-size: var(--font-size-h2);
  white-space: nowrap;
}

.knowledge-training-header .page-header__meta {
  font-size: var(--font-size-caption);
}

.knowledge-training-header .page-header__navigation {
  min-width: 0;
}

@media (max-width: 1100px) {
  .knowledge-training-page > .knowledge-training-header {
    flex-wrap: wrap;
    gap: 0;
  }

  .knowledge-training-header .page-header__navigation {
    width: 100%;
    margin-inline-start: 0;
  }
}
</style>
