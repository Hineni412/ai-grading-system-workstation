<script setup lang="ts">
import { computed } from 'vue'

import type { ConfigPhase } from '../../stores/config-workspace'

const props = defineProps<{
  phase: ConfigPhase
  sessionReady: boolean
  sourceReady: boolean
  generationSubmitted: boolean
  editorReady: boolean
}>()

const stages = computed(() => [
  { id: 'draft', label: '考试草稿', fact: props.sessionReady ? '已创建' : '待创建' },
  { id: 'source', label: '上传与拆题', fact: props.sourceReady ? '来源已读取' : '待上传' },
  { id: 'generation', label: 'AI 生成', fact: props.editorReady ? '已生成' : props.generationSubmitted ? '任务已提交' : '待提交' },
  { id: 'editor', label: '评分依据', fact: props.editorReady ? '可编辑' : '待生成' },
] as const)
</script>

<template>
  <ol class="config-stage-rail" aria-label="考试配置阶段">
    <li
      v-for="(stage, index) in stages"
      :key="stage.id"
      :class="{ 'config-stage-rail__item--active': phase === stage.id }"
      :aria-current="phase === stage.id ? 'step' : undefined"
    >
      <span class="config-stage-rail__number">{{ index + 1 }}</span>
      <span>
        <strong>{{ stage.label }}</strong>
        <small>{{ stage.fact }}</small>
      </span>
    </li>
  </ol>
</template>

<style scoped>
.config-stage-rail {
  display: grid;
  margin: 0;
  padding: 0;
  border-block: var(--border-width) solid var(--color-border-default);
  grid-template-columns: repeat(4, minmax(0, 1fr));
  list-style: none;
}

.config-stage-rail li {
  display: flex;
  min-width: 0;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-inline-end: var(--border-width) solid var(--color-border-subtle);
  color: var(--color-text-secondary);
}

.config-stage-rail li:last-child { border-inline-end: 0; }
.config-stage-rail__item--active { background: var(--color-accent-subtle); }

.config-stage-rail__number {
  display: grid;
  width: var(--space-6);
  height: var(--space-6);
  flex: none;
  place-items: center;
  border: var(--border-width) solid var(--color-border-strong);
  border-radius: var(--radius-tag);
  font-size: var(--font-size-caption);
}

.config-stage-rail strong,
.config-stage-rail small { display: block; }
.config-stage-rail strong { color: var(--color-text-primary); font-size: var(--font-size-dense); }
.config-stage-rail small { margin-block-start: var(--space-1); font-size: var(--font-size-caption); }
</style>
