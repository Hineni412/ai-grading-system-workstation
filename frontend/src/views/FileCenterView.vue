<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import type {
  ReportHistoryJob,
  ReportType,
  ScoreExcelOptions,
} from '../api/exports'
import { storageApi } from '../api/ops'
import {
  TERMINAL_JOB_STATUSES,
  type JobResponse,
} from '../api/jobs'
import { personalReportsApi, type PersonalReportState } from '../api/personal-reports'
import PersonalReportExportDialog from '../components/results-center/PersonalReportExportDialog.vue'
import FileReportLedger from '../components/file-center/FileReportLedger.vue'
import ScoreExcelSettingsDialog from '../components/file-center/ScoreExcelSettingsDialog.vue'
import {
  reportFilename,
  reportTypeLabel,
} from '../components/file-center/report-format'
import { useConfirm } from '../composables/useConfirm'
import { formatBytes } from '../lib/format'
import { useFileCenterStore } from '../stores/file-center'
import { useJobStore } from '../stores/jobs'
import { useResultsCenterStore } from '../stores/results-center'
import { useSessionStore } from '../stores/session'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import AppButton from '../components/design-system/AppButton.vue'
import StatePanel from '../components/design-system/StatePanel.vue'

const props = withDefaults(defineProps<{
  embedded?: boolean
  variant?: 'page' | 'popover'
}>(), {
  embedded: false,
  variant: 'page',
})

const isPopover = computed(() => props.variant === 'popover')
const chromeless = computed(() => props.embedded || isPopover.value)

const router = useRouter()
const sessionStore = useSessionStore()
const { confirm } = useConfirm()
const curriculumScope = useCurriculumScopeStore()
const personalVolumeLabel = computed(() => curriculumScope.volumes.find(v => v.id === sessionStore.currentSession?.curriculum_volume_id)?.label)
const fileCenter = useFileCenterStore()
const jobStore = useJobStore()
const resultsStore = useResultsCenterStore()
const actionMessage = ref('')
const actionError = ref('')
const originalsAvailable = ref(true)
const excelSettingsOpen = ref(false)
const excelForceRegenerate = ref(false)
const hideBottomEnabled = ref(true)
const hideBottomN = ref(8)
const manualHideEnabled = ref(false)
const manualHiddenStudentIds = ref<number[]>([])
const manualStudentSearch = ref('')
const personalExportOpen = ref(false)
const personalStates = ref<PersonalReportState[]>([])
const personalStatesReady = ref(false)
let personalController: AbortController | null = null
const personalSummary = computed(() => personalStatesReady.value
  ? `本场：已生成 ${personalStates.value.filter(s => s.status === 'current').length} · 需重新生成 ${personalStates.value.filter(s => s.status === 'stale').length} · 未生成 ${personalStates.value.filter(s => s.status === 'missing').length} · 不可生成 ${personalStates.value.filter(s => s.status === 'unavailable').length}（人）`
  : '正在读取个人报告状态…')
const personalExamOptions = computed(() => sessionStore.sessions.filter(s => !s.is_deleted &&
  (sessionStore.currentSession?.curriculum_volume_id ? s.curriculum_volume_id === sessionStore.currentSession.curriculum_volume_id : s.id === sessionStore.selectedSessionId)))
async function refreshPersonal() {
  personalController?.abort()
  const sid = sessionStore.selectedSessionId
  if (sid === null) return
  const next = new AbortController()
  personalController = next
  try {
    const value = await personalReportsApi.states(sid, next.signal)
    if (!next.signal.aborted) { personalStates.value = value; personalStatesReady.value = true }
  } catch { if (!next.signal.aborted) actionError.value = '个人报告状态暂时无法读取，请重新加载。' }
}
watch(() => sessionStore.selectedSessionId, () => {
  personalStatesReady.value = false; personalStates.value = []; personalExportOpen.value = false; void refreshPersonal()
}, {immediate: true})
watch(() => resultsStore.updatedAt, () => { void refreshPersonal() })
const personalObservedJobs = new Set<number>()
watch(() => Object.values(jobStore.jobs).map(j => `${j.id}:${j.status}`).join('|'), () => {
  for (const job of Object.values(jobStore.jobs)) {
    const personalExport = job.job_type === 'report_export' && job.payload.report_type === 'personal_analysis_html'
    const pipeline = job.job_type === 'class_analysis_generate' && job.payload.session_id === sessionStore.selectedSessionId
    if (!personalExport && !pipeline) continue
    if (!TERMINAL_JOB_STATUSES.has(job.status)) personalObservedJobs.add(job.id)
    else if (personalObservedJobs.delete(job.id)) { void refreshPersonal(); void fileCenter.load(sessionStore.selectedSessionId!) }
  }
}, {immediate: true})
onBeforeUnmount(() => { personalController?.abort() })
function exportedPersonal() {
  personalExportOpen.value = false
  actionMessage.value = '已加入任务中心，完成后在任务中心下载。'
}
async function openPersonalExport() {
  const sid = sessionStore.selectedSessionId
  if (sid === null) return
  if (resultsStore.sessionId !== sid || !resultsStore.results) await resultsStore.load(sid)
  if (sessionStore.selectedSessionId !== sid) return
  if (!resultsStore.results || resultsStore.sessionId !== sid || resultsStore.state === 'error' || resultsStore.state === 'stale-error') {
    actionError.value = resultsStore.errorMessage || '学生名单暂时无法读取，请重新加载。'
    return
  }
  personalExportOpen.value = true
}

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
    description: '在线查看个人报告；AI 部分由成绩中心「AI 整理」生成。导出复用已生成内容，不调用模型。',
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
  .filter((job) => job.job_type === 'report_export' && job.payload.session_id === sessionStore.selectedSessionId)
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
  (sessionId, _previous, onCleanup) => {
    let cancelled = false
    onCleanup(() => { cancelled = true })
    originalsAvailable.value = true
    if (sessionId !== null) void storageApi.getOriginals(sessionId).then(value => {
      if (!cancelled) originalsAvailable.value = !['clearing', 'cleared'].includes(value.originals_state)
    }).catch(() => { /* Export service still enforces availability. */ })
    actionMessage.value = ''
    actionError.value = ''
    excelSettingsOpen.value = false
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

async function generateReport(type: ReportType, forceRegenerate = false): Promise<void> {
  if (type === 'score_excel') {
    openExcelSettings(forceRegenerate)
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

function closeExcelSettings(): void {
  if (fileCenter.submittingKey === 'report:score_excel') return
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
  const confirmed = await confirm({
    title: `删除「${reportFilename(job)}」？`,
    message: '删除后，本机不再保留这份报告；之后如需这份报告，要重新生成并再次产生模型调用费用。',
    confirmLabel: '删除',
    danger: true,
  })
  if (!confirmed) return
  actionError.value = ''
  actionMessage.value = ''
  try {
    const result = await fileCenter.deleteReport(job.id)
    actionMessage.value = result.deleted
      ? `已删除：${reportFilename(job)}，释放约 ${formatBytes(result.freed_bytes)}。`
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

function openReviewNotes(): void {
  const sessionId = sessionStore.selectedSessionId
  void router.push({
    path: '/results',
    query: {
      tab: 'overview',
      ...(sessionId === null ? {} : { session: String(sessionId) }),
    },
  })
}

const dialogOpen = computed(() => excelSettingsOpen.value || personalExportOpen.value)

defineExpose({ dialogOpen })

</script>

<template>
  <section
    class="file-center"
    :class="{
      'file-center--embedded': props.embedded,
      'file-center--popover': isPopover,
    }"
    :aria-label="'导出文件'"
  >
    <StatePanel
      v-if="sessionStore.selectedSessionId === null"
      kind="empty"
      title="请先选择考试"
      description="在左侧栏“当前考试”中选择。"
    />

    <template v-else>
      <p v-if="actionMessage" class="file-center__notice" role="status">{{ actionMessage }}</p>
      <p v-if="actionError" class="file-center__error" role="alert">{{ actionError }}</p>
      <div v-if="fileCenter.state === 'stale-error'" class="file-center__warning" role="alert">
        <span>当前显示上次成功读取的记录，最新状态暂时无法取得。</span>
        <AppButton variant="ghost" @click="refresh">重新加载</AppButton>
      </div>
      <StatePanel
        v-else-if="fileCenter.state === 'error'"
        kind="error"
        :title="fileCenter.errorMessage || '文件记录暂时无法读取'"
        retry-label="重新加载"
        @retry="refresh"
      />

      <section
        class="file-section"
        :aria-labelledby="chromeless ? undefined : 'report-files-title'"
        :aria-label="chromeless ? '考试文件' : undefined"
      >
        <div v-if="!chromeless" class="file-section__heading">
          <h2 id="report-files-title">考试文件</h2>
        </div>

        <StatePanel
          v-if="fileCenter.state === 'loading' && !fileCenter.reportContext"
          kind="loading"
          title="正在读取文件记录…"
        />
        <FileReportLedger :originals-available="originalsAvailable" :personal-summary="personalSummary"
          v-else
          :rows="reportRows"
          :history-open="historyOpen"
          :is-popover="isPopover"
          @generate="generateReport"
          @export-personal="openPersonalExport"
          @cancel-job="cancelJob"
          @download="download"
          @delete-report="deleteReport"
          @open-excel-settings="openExcelSettings(false)"
          @open-review-notes="openReviewNotes"
          @toggle-history="toggleHistory"
        />

        <PersonalReportExportDialog v-if="personalExportOpen && sessionStore.selectedSessionId !== null"
          :students="resultsStore.results?.students ?? []" :sessions="personalExamOptions" :session-id="sessionStore.selectedSessionId" :volume-label="personalVolumeLabel"
          @close="personalExportOpen = false" @submitted="exportedPersonal" />
        <ScoreExcelSettingsDialog
          v-if="excelSettingsOpen"
          v-model:hide-bottom-enabled="hideBottomEnabled"
          v-model:hide-bottom-n="hideBottomN"
          v-model:manual-hide-enabled="manualHideEnabled"
          v-model:manual-hidden-student-ids="manualHiddenStudentIds"
          v-model:manual-student-search="manualStudentSearch"
          :eligible-students="eligibleExcelStudents"
          :filtered-manual-students="filteredManualStudents"
          :hidden-count="previewHiddenStudentIds.size"
          :visible-count="previewVisibleStudentCount"
          :submitting="fileCenter.submittingKey === 'report:score_excel'"
          @close="closeExcelSettings"
          @submit="submitConfiguredScoreExcel"
        />

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
