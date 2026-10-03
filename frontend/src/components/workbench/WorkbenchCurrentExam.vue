<script setup lang="ts">
import AppButton from '../design-system/AppButton.vue'
import StepProgress, { type StepProgressStep } from '../design-system/StepProgress.vue'
import type { WorkbenchOverview } from '../../api/workbench'
import type { ResourceState } from '../../stores/workbench'
import { formatSessionDate } from '../../lib/session-date'
defineProps<{ overview: WorkbenchOverview | null; state: ResourceState; sessionId: number | null; steps: StepProgressStep[]; current: string }>()
defineEmits<{ open: [path: string]; select: [step: string]; retry: [] }>()
</script>
<template>
  <section class="workbench-panel workbench-current" aria-labelledby="workbench-current-title">
    <header class="workbench-panel-heading"><h2 id="workbench-current-title">当前考试</h2></header>
    <div v-if="state === 'loading' && !overview" class="workbench-skeleton" role="status" aria-label="正在读取当前考试" aria-busy="true"><span v-for="n in 3" :key="n" /></div>
    <div v-else-if="state === 'error'" class="workbench-quiet" role="alert"><p>考试概况暂时无法读取</p><AppButton @click="$emit('retry')">重新加载</AppButton></div>
    <div v-else-if="sessionId === null" class="workbench-quiet">
      <p>尚未选择考试</p><AppButton variant="primary" @click="$emit('open', '/sessions')">新建考试</AppButton>
      <p class="workbench-meta">可在左侧“当前考试”中切换</p>
    </div>
    <template v-else-if="overview?.current_session">
      <p class="workbench-exam-name" :title="overview.current_session.name">{{ overview.current_session.name }}</p>
      <p class="workbench-meta">{{ formatSessionDate(overview.current_session.created_at) || '日期未知' }} · {{ overview.progress?.total_papers ?? '—' }} 份答卷</p>
      <StepProgress :steps="steps" :current="current" orientation="vertical" @select="$emit('select', $event)" />
      <AppButton variant="ghost" class="workbench-footer-link" @click="$emit('open', '/results')">成绩中心 →</AppButton>
    </template>
  </section>
</template>
