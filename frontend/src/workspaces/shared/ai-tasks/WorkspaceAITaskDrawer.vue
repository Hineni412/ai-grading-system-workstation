<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

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

const ordinaryJobs = computed(() => Object.values(jobs.jobs)
  .filter(job => !job.job_type.startsWith('workspace_ai.'))
  .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at)))

const attentionTasks = computed(() => store.orderedTasks.filter(isAttentionTask))
const archivedTasks = computed(() => store.orderedTasks.filter(isArchivedTask))
const attentionJobs = computed(() => ordinaryJobs.value.filter(isAttentionJob))
const archivedJobs = computed(() => ordinaryJobs.value.filter(isArchivedJob))
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
  await router.push(jobLocation(job))
}

async function returnToTask(task: WorkspaceAITask): Promise<void> {
  open.value = false
  await router.push(returnLocation(task))
}

function toggleDrawer(): void {
  open.value = !open.value
}
</script>

<template>
  <div class="workspace-ai-drawer-host">
    <button
      class="workspace-ai-drawer-toggle"
      type="button"
      :aria-expanded="open"
      aria-controls="workspace-ai-task-drawer"
      @click="toggleDrawer"
    >
      任务中心
      <span v-if="attentionCount">{{ attentionCount }}</span>
      <small v-if="livePercent !== null">{{ livePercent }}%</small>
    </button>

    <div
      v-if="open"
      class="workspace-ai-task-drawer-backdrop"
      @click="open = false"
    />

    <aside
      v-if="open"
      id="workspace-ai-task-drawer"
      class="workspace-ai-task-drawer"
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
          <TimelineItem v-for="task in attentionTasks" :key="task.task_id" :tone="taskTone(task)">
            <article>
              <div class="workspace-ai-task-drawer__heading">
                <strong>{{ task.safe_title }}</strong>
                <span>{{ task.safe_source }}</span>
              </div>
              <progress :value="task.progress" max="1" :aria-label="`${task.safe_title}进度`" />
              <p>{{ task.teacher_message }}</p>
              <p class="workspace-ai-task-drawer__meta">{{ task.next_action }}</p>
              <p v-if="store.syncErrors[task.task_id]" role="status">
                {{ store.syncErrors[task.task_id] }}
              </p>
              <footer>
                <AppButton variant="secondary" @click="returnToTask(task)">返回原页</AppButton>
                <AppButton
                  v-if="canCancelTask(task)"
                  variant="ghost"
                  @click="store.cancel(task.task_id)"
                >
                  {{ cancelTaskLabel(task) }}
                </AppButton>
                <AppButton v-else variant="ghost" @click="store.remove(task.task_id)">从列表移除</AppButton>
              </footer>
            </article>
          </TimelineItem>
          <TimelineItem v-for="job in attentionJobs" :key="`job-${job.id}`" :tone="jobTone(job)">
            <article>
              <div class="workspace-ai-task-drawer__heading">
                <strong>{{ jobTitle(job) }}</strong>
                <StatusBadge :tone="jobTone(job)" :label="jobStatusLabel(job)" />
              </div>
              <progress
                v-if="!isQuestionBankLibraryJob(job)"
                :value="Math.max(0, Math.min(1, job.progress))"
                max="1"
                :aria-label="`${jobTitle(job)}进度`"
              />
              <p>{{ jobDetailLine(job) }}</p>
              <p v-if="jobs.syncErrors[job.id]" role="status">任务状态暂时无法更新，已保留上次状态。</p>
              <footer>
              <AppButton variant="secondary" @click="returnToJob(job)">{{
                isQuestionBankLibraryJob(job) ? '回到试卷库' : '返回相关页面'
              }}</AppButton>
                <AppButton
                  v-if="!TERMINAL_JOB_STATUSES.has(job.status)"
                  variant="ghost"
                  @click="jobs.cancel(job.id)"
                >停止任务</AppButton>
                <AppButton v-else variant="ghost" @click="jobs.remove(job.id)">从列表移除</AppButton>
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
  </div>
</template>

<style scoped>
.workspace-ai-drawer-host {
  position: relative;
  display: flex;
  justify-content: flex-end;
}

.workspace-ai-drawer-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 38px;
  padding: 0 13px;
  border: 1px solid var(--primary);
  border-radius: 999px;
  background: var(--primary);
  color: var(--primary-foreground);
  font: inherit;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
}

.workspace-ai-drawer-toggle span {
  display: grid;
  place-items: center;
  min-width: 22px;
  height: 22px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--card);
  color: var(--primary);
}

.workspace-ai-drawer-toggle small {
  font-size: 12px;
  font-weight: 600;
  opacity: 0.92;
}

.workspace-ai-task-drawer-backdrop {
  position: fixed;
  inset: 0;
  z-index: 69;
  background: transparent;
}

.workspace-ai-task-drawer {
  position: fixed;
  top: calc(var(--shell-topbar-height, 68px) + 8px);
  right: 12px;
  z-index: 70;
  display: grid;
  align-content: start;
  width: min(320px, calc(100vw - 24px));
  max-height: min(70vh, calc(100vh - var(--shell-topbar-height, 68px) - 24px));
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
