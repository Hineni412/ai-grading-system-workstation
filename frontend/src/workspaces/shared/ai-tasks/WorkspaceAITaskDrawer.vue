<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Bell } from '@lucide/vue'

import { useWorkspaceAITaskStore } from './store'
import { useJobStore } from '../../../stores/jobs'
import { TERMINAL_JOB_STATUSES, type JobResponse } from '../../../api/jobs'
import StatusBadge from '@/components/design-system/StatusBadge.vue'
import AppButton from '@/components/design-system/AppButton.vue'
import { TimelineItem, type TimelineTone } from '@/components/ui/timeline'
import {
  canCancelTask,
  cancelTaskLabel,
  isArchivedJob,
  isArchivedTask,
  isAttentionJob,
  isAttentionTask,
  jobDetailLine,
  jobLocation,
  jobStatusLabel,
  jobTitle,
  returnLocation,
  isQuestionBankLibraryJob,
} from './taskPresentation'
import type { WorkspaceAITask } from './contracts'

const store = useWorkspaceAITaskStore()
const jobs = useJobStore()
const router = useRouter()
const open = ref(false)
const peekOpen = ref(false)
const peekTaskId = ref<string | null>(null)
const peekJobId = ref<number | null>(null)
const peekFading = ref(false)

const ordinaryJobs = computed(() => Object.values(jobs.jobs)
  .filter(job => !job.job_type.startsWith('workspace_ai.'))
  .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at)))

function isLiveTask(task: WorkspaceAITask): boolean {
  return ['prepared', 'queued', 'running'].includes(task.status)
}

function isLiveJob(job: JobResponse): boolean {
  return job.status === 'queued' || job.status === 'running'
}

const attentionTasks = computed(() => store.orderedTasks.filter(isAttentionTask))
const archivedTasks = computed(() => store.orderedTasks.filter(isArchivedTask))
const attentionJobs = computed(() => ordinaryJobs.value.filter(isAttentionJob))
const archivedJobs = computed(() => ordinaryJobs.value.filter(isArchivedJob))
type AttentionItem =
  | { kind: 'task'; task: WorkspaceAITask; live: boolean; updatedAt: number }
  | { kind: 'job'; job: JobResponse; live: boolean; updatedAt: number }
const attentionItems = computed(() => {
  const items: AttentionItem[] = [
    ...attentionTasks.value.map(task => ({
      kind: 'task' as const,
      task,
      live: isLiveTask(task),
      updatedAt: Date.parse(task.updated_at),
    })),
    ...attentionJobs.value.map(job => ({
      kind: 'job' as const,
      job,
      live: isLiveJob(job),
      updatedAt: Date.parse(job.updated_at),
    })),
  ]
  return items.sort((left, right) => {
    if (left.live !== right.live) return left.live ? -1 : 1
    return right.updatedAt - left.updatedAt
  })
})
const attentionCount = computed(() => attentionTasks.value.length + attentionJobs.value.length)
const archivedCount = computed(() => archivedTasks.value.length + archivedJobs.value.length)
const totalCount = computed(() => attentionCount.value + archivedCount.value)
const livePercent = computed(() => {
  const runningTask = attentionTasks.value.find(
    task => task.status === 'running' || task.status === 'queued' || task.status === 'prepared',
  )
  if (runningTask) {
    return Math.round(Math.max(0, Math.min(1, runningTask.progress)) * 100)
  }
  const runningJob = attentionJobs.value.find(
    job => (job.status === 'running' || job.status === 'queued') && !isQuestionBankLibraryJob(job),
  )
  if (!runningJob) return null
  return Math.round(Math.max(0, Math.min(1, runningJob.progress)) * 100)
})

const peekTask = computed(() => peekTaskId.value ? store.tasks[peekTaskId.value] ?? null : null)
const peekJob = computed(() => peekJobId.value ? jobs.jobs[peekJobId.value] ?? null : null)
const AUTO_MINIMIZE_DELAY_MS = 1200
const FADE_DURATION_MS = 320
let autoMinimizeTimer: ReturnType<typeof setTimeout> | null = null
let fadeTimer: ReturnType<typeof setTimeout> | null = null

function cancelPeekTimers(): void {
  if (autoMinimizeTimer !== null) clearTimeout(autoMinimizeTimer)
  if (fadeTimer !== null) clearTimeout(fadeTimer)
  autoMinimizeTimer = null
  fadeTimer = null
}

function canAutoMinimizePeek(): boolean {
  if (!peekOpen.value) return false
  if (peekJob.value) {
    return peekJob.value.status === 'succeeded' && peekJob.value.progress >= 1
  }
  if (peekTask.value) {
    return peekTask.value.status === 'proposal_ready'
      && peekTask.value.progress >= 1
      && peekTask.value.pending_count === 0
  }
  return false
}

function scheduleAutoMinimize(): void {
  cancelPeekTimers()
  if (!canAutoMinimizePeek()) return
  const taskId = peekTaskId.value
  const jobId = peekJobId.value
  autoMinimizeTimer = setTimeout(() => {
    autoMinimizeTimer = null
    if (
      peekTaskId.value !== taskId
      || peekJobId.value !== jobId
      || !canAutoMinimizePeek()
    ) return
    peekFading.value = true
    fadeTimer = setTimeout(() => {
      fadeTimer = null
      if (peekTaskId.value === taskId && peekJobId.value === jobId) {
        peekOpen.value = false
        peekFading.value = false
      }
    }, FADE_DURATION_MS)
  }, AUTO_MINIMIZE_DELAY_MS)
}

watch(() => store.taskNoticeRevision, () => {
  if (!store.latestStartedTaskId) return
  cancelPeekTimers()
  peekTaskId.value = store.latestStartedTaskId
  peekJobId.value = null
  peekFading.value = false
  peekOpen.value = true
})

watch(() => jobs.jobNoticeRevision, () => {
  if (!jobs.latestTrackedJobId) return
  cancelPeekTimers()
  peekJobId.value = jobs.latestTrackedJobId
  peekTaskId.value = null
  peekFading.value = false
  peekOpen.value = true
})

watch(
  () => [
    peekOpen.value,
    peekTask.value?.status,
    peekTask.value?.progress,
    peekTask.value?.pending_count,
    peekJob.value?.status,
    peekJob.value?.progress,
  ],
  scheduleAutoMinimize,
)

onBeforeUnmount(cancelPeekTimers)

function minimizePeek(): void {
  cancelPeekTimers()
  peekOpen.value = false
  peekFading.value = false
}

function jobTone(job: JobResponse): 'neutral' | 'info' | 'success' | 'warning' | 'danger' {
  if (job.status === 'running' || job.status === 'queued') return 'info'
  if (job.status === 'succeeded') return 'success'
  if (job.status === 'paused') return 'warning'
  if (job.status === 'failed') return 'danger'
  return 'neutral'
}

function taskTone(task: WorkspaceAITask): TimelineTone {
  if (['failed', 'failed_before_dispatch', 'result_unknown', 'invalid_result'].includes(task.status)) {
    return 'danger'
  }
  if (task.status === 'needs_input') return 'warning'
  if (task.status === 'proposal_ready') return 'success'
  if (['prepared', 'queued', 'running'].includes(task.status)) return 'info'
  return 'neutral'
}

async function returnToJob(job: JobResponse): Promise<void> {
  open.value = false
  minimizePeek()
  await router.push(jobLocation(job))
}

async function returnToTask(task: WorkspaceAITask): Promise<void> {
  open.value = false
  minimizePeek()
  await router.push(returnLocation(task))
}

function toggleDrawer(): void {
  open.value = !open.value
  if (open.value) minimizePeek()
}
</script>

<template>
  <div class="workspace-ai-drawer-host">
    <button
      v-if="totalCount > 0 || open"
      class="workspace-ai-drawer-toggle"
      type="button"
      :aria-expanded="open"
      aria-controls="workspace-ai-task-drawer"
      @click="toggleDrawer"
    >
      <Bell :size="18" :stroke-width="1.8" aria-hidden="true" />
      <span class="workspace-ai-drawer-toggle__label">任务中心</span>
      <span v-if="attentionCount" class="workspace-ai-drawer-toggle__badge">{{ attentionCount }}</span>
      <small v-if="livePercent !== null">{{ livePercent }}%</small>
    </button>

    <Teleport to="body">
    <aside
      v-if="peekOpen && (peekTask || peekJob)"
      class="workspace-ai-task-peek"
      :class="{ 'is-fading': peekFading }"
      aria-live="polite"
    >
      <header>
        <strong>{{ peekTask?.safe_title ?? (peekJob ? jobTitle(peekJob) : '后台任务') }}</strong>
        <button type="button" aria-label="收回任务中心" @click="minimizePeek">—</button>
      </header>
      <progress
        v-if="peekTask || (peekJob && !isQuestionBankLibraryJob(peekJob))"
        :value="Math.max(0, Math.min(1, peekTask?.progress ?? peekJob?.progress ?? 0))"
        max="1"
        aria-label="最新任务进度"
      />
      <p v-if="peekTask">{{ peekTask.teacher_message }}</p>
      <p v-else-if="peekJob">{{ jobDetailLine(peekJob) }}</p>
      <footer>
        <AppButton v-if="peekTask" variant="secondary" @click="returnToTask(peekTask)">返回原页</AppButton>
        <AppButton v-else-if="peekJob" variant="secondary" @click="returnToJob(peekJob)">{{
          isQuestionBankLibraryJob(peekJob) ? '回到试卷库' : '返回相关页面'
        }}</AppButton>
        <AppButton variant="ghost" @click="minimizePeek">收回任务中心</AppButton>
      </footer>
    </aside>

    <div
      v-if="open"
      class="workspace-ai-task-drawer-backdrop fx-overlay"
      @click="open = false"
    />

    <aside
      v-if="open"
      id="workspace-ai-task-drawer"
      class="workspace-ai-task-drawer fx-drawer-right"
      aria-label="任务中心"
    >
      <header>
        <div>
          <strong>任务中心</strong>
          <span>进行中的任务在上面；完成后会自动归档。</span>
        </div>
        <button type="button" aria-label="关闭任务中心" @click="open = false">×</button>
      </header>

      <p v-if="totalCount === 0" class="workspace-ai-task-drawer__empty">
        当前没有任务。新任务开始后会在这里显示进度。
      </p>

      <section v-if="attentionCount" class="workspace-ai-task-drawer__section" aria-label="需要处理">
        <h3>需要处理</h3>
        <div class="workspace-ai-task-drawer__timeline">
          <TimelineItem
            v-for="item in attentionItems"
            :key="item.kind === 'task' ? item.task.task_id : `job-${item.job.id}`"
            :tone="item.kind === 'task' ? taskTone(item.task) : jobTone(item.job)"
          >
            <article v-if="item.kind === 'task'">
              <div class="workspace-ai-task-drawer__heading">
                <strong>{{ item.task.safe_title }}</strong>
                <span>{{ item.task.safe_source }}</span>
              </div>
              <progress :value="item.task.progress" max="1" :aria-label="`${item.task.safe_title}进度`" />
              <p>{{ item.task.teacher_message }}</p>
              <p class="workspace-ai-task-drawer__meta">{{ item.task.next_action }}</p>
              <p v-if="store.syncErrors[item.task.task_id]" role="status">
                {{ store.syncErrors[item.task.task_id] }}
              </p>
              <footer>
                <AppButton variant="secondary" @click="returnToTask(item.task)">返回原页</AppButton>
                <AppButton
                  v-if="canCancelTask(item.task)"
                  variant="ghost"
                  @click="store.cancel(item.task.task_id)"
                >
                  {{ cancelTaskLabel(item.task) }}
                </AppButton>
                <AppButton v-else variant="ghost" @click="store.remove(item.task.task_id)">从列表移除</AppButton>
              </footer>
            </article>
            <article v-else>
              <div class="workspace-ai-task-drawer__heading">
                <strong>{{ jobTitle(item.job) }}</strong>
                <StatusBadge :tone="jobTone(item.job)" :label="jobStatusLabel(item.job)" />
              </div>
              <progress
                v-if="!isQuestionBankLibraryJob(item.job)"
                :value="Math.max(0, Math.min(1, item.job.progress))"
                max="1"
                :aria-label="`${jobTitle(item.job)}进度`"
              />
              <p>{{ jobDetailLine(item.job) }}</p>
              <p v-if="jobs.syncErrors[item.job.id]" role="status">任务状态暂时无法更新，已保留上次状态。</p>
              <footer>
              <AppButton variant="secondary" @click="returnToJob(item.job)">{{
                isQuestionBankLibraryJob(item.job) ? '回到试卷库' : '返回相关页面'
              }}</AppButton>
                <AppButton
                  v-if="!TERMINAL_JOB_STATUSES.has(item.job.status)"
                  variant="ghost"
                  @click="jobs.cancel(item.job.id)"
                >停止任务</AppButton>
                <AppButton v-else variant="ghost" @click="jobs.remove(item.job.id)">从列表移除</AppButton>
              </footer>
            </article>
          </TimelineItem>
        </div>
      </section>

      <details v-if="archivedCount" class="workspace-ai-task-drawer__archive">
        <summary>最近完成 {{ archivedCount }}</summary>
        <ul>
          <li v-for="task in archivedTasks" :key="`archived-${task.task_id}`">
            <div>
              <strong>{{ task.safe_title }}</strong>
              <span>{{ task.teacher_message }}</span>
            </div>
            <AppButton variant="ghost" @click="store.remove(task.task_id)">移除</AppButton>
          </li>
          <li v-for="job in archivedJobs" :key="`archived-job-${job.id}`">
            <div>
              <strong>{{ jobTitle(job) }}</strong>
              <span>{{ jobStatusLabel(job) }}</span>
            </div>
            <AppButton variant="ghost" @click="jobs.remove(job.id)">移除</AppButton>
          </li>
        </ul>
      </details>
    </aside>
    </Teleport>
  </div>
</template>

<style scoped>
.workspace-ai-drawer-host {
  display: block;
}

/* 侧栏行样式：与 app-shell.css 中 app-sidebar__link 同一形态（深色底） */
.workspace-ai-drawer-toggle {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 36px;
  padding-inline: var(--sidebar-icon-inset, 9px);
  border: var(--border-width) solid transparent;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--sidebar-ink-2);
  font: inherit;
  font-size: var(--font-size-dense);
  font-weight: var(--font-weight-medium);
  text-align: start;
  white-space: nowrap;
  cursor: pointer;
  transition:
    background-color var(--duration-fast),
    border-color var(--duration-fast),
    color var(--duration-fast);
}

.workspace-ai-drawer-toggle:hover {
  border-color: transparent;
  background: var(--sidebar-hover);
  color: var(--sidebar-ink-1);
}

.workspace-ai-drawer-toggle:focus-visible {
  outline: none;
  box-shadow: var(--sidebar-focus-ring);
}

.workspace-ai-drawer-toggle__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workspace-ai-drawer-toggle__badge {
  display: grid;
  place-items: center;
  min-width: 18px;
  height: 18px;
  margin-inline-start: auto;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--sidebar-status-attention);
  color: var(--color-text-primary);
  font-size: 11px;
  font-weight: var(--font-weight-semibold);
}

.workspace-ai-drawer-toggle small {
  color: var(--sidebar-ink-3);
  font-size: 11px;
  font-weight: var(--font-weight-medium);
}

/* 图标轨：只留图标，计数徽标改为右上角小圆点，保持图标居中于 36px 列 */
.app-shell--rail .workspace-ai-drawer-toggle {
  position: relative;
  justify-content: flex-start;
  padding-inline: var(--sidebar-icon-inset, 9px);
}

.app-shell--rail .workspace-ai-drawer-toggle__badge {
  position: absolute;
  inset-block-start: 3px;
  inset-inline-end: 3px;
  min-width: 14px;
  height: 14px;
  margin-inline-start: 0;
  padding: 0 4px;
  font-size: 9px;
  line-height: 14px;
}

.app-shell--rail .workspace-ai-drawer-toggle__label,
.app-shell--rail .workspace-ai-drawer-toggle small {
  width: 0;
  max-width: 0;
  opacity: 0;
  overflow: hidden;
  pointer-events: none;
  visibility: hidden;
  transition: opacity var(--duration-base) var(--ease-out);
}

.app-shell--rail .app-sidebar:hover .workspace-ai-drawer-toggle__label,
.app-shell--rail .app-sidebar:hover .workspace-ai-drawer-toggle small,
.app-shell--rail .app-sidebar:focus-within .workspace-ai-drawer-toggle__label,
.app-shell--rail .app-sidebar:focus-within .workspace-ai-drawer-toggle small {
  width: auto;
  max-width: 100%;
  opacity: 1;
  pointer-events: auto;
  visibility: visible;
  transition-delay: 60ms;
}

.app-shell--rail .app-sidebar:hover .workspace-ai-drawer-toggle__badge,
.app-shell--rail .app-sidebar:focus-within .workspace-ai-drawer-toggle__badge {
  position: static;
  min-width: 18px;
  height: 18px;
  margin-inline-start: auto;
  padding: 0 5px;
  font-size: 11px;
  line-height: 18px;
}

.workspace-ai-task-drawer-backdrop {
  position: fixed;
  inset: 0;
  z-index: 69;
  background: transparent;
}

/* 贴住侧栏右侧（--shell-sidebar-offset 由 AppShell 按展开/图标轨/抽屉写入），
   避免遮挡工作区右下角的保存等主操作 */
.workspace-ai-task-peek {
  position: fixed;
  z-index: 72;
  inset-block-end: 16px;
  inset-inline-start: calc(var(--shell-sidebar-offset, 0px) + 16px);
  display: grid;
  width: min(340px, calc(100vw - 24px));
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--border);
  border-inline-start: 3px solid var(--primary);
  border-radius: var(--radius);
  background: var(--card);
  box-shadow: var(--shadow-overlay);
  transition: opacity 0.3s ease, transform 0.3s ease;
}

.workspace-ai-task-peek.is-fading {
  opacity: 0;
  transform: scale(0.96);
}

.workspace-ai-task-peek header,
.workspace-ai-task-peek footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.workspace-ai-task-peek header button {
  border: 0;
  background: transparent;
  font-size: 22px;
  cursor: pointer;
}

.workspace-ai-task-peek progress {
  width: 100%;
}

.workspace-ai-task-peek p {
  margin: 0;
  color: var(--color-text-secondary);
}

.workspace-ai-task-peek footer {
  justify-content: flex-start;
  flex-wrap: wrap;
}

.workspace-ai-task-drawer {
  position: fixed;
  top: 12px;
  right: 12px;
  z-index: 70;
  display: grid;
  align-content: start;
  width: min(320px, calc(100vw - 24px));
  max-height: min(70vh, calc(100vh - 24px));
  overflow: auto;
  padding: 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--background);
  box-shadow: var(--shadow-overlay);
}

.workspace-ai-task-drawer > header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  padding-bottom: 10px;
}

.workspace-ai-task-drawer > header div {
  display: grid;
  gap: 2px;
}

.workspace-ai-task-drawer > header span {
  color: var(--muted-foreground);
  font-size: var(--font-size-dense);
}

.workspace-ai-task-drawer > header button {
  border: 0;
  background: transparent;
  font-size: 24px;
  line-height: 1;
  cursor: pointer;
}

.workspace-ai-task-drawer__section h3 {
  margin: 0 0 8px;
  font-size: var(--font-size-dense);
  font-weight: 700;
}

.workspace-ai-task-drawer article {
  display: grid;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--card);
}

.workspace-ai-task-drawer__heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}

.workspace-ai-task-drawer__heading span,
.workspace-ai-task-drawer__meta {
  color: var(--muted-foreground);
  font-size: var(--font-size-dense);
}

.workspace-ai-task-drawer progress {
  width: 100%;
}

.workspace-ai-task-drawer p {
  margin: 0;
}

.workspace-ai-task-drawer footer {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.workspace-ai-task-drawer__empty {
  padding: 24px 8px;
  color: var(--muted-foreground);
  text-align: center;
}

.workspace-ai-task-drawer__timeline {
  display: grid;
  gap: 10px;
}

.workspace-ai-task-drawer__archive {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
}

.workspace-ai-task-drawer__archive summary {
  cursor: pointer;
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
  font-weight: 650;
}

.workspace-ai-task-drawer__archive ul {
  display: grid;
  gap: 6px;
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
}

.workspace-ai-task-drawer__archive li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--card);
}

.workspace-ai-task-drawer__archive li div {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.workspace-ai-task-drawer__archive span {
  color: var(--muted-foreground);
  font-size: var(--font-size-caption);
}
</style>
