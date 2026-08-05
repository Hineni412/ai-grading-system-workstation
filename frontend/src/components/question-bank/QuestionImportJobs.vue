<script setup lang="ts">
import { computed, ref } from 'vue'

import {
  questionBankApi,
  questionJobFailures,
  questionJobFailuresCsv,
  questionJobRetryIds,
} from '../../api/question-bank'
import { TERMINAL_JOB_STATUSES, type JobResponse } from '../../api/jobs'
import { useJobStore } from '../../stores/jobs'
import { useQuestionBankStore } from '../../stores/question-bank'

const props = withDefaults(defineProps<{
  pendingTaxonomyCount?: number
}>(), {
  pendingTaxonomyCount: 0,
})

const emit = defineEmits<{
  reviewTaxonomy: []
}>()

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

function jobCompletionNote(job: JobResponse): string {
  if (!TERMINAL_JOB_STATUSES.has(job.status)) return job.detail || job.stage || '任务等待服务处理'
  if (job.status === 'failed') return job.job_type === 'question_import'
    ? '试卷没有完成入库。'
    : '标签或判定点没有完成；已保存结果不会撤销。'
  if (job.status === 'cancelled') return '任务已取消；已保存结果不会撤销。'
  if (job.job_type === 'question_import') return '试卷已入库，尚未执行标签与判定点分析。'
  const reviewCount = safeCount(job.result, 'criteria_needs_review_count')
  if (reviewCount > 0) return `${reviewCount} 道题的判定点需要审核，本任务不计为分析成功。`
  if (job.result.outcome === 'complete') return '标签、解题证据和训练判定点均已完成。'
  if (job.result.outcome === 'partial') return '部分完成，仍有未完成或待审核项目。'
  return '分析任务已结束，请核对各项结果。'
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
  if (job.job_type === 'tagging_sync') {
    const ids = questionJobRetryIds(job)
    if (ids.length === 0) {
      feedback.value = '服务端没有提供可安全重试的题目范围，请先刷新任务。'
      return
    }
    const confirmed = window.confirm(
      `只重试 ${ids.length} 道失败题目，仍可能产生外部费用。确认继续吗？`,
    )
    if (!confirmed) return
    busy.value = true
    try {
      jobStore.track(await questionBankApi.retryTagging(job.id, ids))
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

async function restoreRequiredPaper(job: JobResponse): Promise<void> {
  const paperId = Number(job.result.restore_paper_id)
  if (!Number.isSafeInteger(paperId) || paperId <= 0 || busy.value) return
  busy.value = true
  feedback.value = ''
  try {
    const restored = await bank.restorePaperFromTrash(paperId)
    feedback.value = restored
      ? '原试卷及其随试卷移入回收站的题目已经恢复。'
      : '原试卷没有恢复，请打开试卷回收站核对当前状态。'
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
  <section class="qb-jobs" aria-labelledby="qb-import-title">
    <header class="qb-jobs__heading">
      <div>
        <p class="qb-eyebrow">IMPORT & TASKS</p>
        <h2 id="qb-import-title">上传试卷与任务</h2>
        <p>Word/PDF 会先安全上传，再由后台生成试卷和题目。</p>
      </div>
      <div class="qb-jobs__heading-actions">
        <span>{{ jobs.length }} 个历史任务</span>
        <button
          type="button"
          class="qb-button is-review"
          @click="emit('reviewTaxonomy')"
        >
          待审核新词
          <strong>{{ props.pendingTaxonomyCount }}</strong>
        </button>
      </div>
    </header>

    <div class="qb-jobs__actions">
      <label class="qb-file-picker">
        <input type="file" accept=".docx,.pdf" multiple :disabled="busy" @change="chooseFiles">
        <span>
          <strong>{{ busy ? '正在处理…' : '选择 Word / PDF' }}</strong>
          <small>可一次选择多个文件，单个文件不超过 200 MB</small>
        </span>
      </label>
      <button
        type="button"
        class="qb-button is-ai"
        :disabled="bank.selectedCount === 0 || busy"
        @click="startTagging"
      >
        AI 标注已选 {{ bank.selectedCount }} 题
      </button>
    </div>
    <p class="qb-help">不会自动调用 AI；只有确认题数和费用提示后才会提交。</p>
    <p v-if="feedback" class="qb-feedback" role="status">{{ feedback }}</p>

    <ul v-if="queuedFiles.length" class="qb-upload-queue" aria-label="本次文件队列">
      <li v-for="file in queuedFiles" :key="file.name">
        <span>{{ file.name }}</span><strong>{{ file.state }}</strong>
      </li>
    </ul>

    <details class="qb-job-history">
      <summary>查看任务记录（{{ jobs.length }}）</summary>
      <div v-if="jobs.length" class="qb-job-list">
        <article v-for="job in jobs" :key="job.id" class="qb-job">
          <header>
            <div>
              <strong>{{ job.job_type === 'tagging_sync' ? 'AI 标注' : '试卷导入' }} #{{ job.id }}</strong>
              <span>{{ statusLabels[job.status] || job.status }} · {{ Math.round(job.progress * 100) }}%</span>
            </div>
            <progress :value="job.progress" max="1">{{ Math.round(job.progress * 100) }}%</progress>
          </header>
          <p>{{ jobCompletionNote(job) }}</p>
          <p v-if="job.result.outcome === 'partial'" class="qb-feedback is-warning">
            部分完成：成功
            {{ safeCount(job.result, 'tagged_count') || safeCount(job.result, 'question_count') }}，
            跳过 {{ safeCount(job.result, 'skipped_complete_count') }}，
            失败 {{ safeCount(job.result, 'failed_count') }}。
          </p>
          <p
            v-if="safeCount(job.result, 'criteria_needs_review_count') > 0"
            class="qb-feedback is-warning"
          >
            待审核判定点 {{ safeCount(job.result, 'criteria_needs_review_count') }} 道；
            审核通过前不会显示为成功。
          </p>
          <p
            v-if="job.job_type === 'question_import' && job.result.restore_required === true"
            class="qb-feedback is-warning"
          >
            相同试卷已在回收站。请恢复原试卷，避免重新入库后产生重复题目。
          </p>
          <p v-if="job.error" class="qb-feedback is-error">{{ job.error }}</p>
          <p v-if="jobStore.syncErrors[job.id]" class="qb-feedback is-error" role="alert">
            {{ jobStore.syncErrors[job.id]?.message }}
            <template v-if="jobStore.syncErrors[job.id]?.requestId">
              请求编号：{{ jobStore.syncErrors[job.id]?.requestId }}
            </template>
          </p>
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
              v-if="job.job_type === 'question_import' && job.result.restore_required === true"
              type="button"
              class="qb-link"
              :disabled="busy"
              @click="restoreRequiredPaper(job)"
            >
              恢复原试卷
            </button>
            <button
              v-if="questionJobFailures(job.result).length"
              type="button"
              class="qb-link"
              @click="downloadFailures(job)"
            >
              下载失败清单
            </button>
            <button
              v-if="
                TERMINAL_JOB_STATUSES.has(job.status) &&
                (
                  job.job_type === 'question_import' ||
                  questionJobRetryIds(job).length > 0
                ) &&
                (
                  job.status !== 'succeeded' ||
                  job.result.outcome === 'partial' ||
                  job.result.restore_required === true
                ) && job.result.restore_required !== true
              "
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
  </section>
</template>
