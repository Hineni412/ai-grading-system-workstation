<script setup lang="ts">
import { computed } from 'vue'

import StepProgress, { type StepProgressStep } from '../design-system/StepProgress.vue'
import type { ConfigPhase } from '../../stores/config-workspace'

type StageId = 'draft' | 'source' | 'generation' | 'editor' | 'template'

const props = defineProps<{
  phase: ConfigPhase
  activeStage?: StageId
  sessionReady: boolean
  sourceReady: boolean
  generationSubmitted: boolean
  editorReady: boolean
  templatePresent?: boolean
  templateReady?: boolean
}>()
const emit = defineEmits<{
  select: [stage: StageId]
}>()

// Completion comes only from real readiness flags; `currentStage` below only
// marks the step being viewed and never changes another step's status.
const steps = computed<StepProgressStep[]>(() => [
  {
    id: 'draft', label: '考试草稿',
    status: props.sessionReady ? 'done' : 'todo',
    available: true,
    hint: props.sessionReady ? '已创建' : '待创建',
  },
  {
    id: 'source', label: '上传与拆题',
    status: props.sourceReady ? 'done' : 'todo',
    available: props.sessionReady,
    hint: props.sourceReady ? '来源已读取' : '待上传',
  },
  {
    id: 'generation', label: '分析并入库',
    status: props.editorReady ? 'done' : props.generationSubmitted ? 'in_progress' : 'todo',
    available: props.sourceReady || props.generationSubmitted || props.editorReady,
    hint: props.editorReady ? '已入库并赋分'
      : props.generationSubmitted ? '任务已提交' : '待提交',
  },
  {
    id: 'editor', label: '本场赋分',
    status: props.editorReady ? 'done' : 'todo',
    available: props.editorReady,
    hint: props.editorReady ? '可检查分值' : '待入库成功',
  },
  {
    id: 'template', label: '样卷题框',
    status: props.templateReady ? 'done' : props.templatePresent ? 'in_progress' : 'todo',
    available: props.editorReady,
    hint: props.templateReady ? '已确认'
      : props.templatePresent ? '标定中'
        : props.editorReady ? '可开始' : '待本场赋分',
  },
])

const currentStage = computed<StageId>(() => props.activeStage ?? props.phase)

function selectStep(id: string) {
  emit('select', id as StageId)
}
</script>

<template>
  <StepProgress
    class="config-stage-rail"
    aria-label="考试配置阶段"
    :steps="steps"
    :current="currentStage"
    @select="selectStep"
  />
</template>
