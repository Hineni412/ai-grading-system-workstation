<script setup lang="ts">
import AppButton from '../design-system/AppButton.vue'
import StatusBadge from '../design-system/StatusBadge.vue'
import { taskName, taskDetail } from '../shell/task-center-format'
import type { JobResponse } from '../../api/jobs'
import type { TodoRow } from './workbench-home'
defineProps<{ need: TodoRow[]; jobs: JobResponse[]; loading: boolean; failures: string[] }>()
defineEmits<{ open: [path: string]; retry: [source: string] }>()
</script>
<template>
  <section class="workbench-panel workbench-todo" aria-labelledby="workbench-todo-title">
    <header class="workbench-panel-heading"><h2 id="workbench-todo-title">需要处理</h2><span>{{ need.length }} 项</span></header>
    <template v-if="jobs.length">
      <h3 class="workbench-group-title">正在运行</h3>
      <div v-for="job in jobs.slice(0, 3)" :key="job.id" class="workbench-todo-row workbench-running-row">
        <StatusBadge tone="neutral" label="任务" />
        <div class="workbench-todo-copy"><strong>{{ taskName(job) }}</strong><p>{{ taskDetail(job) }}</p></div>
        <span class="workbench-bar workbench-bar--running" aria-hidden="true"><i :style="{ width: `${Math.round(job.progress * 100)}%` }" /></span>
        <span class="workbench-numeric">{{ Math.round(job.progress * 100) }}%</span>
      </div>
      <p v-if="jobs.length > 3" class="workbench-meta">还有 {{ jobs.length - 3 }} 项，见任务中心</p>
    </template>
    <div v-for="(row, index) in need" :key="row.id" class="workbench-todo-row" :data-todo="row.id">
      <StatusBadge :tone="row.tone" :label="row.module" />
      <div class="workbench-todo-copy"><strong>{{ row.title }}</strong><p>{{ row.fact }}</p></div>
      <AppButton :variant="index === 0 ? 'primary' : 'secondary'" @click="$emit('open', row.path)">{{ row.action }}</AppButton>
    </div>
    <div v-if="loading" class="workbench-skeleton" role="status" aria-label="正在读取待处理事项" aria-busy="true"><span v-for="n in 3" :key="n" /></div>
    <p v-else-if="!jobs.length && !need.length" class="workbench-all-clear"><span class="workbench-ok-dot" aria-hidden="true">✓</span>当前没有需要处理的事项</p>
    <div v-for="source in failures" :key="source" class="workbench-source-error" role="status">
      <span>{{ source }}暂时无法读取</span><AppButton variant="ghost" @click="$emit('retry', source)">重试</AppButton>
    </div>
  </section>
</template>
