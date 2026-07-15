<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  progressValue: string | null
  progressStatus: string
  reviewValue: string | null
  reviewStatus: string
  anomalyValue: string | null
  anomalyStatus: string
  jobValue: string | null
  jobStatus: string
}>()

const emit = defineEmits<{
  openGrading: []
  openReview: []
  openAnomalies: []
  openJobs: []
}>()

const nodes = computed(() => [{
  label: '批改进度',
  value: props.progressValue,
  status: props.progressStatus,
  action: '查看批改',
  open: () => emit('openGrading'),
}, {
  label: '待复核',
  value: props.reviewValue,
  status: props.reviewStatus,
  action: '去复核',
  open: () => emit('openReview'),
}, {
  label: '异常记录',
  value: props.anomalyValue,
  status: props.anomalyStatus,
  action: '查看异常',
  open: () => emit('openAnomalies'),
}, {
  label: '最近任务',
  value: props.jobValue,
  status: props.jobStatus,
  action: '查看任务',
  open: () => emit('openJobs'),
}])
</script>

<template>
  <ol class="workbench-progress-rail" data-testid="progress-action-rail">
    <li v-for="node in nodes" :key="node.label" class="workbench-progress-rail__node">
      <span class="workbench-progress-rail__marker" aria-hidden="true" />
      <span class="workbench-progress-rail__label">{{ node.label }}</span>
      <strong
        class="workbench-progress-rail__value"
        :aria-label="`${node.label}：${node.value ?? '暂不可用'}`"
      >
        {{ node.value ?? '暂不可用' }}
      </strong>
      <span class="workbench-progress-rail__status">{{ node.status }}</span>
      <button type="button" class="workbench-link-button" @click="node.open">
        {{ node.action }}
      </button>
    </li>
  </ol>
</template>
