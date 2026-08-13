<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
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
  dispatchEvidenceLabel,
  handoffSummary,
  returnLocation,
} from './taskPresentation'
import type { WorkspaceAITask } from './contracts'

const store = useWorkspaceAITaskStore()
const jobs = useJobStore()
const router = useRouter()
const open = ref(false)
const peekOpen = ref(false)
const peekTaskId = ref<string | null>(null)
const peekJobId = ref<number | null>(null)
const ordinaryJobs = computed(() => Object.values(jobs.jobs)
  .filter(job => !job.job_type.startsWith('workspace_ai.'))
  .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at)))
const totalCount = computed(() => store.orderedTasks.length + ordinaryJobs.value.length)
const activeCount = computed(() => store.activeCount + ordinaryJobs.value.filter(
  job => !TERMINAL_JOB_STATUSES.has(job.status),
).length)
const peekTask = computed(() => peekTaskId.value ? store.tasks[peekTaskId.value] ?? null : null)
const peekJob = computed(() => peekJobId.value ? jobs.jobs[peekJobId.value] ?? null : null)
const AUTO_MINIMIZE_DELAY_MS = 1000
let autoMinimizeTimer: ReturnType<typeof setTimeout> | null = null

function cancelAutoMinimize(): void {
  if (autoMinimizeTimer !== null) clearTimeout(autoMinimizeTimer)
  autoMinimizeTimer = null
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
  cancelAutoMinimize()
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
    peekOpen.value = false
  }, AUTO_MINIMIZE_DELAY_MS)
}

watch(() => store.taskNoticeRevision, () => {
  if (!store.latestStartedTaskId) return
  peekTaskId.value = store.latestStartedTaskId
  peekJobId.value = null
  peekOpen.value = true
})

watch(() => jobs.jobNoticeRevision, () => {
  if (!jobs.latestTrackedJobId) return
  peekJobId.value = jobs.latestTrackedJobId
  peekTaskId.value = null
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

onBeforeUnmount(cancelAutoMinimize)

const jobTitles: Record<string, string> = {
  config_generation: '生成评分依据',
  question_import: '试卷入库',
  tagging_sync: '题库标签补齐',
  taxonomy_suggestion: '新词归并建议',
  scan_analysis: '答卷扫描预检',
  grading_run: '考试批改',
  teaching_prep_material_parse: '备课资料解析',
}

function jobTitle(job: JobResponse): string {
  return jobTitles[job.job_type] ?? '后台处理任务'
}

function jobStatus(job: JobResponse): string {
  if (job.status === 'queued') return '等待开始'
  if (job.status === 'running') return '正在处理'
  if (job.status === 'paused') return '已暂停'
  if (job.status === 'succeeded') return '已完成'
  if (job.status === 'cancelled') return '已取消'
  return '未完成'
}

function jobTone(job: JobResponse): 'neutral' | 'info' | 'success' | 'warning' | 'danger' {
  if (job.status === 'running') return 'info'
  if (job.status === 'succeeded') return 'success'
  if (job.status === 'paused') return 'warning'
  if (job.status === 'failed') return 'danger'
  return 'neutral'
}

function taskTone(task: WorkspaceAITask): TimelineTone {
  if (['failed', 'failed_before_dispatch', 'result_unknown', 'invalid_result'].includes(task.status)) return 'danger'
  if (task.status === 'needs_input') return 'warning'
  if (task.status === 'proposal_ready') return 'success'
  if (['prepared', 'queued', 'running'].includes(task.status)) return 'info'
  return 'neutral'
}

function jobLocation(job: JobResponse): string {
  if (['config_generation'].includes(job.job_type)) return '/sessions'
  if (['question_import', 'tagging_sync', 'taxonomy_suggestion'].includes(job.job_type)) return '/question-bank'
  if (['scan_analysis', 'grading_run'].includes(job.job_type)) return '/grading'
  if (job.job_type.startsWith('teaching_prep')) return '/teaching-prep'
  return '/workbench'
}

async function returnToJob(job: JobResponse): Promise<void> {
  open.value = false
  peekOpen.value = false
  await router.push(jobLocation(job))
}

async function returnToTask(task: WorkspaceAITask): Promise<void> {
  open.value = false
  peekOpen.value = false
  await router.push(returnLocation(task))
}

function toggleDrawer(): void {
  open.value = !open.value
  if (open.value) peekOpen.value = false
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
      任务中心<span v-if="activeCount">{{ activeCount }}</span>
    </button>

    <aside v-if="peekOpen && (peekTask || peekJob)" class="workspace-ai-task-peek" aria-live="polite">
      <header>
        <strong>{{ peekTask?.safe_title ?? (peekJob ? jobTitle(peekJob) : '后台任务') }}</strong>
        <button type="button" aria-label="缩到任务中心" @click="peekOpen = false">—</button>
      </header>
      <progress
        :value="peekTask?.progress ?? peekJob?.progress ?? 0"
        max="1"
        aria-label="最新任务进度"
      />
      <p v-if="peekTask">{{ peekTask.teacher_message }}</p>
      <p v-else-if="peekJob">{{ Math.round(peekJob.progress * 100) }}% · {{ jobStatus(peekJob) }}</p>
      <footer>
        <AppButton v-if="peekTask" variant="secondary" @click="returnToTask(peekTask)">返回相关页面</AppButton>
        <AppButton v-else-if="peekJob" variant="secondary" @click="returnToJob(peekJob)">返回相关页面</AppButton>
        <AppButton variant="ghost" @click="peekOpen = false">缩到任务中心</AppButton>
      </footer>
    </aside>

    <aside
      v-if="open"
      id="workspace-ai-task-drawer"
      class="workspace-ai-task-drawer"
      aria-label="任务中心"
    >
    <header>
      <div><strong>任务中心</strong><span>汇总上传、批改、题库与教师工作台任务</span></div>
      <button type="button" aria-label="关闭任务中心" @click="open = false">×</button>
    </header>
      <p v-if="totalCount === 0" class="workspace-ai-task-drawer__empty">当前没有任务。新任务开始后会在这里持续显示进度。</p>
      <div class="workspace-ai-task-drawer__timeline">
      <TimelineItem v-for="task in store.orderedTasks" :key="task.task_id" :tone="taskTone(task)">
      <article>
      <div class="workspace-ai-task-drawer__heading">
        <strong>{{ task.safe_title }}</strong><span>{{ task.safe_source }}</span>
      </div>
      <progress :value="task.progress" max="1" :aria-label="`${task.safe_title}进度`" />
      <p>{{ task.teacher_message }}</p>
      <ul>
        <li>{{ dispatchEvidenceLabel(task) }}</li>
        <li>{{ handoffSummary(task) }}</li>
        <li>{{ task.next_action }}</li>
      </ul>
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
      <TimelineItem v-for="job in ordinaryJobs" :key="`job-${job.id}`" :tone="jobTone(job)">
      <article>
      <div class="workspace-ai-task-drawer__heading">
        <strong>{{ jobTitle(job) }}</strong><StatusBadge :tone="jobTone(job)" :label="jobStatus(job)" />
      </div>
      <progress :value="Math.max(0, Math.min(1, job.progress))" max="1" :aria-label="`${jobTitle(job)}进度`" />
      <p>{{ Math.round(Math.max(0, Math.min(1, job.progress)) * 100) }}% · {{ jobStatus(job) }}</p>
      <p v-if="jobs.syncErrors[job.id]" role="status">任务状态暂时无法更新，已保留上次状态。</p>
      <footer>
        <AppButton variant="secondary" @click="returnToJob(job)">返回相关页面</AppButton>
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
    </aside>
  </div>
</template>

<style scoped>
.workspace-ai-drawer-host{position:relative;display:flex;justify-content:flex-end}.workspace-ai-drawer-toggle{display:flex;align-items:center;gap:8px;min-height:38px;padding:0 13px;border:1px solid var(--primary);border-radius:999px;background:var(--primary);color:var(--primary-foreground);font:inherit;font-weight:700;white-space:nowrap;cursor:pointer}
.workspace-ai-drawer-toggle span{display:grid;place-items:center;min-width:22px;height:22px;padding:0 6px;border-radius:999px;background:var(--card);color:var(--primary)}
.workspace-ai-task-peek{position:absolute;z-index:72;inset-block-start:calc(100% + 14px);inset-inline-end:0;display:grid;width:min(370px,calc(100vw - 24px));gap:10px;padding:16px;border:1px solid var(--border);border-inline-start:3px solid var(--primary);border-radius:var(--radius);background:var(--card);box-shadow:var(--shadow-overlay)}.workspace-ai-task-peek header,.workspace-ai-task-peek footer{display:flex;align-items:center;justify-content:space-between;gap:10px}.workspace-ai-task-peek header button{border:0;background:transparent;font-size:22px;cursor:pointer}.workspace-ai-task-peek progress{width:100%}.workspace-ai-task-peek p{margin:0;color:var(--color-text-secondary)}.workspace-ai-task-peek footer{justify-content:flex-start}
.workspace-ai-task-drawer{position:fixed;inset:0 0 0 auto;z-index:70;width:min(430px,100vw);overflow:auto;padding:20px;background:var(--background);border-left:1px solid var(--border);box-shadow:var(--shadow-overlay)}
.workspace-ai-task-drawer>header{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;position:sticky;top:-20px;z-index:1;padding:20px 0 14px;background:var(--background)}
.workspace-ai-task-drawer>header div{display:grid;gap:4px}.workspace-ai-task-drawer>header span{color:var(--muted-foreground);font-size:var(--font-size-dense)}
.workspace-ai-task-drawer>header button{border:0;background:transparent;font-size:28px;line-height:1;cursor:pointer}
.workspace-ai-task-drawer article{display:grid;gap:10px;margin:12px 0;padding:16px;border:1px solid var(--border);border-radius:var(--radius);background:var(--card)}
.workspace-ai-task-drawer__heading{display:flex;justify-content:space-between;align-items:center;gap:12px}.workspace-ai-task-drawer__heading span{color:var(--muted-foreground)}
.workspace-ai-task-drawer progress{width:100%}.workspace-ai-task-drawer p,.workspace-ai-task-drawer ul{margin:0}.workspace-ai-task-drawer ul{padding-left:20px;color:var(--color-text-secondary)}
.workspace-ai-task-drawer footer{display:flex;flex-wrap:wrap;gap:8px}
.workspace-ai-task-drawer__empty{padding:32px 12px;color:var(--muted-foreground);text-align:center}
.workspace-ai-task-drawer__timeline{display:grid;gap:12px;margin:12px 0}.workspace-ai-task-drawer__timeline article{margin:0}
</style>
