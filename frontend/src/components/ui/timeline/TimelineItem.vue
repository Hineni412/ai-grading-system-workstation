<!--
  时间线条目：竖线 + 状态色节点 + 内容插槽。
  视觉取自原型 ui-component-proposals · inspira/Timeline.vue 的节点/竖线语言，
  去掉滚动填充动画（纯静态样式，jsdom 下天然落终态），颜色走语义令牌。
-->
<script setup lang="ts">
import type { TimelineTone } from '.'

withDefaults(defineProps<{
  tone?: TimelineTone
}>(), {
  tone: 'neutral',
})
</script>

<template>
  <div class="timeline-item" :data-tone="tone">
    <span class="timeline-item__node" aria-hidden="true" />
    <div class="timeline-item__content">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.timeline-item {
  position: relative;
  display: grid;
  grid-template-columns: 14px minmax(0, 1fr);
  gap: 12px;
}

.timeline-item::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 6px;
  width: 2px;
  background: var(--border);
}

.timeline-item:first-child::before {
  top: 9px;
}

.timeline-item:last-child::before {
  bottom: auto;
  height: 9px;
}

.timeline-item:only-child::before {
  display: none;
}

.timeline-item__node {
  position: relative;
  z-index: 1;
  width: 14px;
  height: 14px;
  margin-top: 4px;
  border: 2px solid var(--muted-foreground);
  border-radius: 999px;
  background: var(--card);
}

.timeline-item[data-tone='info'] .timeline-item__node {
  border-color: var(--color-info);
  background: var(--color-info);
}

.timeline-item[data-tone='success'] .timeline-item__node {
  border-color: var(--color-success);
  background: var(--color-success);
}

.timeline-item[data-tone='warning'] .timeline-item__node {
  border-color: var(--color-warning);
  background: var(--color-warning);
}

.timeline-item[data-tone='danger'] .timeline-item__node {
  border-color: var(--destructive);
  background: var(--destructive);
}

.timeline-item__content {
  min-width: 0;
  padding-bottom: 4px;
}
</style>
