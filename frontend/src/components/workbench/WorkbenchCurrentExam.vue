<script setup lang="ts">
import { computed } from 'vue'
import AppButton from '../design-system/AppButton.vue'
import StepProgress, { type StepProgressStep } from '../design-system/StepProgress.vue'
import type { WorkbenchOverview } from '../../api/workbench'
import type { ResourceState } from '../../stores/workbench'
import type { TodoRow } from './workbench-home'
import { formatSessionDate } from '../../lib/session-date'
const props = defineProps<{
  overview: WorkbenchOverview | null; state: ResourceState; sessionId: number | null
  steps: StepProgressStep[]; current: string
  suggestions: TodoRow[]; reports: TodoRow | null; reportsStale: number; reportsCurrent: number
}>()
defineEmits<{ open: [path: string]; select: [step: string]; retry: [] }>()
const allDone = computed(() => props.steps.length > 0 && props.steps.every(step => step.status === 'done'))
const assembly = computed(() => props.suggestions.find(row => row.id === 'assembly'))
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
      <template v-if="allDone">
        <div class="workbench-done-line"><span class="workbench-ok-dot" aria-hidden="true">✓</span>已全部完成 · {{ overview.progress?.matched_papers ?? 0 }} 份答卷已批改</div>
        <details class="workbench-steps-more">
          <summary>查看步骤</summary>
          <StepProgress :steps="steps" :current="current" orientation="vertical" @select="$emit('select', $event)" />
        </details>
        <p v-if="reportsStale > 0" class="workbench-report-line" :title="`${reportsCurrent} 人已是最新`">
          个人报告 <b>{{ reportsStale }} 人</b>需重新生成 · <button type="button" class="workbench-link-btn" @click="$emit('open', reports?.path ?? '/results?tab=details')">去查看</button>
        </p>
        <div class="workbench-card-acts">
          <AppButton variant="primary" @click="$emit('open', '/results')">成绩中心</AppButton>
          <AppButton variant="secondary" @click="$emit('open', '/question-bank?tab=exam')">章节考情</AppButton>
          <AppButton v-if="assembly" variant="secondary" @click="$emit('open', assembly.path)">{{ assembly.title }}</AppButton>
          <AppButton variant="secondary" @click="$emit('open', `/teaching-research?session=${sessionId}`)">命题评价</AppButton>
        </div>
      </template>
      <template v-else>
        <StepProgress :steps="steps" :current="current" orientation="vertical" @select="$emit('select', $event)" />
        <p v-if="reportsStale > 0" class="workbench-report-line" :title="`${reportsCurrent} 人已是最新`">
          个人报告 <b>{{ reportsStale }} 人</b>需重新生成 · <button type="button" class="workbench-link-btn" @click="$emit('open', reports?.path ?? '/results?tab=details')">去查看</button>
        </p>
        <AppButton variant="ghost" class="workbench-footer-link" @click="$emit('open', '/results')">成绩中心 →</AppButton>
      </template>
    </template>
  </section>
</template>
