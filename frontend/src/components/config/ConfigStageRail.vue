<script setup lang="ts">
import { computed } from 'vue'

import type { ConfigPhase } from '../../stores/config-workspace'

const props = defineProps<{
  phase: ConfigPhase
  activeStage?: 'draft' | 'source' | 'generation' | 'editor' | 'template'
  sessionReady: boolean
  sourceReady: boolean
  generationSubmitted: boolean
  editorReady: boolean
  templatePresent?: boolean
  templateReady?: boolean
}>()
const emit = defineEmits<{
  select: [stage: 'draft' | 'source' | 'generation' | 'editor' | 'template']
}>()

const stages = computed(() => [
  { id: 'draft', label: '考试草稿', fact: props.sessionReady ? '已创建' : '待创建',
    available: true },
  { id: 'source', label: '上传与拆题', fact: props.sourceReady ? '来源已读取' : '待上传',
    available: props.sessionReady },
  { id: 'generation', label: '分析并入库', fact: props.editorReady ? '已入库并赋分' : props.generationSubmitted ? '任务已提交' : '待提交',
    available: props.sourceReady || props.generationSubmitted || props.editorReady },
  { id: 'editor', label: '本场赋分', fact: props.editorReady ? '可检查分值' : '待入库成功',
    available: props.editorReady },
  { id: 'template', label: '样卷题框', fact: props.templateReady ? '已确认'
    : props.templatePresent ? '标定中' : props.editorReady ? '可开始' : '待本场赋分',
    available: props.editorReady },
] as const)

const currentStage = computed(() => props.activeStage ?? props.phase)
const activeIndex = computed(() => Math.max(
  0,
  stages.value.findIndex((stage) => stage.id === currentStage.value),
))

function itemState(index: number): 'done' | 'current' | 'pending' {
  if (stages.value[index]!.id === currentStage.value) return 'current'
  return index < activeIndex.value ? 'done' : 'pending'
}
</script>

<template>
  <ol class="config-stage-rail" aria-label="考试配置阶段">
    <li
      v-for="(stage, index) in stages"
      :key="stage.id"
      :class="`config-stage-rail__item--${itemState(index)}`"
    >
      <button
        type="button"
        :disabled="!stage.available"
        :aria-current="currentStage === stage.id ? 'step' : undefined"
        :aria-description="stage.fact"
        :title="stage.fact"
        @click="emit('select', stage.id)"
      >
        <span class="config-stage-rail__mark" aria-hidden="true">
          <svg v-if="itemState(index) === 'done'" viewBox="0 0 16 16" width="12" height="12">
            <path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor"
              stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <template v-else>{{ index + 1 }}</template>
        </span>
        <span class="config-stage-rail__label">{{ stage.label }}</span>
      </button>
    </li>
  </ol>
</template>

<style scoped>
.config-stage-rail {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.config-stage-rail li {
  min-width: 0;
  display: flex;
}

.config-stage-rail__pill,
.config-stage-rail button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 30px;
  padding: 0 var(--space-3);
  border: var(--border-width) solid var(--color-border-default);
  border-radius: 999px;
  background: var(--color-bg-surface);
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
  white-space: nowrap;
  cursor: pointer;
  transition:
    background-color var(--duration-base) var(--ease-out),
    border-color var(--duration-base) var(--ease-out),
    color var(--duration-base) var(--ease-out);
}

.config-stage-rail button:hover:not(:disabled) {
  border-color: var(--color-border-strong);
  color: var(--color-text-primary);
}

.config-stage-rail button:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.config-stage-rail button:disabled {
  cursor: not-allowed;
  opacity: var(--opacity-disabled);
}

.config-stage-rail__mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: var(--radius-circle);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.config-stage-rail__item--done button {
  border-color: transparent;
  background: transparent;
  color: var(--color-text-muted);
}

.config-stage-rail__item--done .config-stage-rail__mark {
  color: var(--color-success);
}

.config-stage-rail__item--current button {
  border-color: var(--color-accent);
  background: var(--color-accent);
  color: var(--color-bg-surface);
  font-weight: var(--font-weight-medium);
}

.config-stage-rail__item--current button:hover:not(:disabled) {
  background: var(--color-accent-hover);
  color: var(--color-bg-surface);
}
</style>
