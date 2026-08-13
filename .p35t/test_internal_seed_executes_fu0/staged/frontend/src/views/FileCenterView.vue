<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type {
  ReportFileStatus,
  ReportHistoryJob,
  ReportType,
  TrainingExportRequest,
} from '../api/exports'
import type { JobResponse, JobStatus } from '../api/jobs'
import { useFileCenterStore } from '../stores/file-center'
import { useJobStore } from '../stores/jobs'
import { useSessionStore } from '../stores/session'

const props = withDefaults(defineProps<{
  embedded?: boolean
}>(), {
  embedded: false,
})

const sessionStore = useSessionStore()
const fileCenter = useFileCenterStore()
const jobStore = useJobStore()
const actionMessage = ref('')
const actionError = ref('')
const trainingPanelOpen = ref(false)
const trainingMode = ref<'bundle' | 'variant'>('bundle')
const trainingVariantId = ref('')
const trainingFormat = ref<'docx' | 'markdown'>('docx')
const trainingAudience = ref<'student' | 'teacher'>('student')
type ReportDisplayStatus = ReportFileStatus | 'stale'

const reportDefinitions: Array<{
  type: ReportType
  eyebrow: string
  title: string
  description: string
}> = [
  {
    type: 'score_excel',
    eyebrow: '表格',
    title: '成绩表',
    description: '用于汇总、复核和后续统计的 Excel 成绩文件。',
  },
  {
    type: 'annotated_original_pdf',
    eyebrow: '原卷',
    title: '批注原卷',
    description: '保留原始试卷版面和批改标记的 PDF 文件。',
  },
]

const liveJobs = computed(() => Object.values(jobStore.jobs)
  .filter((job) => job.job_type === 'report_export' || job.job_type === 'training_export')
  .sort((left, right) => right.id - left.id))

watch(
  () => sessionStore.selectedSessionId,
  (sessionId) => {
    actionMessage.value = ''
    actionError.value = ''
    if (sessionId === null) {
      fileCenter.reset()
      return
    }
    void fileCenter.load(sessionId)
  },
  { immediate: true },
)

function reportJobs(type: ReportType): ReportHistoryJob[] {
  return (fileCenter.reportContext?.jobs ?? [])
    .filter((job) => job.payload.report_type === type)
}

function latestReport(type: ReportType): ReportHistoryJob | null {
  const jobs = reportJobs(type)
  return jobs.find((job) => job.is_current_revision) ?? jobs[0] ?? null
}

function reportFilename(job: ReportHistoryJob): string {
  return typeof job.result.filename === 'string'
    ? job.result.filename
    : reportTypeLabel(job.payload.report_type)
}

function reportTypeLabel(value: unknown): string {
  if (value === 'score_excel') return '成绩表'
  if (value === 'annotated_original_pdf') return '批注原卷'
  return '报表文件'
}

function statusLabel(status: JobStatus): string {
  return {
    queued: '等待生成',
    running: '正在生成',
    paused: '已暂停',
    succeeded: '生成完成',
    failed: '生成失败',
    cancelled: '已取消',
  }[status]
}

function reportDisplayStatus(job: ReportHistoryJob): ReportDisplayStatus {
  return job.is_current_revision ? job.file_status : 'stale'
}

function fileStatusLabel(status: ReportDisplayStatus): string {
  return {
    pending: '正在准备',
    available: '可下载',
    expired: '文件已过期，可重新生成',
    failed: '生成失败',
    cancelled: '已取消',
    unavailable: '文件暂不可用',
    stale: '成绩已变化，需重新生成',
  }[status]
}

function statusTone(status: JobStatus | ReportDisplayStatus): string {
  if (status === 'succeeded' || status === 'available') return 'success'
  if (
    status === 'failed'
    || status === 'expired'
    || status === 'unavailable'
    || status === 'stale'
  ) return 'danger'
  if (status === 'cancelled') return 'muted'
  return 'progress'
}

function formatTime(value: string | null): string {
  if (!value) return '时间未记录'
  return value.replace('T', ' ').replace('Z', '').slice(0, 19)
}

async function generateReport(type: ReportType, forceRegenerate = false): Promise<void> {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId === null) return
  actionError.value = ''
  actionMessage.value = ''
  try {
    const job = await fileCenter.submitReport(sessionId, type, forceRegenerate)
    actionMessage.value = job.status === 'succeeded'
      ? '已有可用文件，已保留原文件。'
      : `${reportTypeLabel(type)}已加入生成队列。`
  } catch {
    actionError.value = '文件生成请求未能提交，请刷新登记簿后重试。'
  }
}

async function exportTraining(taskId: number): Promise<void> {
  actionError.value = ''
  actionMessage.value = ''
  try {
    await fileCenter.submitTrainingBundle(taskId)
    actionMessage.value = '训练材料已加入生成队列。'
  } catch {
    actionError.value = '训练材料生成请求未能提交，请稍后重试。'
  }
}

async function configureTraining(taskId: number): Promise<void> {
  actionError.value = ''
  try {
    const detail = await fileCenter.loadTrainingTask(taskId)
    if (!detail || fileCenter.selectedTrainingTask?.id !== taskId) return
    trainingPanelOpen.value = true
    trainingMode.value = 'bundle'
    trainingFormat.value = 'docx'
    trainingAudience.value = 'student'
    trainingVariantId.value = String(detail.variants[0]?.id ?? '')
  } catch {
    actionError.value = '训练任务详情暂时无法读取，请稍后重试。'
  }
}

async function submitTrainingChoice(): Promise<void> {
  const task = fileCenter.selectedTrainingTask
  if (!task) return
  const body: TrainingExportRequest = {
    format: trainingFormat.value,
  }
  if (trainingMode.value === 'variant') {
    const variantId = Number(trainingVariantId.value)
    if (!Number.isSafeInteger(variantId) || variantId <= 0) {
      actionError.value = '请选择一个训练版本。'
      return
    }
    body.variant_id = variantId
    body.audience = trainingAudience.value
  }
  actionError.value = ''
  actionMessage.value = ''
  try {
    await fileCenter.submitTraining(task.id, body)
    actionMessage.value = trainingMode.value === 'bundle'
      ? '整任务训练材料已加入生成队列。'
      : '所选训练版本已加入生成队列。'
  } catch {
    actionError.value = '训练材料生成请求未能提交，请稍后重试。'
  }
}

async function retryTraining(jobId: number): Promise<void> {
  actionError.value = ''
  actionMessage.value = ''
  try {
    await fileCenter.retryTrainingExport(jobId)
    actionMessage.value = '训练材料已重新加入生成队列。'
  } catch {
    actionError.value = '该训练材料暂时无法重试，请刷新登记簿后再试。'
  }
}

async function download(jobId: number): Promise<void> {
  actionError.value = ''
  actionMessage.value = ''
  try {
    const downloaded = await fileCenter.download(jobId)
    const url = URL.createObjectURL(downloaded.blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = downloaded.filename
    document.body.append(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
    actionMessage.value = `已开始下载：${downloaded.filename}`
  } catch {
    actionError.value = '文件已失效或暂时无法下载，可以重新生成后再试。'
  }
}

async function cancelJob(jobId: number): Promise<void> {
  actionError.value = ''
  actionMessage.value = ''
  try {
    await fileCenter.cancelTrackedJob(jobId)
    actionMessage.value = '已提交取消请求，任务停止后状态会自动更新。'
  } catch {
    actionError.value = '当前任务暂时无法取消，请刷新状态后再试。'
  }
}

function refresh(): void {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId !== null) void fileCenter.load(sessionId)
}

function isTrainingDownloadable(job: JobResponse): boolean {
  return job.status === 'succeeded'
    && typeof job.result.download_url === 'string'
}
</script>

<template>
  <section
    class="file-center"
    :class="{ 'file-center--embedded': props.embedded }"
    :aria-labelledby="props.embedded ? undefined : 'file-center-title'"
    :aria-label="props.embedded ? '导出文件' : undefined"
  >
    <header v-if="!props.embedded" class="file-center__hero">
      <div>
        <p class="file-center__eyebrow">安全出件登记簿</p>
        <h1 id="file-center-title" tabindex="-1">文件中心</h1>
        <p>统一生成和下载成绩表、批注原卷与训练材料，不显示本机存放位置。</p>
      </div>
      <div class="file-center__hero-actions">
        <span v-if="fileCenter.updatedAt">更新于 {{ formatTime(fileCenter.updatedAt) }}</span>
        <button type="button" class="file-button file-button--secondary" @click="refresh">
          刷新登记簿
        </button>
      </div>
    </header>

    <div v-if="sessionStore.selectedSessionId === null" class="file-center__empty">
      <strong>请先在顶部选择考试</strong>
      <span>选择后，这里会显示该考试的成绩表和批注原卷。</span>
    </div>

    <template v-else>
      <p v-if="actionMessage" class="file-center__notice" role="status">{{ actionMessage }}</p>
      <p v-if="actionError" class="file-center__error" role="alert">{{ actionError }}</p>
      <div v-if="fileCenter.state === 'stale-error'" class="file-center__warning" role="alert">
        <span>当前显示上次成功读取的记录，最新状态暂时无法取得。</span>
        <button type="button" class="file-link-button" @click="refresh">重新加载</button>
      </div>
      <div v-else-if="fileCenter.state === 'error'" class="file-center__error" role="alert">
        <span>{{ fileCenter.errorMessage }}</span>
        <button type="button" class="file-link-button" @click="refresh">重新加载</button>
      </div>

      <section class="file-section" aria-labelledby="report-files-title">
        <div class="file-section__heading">
          <div>
            <p class="file-center__eyebrow">当前考试</p>
            <h2 id="report-files-title">考试文件</h2>
          </div>
          <span class="file-section__context">{{ sessionStore.currentSession?.name }}</span>
        </div>

        <div class="file-product-grid">
          <article
            v-for="definition in reportDefinitions"
            :key="definition.type"
            class="file-product"
          >
            <div class="file-product__topline">
              <span>{{ definition.eyebrow }}</span>
              <span
                v-if="latestReport(definition.type)"
                class="file-status"
                :class="`file-status--${statusTone(reportDisplayStatus(latestReport(definition.type)!))}`"
              >
                {{ fileStatusLabel(reportDisplayStatus(latestReport(definition.type)!)) }}
              </span>
              <span v-else class="file-status file-status--muted">尚未生成</span>
            </div>
            <h3>{{ definition.title }}</h3>
            <p>{{ definition.description }}</p>
            <p
              v-if="latestReport(definition.type) && reportDisplayStatus(latestReport(definition.type)!) === 'available'"
              class="file-product__filename"
            >
              {{ reportFilename(latestReport(definition.type)!) }}
            </p>
            <div class="file-product__actions">
              <button
                v-if="latestReport(definition.type) && reportDisplayStatus(latestReport(definition.type)!) === 'available'"
                type="button"
                class="file-button file-button--primary"
                :data-testid="`download-report-${latestReport(definition.type)!.id}`"
                @click="download(latestReport(definition.type)!.id)"
              >
                下载
              </button>
              <button
                v-else
                type="button"
                class="file-button file-button--primary"
                :data-testid="`generate-${definition.type}`"
                :disabled="
                  fileCenter.submittingKey === `report:${definition.type}`
                  || fileCenter.reportContext?.has_results === false
                "
                @click="generateReport(definition.type)"
              >
                生成文件
              </button>
              <button
                v-if="latestReport(definition.type) && reportDisplayStatus(latestReport(definition.type)!) === 'available'"
                type="button"
                class="file-button file-button--secondary"
                :data-testid="`regenerate-${definition.type}`"
                @click="generateReport(definition.type, true)"
              >
                重新生成
              </button>
            </div>
          </article>
        </div>
        <p
          v-if="fileCenter.reportContext?.has_results === false"
          class="file-center__warning"
          role="status"
        >
          当前考试还没有已保存成绩，完成批改后才能生成考试文件。
        </p>

        <div class="file-ledger">
          <h3>报表记录</h3>
          <div v-if="fileCenter.state === 'loading' && !fileCenter.reportContext" class="file-ledger__empty" role="status">
            正在读取文件记录…
          </div>
          <div v-else-if="!fileCenter.reportContext?.jobs.length" class="file-ledger__empty">
            还没有报表记录，生成后会显示在这里。
          </div>
          <ul v-else class="file-ledger__list">
            <li v-for="job in fileCenter.reportContext.jobs" :key="job.id">
              <div>
                <strong>{{ reportTypeLabel(job.payload.report_type) }}</strong>
                <span>{{ formatTime(job.created_at) }} · 任务 #{{ job.id }}</span>
              </div>
              <span class="file-status" :class="`file-status--${statusTone(reportDisplayStatus(job))}`">
                {{ fileStatusLabel(reportDisplayStatus(job)) }}
              </span>
              <button
                v-if="reportDisplayStatus(job) === 'available'"
                type="button"
                class="file-link-button"
                @click="download(job.id)"
              >
                下载
              </button>
              <button
                v-else-if="
                  reportDisplayStatus(job) === 'expired'
                  || reportDisplayStatus(job) === 'unavailable'
                  || reportDisplayStatus(job) === 'stale'
                "
                type="button"
                class="file-link-button"
                @click="generateReport(job.payload.report_type as ReportType, true)"
              >
                重新生成
              </button>
            </li>
          </ul>
        </div>
      </section>

      <section class="file-section" aria-labelledby="training-files-title">
        <div class="file-section__heading">
          <div>
            <p class="file-center__eyebrow">练习与讲评</p>
            <h2 id="training-files-title">训练材料</h2>
          </div>
          <span class="file-section__context">默认生成 Word 文件包</span>
        </div>

        <div v-if="!fileCenter.trainingTasks.length" class="file-ledger__empty">
          暂无可出件的训练任务。
        </div>
        <div v-else class="training-task-grid">
          <article v-for="task in fileCenter.trainingTasks" :key="task.id" class="training-task">
            <div>
              <span class="training-task__code">{{ task.task_code }}</span>
              <h3>训练任务 #{{ task.id }}</h3>
              <p>状态：{{ task.status }}</p>
            </div>
            <button
              type="button"
              class="file-button file-button--secondary"
              :data-testid="`export-training-${task.id}`"
              :disabled="fileCenter.submittingKey === `training:${task.id}`"
              @click="exportTraining(task.id)"
            >
              整任务打包
            </button>
            <button
              type="button"
              class="file-button file-button--primary"
              :data-testid="`configure-training-${task.id}`"
              @click="configureTraining(task.id)"
            >
              选择版本与格式
            </button>
          </article>
        </div>

        <form
          v-if="trainingPanelOpen && fileCenter.selectedTrainingTask"
          class="training-export-form"
          data-testid="training-export-form"
          @submit.prevent="submitTrainingChoice"
        >
          <div class="training-export-form__heading">
            <div>
              <p class="file-center__eyebrow">出件设置</p>
              <h3>{{ fileCenter.selectedTrainingTask.task_code }}</h3>
            </div>
            <button type="button" class="file-link-button" @click="trainingPanelOpen = false">
              收起
            </button>
          </div>
          <div class="training-export-form__fields">
            <label>
              <span>出件范围</span>
              <select v-model="trainingMode" data-testid="training-mode">
                <option value="bundle">整任务材料包</option>
                <option value="variant">指定训练版本</option>
              </select>
            </label>
            <label v-if="trainingMode === 'variant'">
              <span>训练版本</span>
              <select v-model="trainingVariantId" data-testid="training-variant">
                <option
                  v-for="variant in fileCenter.selectedTrainingTask.variants"
                  :key="variant.id"
                  :value="String(variant.id)"
                >
                  {{ variant.variant_key }}（#{{ variant.id }}）
                </option>
              </select>
            </label>
            <label>
              <span>文件格式</span>
              <select v-model="trainingFormat" data-testid="training-format">
                <option value="docx">Word（DOCX）</option>
                <option value="markdown">Markdown</option>
              </select>
            </label>
            <label v-if="trainingMode === 'variant'">
              <span>使用版本</span>
              <select v-model="trainingAudience" data-testid="training-audience">
                <option value="student">学生版</option>
                <option value="teacher">教师版</option>
              </select>
            </label>
          </div>
          <div class="training-export-form__actions">
            <span>
              {{ trainingMode === 'bundle' ? '整任务会生成一个材料包。' : '指定版本需同时选择学生版或教师版。' }}
            </span>
            <button
              type="submit"
              class="file-button file-button--primary"
              data-testid="submit-training-choice"
              :disabled="fileCenter.submittingKey === `training:${fileCenter.selectedTrainingTask.id}`"
            >
              开始生成
            </button>
          </div>
        </form>

        <div class="file-ledger">
          <h3>训练材料记录</h3>
          <div v-if="!fileCenter.trainingJobs.length" class="file-ledger__empty">
            还没有训练材料生成记录。
          </div>
          <ul v-else class="file-ledger__list">
            <li v-for="job in fileCenter.trainingJobs" :key="job.id">
              <div>
                <strong>训练任务 #{{ job.payload.task_id }}</strong>
                <span>{{ formatTime(job.created_at) }} · 任务 #{{ job.id }}</span>
              </div>
              <span class="file-status" :class="`file-status--${statusTone(job.status)}`">
                {{ statusLabel(job.status) }}
              </span>
              <button
                v-if="isTrainingDownloadable(job)"
                type="button"
                class="file-link-button"
                :data-testid="`download-training-${job.id}`"
                @click="download(job.id)"
              >
                下载
              </button>
              <button
                v-else-if="job.status === 'failed' || job.status === 'cancelled'"
                type="button"
                class="file-link-button"
                :data-testid="`retry-training-${job.id}`"
                @click="retryTraining(job.id)"
              >
                重试
              </button>
            </li>
          </ul>
        </div>
      </section>

      <section v-if="liveJobs.length" class="file-section file-section--compact" aria-labelledby="live-jobs-title">
        <div class="file-section__heading">
          <div>
            <p class="file-center__eyebrow">本机恢复</p>
            <h2 id="live-jobs-title">正在跟踪</h2>
          </div>
        </div>
        <ul class="file-ledger__list">
          <li v-for="job in liveJobs" :key="job.id">
            <div>
              <strong>{{ job.job_type === 'report_export' ? reportTypeLabel(job.payload.report_type) : '训练材料' }}</strong>
              <span>任务 #{{ job.id }} · {{ Math.round(job.progress * 100) }}%</span>
            </div>
            <span class="file-status" :class="`file-status--${statusTone(job.status)}`">
              {{ statusLabel(job.status) }}
            </span>
            <button
              v-if="job.status === 'queued' || job.status === 'running' || job.status === 'paused'"
              type="button"
              class="file-link-button file-link-button--danger"
              :data-testid="`cancel-job-${job.id}`"
              @click="cancelJob(job.id)"
            >
              取消
            </button>
          </li>
        </ul>
      </section>
    </template>
  </section>
</template>
