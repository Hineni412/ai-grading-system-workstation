<script setup lang="ts">
import AppButton from '../design-system/AppButton.vue'
import StatusBadge, { type StatusTone } from '../design-system/StatusBadge.vue'
import type { RecentSessionSummary } from '../../api/workbench'
import { formatSessionDate } from '../../lib/session-date'
import { sessionStatusLabel } from '../../lib/session-status'
defineProps<{ items: RecentSessionSummary[]; sessionId: number | null; reviewCount: number | null; loading: boolean; error: boolean }>()
defineEmits<{ select: [id: number, results: boolean]; create: [] }>()
function tone(status: string): StatusTone {
  return status === 'completed' ? 'success' : status === 'grading' ? 'info' : status === 'failed' ? 'danger' : 'neutral'
}
</script>
<template>
  <section class="workbench-panel workbench-recent" aria-labelledby="workbench-recent-title">
    <header class="workbench-panel-heading"><h2 id="workbench-recent-title">最近考试</h2><span>近 5 场</span></header>
    <div v-if="loading && !items.length" class="workbench-skeleton" role="status" aria-label="正在读取最近考试" aria-busy="true"><span v-for="n in 3" :key="n" /></div>
    <p v-else-if="error" class="workbench-quiet">考试概况暂时无法读取</p>
    <div v-else-if="!items.length" class="workbench-quiet"><p>还没有考试</p><AppButton @click="$emit('create')">新建考试</AppButton></div>
    <table v-else class="workbench-exams-table">
      <thead><tr><th scope="col">考试</th><th scope="col">创建日期</th><th scope="col">答卷</th><th scope="col">批改</th><th scope="col">状态</th><th scope="col"><span class="sr-only">操作</span></th></tr></thead>
      <tbody><tr v-for="item in items" :key="item.session.id" :class="{ 'is-current': item.session.id === sessionId }">
        <td class="workbench-exams-name"><button type="button" :title="item.session.name" @click="$emit('select', item.session.id, false)">{{ item.session.name }}</button><StatusBadge v-if="item.session.id === sessionId" tone="neutral" label="当前" /></td>
        <td>{{ formatSessionDate(item.session.created_at) || '日期未知' }}</td><td>{{ item.progress.total_papers }} 份</td>
        <td><span class="workbench-table-progress"><span class="workbench-bar" aria-hidden="true"><i :style="{ width: `${item.progress.matched_papers ? Math.min(100, item.progress.graded_papers / item.progress.matched_papers * 100) : 0}%` }" /></span><span>{{ item.progress.graded_papers }}/{{ item.progress.matched_papers }}</span></span></td>
        <td><div class="workbench-exams-status"><StatusBadge :tone="tone(item.session.status)" :label="sessionStatusLabel(item.session.status)" /><StatusBadge v-if="item.session.id === sessionId ? (reviewCount ?? 0) > 0 : item.progress.needs_human_review > 0" tone="warning" label="有待复核" /></div></td>
        <td><AppButton v-if="item.session.id !== sessionId" variant="ghost" @click="$emit('select', item.session.id, true)">看成绩</AppButton></td>
      </tr></tbody>
    </table>
  </section>
</template>
