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
</script>

<template>
  <ol
    class="config-stage-rail"
    aria-label="考试配置阶段"
    :style="{ '--stage-index': activeIndex }"
  >
    <li
      v-for="(stage, index) in stages"
      :key="stage.id"
      :class="{ 'config-stage-rail__item--active': currentStage === stage.id }"
    >
      <button
        type="button"
        :disabled="!stage.available"
        :aria-current="currentStage === stage.id ? 'step' : undefined"
        @click="emit('select', stage.id)"
      >
        <span class="config-stage-rail__number">{{ index + 1 }}</span>
        <span>
          <strong>{{ stage.label }}</strong>
          <small>{{ stage.fact }}</small>
        </span>
      </button>
    </li>
  </ol>
</template>

<style scoped>
.config-stage-rail {
  position: relative;
  display: grid;
  overflow: hidden;
  margin: 0;
  padding: 0;
  border-block: var(--border-width) solid var(--border);
  grid-template-columns: repeat(5, minmax(0, 1fr));
  list-style: none;
}

.config-stage-rail::after {
  position: absolute;
  z-index: 2;
  inset-block-end: 0;
  inset-inline-start: 0;
  width: 20%;
  height: 3px;
  background: var(--color-accent);
  content: "";
  pointer-events: none;
  transform: translateX(calc(var(--stage-index) * 100%));
  transition: transform 220ms cubic-bezier(.2, .75, .25, 1);
}

.config-stage-rail li {
  min-width: 0;
  border-inline-end: var(--border-width) solid var(--color-border-subtle);
}

.config-stage-rail button {
  display: flex;
  width: 100%;
  height: 100%;
  min-width: 0;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border: 0;
  border-block-end: 3px solid transparent;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  text-align: start;
  transition: background-color 160ms ease, border-color 160ms ease, color 160ms ease;
}

.config-stage-rail li:last-child { border-inline-end: 0; }
.config-stage-rail__item--active button {
  background: var(--color-accent-subtle);
}
.config-stage-rail button:hover:not(:disabled) { background: var(--secondary); }
.config-stage-rail button:focus-visible { outline: 2px solid var(--color-accent); outline-offset: -2px; }
.config-stage-rail button:disabled { cursor: not-allowed; opacity: .48; }

.config-stage-rail__number {
  display: grid;
  width: var(--space-6);
  height: var(--space-6);
  flex: none;
  place-items: center;
  border: var(--border-width) solid var(--color-border-strong);
  border-radius: var(--radius-circle);
  font-size: var(--font-size-caption);
}

.config-stage-rail strong,
.config-stage-rail small { display: block; }
.config-stage-rail strong { color: var(--color-text-primary); font-size: var(--font-size-dense); }
.config-stage-rail small { margin-block-start: var(--space-1); font-size: var(--font-size-caption); }

@media (prefers-reduced-motion: reduce) {
  .config-stage-rail button { transition: none; }
  .config-stage-rail::after { transition: none; }
}
</style>
