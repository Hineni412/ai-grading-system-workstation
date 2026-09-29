<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type {
  AnalysisPreflight,
  ReportFileStatus,
  ReportHistoryJob,
  ReportType,
  ScoreExcelOptions,
} from '../api/exports'
import { exportsApi } from '../api/exports'
import {
  TERMINAL_JOB_STATUSES,
  type JobResponse,
  type JobStatus,
} from '../api/jobs'
import { useFileCenterStore } from '../stores/file-center'
import { useJobStore } from '../stores/jobs'
import { useResultsCenterStore } from '../stores/results-center'
import { useSessionStore } from '../stores/session'

const props = withDefaults(defineProps<{
  embedded?: boolean
  variant?: 'page' | 'popover'
}>(), {
  embedded: false,
  variant: 'page',
})

const isPopover = computed(() => props.variant === 'popover')
const chromeless = computed(() => props.embedded || isPopover.value)

const sessionStore = useSessionStore()
const fileCenter = useFileCenterStore()
const jobStore = useJobStore()
const resultsStore = useResultsCenterStore()
const actionMessage = ref('')
const actionError = ref('')
const excelSettingsOpen = ref(false)
const excelForceRegenerate = ref(false)
const hideBottomEnabled = ref(true)
const hideBottomN = ref(8)
const manualHideEnabled = ref(false)
const manualHiddenStudentIds = ref<number[]>([])
const manualStudentSearch = ref('')
const analysisConfirmOpen = ref(false)
const analysisPreflight = ref<AnalysisPreflight | null>(null)
const analysisPreflightLoading = ref(false)
const analysisPreflightType = ref<ReportType | null>(null)
const analysisForceRegenerate = ref(false)
type ReportDisplayStatus = ReportFileStatus | 'stale'

const ANALYSIS_REPORT_TYPES = new Set<ReportType>([
  'personal_analysis_html',
])

const eligibleExcelStudents = computed(() => (
  resultsStore.results?.students ?? []
).filter((student) => (
  student.ungraded_count === 0
  && student.failed_count === 0
)))

const filteredManualStudents = computed(() => {
  const query = manualStudentSearch.value.trim().toLocaleLowerCase()
  if (!query) return eligibleExcelStudents.value
  return eligibleExcelStudents.value.filter((student) => [
    student.student_name,
    student.student_code,
    student.class_name,
  ].some((value) => value?.toLocaleLowerCase().includes(query)))
})

const automaticHiddenStudentIds = computed(() => {
  if (!hideBottomEnabled.value) return new Set<number>()
  const safeN = Math.max(0, Math.min(100, Math.trunc(hideBottomN.value || 0)))
  if (safeN === 0) return new Set<number>()
  const byClass = new Map<string, typeof eligibleExcelStudents.value>()
  for (const student of eligibleExcelStudents.value) {
    const className = student.class_name?.trim() || '未分班'
    const group = byClass.get(className) ?? []
    group.push(student)
    byClass.set(className, group)
  }
  const hidden = new Set<number>()
  for (const group of byClass.values()) {
    if (group.length <= safeN) continue
    const ordered = [...group].sort((left, right) => (
      left.current_score - right.current_score
      || (left.student_code ?? '').localeCompare(right.student_code ?? '', 'zh-CN')
      || left.student_name.localeCompare(right.student_name, 'zh-CN')
      || left.student_id - right.student_id
    ))
    for (const student of ordered.slice(0, safeN)) hidden.add(student.student_id)
  }
  return hidden
})

const previewHiddenStudentIds = computed(() => {
  const hidden = new Set(automaticHiddenStudentIds.value)
  if (manualHideEnabled.value) {
    for (const studentId of manualHiddenStudentIds.value) hidden.add(studentId)
  }
  return hidden
})

const previewVisibleStudentCount = computed(() => Math.max(
  0,
  eligibleExcelStudents.value.length - previewHiddenStudentIds.value.size,
))

const reportDefinitions: Array<{
  type: ReportType
  kind: string
  title: string
  description: string
}> = [
  {
    type: 'score_excel',
    kind: '表格',
    title: '成绩表',
    description: '用于汇总、复核和后续统计的 Excel 成绩文件。',
  },
  {
    type: 'annotated_original_pdf',
    kind: 'PDF',
    title: '批注原卷',
    description: '保留原始试卷版面和批改标记的 PDF 文件。',
  },
  {
    type: 'personal_analysis_html',
    kind: 'AI 分析',
    title: '学生个人分析报告',
    description: '每名学生一份自包含 HTML 分析报告，打包为 ZIP，含 AI 生成的个性化叙述。',
  },
]

const historyOpen = ref(new Set<ReportType>())

function toggleHistory(type: ReportType): void {
  const next = new Set(historyOpen.value)
  if (next.has(type)) next.delete(type)
  else next.add(type)
  historyOpen.value = next
}

const liveJobs = computed(() => Object.values(jobStore.jobs)
  .filter((job) => job.job_type === 'report_export')
  .sort((left, right) => right.id - left.id))

function liveJobFor(type: ReportType): JobResponse | null {
  return liveJobs.value.find(
    (job) => job.payload.report_type === type
      && !TERMINAL_JOB_STATUSES.has(job.status),
  ) ?? null
}

const reportRows = computed(() => reportDefinitions.map((definition) => {
  const jobs = [...reportJobs(definition.type)]
    .sort((left, right) => right.created_at.localeCompare(left.created_at))
  const latest = jobs.find((job) => job.is_current_revision) ?? jobs[0] ?? null
  return {
    ...definition,
    jobs,
    latest,
    history: latest === null ? jobs : jobs.filter((job) => job.id !== latest.id),
    liveJob: liveJobFor(definition.type),
  }
}))

function reportLiveDetail(job: JobResponse): string {
  return typeof job.detail === 'string' && job.detail.length > 0
    ? job.detail
    : ''
}
const pendingReportStatusSignal = computed(() => (
  fileCenter.reportContext?.jobs ?? []
)
  .filter((job) => !TERMINAL_JOB_STATUSES.has(job.status))
  .map((job) => {
    const live = jobStore.jobs[job.id]
    return `${job.id}:${live?.status ?? job.status}:${live?.updated_at ?? job.updated_at}`
  })
  .join('|'))
const refreshedTerminalReportIds = new Set<number>()

watch(
  () => sessionStore.selectedSessionId,
  (sessionId) => {
    actionMessage.value = ''
    actionError.value = ''
    excelSettingsOpen.value = false
    analysisConfirmOpen.value = false
    manualHiddenStudentIds.value = []
    manualStudentSearch.value = ''
    historyOpen.value = new Set()
    refreshedTerminalReportIds.clear()
    if (sessionId === null) {
      fileCenter.reset()
      return
    }
    void fileCenter.load(sessionId)
  },
  { immediate: true },
)

watch(pendingReportStatusSignal, () => {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId === null) return
  const newlyCompleted = (fileCenter.reportContext?.jobs ?? [])
    .filter((snapshot) => {
      if (TERMINAL_JOB_STATUSES.has(snapshot.status)
        || refreshedTerminalReportIds.has(snapshot.id)) return false
      const live = jobStore.jobs[snapshot.id]
      return live !== undefined && TERMINAL_JOB_STATUSES.has(live.status)
    })
  if (newlyCompleted.length === 0) return
  for (const job of newlyCompleted) refreshedTerminalReportIds.add(job.id)
  void fileCenter.load(sessionId)
})

function reportJobs(type: ReportType): ReportHistoryJob[] {
  return (fileCenter.reportContext?.jobs ?? [])
    .filter((job) => job.payload.report_type === type)
}

function reportFilename(job: ReportHistoryJob): string {
  return typeof job.result.filename === 'string'
    ? job.result.filename
    : reportTypeLabel(job.payload.report_type)
}

function reportTypeLabel(value: unknown): string {
  if (value === 'score_excel') return '成绩表'
  if (value === 'annotated_original_pdf') return '批注原卷'
  if (value === 'personal_analysis_html') return '学生个人分析报告'
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

function formatShortTime(value: string | null): string {
  if (!value) return ''
  const normalized = value.replace('T', ' ').replace('Z', '')
  return normalized.slice(5, 16)
}

async function generateReport(type: ReportType, forceRegenerate = false): Promise<void> {
  if (type === 'score_excel') {
    openExcelSettings(forceRegenerate)
    return
  }
  if (ANALYSIS_REPORT_TYPES.has(type)) {
    await openAnalysisConfirm(type, forceRegenerate)
    return
  }
  const sessionId = sessionStore.selectedSessionId
  if (sessionId === null) return
  actionError.value = ''
  actionMessage.value = ''
  try {
    const job = await fileCenter.submitReport(
      sessionId,
      type,
      forceRegenerate,
    )
    if (sessionStore.selectedSessionId !== sessionId) return
    await fileCenter.load(sessionId)
    if (sessionStore.selectedSessionId !== sessionId) return
    actionMessage.value = job.status === 'succeeded'
      ? '已有可下载文件。'
      : `${reportTypeLabel(type)}已加入生成队列。`
  } catch {
    if (sessionStore.selectedSessionId !== sessionId) return
    actionError.value = '文件生成请求未能提交，请刷新登记簿后重试。'
  }
}

function openExcelSettings(forceRegenerate = false): void {
  excelForceRegenerate.value = forceRegenerate
  excelSettingsOpen.value = true
  actionError.value = ''
}

async function openAnalysisConfirm(
  type: ReportType,
  forceRegenerate: boolean,
): Promise<void> {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId === null) return
  analysisPreflightType.value = type
  analysisForceRegenerate.value = forceRegenerate
  analysisPreflight.value = null
  analysisPreflightLoading.value = true
  analysisConfirmOpen.value = true
  actionError.value = ''
  try {
    const preflight = await exportsApi.getAnalysisPreflight(sessionId, type)
    if (sessionStore.selectedSessionId !== sessionId) return
    analysisPreflight.value = preflight
  } catch {
    if (sessionStore.selectedSessionId !== sessionId) return
    analysisConfirmOpen.value = false
    actionError.value = '分析报告生成条件暂时无法读取，请稍后重试。'
  } finally {
    if (sessionStore.selectedSessionId === sessionId) {
      analysisPreflightLoading.value = false
    }
  }
}

function closeAnalysisConfirm(): void {
  analysisConfirmOpen.value = false
  analysisPreflight.value = null
  analysisPreflightType.value = null
}

async function confirmAnalysis(): Promise<void> {
  const sessionId = sessionStore.selectedSessionId
  const type = analysisPreflightType.value
  if (sessionId === null || type === null) return
  if (analysisPreflightLoading.value || analysisPreflight.value?.configured !== true) return
  actionError.value = ''
  actionMessage.value = ''
  try {
    const job = await fileCenter.submitReport(
      sessionId,
      type,
      analysisForceRegenerate.value,
    )
    if (sessionStore.selectedSessionId !== sessionId) return
    await fileCenter.load(sessionId)
    if (sessionStore.selectedSessionId !== sessionId) return
    closeAnalysisConfirm()
    actionMessage.value = job.status === 'succeeded'
      ? '已有可下载文件。'
      : `${reportTypeLabel(type)}已加入生成队列。`
  } catch {
    if (sessionStore.selectedSessionId !== sessionId) return
    actionError.value = '分析报告生成请求未能提交，请稍后重试。'
  }
}

function formatTokenCount(value: number): string {
  return value.toLocaleString('zh-CN')
}

function closeExcelSettings(): void {
  excelSettingsOpen.value = false
  manualStudentSearch.value = ''
}

async function submitConfiguredScoreExcel(): Promise<void> {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId === null) return
  const safeBottomN = Math.max(
    0,
    Math.min(100, Math.trunc(hideBottomN.value || 0)),
  )
  hideBottomN.value = safeBottomN
  const eligibleIds = new Set(
    eligibleExcelStudents.value.map((student) => student.student_id),
  )
  const options: ScoreExcelOptions = {
    hide_bottom_enabled: hideBottomEnabled.value,
    hide_bottom_n: hideBottomEnabled.value ? safeBottomN : 0,
    manual_hidden_student_ids: manualHideEnabled.value
      ? [...new Set(manualHiddenStudentIds.value)]
        .filter((studentId) => eligibleIds.has(studentId))
        .sort((left, right) => left - right)
      : [],
  }
  actionError.value = ''
  actionMessage.value = ''
  try {
    const job = await fileCenter.submitReport(
      sessionId,
      'score_excel',
      excelForceRegenerate.value,
      options,
    )
    if (sessionStore.selectedSessionId !== sessionId) return
    await fileCenter.load(sessionId)
    if (sessionStore.selectedSessionId !== sessionId) return
    closeExcelSettings()
    actionMessage.value = job.status === 'succeeded'
      ? '已有相同设置的成绩表。'
      : '成绩表已按当前设置加入生成队列。'
  } catch {
    if (sessionStore.selectedSessionId !== sessionId) return
    actionError.value = '成绩表生成请求未能提交，请核对设置后重试。'
  }
}

function isRetainedReport(job: JobResponse): boolean {
  return job.payload.report_type === 'personal_analysis_html'
}

async function download(job: JobResponse): Promise<void> {
  actionError.value = ''
  actionMessage.value = ''
  try {
    const downloaded = await fileCenter.download(job.id)
    const url = URL.createObjectURL(downloaded.blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = downloaded.filename
    document.body.append(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
    actionMessage.value = isRetainedReport(job)
      ? `已开始下载：${downloaded.filename}。这份报告已留存在本机，之后可随时再次下载，不会重新产生费用。`
      : `已开始下载：${downloaded.filename}。本机副本随后删除，需要时请重新生成。`
    const sessionId = sessionStore.selectedSessionId
    if (sessionId !== null) {
      try {
        await fileCenter.load(sessionId)
      } catch {
        // 浏览器已拿到文件；登记簿刷新失败不改下载结果。
      }
    }
  } catch {
    actionError.value = '文件已失效或暂时无法下载，可以重新生成后再试。'
  }
}

async function deleteReport(job: ReportHistoryJob): Promise<void> {
  const confirmed = window.confirm(
    `删除「${reportFilename(job)}」后，本机不再保留这份报告；之后如需这份报告，要重新生成并再次产生模型调用费用。确认删除吗？`,
  )
  if (!confirmed) return
  actionError.value = ''
  actionMessage.value = ''
  try {
    const result = await fileCenter.deleteReport(job.id)
    actionMessage.value = result.deleted
      ? `已删除：${reportFilename(job)}，释放约 ${(result.freed_bytes / 1024 / 1024).toFixed(1)} MB。`
      : '这份报告此前已删除。'
  } catch {
    actionError.value = '这份报告暂时无法删除，请刷新登记簿后再试。'
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

const dialogOpen = computed(() => excelSettingsOpen.value || analysisConfirmOpen.value)

defineExpose({ dialogOpen })

</script>

<template>
  <section
    class="file-center"
    :class="{
      'file-center--embedded': props.embedded,
      'file-center--popover': isPopover,
    }"
    :aria-labelledby="chromeless ? undefined : 'file-center-title'"
    :aria-label="chromeless ? '导出文件' : undefined"
  >
    <header v-if="!chromeless" class="file-center__hero">
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

      <section
        class="file-section"
        :aria-labelledby="chromeless ? undefined : 'report-files-title'"
        :aria-label="chromeless ? '考试文件' : undefined"
      >
        <div v-if="!chromeless" class="file-section__heading">
          <h2 id="report-files-title">考试文件</h2>
        </div>

        <div
          v-if="fileCenter.state === 'loading' && !fileCenter.reportContext"
          class="file-ledger__empty"
          role="status"
        >
          正在读取文件记录…
        </div>
        <template v-else>
        <ul v-if="isPopover" class="file-center__rows">
          <li v-for="row in reportRows" :key="row.type" class="file-center__row">
            <div class="file-center__row-head">
              <strong>{{ row.title }}</strong>
              <span class="file-report-table__kind">{{ row.kind }}</span>
              <template v-if="row.liveJob">
                <span class="file-status file-status--progress">
                  {{ statusLabel(row.liveJob.status) }}
                  {{ Math.round(row.liveJob.progress * 100) }}%
                </span>
                <span v-if="reportLiveDetail(row.liveJob)" class="file-report-table__detail">
                  {{ reportLiveDetail(row.liveJob) }}
                </span>
              </template>
              <span
                v-else-if="row.latest"
                class="file-status"
                :class="`file-status--${statusTone(reportDisplayStatus(row.latest))}`"
              >
                {{ fileStatusLabel(reportDisplayStatus(row.latest)) }}
              </span>
              <span v-else class="file-status file-status--muted">尚未生成</span>
              <span v-if="row.latest" class="file-center__row-time">
                {{ formatShortTime(row.latest.finished_at ?? row.latest.created_at) }}
              </span>
            </div>
            <div class="file-center__row-actions">
              <button
                v-if="row.liveJob && !TERMINAL_JOB_STATUSES.has(row.liveJob.status)"
                type="button"
                class="file-link-button file-link-button--danger"
                :data-testid="`cancel-job-${row.liveJob.id}`"
                @click="cancelJob(row.liveJob.id)"
              >
                取消
              </button>
              <button
                v-if="row.latest && reportDisplayStatus(row.latest) === 'available'"
                type="button"
                class="file-button file-button--primary"
                :data-testid="`download-report-${row.latest.id}`"
                @click="download(row.latest)"
              >
                下载
              </button>
              <button
                v-if="row.latest && reportDisplayStatus(row.latest) === 'available'"
                type="button"
                class="file-button file-button--secondary"
                :data-testid="`regenerate-${row.type}`"
                :disabled="
                  fileCenter.submittingKey === `report:${row.type}`
                  || fileCenter.reportContext?.has_results === false
                "
                @click="generateReport(row.type, true)"
              >
                重新生成
              </button>
              <button
                v-else
                type="button"
                class="file-button file-button--secondary"
                :data-testid="`generate-${row.type}`"
                :disabled="
                  fileCenter.submittingKey === `report:${row.type}`
                  || fileCenter.reportContext?.has_results === false
                "
                @click="generateReport(row.type)"
              >
                {{ row.latest ? '重新生成' : '生成' }}
              </button>
              <button
                v-if="row.type === 'score_excel'"
                type="button"
                class="file-link-button"
                data-testid="configure-score-excel"
                :disabled="fileCenter.reportContext?.has_results === false"
                @click="openExcelSettings(false)"
              >
                导出设置
              </button>
              <button
                v-if="row.history.length"
                type="button"
                class="file-link-button"
                :aria-expanded="historyOpen.has(row.type)"
                @click="toggleHistory(row.type)"
              >
                历史 {{ row.history.length }} 份
              </button>
            </div>
            <ul v-if="row.history.length && historyOpen.has(row.type)" class="file-report-table__history-list">
              <li v-for="job in row.history" :key="job.id">
                <span class="file-report-table__history-name">
                  {{ reportFilename(job) }}
                  <small>{{ formatTime(job.created_at) }} · 任务 #{{ job.id }}</small>
                </span>
                <span class="file-status" :class="`file-status--${statusTone(reportDisplayStatus(job))}`">
                  {{ fileStatusLabel(reportDisplayStatus(job)) }}
                </span>
                <button
                  v-if="reportDisplayStatus(job) === 'available'"
                  type="button"
                  class="file-link-button"
                  :data-testid="`download-report-${job.id}`"
                  @click="download(job)"
                >
                  下载
                </button>
                <button
                  v-if="reportDisplayStatus(job) === 'available' && isRetainedReport(job)"
                  type="button"
                  class="file-link-button"
                  :data-testid="`delete-report-${job.id}`"
                  @click="deleteReport(job)"
                >
                  删除
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
          </li>
        </ul>
        <table v-else class="file-report-table">
          <thead>
            <tr>
              <th scope="col">文件</th>
              <th scope="col">状态</th>
              <th scope="col">最近生成</th>
              <th scope="col" class="file-report-table__actions-head">操作</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="row in reportRows" :key="row.type">
              <tr>
                <th scope="row" :title="row.description">
                  {{ row.title }}<span class="file-report-table__kind">{{ row.kind }}</span>
                  <span v-if="row.latest" class="file-report-table__filename">{{ reportFilename(row.latest) }}</span>
                </th>
                <td>
                  <template v-if="row.liveJob">
                    <span class="file-status file-status--progress">
                      {{ statusLabel(row.liveJob.status) }}
                      {{ Math.round(row.liveJob.progress * 100) }}%
                    </span>
                    <span v-if="reportLiveDetail(row.liveJob)" class="file-report-table__detail">
                      {{ reportLiveDetail(row.liveJob) }}
                    </span>
                  </template>
                  <span
                    v-else-if="row.latest"
                    class="file-status"
                    :class="`file-status--${statusTone(reportDisplayStatus(row.latest))}`"
                  >
                    {{ fileStatusLabel(reportDisplayStatus(row.latest)) }}
                  </span>
                  <span v-else class="file-status file-status--muted">尚未生成</span>
                </td>
                <td>{{ row.latest ? formatTime(row.latest.finished_at ?? row.latest.created_at) : '—' }}</td>
                <td class="file-report-table__actions">
                  <button
                    v-if="row.liveJob && !TERMINAL_JOB_STATUSES.has(row.liveJob.status)"
                    type="button"
                    class="file-link-button file-link-button--danger"
                    :data-testid="`cancel-job-${row.liveJob.id}`"
                    @click="cancelJob(row.liveJob.id)"
                  >
                    取消
                  </button>
                  <button
                    v-if="row.latest && reportDisplayStatus(row.latest) === 'available'"
                    type="button"
                    class="file-button file-button--primary"
                    :data-testid="`download-report-${row.latest.id}`"
                    @click="download(row.latest)"
                  >
                    下载
                  </button>
                  <button
                    v-if="row.latest && reportDisplayStatus(row.latest) === 'available'"
                    type="button"
                    class="file-button file-button--secondary"
                    :data-testid="`regenerate-${row.type}`"
                    :disabled="
                      fileCenter.submittingKey === `report:${row.type}`
                      || fileCenter.reportContext?.has_results === false
                    "
                    @click="generateReport(row.type, true)"
                  >
                    重新生成
                  </button>
                  <button
                    v-else
                    type="button"
                    class="file-button file-button--secondary"
                    :data-testid="`generate-${row.type}`"
                    :disabled="
                      fileCenter.submittingKey === `report:${row.type}`
                      || fileCenter.reportContext?.has_results === false
                    "
                    @click="generateReport(row.type)"
                  >
                    {{ row.latest ? '重新生成' : '生成' }}
                  </button>
                  <button
                    v-if="row.type === 'score_excel'"
                    type="button"
                    class="file-link-button"
                    data-testid="configure-score-excel"
                    :disabled="fileCenter.reportContext?.has_results === false"
                    @click="openExcelSettings(false)"
                  >
                    导出设置
                  </button>
                  <button
                    v-if="row.history.length"
                    type="button"
                    class="file-link-button"
                    :aria-expanded="historyOpen.has(row.type)"
                    @click="toggleHistory(row.type)"
                  >
                    历史 {{ row.history.length }} 份
                  </button>
                </td>
              </tr>
              <tr v-if="row.history.length && historyOpen.has(row.type)" class="file-report-table__history">
                <td colspan="4">
                  <ul class="file-report-table__history-list">
                    <li v-for="job in row.history" :key="job.id">
                      <span class="file-report-table__history-name">
                        {{ reportFilename(job) }}
                        <small>{{ formatTime(job.created_at) }} · 任务 #{{ job.id }}</small>
                      </span>
                      <span class="file-status" :class="`file-status--${statusTone(reportDisplayStatus(job))}`">
                        {{ fileStatusLabel(reportDisplayStatus(job)) }}
                      </span>
                      <button
                        v-if="reportDisplayStatus(job) === 'available'"
                        type="button"
                        class="file-link-button"
                        :data-testid="`download-report-${job.id}`"
                        @click="download(job)"
                      >
                        下载
                      </button>
                      <button
                        v-if="reportDisplayStatus(job) === 'available' && isRetainedReport(job)"
                        type="button"
                        class="file-link-button"
                        :data-testid="`delete-report-${job.id}`"
                        @click="deleteReport(job)"
                      >
                        删除
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
                </td>
              </tr>
            </template>
          </tbody>
        </table>
        </template>

        <div
          v-if="excelSettingsOpen"
          class="excel-settings-backdrop"
          data-testid="excel-settings-backdrop"
          @click.self="closeExcelSettings"
        >
          <form
            class="excel-settings-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="excel-settings-title"
            data-testid="excel-settings-dialog"
            @submit.prevent="submitConfiguredScoreExcel"
          >
            <div class="excel-settings-dialog__heading">
              <div>
                <p class="file-center__eyebrow">Excel 导出设置</p>
                <h3 id="excel-settings-title">精简打印姓名，不改变成绩统计</h3>
              </div>
              <button
                type="button"
                class="file-link-button"
                @click="closeExcelSettings"
              >
                关闭
              </button>
            </div>

            <p class="excel-settings-dialog__explanation">
              均分、得分率、失分人数和排名始终统计全部正常参考且已完整批改的学生。下面的选择只影响每道题打印哪些失分学生姓名。
            </p>

            <div class="excel-settings-preview" aria-label="导出设置预览">
              <div>
                <span>正式统计</span>
                <strong data-testid="excel-preview-statistical">
                  {{ eligibleExcelStudents.length }} 人
                </strong>
              </div>
              <div>
                <span>最多隐藏姓名</span>
                <strong data-testid="excel-preview-hidden">
                  {{ previewHiddenStudentIds.size }} 人
                </strong>
              </div>
              <div>
                <span>仍可显示姓名</span>
                <strong data-testid="excel-preview-visible">
                  {{ previewVisibleStudentCount }} 人
                </strong>
              </div>
            </div>

            <fieldset class="excel-settings-group">
              <legend>自动精简</legend>
              <label class="excel-settings-checkline">
                <input
                  v-model="hideBottomEnabled"
                  type="checkbox"
                  data-testid="excel-hide-bottom-enabled"
                >
                <span>每班隐藏总分最后</span>
                <input
                  v-model.number="hideBottomN"
                  type="number"
                  min="0"
                  max="100"
                  step="1"
                  data-testid="excel-hide-bottom-n"
                  :disabled="!hideBottomEnabled"
                  aria-label="每班隐藏最后人数"
                >
                <span>名</span>
              </label>
              <small>班级人数不超过填写人数时不会自动隐藏全班；同分时按学号和姓名稳定选取准确人数。</small>
            </fieldset>

            <fieldset class="excel-settings-group">
              <legend>手动补充</legend>
              <label class="excel-settings-checkline">
                <input
                  v-model="manualHideEnabled"
                  type="checkbox"
                  data-testid="excel-manual-enabled"
                >
                <span>另外手动隐藏指定学生姓名</span>
              </label>
              <template v-if="manualHideEnabled">
                <label class="excel-settings-search">
                  <span>查找学生</span>
                  <input
                    v-model="manualStudentSearch"
                    type="search"
                    placeholder="输入姓名、学号或班级"
                    data-testid="excel-student-search"
                  >
                </label>
                <div
                  v-if="filteredManualStudents.length"
                  class="excel-settings-students"
                  data-testid="excel-student-options"
                >
                  <label
                    v-for="student in filteredManualStudents"
                    :key="student.student_id"
                  >
                    <input
                      v-model="manualHiddenStudentIds"
                      type="checkbox"
                      :value="student.student_id"
                      :data-testid="`excel-student-${student.student_id}`"
                    >
                    <span>
                      <strong>{{ student.student_name }}</strong>
                      {{ student.student_code || '无学号' }} · {{ student.class_name || '未分班' }} · {{ student.current_score }} 分
                    </span>
                  </label>
                </div>
                <p v-else class="excel-settings-empty">
                  {{ eligibleExcelStudents.length ? '没有匹配的完整成绩学生。' : '完整成绩读取完成后，可在这里手动选择学生。' }}
                </p>
              </template>
            </fieldset>

            <div class="excel-settings-dialog__actions">
              <span>这些设置只用于本次导出的 Excel，不会修改成绩中心数据。</span>
              <div>
                <button
                  type="button"
                  class="file-button file-button--secondary"
                  @click="closeExcelSettings"
                >
                  取消
                </button>
                <button
                  type="submit"
                  class="file-button file-button--primary"
                  data-testid="submit-score-excel"
                  :disabled="fileCenter.submittingKey === 'report:score_excel'"
                >
                  生成成绩表
                </button>
              </div>
            </div>
          </form>
        </div>

        <div
          v-if="analysisConfirmOpen"
          class="excel-settings-backdrop"
          data-testid="analysis-confirm-backdrop"
          @click.self="closeAnalysisConfirm"
        >
          <form
            class="excel-settings-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="analysis-confirm-title"
            data-testid="analysis-confirm-dialog"
            @submit.prevent="confirmAnalysis"
          >
            <div class="excel-settings-dialog__heading">
              <div>
                <p class="file-center__eyebrow">AI 内容生成确认</p>
                <h3 id="analysis-confirm-title">
                  {{ analysisPreflightType ? reportTypeLabel(analysisPreflightType) : '分析报告' }}
                </h3>
              </div>
              <button
                type="button"
                class="file-link-button"
                @click="closeAnalysisConfirm"
              >
                关闭
              </button>
            </div>

            <p
              v-if="analysisPreflightLoading"
              class="excel-settings-dialog__explanation"
              role="status"
            >
              正在读取生成条件…
            </p>

            <template v-else-if="analysisPreflight">
              <p
                v-if="!analysisPreflight.configured"
                class="file-center__warning"
                role="alert"
                data-testid="analysis-not-configured"
              >
                未配置内容生成模型，请前往 设置→模型配置 绑定后重试。
              </p>

              <div class="excel-settings-preview" aria-label="生成条件概览">
                <div>
                  <span>目标服务</span>
                  <strong data-testid="analysis-service">
                    {{ analysisPreflight.service_name ?? '未配置' }}
                  </strong>
                </div>
                <div>
                  <span>模型</span>
                  <strong data-testid="analysis-model">
                    {{ analysisPreflight.model_name ?? '未配置' }}
                  </strong>
                </div>
                <div>
                  <span>模型调用次数</span>
                  <strong data-testid="analysis-call-count">
                    {{ analysisPreflight.call_count + analysisPreflight.cause_call_count }} 次
                  </strong>
                </div>
                <div>
                  <span>文本 token 量（粗略估算）</span>
                  <strong data-testid="analysis-tokens">
                    {{ formatTokenCount(analysisPreflight.estimated_total_tokens + analysisPreflight.cause_estimated_tokens) }}
                  </strong>
                </div>
              </div>

              <p
                v-if="analysisPreflight.cause_total_questions > 0"
                class="excel-settings-dialog__explanation"
                data-testid="analysis-cause-count"
              >
                其中先整理错因：{{ analysisPreflight.cause_call_count }} 次调用（共
                {{ analysisPreflight.cause_total_questions }} 道失分题，已整理或整理失败的题不重复调用）；
                报告叙述：{{ analysisPreflight.call_count }} 次调用。
              </p>

              <p
                v-if="analysisPreflight.cache_hits > 0"
                class="excel-settings-dialog__explanation"
                data-testid="analysis-cache-hits"
              >
                其中 {{ analysisPreflight.cache_hits }} 份复用已生成内容，不重复计费。
              </p>

              <p class="excel-settings-dialog__explanation">
                将向上述服务发送题目资料和学生答卷图片，请使用支持图片的内容生成模型。
                图片用量另计，实际费用取决于服务商定价。AI 分析内容仅供参考，最终成绩保持教师确认结果。
              </p>
            </template>

            <div class="excel-settings-dialog__actions">
              <span>确认后才会发起模型调用并产生费用。</span>
              <div>
                <button
                  type="button"
                  class="file-button file-button--secondary"
                  @click="closeAnalysisConfirm"
                >
                  取消
                </button>
                <button
                  type="submit"
                  class="file-button file-button--primary"
                  data-testid="confirm-analysis"
                  :disabled="
                    analysisPreflightLoading
                    || analysisPreflight?.configured !== true
                    || (analysisPreflightType !== null
                      && fileCenter.submittingKey === `report:${analysisPreflightType}`)
                  "
                >
                  确认生成
                </button>
              </div>
            </div>
          </form>
        </div>

        <p
          v-if="fileCenter.reportContext?.has_results === false"
          class="file-center__warning"
          role="status"
        >
          当前考试还没有已保存成绩，完成批改后才能生成考试文件。
        </p>
      </section>
    </template>
  </section>
</template>
