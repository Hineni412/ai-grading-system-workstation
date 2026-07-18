<script setup lang="ts">
import { computed, ref } from 'vue'

import {
  questionBankApi,
  questionJobFailures,
  questionJobFailuresCsv,
} from '../../api/question-bank'
import { TERMINAL_JOB_STATUSES, type JobResponse } from '../../api/jobs'
import { useJobStore } from '../../stores/jobs'
import { useQuestionBankStore } from '../../stores/question-bank'

const bank = useQuestionBankStore()
const jobStore = useJobStore()
const busy = ref(false)
const feedback = ref('')
const queuedFiles = ref<Array<{ name: string; state: string }>>([])

const jobs = computed(() => Object.values(jobStore.jobs)
  .filter((job) => job.job_type === 'question_import' || job.job_type === 'tagging_sync')
  .sort((left, right) => right.id - left.id))

const statusLabels: Record<string, string> = {
  queued: '等待中',
  running: '进行中',
  paused: '已暂停',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

function safeCount(result: Record<string, unknown>, key: string): number {
  const value = result[key]
  return Number.isSafeInteger(value) && Number(value) >= 0 ? Number(value) : 0
}

async function chooseFiles(event: Event): Promise<void> {
  const input = event.currentTarget as HTMLInputElement
  const files = [...(input.files ?? [])]
  input.value = ''
  if (files.length === 0 || busy.value) return
  busy.value = true
  feedback.value = ''
  queuedFiles.value = files.map(({ name }) => ({ name, state: '等待上传' }))
  for (const [index, file] of files.entries()) {
    const row = queuedFiles.value[index]!
    try {
      row.state = '正在安全上传'
      const upload = await questionBankApi.stageImport(file)
      row.state = '正在核对导入请求'
      const request = await questionBankApi.createImportRequest(upload.upload_id)
      row.state = '已提交任务'
      jobStore.track(await questionBankApi.submitImportJob(request.request_id))
    } catch {
      row.state = '提交失败'
    }
  }
  busy.value = false
  feedback.value = '导入队列已处理。每个文件都有独立任务，可在下方继续跟踪。'
}

async function startTagging(): Promise<void> {
  const count = bank.selectedQuestionIds.length
  if (count === 0 || busy.value) return
  const confirmed = window.confirm(
    `将对已选 ${count} 道题调用 AI 标注，可能产生外部费用。确认现在启动吗？`,
  )
  if (!confirmed) return
  busy.value = true
  feedback.value = ''
  try {
    const job = await questionBankApi.submitTagging(bank.selectedQuestionIds)
    jobStore.track(job)
    feedback.value = `已提交 ${count} 道题的 AI 标注任务。`
  } catch {
    feedback.value = 'AI 标注任务没有提交，当前选择保持不变。'
  } finally {
    busy.value = false
  }
}

async function retry(job: JobResponse): Promise<void> {
  if (busy.value) return
  const failures = questionJobFailures(job.result)
  if (job.job_type === 'tagging_sync') {
    const ids = failures.map(({ question_id }) => question_id)
    const count = ids.length
    const confirmed = window.confirm(
      `将只重试 ${count || '服务端允许的'} 道失败题目，可能产生外部费用。确认继续吗？`,
    )
    if (!confirmed) return
    busy.value = true
    try {
      jobStore.track(await questionBankApi.retryTagging(job.id, count ? ids : undefined))
      feedback.value = '失败题目已提交新的重试任务。'
    } catch {
      feedback.value = '重试任务没有提交，原任务记录保持不变。'
    } finally {
      busy.value = false
    }
    return
  }
  busy.value = true
  try {
    jobStore.track(await questionBankApi.retryImport(job.id))
    feedback.value = '导入重试已经提交。'
  } catch {
    feedback.value = '导入重试没有提交，原任务记录保持不变。'
  } finally {
    busy.value = false
  }
}

function downloadFailures(job: JobResponse): void {
  const csv = `\uFEFF${questionJobFailuresCsv(job.result)}`
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `题库任务-${job.id}-失败清单.csv`
  link.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <details class="qb-jobs" open>
    <summary>
      <span>
        <strong>导入与任务</strong>
        <small>受控 DOCX/PDF、AI 标注、取消与失败重试</small>
      </span>
      <span>{{ jobs.length }} 个任务</span>
    </summary>

    <div class="qb-jobs__actions">
      <label class="qb-file-picker">
        <span>选择 DOCX/PDF，可一次添加多个文件</span>
        <input type="file" accept=".docx,.pdf" multiple :disabled="busy" @change="chooseFiles">
        <strong>{{ busy ? '正在处理……' : '选择文件' }}</strong>
      </label>
      <button
        type="button"
        class="qb-button qb-button--ai"
        :disabled="bank.selectedCount === 0 || busy"
        @click="startTagging"
      >
        AI 标注 {{ bank.selectedCount }} 题
      </button>
    </div>
    <p class="qb-help">不会自动调用 AI；只有确认题数和费用提示后才会提交。</p>
    <p v-if="feedback" class="qb-feedback" role="status">{{ feedback }}</p>

    <ul v-if="queuedFiles.length" class="qb-upload-queue" aria-label="本次文件队列">
      <li v-for="file in queuedFiles" :key="file.name">
        <span>{{ file.name }}</span><strong>{{ file.state }}</strong>
      </li>
    </ul>

    <div v-if="jobs.length" class="qb-job-list">
      <article v-for="job in jobs" :key="job.id" class="qb-job">
        <header>
          <div>
            <strong>{{ job.job_type === 'tagging_sync' ? 'AI 标注' : '试卷导入' }} #{{ job.id }}</strong>
            <span>{{ statusLabels[job.status] || job.status }} · {{ Math.round(job.progress * 100) }}%</span>
          </div>
          <progress :value="job.progress" max="1">{{ Math.round(job.progress * 100) }}%</progress>
        </header>
        <p>{{ job.detail || job.stage || '任务等待服务处理' }}</p>
        <p v-if="job.result.outcome === 'partial'" class="qb-feedback is-warning">
          部分完成：成功 {{ safeCount(job.result, 'tagged_count') || safeCount(job.result, 'question_count') }}，
          跳过 {{ safeCount(job.result, 'skipped_complete_count') }}，
          失败 {{ safeCount(job.result, 'failed_count') }}。
        </p>
        <p v-if="job.error" class="qb-feedback is-error">{{ job.error }}</p>
        <div class="qb-job__actions">
          <button
            v-if="!TERMINAL_JOB_STATUSES.has(job.status)"
            type="button"
            class="qb-link"
            @click="jobStore.cancel(job.id)"
          >
            请求取消
          </button>
          <button type="button" class="qb-link" @click="jobStore.refresh(job.id)">刷新</button>
          <button
            v-if="questionJobFailures(job.result).length"
            type="button"
            class="qb-link"
            @click="downloadFailures(job)"
          >
            下载失败清单
          </button>
          <button
            v-if="TERMINAL_JOB_STATUSES.has(job.status) && (job.status !== 'succeeded' || job.result.outcome === 'partial')"
            type="button"
            class="qb-link"
            :disabled="busy"
            @click="retry(job)"
          >
            重试允许的失败项
          </button>
        </div>
      </article>
    </div>
    <p v-else class="qb-empty">当前浏览器还没有记录题库任务。</p>
  </details>
</template>
