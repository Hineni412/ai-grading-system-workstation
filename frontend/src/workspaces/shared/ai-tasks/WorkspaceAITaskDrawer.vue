<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useWorkspaceAITaskStore } from './store'
import { useJobStore } from '../../../stores/jobs'
import { TERMINAL_JOB_STATUSES, type JobResponse } from '../../../api/jobs'
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
const ordinaryJobs = computed(() => Object.values(jobs.jobs)
  .filter(job => !job.job_type.startsWith('workspace_ai.'))
  .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at)))
const totalCount = computed(() => store.orderedTasks.length + ordinaryJobs.value.length)
const activeCount = computed(() => store.activeCount + ordinaryJobs.value.filter(
  job => !TERMINAL_JOB_STATUSES.has(job.status),
).length)

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

function jobLocation(job: JobResponse): string {
  if (['config_generation'].includes(job.job_type)) return '/sessions'
  if (['question_import', 'tagging_sync', 'taxonomy_suggestion'].includes(job.job_type)) return '/question-bank'
  if (['scan_analysis', 'grading_run'].includes(job.job_type)) return '/grading'
  if (job.job_type.startsWith('teaching_prep')) return '/teaching-prep'
  return '/workbench'
}

async function returnToJob(job: JobResponse): Promise<void> {
  open.value = false
  await router.push(jobLocation(job))
}

async function returnToTask(task: WorkspaceAITask): Promise<void> {
  open.value = false
  await router.push(returnLocation(task))
}
</script>

<template>
  <button
    v-if="totalCount > 0"
    class="workspace-ai-drawer-toggle"
    type="button"
    :aria-expanded="open"
    aria-controls="workspace-ai-task-drawer"
    @click="open = !open"
  >
    任务中心<span v-if="activeCount">{{ activeCount }}</span>
  </button>

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
    <article v-for="task in store.orderedTasks" :key="task.task_id">
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
        <button type="button" @click="returnToTask(task)">返回原页</button>
        <button
          v-if="canCancelTask(task)"
          type="button"
          @click="store.cancel(task.task_id)"
        >
          {{ cancelTaskLabel(task) }}
        </button>
        <button v-else type="button" @click="store.remove(task.task_id)">从列表移除</button>
      </footer>
    </article>
    <article v-for="job in ordinaryJobs" :key="`job-${job.id}`">
      <div class="workspace-ai-task-drawer__heading">
        <strong>{{ jobTitle(job) }}</strong><span>{{ jobStatus(job) }}</span>
      </div>
      <progress :value="Math.max(0, Math.min(1, job.progress))" max="1" :aria-label="`${jobTitle(job)}进度`" />
      <p>{{ Math.round(Math.max(0, Math.min(1, job.progress)) * 100) }}% · {{ jobStatus(job) }}</p>
      <p v-if="jobs.syncErrors[job.id]" role="status">任务状态暂时无法更新，已保留上次状态。</p>
      <footer>
        <button type="button" @click="returnToJob(job)">返回相关页面</button>
        <button
          v-if="!TERMINAL_JOB_STATUSES.has(job.status)"
          type="button"
          @click="jobs.cancel(job.id)"
        >停止任务</button>
        <button v-else type="button" @click="jobs.remove(job.id)">从列表移除</button>
      </footer>
    </article>
  </aside>
</template>

<style scoped>
.workspace-ai-drawer-toggle{display:flex;align-items:center;gap:8px;min-height:38px;margin-left:auto;padding:0 13px;border:1px solid var(--color-accent);border-radius:999px;background:var(--color-accent);color:var(--color-bg-surface);font:inherit;font-weight:700;white-space:nowrap}
.workspace-ai-drawer-toggle span{display:grid;place-items:center;min-width:22px;height:22px;padding:0 6px;border-radius:999px;background:var(--color-bg-surface);color:var(--color-accent)}
.workspace-ai-task-drawer{position:fixed;inset:0 0 0 auto;z-index:70;width:min(430px,100vw);overflow:auto;padding:20px;background:var(--color-bg-canvas);border-left:1px solid var(--color-border-default);box-shadow:var(--shadow-floating)}
.workspace-ai-task-drawer>header{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;position:sticky;top:-20px;z-index:1;padding:20px 0 14px;background:var(--color-bg-canvas)}
.workspace-ai-task-drawer>header div{display:grid;gap:4px}.workspace-ai-task-drawer>header span{color:var(--color-text-secondary);font-size:var(--font-size-dense)}
.workspace-ai-task-drawer>header button{border:0;background:transparent;font-size:28px;line-height:1}
.workspace-ai-task-drawer article{display:grid;gap:10px;margin:12px 0;padding:16px;border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface)}
.workspace-ai-task-drawer__heading{display:flex;justify-content:space-between;gap:12px}.workspace-ai-task-drawer__heading span{color:var(--color-text-secondary)}
.workspace-ai-task-drawer progress{width:100%}.workspace-ai-task-drawer p,.workspace-ai-task-drawer ul{margin:0}.workspace-ai-task-drawer ul{padding-left:20px;color:var(--color-text-secondary)}
.workspace-ai-task-drawer footer{display:flex;flex-wrap:wrap;gap:8px}.workspace-ai-task-drawer footer button{min-height:38px;padding:0 12px;border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);font:inherit}.workspace-ai-task-drawer footer button:first-child{border-color:var(--color-accent);color:var(--color-accent)}
.workspace-ai-task-drawer__empty{padding:32px 12px;color:var(--color-text-secondary);text-align:center}
</style>
