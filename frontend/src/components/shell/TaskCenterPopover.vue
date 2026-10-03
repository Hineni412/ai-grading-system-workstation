<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ListTodo } from '@lucide/vue'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import { TERMINAL_JOB_STATUSES } from '../../api/jobs'
import { questionBankApi } from '../../api/question-bank'
import { exportsApi } from '../../api/exports'
import { useJobStore } from '../../stores/jobs'
import { useSessionStore } from '../../stores/session'
import AppButton from '../design-system/AppButton.vue'
import { taskDetail, taskName, taskOutcome, taskScope } from './task-center-format'

const jobs = useJobStore()
const sessions = useSessionStore()
const open = ref(false)
const refreshing = ref(false)
const cancelling = ref<number | null>(null)
const downloading = ref<number | null>(null)
const downloaded = ref(new Set<number>())
const downloadError = ref('')
async function downloadPersonalReport(id: number) {
  if (downloading.value !== null) return
  downloading.value = id
  downloadError.value = ''
  try {
    const file = await exportsApi.downloadJobFile(id)
    const url = URL.createObjectURL(file.blob)
    const link = document.createElement('a')
    link.href = url
    link.download = file.filename
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    downloaded.value = new Set([...downloaded.value, id])
  } catch { downloadError.value = '下载未完成，文件可能已经下载或过期。需要时可重新导出。' }
  finally { downloading.value = null }
}
const entries = computed(() => Object.values(jobs.jobs).sort((a, b) => b.id - a.id))
const activeCount = computed(() => entries.value.filter(job => !TERMINAL_JOB_STATUSES.has(job.status)).length)
const examNames = computed(() => new Map(sessions.sessions.map(session => [session.id, session.name])))
const paperLabels = ref<Record<number, string>>({})
const contextVersions = new Map<number, string>()
const contextRequests = new Map<number, { version: string; controller: AbortController; promise: Promise<void> }>()
const paperTaskTypes = new Set(['question_import', 'tagging_sync', 'question_bank_repair', 'criterion_backfill', 'knowledge_link', 'answer_draft'])
async function loadContexts(force = false) {
  await Promise.all(entries.value.filter(job => paperTaskTypes.has(job.job_type)).map(async job => {
    const version = `${job.status}:${JSON.stringify(job.payload.question_ids ?? [])}`
    const pending = contextRequests.get(job.id)
    if (pending?.version === version) return pending.promise
    if (!force && contextVersions.get(job.id) === version) return
    pending?.controller.abort()
    const controller = new AbortController()
    const promise = (async () => {
      try {
        const context = await questionBankApi.taskContext(job.id, controller.signal)
        if (controller.signal.aborted) return
        const titles = context.papers.slice(0, 2).map(paper => paper.title?.trim() || `试卷 ${paper.id}`)
        paperLabels.value[job.id] = titles.length
          ? `试卷：${titles.join('、')}${context.papers.length > 2 ? ` 等 ${context.papers.length} 份` : ''}`
          : context.source_filename ? `导入文件：${context.source_filename}` : '所属试卷未记录或已移除'
        contextVersions.set(job.id, version)
      } catch {
        if (!controller.signal.aborted) {
          paperLabels.value[job.id] = '所属试卷暂时无法读取'
          contextVersions.delete(job.id)
        }
      } finally {
        if (contextRequests.get(job.id)?.controller === controller) contextRequests.delete(job.id)
      }
    })()
    contextRequests.set(job.id, { version, controller, promise })
    await promise
  }))
}
watch(() => open.value ? entries.value.map(job => `${job.id}:${job.status}`).join(',') : '', () => {
  if (open.value && !refreshing.value) void loadContexts()
})
onBeforeUnmount(() => { for (const request of contextRequests.values()) request.controller.abort() })
async function refresh() {
  refreshing.value = true
  try { await jobs.initialize(); await Promise.all(entries.value.map(job => jobs.refresh(job.id))); await loadContexts(true) }
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
        <p v-if="downloadError" role="alert">{{ downloadError }}</p>
        <ul v-if="entries.length">
          <li v-for="job in entries" :key="job.id" :data-job-id="job.id">
            <div class="task-center-line"><strong>{{ taskName(job) }}</strong><span class="task-center-status" :class="`is-${taskOutcome(job).tone}`">{{ taskOutcome(job).label }}</span></div>
            <p class="task-center-scope">{{ taskScope(job, examNames, paperLabels[job.id] ?? (paperTaskTypes.has(job.job_type) ? '正在读取所属试卷…' : undefined)) }}</p>
            <p>{{ taskDetail(job) }}</p>
            <AppButton v-if="job.job_type === 'personal_report_bundle' && job.status === 'succeeded' && typeof job.result.download_url === 'string' && !downloaded.has(job.id)"
              variant="secondary" :disabled="downloading !== null" @click="downloadPersonalReport(job.id)">{{ downloading === job.id ? '正在下载…' : '下载个人报告' }}</AppButton>
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
.task-center-popover .is-warning { color: var(--color-warning); }
.task-center-popover .is-success { color: var(--color-success); }
.task-center-popover .task-center-scope { color: var(--color-text-secondary); }
.task-center-status { flex-shrink: 0; }
.task-center-popover .app-button { --app-control-height: 28px; --app-control-font-size: 12px; --app-control-padding-x: 8px; }
</style>
