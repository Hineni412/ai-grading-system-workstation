<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Check, CircleAlert, X } from '@lucide/vue'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import { TERMINAL_JOB_STATUSES, type JobResponse } from '../../api/jobs'
import { questionBankApi } from '../../api/question-bank'
import { exportsApi } from '../../api/exports'
import { useJobStore } from '../../stores/jobs'
import { useSessionStore } from '../../stores/session'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import { needsAttention, taskDetail, taskName, taskOutcome, taskScope, taskSessionId, taskTarget } from './task-center-format'

const jobs = useJobStore()
const sessions = useSessionStore()
const route = useRoute()
const router = useRouter()
const open = ref(false)
const refreshing = ref(false)
const cancelling = ref<number | null>(null)
const downloading = ref<number | null>(null)
const downloaded = ref(new Set<number>())
const downloadError = ref('')
const navigationError = ref<{ id: number; message: string } | null>(null)
const cards = ref<number[]>([])
const announcement = ref('')
const now = ref(Date.now())
const dockBottom = ref(20)
let announcementTimer: ReturnType<typeof setTimeout> | undefined
let clockTimer: ReturnType<typeof setInterval> | undefined
let expiryTimer: ReturnType<typeof setInterval> | undefined
let placementFrame: number | undefined
let placementObserver: MutationObserver | undefined

const entries = computed(() => Object.values(jobs.jobs)
  .filter(job => jobs.references.has(job.id) && !jobs.references.get(job.id)?.dismissedAt)
  .sort((a, b) => b.id - a.id))
const active = computed(() => entries.value.filter(job => !TERMINAL_JOB_STATUSES.has(job.status)))
const attention = computed(() => entries.value.filter(needsAttention))
const completed = computed(() => entries.value.filter(job => TERMINAL_JOB_STATUSES.has(job.status) && !needsAttention(job))
  .sort((a, b) => Date.parse(b.finished_at ?? b.updated_at) - Date.parse(a.finished_at ?? a.updated_at) || b.id - a.id))
const groups = computed(() => [
  { title: '进行中', items: active.value },
  { title: '需要处理', items: attention.value },
  { title: '已结束', items: completed.value },
])
const unread = computed(() => entries.value.filter(job => TERMINAL_JOB_STATUSES.has(job.status) && !jobs.references.get(job.id)?.readAt))
const averageProgress = computed(() => active.value.length
  ? active.value.reduce((sum, job) => sum + Math.min(1, Math.max(0, job.progress)), 0) / active.value.length : 1)
const ringTone = computed(() => active.value.length ? 'active'
  : unread.value.some(needsAttention) ? 'warning' : unread.value.length ? 'success' : 'neutral')
const ringLabel = computed(() => `任务中心，${active.value.length} 个任务进行中${active.value.length ? `，平均 ${Math.round(averageProgress.value * 100)}%` : ''}；${unread.value.length} 项结果待查看`)
const cardJobs = computed(() => cards.value.map(id => jobs.jobs[id]).filter((job): job is JobResponse => !!job && !jobs.references.get(job.id)?.dismissedAt))
const examNames = computed(() => new Map(sessions.sessions.map(session => [session.id, session.name])))
const paperLabels = ref<Record<number, string>>({})
const paperIds = ref<Record<number, number | undefined>>({})
const contextVersions = new Map<number, string>()
const contextRequests = new Map<number, { version: string; controller: AbortController; promise: Promise<void> }>()
const paperTaskTypes = new Set(['question_import', 'tagging_sync', 'question_bank_repair', 'criterion_backfill', 'knowledge_link', 'answer_draft', 'taxonomy_suggestion', 'chapter_type_organize'])

function durationMs(job: JobResponse): number {
  return Math.max(0, (Date.parse(job.finished_at ?? '') || now.value) - Date.parse(job.started_at ?? job.created_at))
}
function elapsed(job: JobResponse): string {
  const seconds = Math.max(0, Math.floor(durationMs(job) / 1000))
  return `已用时 ${Math.floor(seconds / 60)} 分 ${String(seconds % 60).padStart(2, '0')} 秒`
}
function startedAt(job: JobResponse): string {
  return new Date(job.started_at ?? job.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
}
function scope(job: JobResponse): string {
  return taskScope(job, examNames.value, paperLabels.value[job.id] ?? (paperTaskTypes.has(job.job_type) ? '正在读取所属试卷…' : undefined))
}
function expireCompleted(): void {
  const expired = entries.value.filter(job => {
    const readAt = jobs.references.get(job.id)?.readAt
    return TERMINAL_JOB_STATUSES.has(job.status) && !!readAt
      && Date.now() - Date.parse(readAt) >= 600_000
  })
  jobs.dismiss([...new Set([...expired, ...completed.value.slice(5)].map(job => job.id))])
}
function markVisibleRead(): void {
  for (const job of completed.value) jobs.markRead(job.id)
}
function onSourcePage(job: JobResponse): boolean {
  const target = taskTarget(job)
  const sessionId = taskSessionId(job)
  return !!target && route.name === target.source
    && (sessionId === null || sessionId === sessions.selectedSessionId)
}
function hideCard(id: number, read = false): void {
  cards.value = cards.value.filter(value => value !== id)
  const job = jobs.jobs[id]
  if (read && job && !needsAttention(job)) jobs.markRead(id)
}

watch(() => jobs.completions.length, () => {
  for (const event of jobs.completions.splice(0)) {
    const job = jobs.jobs[event.id]
    if (!job || !event.live || jobs.references.get(job.id)?.dismissedAt) continue
    if (open.value || onSourcePage(job)) {
      if (!needsAttention(job)) jobs.markRead(job.id)
      continue
    }
    if (job.status === 'cancelled' || durationMs(job) <= 5000) continue
    cards.value = [...cards.value.filter(id => id !== job.id), job.id].slice(-3)
  }
  expireCompleted()
}, { immediate: true })

function showNextAnnouncement(): void {
  const id = jobs.announcements.shift()
  const job = id === undefined ? undefined : jobs.jobs[id]
  if (!job) { announcement.value = ''; announcementTimer = undefined; return }
  announcement.value = `已在后台开始：${taskName(job)}，可以去做别的事`
  announcementTimer = setTimeout(() => { announcement.value = ''; showNextAnnouncement() }, 3000)
}
watch(() => jobs.announcements.length, () => {
  if (announcementTimer === undefined) showNextAnnouncement()
}, { immediate: true })

async function loadContexts(force = false): Promise<void> {
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
        paperIds.value[job.id] = context.papers.length === 1 ? context.papers[0]?.id : undefined
        contextVersions.set(job.id, version)
      } catch {
        if (!controller.signal.aborted) {
          paperLabels.value[job.id] = '所属试卷暂时无法读取'
          paperIds.value[job.id] = undefined
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
  if (open.value && !refreshing.value) { markVisibleRead(); void loadContexts() }
})
async function refresh(): Promise<void> {
  refreshing.value = true
  try {
    await jobs.initialize()
    await Promise.all(entries.value.map(job => jobs.refresh(job.id)))
    expireCompleted()
    markVisibleRead()
    await loadContexts(true)
  } finally { refreshing.value = false }
}
function setOpen(value: boolean): void {
  if (!value) return
  navigationError.value = null
  cards.value = []
  expireCompleted()
  markVisibleRead()
  void refresh()
}
async function viewJob(job: JobResponse): Promise<void> {
  navigationError.value = null
  if (paperTaskTypes.has(job.job_type)) await loadContexts()
  const target = taskTarget(job, paperIds.value[job.id])
  if (!target) return
  try {
    if (target.sessionId !== null) {
      sessions.selectSession(target.sessionId)
      await nextTick()
      if (sessions.selectedSessionId !== target.sessionId) {
        navigationError.value = { id: job.id, message: '当前考试有未保存的修改，请先保存后再查看' }
        return
      }
    }
    const failure = await router.push(target.route)
    if (failure && router.currentRoute.value.fullPath !== router.resolve(target.route).fullPath) return
    jobs.markRead(job.id)
    hideCard(job.id)
    open.value = false
  } catch {
    navigationError.value = { id: job.id, message: '无法打开所属页面，请检查考试是否仍在列表中。' }
  }
}
async function cancel(id: number): Promise<void> {
  cancelling.value = id
  try { await jobs.cancel(id) } finally { cancelling.value = null }
}
async function downloadPersonalReport(id: number): Promise<void> {
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
function updatePlacement(): void {
  placementFrame = undefined
  let bottom = 20
  for (const element of document.querySelectorAll<HTMLElement>('.review-batch-actions, .review-scoring-inspector__footer, .review-feedback-toast, .config-editor__save-bar')) {
    const rect = element.getBoundingClientRect()
    if (rect.height && rect.right > window.innerWidth - 380 && rect.top < window.innerHeight && rect.bottom > window.innerHeight - 240) {
      bottom = Math.max(bottom, window.innerHeight - rect.top + 12)
    }
  }
  dockBottom.value = bottom
}
function schedulePlacement(): void {
  if (placementFrame === undefined) placementFrame = requestAnimationFrame(updatePlacement)
}
onMounted(() => {
  expireCompleted()
  clockTimer = setInterval(() => { now.value = Date.now() }, 1000)
  expiryTimer = setInterval(expireCompleted, 30_000)
  placementObserver = new MutationObserver(schedulePlacement)
  placementObserver.observe(document.body, { childList: true, subtree: true })
  window.addEventListener('resize', schedulePlacement)
  window.addEventListener('scroll', schedulePlacement, true)
  schedulePlacement()
})
onBeforeUnmount(() => {
  clearTimeout(announcementTimer)
  clearInterval(clockTimer)
  clearInterval(expiryTimer)
  if (placementFrame !== undefined) cancelAnimationFrame(placementFrame)
  placementObserver?.disconnect()
  window.removeEventListener('resize', schedulePlacement)
  window.removeEventListener('scroll', schedulePlacement, true)
  for (const request of contextRequests.values()) request.controller.abort()
})
</script>

<template>
  <Teleport to="body">
    <div class="task-center-dock" :style="{ '--task-center-bottom': `${dockBottom}px` }">
      <PopoverRoot v-model:open="open" @update:open="setOpen">
        <PopoverTrigger v-if="entries.length || open" as-child>
          <button type="button" class="task-center-ring" :class="`is-${ringTone}`" :aria-label="ringLabel" :title="ringLabel">
            <span class="task-center-ring__graphic" aria-hidden="true">
              <svg width="20" height="20" viewBox="0 0 20 20">
                <circle class="task-center-ring__track" cx="10" cy="10" r="8" />
                <circle class="task-center-ring__value" cx="10" cy="10" r="8" :stroke-dasharray="`${averageProgress * 50.265} 50.265`" />
              </svg>
              <CircleAlert v-if="ringTone === 'warning'" :size="11" />
              <Check v-else-if="ringTone === 'success'" :size="11" />
            </span>
            <span class="task-center-ring__hint" aria-hidden="true">{{ ringLabel.replace('任务中心，', '') }}</span>
            <span v-if="unread.length" class="task-center-ring__badge" aria-hidden="true">{{ unread.length }}</span>
          </button>
        </PopoverTrigger>
        <PopoverPortal>
          <PopoverContent class="task-center-popover fx-popover" side="top" align="end" :side-offset="12" :collision-padding="12" aria-label="任务中心">
            <header class="task-center-line">
              <strong>任务中心</strong>
              <AppButton variant="ghost" :disabled="!completed.length && !attention.length" @click="jobs.clearCompleted()">清除已结束</AppButton>
            </header>
            <StatePanel v-if="!entries.length" kind="empty" compact title="当前没有任务" />
            <p v-if="refreshing" class="task-center-muted" role="status">正在刷新…</p>
            <p v-if="downloadError" class="is-error" role="alert">{{ downloadError }}</p>
            <template v-for="group in groups" :key="group.title">
              <section v-if="group.items.length" class="task-center-group" :aria-label="group.title">
                <h3>{{ group.title }} <span>{{ group.items.length }}</span></h3>
                <ul>
                  <li v-for="job in group.items" :key="job.id" :data-job-id="job.id">
                    <div class="task-center-line"><strong>{{ taskName(job) }}</strong><span class="task-center-status" :class="`is-${taskOutcome(job).tone}`">{{ taskOutcome(job).label }}</span></div>
                    <p class="task-center-scope">{{ scope(job) }}</p>
                    <p>{{ taskDetail(job) }}</p>
                    <p class="task-center-meta">{{ elapsed(job) }} · 开始于 {{ startedAt(job) }}<span v-if="TERMINAL_JOB_STATUSES.has(job.status) && jobs.references.get(job.id)?.readAt"> · 已查看 · 稍后自动移出列表</span></p>
                    <div v-if="!TERMINAL_JOB_STATUSES.has(job.status)" class="task-center-line task-center-progress">
                      <progress :value="job.progress" max="1" :aria-label="`${taskName(job)}进度`" />
                      <span>{{ Math.round(job.progress * 100) }}%</span>
                      <AppButton variant="ghost" :disabled="job.cancel_requested || cancelling === job.id" @click="cancel(job.id)">取消任务</AppButton>
                    </div>
                    <div v-else class="task-center-actions">
                      <AppButton v-if="taskTarget(job)" variant="secondary" @click="viewJob(job)">去查看</AppButton>
                      <AppButton v-if="job.job_type === 'personal_report_bundle' && job.status === 'succeeded' && typeof job.result.download_url === 'string' && !downloaded.has(job.id)"
                        variant="secondary" :disabled="downloading !== null" @click="downloadPersonalReport(job.id)">{{ downloading === job.id ? '正在下载…' : '下载个人报告' }}</AppButton>
                    </div>
                    <p v-if="navigationError?.id === job.id" class="is-error" role="alert">{{ navigationError.message }}</p>
                    <p v-if="jobs.syncErrors[job.id]" class="is-error" role="alert">{{ jobs.syncErrors[job.id]?.message }}</p>
                  </li>
                </ul>
              </section>
            </template>
            <p class="task-center-footnote">已查看的完成任务会在 10 分钟后自动移出列表，不影响已保存的结果。</p>
          </PopoverContent>
        </PopoverPortal>
      </PopoverRoot>
      <div class="task-center-cards" aria-live="polite" aria-relevant="additions">
        <article v-for="job in cardJobs" :key="job.id" class="task-center-card" :data-job-id="job.id" :class="`is-${taskOutcome(job).tone}`">
          <div class="task-center-line">
            <strong>{{ taskName(job) }}</strong>
            <span class="task-center-status" :class="`is-${taskOutcome(job).tone}`">{{ taskOutcome(job).label }}</span>
            <button type="button" class="task-center-card__close" :aria-label="`关闭${taskName(job)}结果提醒`" @click="hideCard(job.id, true)"><X :size="16" aria-hidden="true" /></button>
          </div>
          <p>{{ taskOutcome(job).note || taskDetail(job) }}</p>
          <div class="task-center-line">
            <span class="task-center-muted">{{ elapsed(job) }}</span>
            <AppButton v-if="taskTarget(job)" variant="secondary" @click="viewJob(job)">去查看</AppButton>
          </div>
          <p v-if="navigationError?.id === job.id" class="is-error" role="alert">{{ navigationError.message }}</p>
          <div class="task-center-card__countdown" aria-hidden="true"><i @animationend="hideCard(job.id)" /></div>
        </article>
      </div>
      <div class="task-center-announcement" role="status" aria-live="polite" aria-atomic="true"><span v-if="announcement">{{ announcement }}</span></div>
    </div>
  </Teleport>
</template>

<style>
.task-center-dock { position: fixed; right: 20px; bottom: var(--task-center-bottom); z-index: 300; }
.task-center-ring { position: relative; box-sizing: border-box; display: inline-flex; flex-direction: row-reverse; align-items: center; min-width: 40px; max-width: 40px; height: 40px; padding: 9px; border: 1px solid var(--color-border-default); border-radius: 999px; background: var(--color-bg-surface); box-shadow: var(--shadow-overlay); color: var(--color-text-primary); font-size: var(--font-size-caption); white-space: nowrap; transition: max-width 220ms ease; cursor: pointer; }
.task-center-ring:hover, .task-center-ring:focus-visible { max-width: calc(100vw - 40px); border-color: var(--color-border-strong); }
.task-center-ring:focus-visible { outline: 2px solid var(--color-accent); outline-offset: 3px; }
.task-center-ring__hint { max-width: 0; opacity: 0; overflow: hidden; text-overflow: ellipsis; transition: max-width 220ms ease, opacity 160ms ease, margin-right 220ms ease; }
.task-center-ring:hover .task-center-ring__hint, .task-center-ring:focus-visible .task-center-ring__hint { max-width: min(360px, calc(100vw - 88px)); margin-right: 8px; opacity: 1; }
.task-center-ring__graphic { position: relative; display: block; width: 20px; height: 20px; flex-shrink: 0; }
.task-center-ring__graphic > svg:first-child { display: block; transform: rotate(-90deg); }
.task-center-ring__graphic > svg:not(:first-child) { position: absolute; top: 4.5px; left: 4.5px; }
.task-center-ring circle { fill: none; stroke-width: 2.5px; }
.task-center-ring__track { stroke: var(--color-border-subtle); }
.task-center-ring__value { stroke: var(--color-text-muted); stroke-linecap: round; transition: stroke-dasharray 200ms linear; }
.task-center-ring.is-active .task-center-ring__value { stroke: var(--color-accent); }
.task-center-ring.is-success { color: var(--color-success); }
.task-center-ring.is-success .task-center-ring__value { stroke: var(--color-success); }
.task-center-ring.is-warning { color: var(--color-warning); }
.task-center-ring.is-warning .task-center-ring__value { stroke: var(--color-warning); }
.task-center-ring__badge { position: absolute; top: -4px; right: -4px; min-width: 16px; height: 16px; padding: 0 4px; border-radius: 999px; background: var(--color-danger); color: var(--color-bg-surface); font-size: 11px; line-height: 16px; text-align: center; }
.task-center-popover { box-sizing: border-box; width: 360px; max-width: calc(100vw - 24px); max-height: min(80vh, var(--reka-popover-content-available-height)); overflow: auto; z-index: 310; padding: 10px 14px; border: 1px solid var(--color-border-default); border-radius: var(--radius-panel); background: var(--color-bg-surface); color: var(--color-text-primary); box-shadow: var(--shadow-overlay); font-size: var(--font-size-caption); }
.task-center-popover > header { padding-bottom: 6px; border-bottom: 1px solid var(--color-border-subtle); }
.task-center-line { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.task-center-line > strong { flex: 1; min-width: 0; overflow-wrap: anywhere; font-weight: var(--font-weight-semibold); }
.task-center-group h3 { margin: 12px 0 4px; color: var(--color-text-muted); font-size: var(--font-size-caption); font-weight: var(--font-weight-medium); }
.task-center-group h3 span { margin-left: 4px; font-family: var(--font-family-mono); }
.task-center-group ul { padding: 0; margin: 0; list-style: none; }
.task-center-group li { padding: 10px 0; border-bottom: 1px solid var(--color-border-subtle); }
.task-center-group li:last-child { border-bottom: 0; }
.task-center-popover p, .task-center-card p { margin: 5px 0; color: var(--color-text-muted); overflow-wrap: anywhere; }
.task-center-popover .task-center-scope { color: var(--color-text-secondary); }
.task-center-meta { font-size: 12px; line-height: var(--line-height-relaxed); }
.task-center-muted { color: var(--color-text-muted); }
.task-center-status { flex-shrink: 0; font-size: 12px; }
.task-center-popover .is-error, .task-center-card .is-error { color: var(--color-danger); }
.task-center-popover .is-warning, .task-center-card .is-warning { color: var(--color-warning); }
.task-center-popover .is-success, .task-center-card .is-success { color: var(--color-success); }
.task-center-actions { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
.task-center-progress { margin-top: 6px; }
.task-center-progress progress { flex: 1; min-width: 0; height: 5px; border: 0; border-radius: 999px; overflow: hidden; accent-color: var(--color-accent); }
.task-center-progress progress::-webkit-progress-bar { background: var(--color-bg-subtle); }
.task-center-progress progress::-webkit-progress-value { background: var(--color-accent); }
.task-center-progress progress::-moz-progress-bar { background: var(--color-accent); }
.task-center-popover .app-button, .task-center-card .app-button { --app-control-height: var(--control-height-small); --app-control-font-size: var(--font-size-caption); --app-control-padding-x: 8px; }
.task-center-footnote { padding-top: 8px; border-top: 1px solid var(--color-border-subtle); font-size: 12px; line-height: var(--line-height-relaxed); }
.task-center-cards { position: fixed; right: 20px; bottom: calc(var(--task-center-bottom) + 52px); display: flex; flex-direction: column-reverse; gap: 10px; width: min(340px, calc(100vw - 40px)); max-height: calc(100vh - var(--task-center-bottom) - 64px); overflow: auto; pointer-events: none; }
.task-center-card { --task-center-tone: var(--color-info); position: relative; padding: 12px 14px 16px; border: 1px solid var(--color-border-default); border-radius: var(--radius-panel); background: var(--color-bg-surface); color: var(--color-text-primary); box-shadow: var(--shadow-overlay); font-size: var(--font-size-caption); pointer-events: auto; animation: task-center-card-in 200ms ease-out; }
.task-center-card.is-success { --task-center-tone: var(--color-success); }
.task-center-card.is-warning { --task-center-tone: var(--color-warning); }
.task-center-card.is-error { --task-center-tone: var(--color-danger); }
.task-center-card > .task-center-line:last-of-type { margin-top: 8px; }
.task-center-card__close { display: grid; place-items: center; flex-shrink: 0; width: 24px; height: 24px; padding: 0; border: 0; border-radius: var(--radius-tag); background: transparent; color: var(--color-text-muted); cursor: pointer; }
.task-center-card__close:hover { background: var(--color-bg-subtle); color: var(--color-text-primary); }
.task-center-card__countdown { position: absolute; left: 0; right: 0; bottom: 0; height: 4px; border-radius: 0 0 var(--radius-panel) var(--radius-panel); overflow: hidden; background: var(--color-bg-subtle); }
.task-center-card__countdown > i { display: block; width: 100%; height: 100%; background: var(--task-center-tone); animation: task-center-countdown 6s linear forwards; }
.task-center-card:hover .task-center-card__countdown > i, .task-center-card:focus-within .task-center-card__countdown > i { animation-play-state: paused; }
.task-center-announcement { position: fixed; left: 50%; bottom: 20px; transform: translateX(-50%); width: max-content; max-width: calc(100vw - 120px); font-size: var(--font-size-caption); pointer-events: none; }
.task-center-announcement span { display: block; padding: 10px 16px; border-radius: var(--radius-control); background: var(--color-text-primary); color: var(--color-bg-surface); box-shadow: var(--shadow-overlay); }
@keyframes task-center-countdown { from { width: 100%; } to { width: 0; } }
@keyframes task-center-card-in { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
</style>
