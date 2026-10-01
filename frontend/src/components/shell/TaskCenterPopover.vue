<script setup lang="ts">
import { computed, ref } from 'vue'
import { ListTodo } from '@lucide/vue'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import { TERMINAL_JOB_STATUSES, type JobStatus } from '../../api/jobs'
import { useJobStore } from '../../stores/jobs'
import AppButton from '../design-system/AppButton.vue'

const jobs = useJobStore()
const open = ref(false)
const refreshing = ref(false)
const cancelling = ref<number | null>(null)
const entries = computed(() => Object.values(jobs.jobs).sort((a, b) => b.id - a.id))
const activeCount = computed(() => entries.value.filter(job => !TERMINAL_JOB_STATUSES.has(job.status)).length)
const labels: Record<JobStatus, string> = { queued: '等待中', running: '进行中', paused: '已暂停', succeeded: '已完成', failed: '失败', cancelled: '已取消' }
const names: Record<string, string> = { ops_backup: '备份', ops_restore_prepare: '准备恢复', scan_analysis: '扫描预检', grading: '批改', question_bank_sync: '题库同步', config_generate: '生成评分标准', class_analysis: '班级分析', individual_report: '个人报告' }
async function refresh() {
  refreshing.value = true
  try { await jobs.initialize(); await Promise.all(entries.value.map(job => jobs.refresh(job.id))) }
  finally { refreshing.value = false }
}
async function cancel(id: number) {
  cancelling.value = id
  try { await jobs.cancel(id) } finally { cancelling.value = null }
}
</script>
<template>
  <PopoverRoot v-model:open="open" @update:open="value => { if (value) void refresh() }">
    <PopoverTrigger as-child>
      <button type="button" class="app-sidebar__link task-center-trigger" aria-label="任务中心"><ListTodo :size="18" :stroke-width="1.8" aria-hidden="true" /><span>任务中心<span v-if="activeCount" class="task-center-count">{{ activeCount }}</span></span></button>
    </PopoverTrigger>
    <PopoverPortal>
      <PopoverContent class="task-center-popover fx-popover" side="right" align="end" :side-offset="12" :collision-padding="12" aria-label="任务中心">
        <header><strong>任务中心</strong><AppButton variant="ghost" :disabled="refreshing" @click="refresh">{{ refreshing ? '正在刷新…' : '刷新' }}</AppButton></header>
        <p v-if="!entries.length" class="task-center-empty">当前没有任务</p>
        <ul v-else>
          <li v-for="job in entries" :key="job.id">
            <div class="task-center-line"><strong>{{ names[job.job_type] ?? '后台任务' }}</strong><span :class="{ 'is-error': job.status === 'failed' }">{{ job.cancel_requested && !TERMINAL_JOB_STATUSES.has(job.status) ? '正在取消…' : labels[job.status] }}</span></div>
            <p v-if="job.detail">{{ job.detail }}</p>
            <div v-if="!TERMINAL_JOB_STATUSES.has(job.status)" class="task-center-line"><span>{{ Math.round(job.progress * 100) }}%</span><AppButton variant="ghost" :disabled="job.cancel_requested || cancelling === job.id" @click="cancel(job.id)">取消任务</AppButton></div>
            <p v-if="jobs.syncErrors[job.id]" class="is-error">{{ jobs.syncErrors[job.id]?.message }}</p>
          </li>
        </ul>
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
</template>
<style>
.task-center-trigger.app-sidebar__link { background: transparent; color: var(--sidebar-ink-2); text-align: left; }
.task-center-trigger.app-sidebar__link:hover, .task-center-trigger.app-sidebar__link[aria-expanded=true] { background: var(--sidebar-hover); color: var(--sidebar-ink-1); }
.task-center-count { margin-left: 8px; font-size: 11px; color: var(--sidebar-status-warning); }
.task-center-popover { width: 340px; max-width: calc(100vw - 24px); max-height: min(480px, 80vh); overflow: auto; z-index: 70; border: 1px solid var(--color-border-default); border-radius: var(--radius-panel); background: var(--color-bg-surface); color: var(--color-text-primary); box-shadow: var(--shadow-overlay); padding: 10px 14px; font-size: 12.5px; }
.task-center-popover header, .task-center-line { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.task-center-popover strong { font-weight: 600; }
.task-center-popover ul { padding: 0; margin: 6px 0 0; list-style: none; }
.task-center-popover li { padding: 10px 0; border-top: 1px solid var(--color-border-subtle); }
.task-center-popover p { color: var(--color-text-muted); overflow-wrap: anywhere; margin: 5px 0; }
.task-center-popover .task-center-empty { padding: 20px 0; text-align: center; }
.task-center-popover .is-error { color: var(--color-danger); }
.task-center-popover .app-button { --app-control-height: 28px; --app-control-font-size: 12px; --app-control-padding-x: 8px; }
</style>
