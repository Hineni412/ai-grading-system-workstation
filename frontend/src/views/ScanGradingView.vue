<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { TERMINAL_JOB_STATUSES } from '../api/jobs'
import {
  fetchReviewQuestions,
  type ReviewQuestionSummary,
} from '../api/review'
import type {
  AutomatedGradingMode,
  GradingMode,
  GradingPlanMetrics,
  ScanDecision,
} from '../api/scan-grading'
import AppButton from '../components/design-system/AppButton.vue'
import StudentMatchSelect from '../components/scan-grading/StudentMatchSelect.vue'
import { BorderBeam } from '@/components/ui/border-beam'
import { useScanGradingStore } from '../stores/scan-grading'
import '../styles/scan-grading.css'

const route = useRoute()
const router = useRouter()
const store = useScanGradingStore()
const sessionId = computed(() => Number(route.params.sessionId))
const confirmPending = ref(false)
const selectedStudents = ref<Record<string, number | undefined>>({})
const gradingSubmissionPending = ref(false)
const runSection = ref<HTMLElement | null>(null)
const interventionQuestions = ref<ReviewQuestionSummary[]>([])
const interventionState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const viewerTargetKey = ref<string | null>(null)
const viewerSide = ref<'front' | 'back'>('front')
const viewerZoom = ref(1)
const viewerCanvas = ref<HTMLElement | null>(null)
let viewerPointerId: number | null = null
let viewerPointerX = 0
let viewerPointerY = 0
let viewerScrollLeft = 0
let viewerScrollTop = 0
const gradingStarting = computed(() => !store.gradingRun && (
  gradingSubmissionPending.value
  || store.busyAction === 'grading'
  || store.activeJobId !== null
))
const gradingCompletedWithoutRun = computed(() => !store.gradingRun
  && store.workspace?.grading_job?.status === 'succeeded'
  && store.workspace.grading_job.scan_batch_id === store.uploadBatch?.batch_id)
const editableUploadBatch = computed(() => store.replacementBatch ?? store.uploadBatch)
const preflightActive = computed(() => Boolean(
  store.preflightJob && !TERMINAL_JOB_STATUSES.has(store.preflightJob.status),
))
const preflightProgress = computed(() => Math.min(
  100,
  Math.max(0, Math.round((store.preflightJob?.progress ?? 0) * 100)),
))
const preflightProgressText = computed(() => {
  const detail = store.preflightJob?.detail.trim()
  if (detail && detail !== 'starting') return detail
  if (store.preflightJob?.status === 'queued') return '正在等待开始预检'
  return '正在准备答卷页面'
})

interface GradingModeOption {
  mode: GradingMode
  label: string
  title: string
  description: string
  note: string
}

const gradingModes: GradingModeOption[] = [
  {
    mode: 'full_paper',
    label: '整卷批改',
    title: '按考生提交整份答卷',
    description: '保留整张试卷的上下文，适合需要综合判断整卷作答的情况。',
    note: '会调用 AI · 每份答卷独立处理',
  },
  {
    mode: 'manual',
    label: '人工批改',
    title: '直接进入按题评分',
    description: '不调用 AI，使用现有评分工作台逐题查看全班答题区域。',
    note: 'AI 请求 0 · 教师分数为最终结果',
  },
  {
    mode: 'hybrid_batch',
    label: '混合批改',
    title: '客观题整块，解答题分组',
    description: '排除教师已完成题目，再按客观题整图和解答题答题框组织请求。',
    note: '会调用 AI · 执行前显示请求估算',
  },
]

const stage = computed(() => {
  if (
    store.gradingRun
    && ['completed', 'failed', 'cancelled'].includes(store.gradingRun.state)
  ) return 5
  if (gradingCompletedWithoutRun.value) return 5
  if (store.gradingRun || gradingStarting.value) return 4
  if (preflightActive.value) return 2
  if (store.preflight) return 3
  if (store.uploadBatch?.state === 'frozen') return 2
  return 1
})
const pendingCount = computed(() => store.preflight?.pending_issue_count ?? 0)
const matchConflicts = computed(() => store.preflight?.match_conflicts ?? [])
const decisionNotice = ref('')
const canPreviewPlan = computed(() => Boolean(store.preflight)
  && !store.gradingRun && !gradingStarting.value && !gradingCompletedWithoutRun.value
  && !store.busyAction && store.planState !== 'loading')
const canConfirmPlan = computed(() => Boolean(
  store.gradingPlan
  && store.gradingPlan.mode === store.selectedMode
  && store.gradingPlan.status === 'ready'
  && store.planState === 'ready'
  && matchConflicts.value.length === 0
  && (pendingCount.value === 0 || confirmPending.value)
  && !store.busyAction
  && !gradingStarting.value,
))
const invalidCount = computed(() => store.preflight?.decisions
  .filter((item) => item.action === 'invalid').length ?? 0)
const missingBackCount = computed(() => store.preflight?.issues
  .filter((item) => item.issue_type === 'missing_back' || item.issue_type === 'orphan_page').length ?? 0)
const preflightPageAssignment = computed(() => (
  store.preflight?.page_assignment
  ?? { first_page_role: 'front' as const, front_page_parity: 'odd' as const }
))
const lowConfidenceGroups = computed(() => store.preflight?.groups.filter((item) => (
  item.match_method !== 'exact' || Number(item.match_score ?? 0) < 1
  || matchConflicts.value.some((conflict) => conflict.targets.some((target) => target.target_type === 'group' && target.target_id === String(item.id)))
  || store.preflight?.decisions.some((decision) => decision.target_type === 'group' && decision.target_id === String(item.id))
)) ?? [])
const selectedMatches = computed<ScanDecision[]>(() => {
  const selected: ScanDecision[] = []
  for (const [targetType, items] of [
    ['group', lowConfidenceGroups.value], ['issue', store.preflight?.issues ?? []],
  ] as const) {
    for (const item of items) {
      const targetId = String(item.id)
      const studentId = selectedStudents.value[targetId]
      const saved = decisionFor(targetType, targetId)
      if (!studentId || !item.back_media_url || (saved?.action === 'match' && saved.student_id === studentId)) continue
      selected.push({ target_type: targetType, target_id: targetId, action: 'match', student_id: studentId })
    }
  }
  return selected
})
const confirmedDecisions = computed(() => store.preflight?.decisions
  .filter((item) => item.action !== 'pending') ?? [])
const runProcessed = computed(() => {
  const counts = store.gradingRun?.counts
  if (!counts) return 0
  return Math.max(0, counts.total - counts.pending - counts.grading)
})
const runProgress = computed(() => {
  const total = store.gradingRun?.counts.total ?? 0
  const savedPaperProgress = total > 0 ? Math.round((runProcessed.value / total) * 100) : 0
  const durableJobProgress = Math.round((store.gradingJob?.progress ?? 0) * 100)
  return Math.min(100, Math.max(savedPaperProgress, durableJobProgress))
})
const runProgressText = computed(() => {
  const total = store.gradingRun?.counts.total ?? 0
  if (total > 0 && runProcessed.value > 0) return `已保存 ${runProcessed.value} / ${total} 份答卷结果`
  if (store.gradingJob) return `后台批改进度 ${runProgress.value}%`
  return runProgressDetail.value
})
const runProgressDetail = computed(() => {
  const job = store.gradingJob
  const detail = job?.detail.trim() ?? ''
  const legacyTotal = detail.match(/^total=(\d+)$/i)
  if (legacyTotal) return `批改队列已建立，共 ${legacyTotal[1]} 份答卷`
  const stage = job?.stage ?? ''
  const stageLabels: Record<string, string> = {
    grading_starting: '正在建立本次批改队列',
    grading_queue_ready: '批改队列已建立，正在准备识别作答',
    grading_objective: '正在识别客观题作答',
    grading_subjective: '正在批改主观题',
    grading_saving: '正在保存批改成绩',
    grading_paused: '本次批改已安全暂停',
    grading_completed: '本次批改已完成',
    grading_cancelled: '本次批改已取消',
    grading_finished: '本次批改处理已结束',
  }
  if (stageLabels[stage]) return stageLabels[stage]
  if (/[\u3400-\u9fff]/u.test(detail)) return detail
  return '正在处理本次批改任务'
})
const interventionSummary = computed(() => interventionQuestions.value.reduce(
  (summary, question) => ({
    total: summary.total + question.total_count,
    ungraded: summary.ungraded + (question.ungraded_count ?? 0),
    failed: summary.failed + (question.failed_count ?? 0),
    review: summary.review + question.needs_review_count,
    aiReady: summary.aiReady + (question.ai_ready_count ?? 0),
    teacher: summary.teacher + (question.teacher_confirmed_count ?? 0),
  }),
  { total: 0, ungraded: 0, failed: 0, review: 0, aiReady: 0, teacher: 0 },
))
const hybridPhase = computed(() => {
  const stage = store.gradingJob?.stage ?? ''
  if (['grading_saving', 'grading_completed', 'grading_finished'].includes(stage)) return 4
  if (stage === 'grading_subjective') return 3
  if (stage === 'grading_objective') return 2
  return 1
})
const hybridAiPending = computed(() => (
  interventionSummary.value.ungraded + interventionSummary.value.failed
))
const runStateLabel = computed(() => ({
  running: '批改运行中', pause_requested: '正在安全暂停', paused: '已安全暂停',
  starting: '批改任务正在启动',
  interrupted: '上次运行已中断', cancel_requested: '正在安全取消', cancelled: '本次运行已取消',
  completed: '本次运行已完成', failed: '本次运行有失败项',
}[store.gradingRun?.state ?? ''] ?? store.gradingRun?.state ?? ''))
// 边框流光只在批改实际运行（含启动与安全收尾）时显示，暂停、中断和终态立即消失。
const gradingRunActive = computed(() => (
  ['running', 'starting', 'pause_requested', 'cancel_requested'].includes(store.gradingRun?.state ?? '')
))
const selectedModeOption = computed(() => gradingModes.find((item) => item.mode === store.selectedMode) ?? null)
const confirmPlanLabel = computed(() => {
  if (store.selectedMode === 'manual') return '进入人工批改'
  if (store.selectedMode === 'hybrid_batch') return '确认并开始混合批改'
  return '确认并开始整卷批改'
})
const requestTotal = computed(() => {
  if (store.selectedMode === 'manual') return 0
  return planNumber(store.gradingPlan?.requests, ['total', 'total_requests', 'request_count'])
})

interface ViewerTarget {
  key: string
  targetType: 'group' | 'issue'
  targetId: string
  title: string
  detectedName: string
  frontUrl: string
  backUrl: string | null
}

function itemText(item: Record<string, unknown>, key: string): string {
  const value = item[key]
  return typeof value === 'string' ? value.trim() : ''
}

function viewerTarget(
  targetType: 'group' | 'issue',
  item: Record<string, unknown>,
): ViewerTarget | null {
  const targetId = itemText(item, 'id')
  const frontUrl = itemText(item, 'front_media_url')
  if (!targetId || !frontUrl) return null
  return {
    key: `${targetType}:${targetId}`,
    targetType,
    targetId,
    title: itemText(item, 'source_label') || (targetType === 'group' ? '自动匹配答卷' : '异常答卷'),
    detectedName: itemText(item, 'student_name') || itemText(item, 'detected_name') || '未识别姓名',
    frontUrl,
    backUrl: itemText(item, 'back_media_url') || null,
  }
}

const viewerTargets = computed(() => [
  ...lowConfidenceGroups.value.flatMap((item) => {
    const target = viewerTarget('group', item)
    return target ? [target] : []
  }),
  ...(store.preflight?.issues ?? []).flatMap((item) => {
    const target = viewerTarget('issue', item)
    return target ? [target] : []
  }),
])
const activeViewerTarget = computed(() => (
  viewerTargets.value.find((item) => item.key === viewerTargetKey.value) ?? null
))
const activeViewerIndex = computed(() => (
  activeViewerTarget.value
    ? viewerTargets.value.findIndex((item) => item.key === activeViewerTarget.value?.key)
    : -1
))
const activeViewerUrl = computed(() => {
  const target = activeViewerTarget.value
  if (!target) return ''
  return viewerSide.value === 'back' && target.backUrl ? target.backUrl : target.frontUrl
})

async function loadInterventionSummary(id: number): Promise<void> {
  interventionState.value = 'loading'
  try {
    const questions = await fetchReviewQuestions(id, { scope: 'all' })
    if (sessionId.value !== id) return
    interventionQuestions.value = questions
    interventionState.value = 'ready'
  } catch {
    if (sessionId.value !== id) return
    interventionState.value = 'error'
  }
}

async function loadRoute(): Promise<void> {
  const id = sessionId.value
  if (!Number.isSafeInteger(id) || id <= 0) return
  interventionQuestions.value = []
  interventionState.value = 'idle'
  await store.load(id)
  if (sessionId.value === id && store.preflight) void loadInterventionSummary(id)
}

function openManualIntervention(): void {
  void router.push({
    path: '/grading',
    query: {
      session: String(sessionId.value),
      scope: 'all',
      entry: 'intervention',
    },
  })
}
function openResults(): void {
  void router.push({
    path: '/results',
    query: { session: String(sessionId.value) },
  })
}
function chooseFiles(event: Event): void {
  const input = event.target as HTMLInputElement
  if (input.files?.length) void store.addFiles([...input.files])
  input.value = ''
}
function chooseMode(mode: GradingMode): void {
  if (!canPreviewPlan.value) return
  void store.previewPlan(mode)
}
function retryPlan(): void {
  if (store.selectedMode && canPreviewPlan.value) void store.previewPlan(store.selectedMode)
}
async function startAutomated(mode: AutomatedGradingMode): Promise<void> {
  gradingSubmissionPending.value = true
  const submission = store.begin(mode, confirmPending.value)
  await nextTick()
  runSection.value?.scrollIntoView({ block: 'start' })
  await submission
  if (store.errorMessage && !store.gradingRun && store.activeJobId === null) {
    gradingSubmissionPending.value = false
  }
}
async function confirmPlan(): Promise<void> {
  const plan = store.gradingPlan
  if (!plan || !canConfirmPlan.value) return
  if (plan.mode === 'manual') {
    await router.push({
      path: '/grading',
      query: {
        session: String(sessionId.value),
        scope: 'all',
        entry: 'manual',
      },
    })
    return
  }
  await startAutomated(plan.mode)
}
function planNumber(source: GradingPlanMetrics | undefined, keys: string[]): number | null {
  if (!source) return null
  for (const key of keys) {
    const raw = source[key]
    const value = typeof raw === 'number'
      ? raw
      : typeof raw === 'string' && raw.trim() !== ''
        ? Number(raw)
        : Number.NaN
    if (Number.isFinite(value) && value >= 0) return value
  }
  return null
}
function formatPlanNumber(source: GradingPlanMetrics | undefined, keys: string[]): string {
  const value = planNumber(source, keys)
  return value === null ? '—' : value.toLocaleString('zh-CN')
}
function issueText(message: string, count?: number): string {
  return count === undefined ? message : `${message}（${count} 项）`
}
function decisionFor(targetType: 'group' | 'issue', targetId: string): ScanDecision | undefined {
  return store.preflight?.decisions.find((item) => (
    item.target_type === targetType && item.target_id === targetId
  ))
}
function decisionStatus(decision: ScanDecision | undefined): string {
  if (!decision) return ''
  if (decision.action === 'invalid') return '已保存：标记无效'
  if (decision.action === 'pending') return '已保存：稍后处理'
  const student = store.students.find((item) => item.id === decision.student_id)
  return `已保存：匹配至 ${student ? [student.name, student.student_code, student.class_name].filter(Boolean).join(' · ') : '已选学生'}`
}
function decisionTargetLabel(decision: ScanDecision): string {
  const items = decision.target_type === 'group'
    ? store.preflight?.groups
    : store.preflight?.issues
  const item = items?.find((candidate) => String(candidate.id) === decision.target_id)
  return String(
    item?.student_name
    || item?.detected_name
    || item?.source_label
    || (decision.target_type === 'group' ? '自动匹配答卷' : '异常答卷'),
  )
}
async function submitDecisions(changes: ScanDecision[]): Promise<void> {
  if (!changes.length || store.busyAction) return
  decisionNotice.value = ''
  const decisions = (store.preflight?.decisions ?? []).filter((item) => !changes.some(
    (change) => change.target_type === item.target_type && change.target_id === item.target_id,
  ))
  const succeeded = await store.saveDecisions([...decisions, ...changes])
  if (!succeeded) return
  for (const change of changes) {
    if (selectedStudents.value[change.target_id] === change.student_id) delete selectedStudents.value[change.target_id]
  }
  decisionNotice.value = `已保存 ${changes.length} 项；仍有 ${pendingCount.value} 份待处理。`
}
function saveDecision(targetType: 'group' | 'issue', targetId: string, action: 'match' | 'invalid' | 'pending'): void {
  void submitDecisions([{ target_type: targetType, target_id: targetId, action,
    ...(action === 'match' ? { student_id: selectedStudents.value[targetId] } : {}) }])
}
function conflictMessages(targetType: 'group' | 'issue', targetId: string): string {
  return matchConflicts.value.filter((c) => c.targets.some((t) => t.target_type === targetType && t.target_id === targetId))
    .map((c) => c.message).join(' ')
}
function classEvidence(item: Record<string, unknown>, targetType: 'group' | 'issue'): string {
  if (!item.detected_class_name) return ''
  const saved = decisionFor(targetType, String(item.id))
  const student = store.students.find((s) => s.id === (selectedStudents.value[String(item.id)]
    ?? (saved?.action === 'match' ? saved.student_id : item.student_id)))
  return `卷面班级：${item.detected_class_name}；所选学生班级：${student?.class_name || '尚未选择'}。请看卷核对。`
}
function openViewer(
  targetType: 'group' | 'issue',
  item: Record<string, unknown>,
  side: 'front' | 'back',
): void {
  const target = viewerTarget(targetType, item)
  if (!target) return
  viewerTargetKey.value = target.key
  viewerSide.value = side === 'back' && target.backUrl ? 'back' : 'front'
  viewerZoom.value = 1
  void nextTick(() => {
    if (viewerCanvas.value) {
      viewerCanvas.value.scrollLeft = 0
      viewerCanvas.value.scrollTop = 0
    }
  })
}
function closeViewer(): void {
  viewerTargetKey.value = null
  viewerZoom.value = 1
}
function moveViewer(offset: number): void {
  if (viewerTargets.value.length === 0) return
  const nextIndex = Math.min(
    viewerTargets.value.length - 1,
    Math.max(0, activeViewerIndex.value + offset),
  )
  const next = viewerTargets.value[nextIndex]
  if (!next) return
  viewerTargetKey.value = next.key
  viewerSide.value = 'front'
  viewerZoom.value = 1
  void nextTick(() => {
    if (viewerCanvas.value) {
      viewerCanvas.value.scrollLeft = 0
      viewerCanvas.value.scrollTop = 0
    }
  })
}
function setViewerSide(side: 'front' | 'back'): void {
  if (side === 'back' && !activeViewerTarget.value?.backUrl) return
  viewerSide.value = side
  viewerZoom.value = 1
}
function changeViewerZoom(delta: number): void {
  viewerZoom.value = Math.min(2.5, Math.max(0.75, Number((viewerZoom.value + delta).toFixed(2))))
}
function beginViewerPan(event: PointerEvent): void {
  if (!viewerCanvas.value || viewerZoom.value <= 1) return
  viewerPointerId = event.pointerId
  viewerPointerX = event.clientX
  viewerPointerY = event.clientY
  viewerScrollLeft = viewerCanvas.value.scrollLeft
  viewerScrollTop = viewerCanvas.value.scrollTop
  viewerCanvas.value.setPointerCapture(event.pointerId)
}
function moveViewerPan(event: PointerEvent): void {
  if (!viewerCanvas.value || viewerPointerId !== event.pointerId) return
  viewerCanvas.value.scrollLeft = viewerScrollLeft - (event.clientX - viewerPointerX)
  viewerCanvas.value.scrollTop = viewerScrollTop - (event.clientY - viewerPointerY)
}
function endViewerPan(event: PointerEvent): void {
  if (viewerPointerId !== event.pointerId) return
  viewerPointerId = null
  if (viewerCanvas.value?.hasPointerCapture(event.pointerId)) {
    viewerCanvas.value.releasePointerCapture(event.pointerId)
  }
}
function cancelRun(): void {
  if (window.confirm('取消后，本次运行会结束；已经完成的成绩会保留，未完成部分以后可重新发起。确认取消吗？')) {
    void store.cancel()
  }
}
function confirmReplacement(): void {
  const confirmed = window.confirm(
    '确认改用这批最新答卷？\n\n确认后会永久删除旧答卷、预检结果、批改进度、教师最终分、报表和知识图谱贡献，且无法恢复。考试配置、评分规则、模板、题框和学生名单会保留。',
  )
  if (confirmed) void store.commitReplacement()
}

function onWindowKeydown(event: KeyboardEvent): void {
  if (!activeViewerTarget.value) return
  if (event.key === 'Escape') closeViewer()
  if (event.key === 'ArrowLeft') moveViewer(-1)
  if (event.key === 'ArrowRight') moveViewer(1)
}

onMounted(() => {
  window.addEventListener('keydown', onWindowKeydown)
  void loadRoute()
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onWindowKeydown)
})
watch(sessionId, () => {
  selectedStudents.value = {}
  decisionNotice.value = ''
  gradingSubmissionPending.value = false
  closeViewer()
  void loadRoute()
})
watch(() => store.gradingRun, (run) => {
  if (run) gradingSubmissionPending.value = false
})
watch(() => store.workspace?.grading_job?.status ?? null, (status) => {
  if (status && TERMINAL_JOB_STATUSES.has(status) && !store.gradingRun) {
    gradingSubmissionPending.value = false
    if (sessionId.value > 0) void loadInterventionSummary(sessionId.value)
  }
})
watch(
  () => `${store.gradingRun?.state ?? ''}:${store.preflight?.revision ?? -1}`,
  (marker, previous) => {
    const runState = store.gradingRun?.state ?? ''
    if (
      marker !== previous
      && store.preflight
      && sessionId.value > 0
      && (
        previous === undefined
        || ['completed', 'failed', 'cancelled'].includes(runState)
        || marker.split(':').slice(-1)[0] !== previous.split(':').slice(-1)[0]
      )
    ) {
      void loadInterventionSummary(sessionId.value)
    }
  },
)
watch(
  () => `${store.uploadBatch?.batch_id ?? ''}:${store.uploadBatch?.revision ?? -1}:${store.preflight?.revision ?? -1}`,
  () => { confirmPending.value = false },
)
</script>

<template>
  <article class="scan-grading">
    <header class="scan-grading__header">
      <div>
        <span class="scan-grading__eyebrow">批改执行 · 考试会话 {{ sessionId }}</span>
        <h1>整班答卷批改</h1>
        <p>从答卷入库到异常核对，再到正式批改与补批，状态都保存在本机服务中。</p>
      </div>
      <div class="scan-grading__header-actions">
        <button type="button" class="secondary" @click="router.push('/sessions')">返回考试配置</button>
      </div>
    </header>

    <div class="scan-grading__body">
      <ol class="scan-run-rail" aria-label="批改执行阶段">
        <li v-for="(label, index) in ['上传答卷', '扫描预检', '开始批改', '运行与补批', '人工干预']" :key="label"
          :data-state="stage > index + 1 ? 'complete' : stage === index + 1 ? 'current' : 'waiting'">
          <span>{{ index + 1 }}</span><strong>{{ label }}</strong>
        </li>
      </ol>
      <main class="scan-grading__main">

    <p v-if="store.errorMessage" class="scan-grading__notice" role="alert">{{ store.errorMessage }}</p>
    <p v-if="store.loadState === 'loading'" class="scan-grading__notice" role="status">正在恢复本次批改工作区…</p>

    <template v-if="store.workspace">
      <section class="scan-stage" aria-labelledby="upload-title">
        <div class="scan-stage__heading">
          <div><span>01</span><h2 id="upload-title">上传答卷</h2></div>
          <strong>{{ editableUploadBatch?.file_count ?? 0 }} 个文件 · {{ Math.ceil((editableUploadBatch?.total_bytes ?? 0) / 1024) }} KB</strong>
        </div>
        <p v-if="store.replacementBatch" class="scan-warning">
          <strong>正在准备替换答卷。</strong>旧答卷和已有成果目前仍然保留；只有点击“确认替换并开始预检”后才会永久清除。
        </p>
        <label class="scan-drop" :data-disabled="Boolean(store.busyAction)">
          <input type="file" multiple accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
            :disabled="Boolean(store.busyAction)" @change="chooseFiles">
          <span class="scan-drop__icon" aria-hidden="true">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 16.5V4.81m0 0L8.03 8.03M12 4.81l3.22 3.22" />
              <path d="M3.75 15.75v1.5A2.25 2.25 0 0 0 6 19.5h12a2.25 2.25 0 0 0 2.25-2.25v-1.5" />
            </svg>
          </span>
          <strong>{{ store.uploadBatch?.state === 'frozen' && !store.replacementBatch ? '重新上传答卷' : '选择或继续添加答卷' }}</strong>
          <span>支持 PDF、JPG、PNG；重复内容会自动跳过。</span>
        </label>
        <div v-if="editableUploadBatch?.files.length" class="scan-file-list">
          <div v-for="file in editableUploadBatch.files" :key="file.id">
            <span><strong>{{ file.name }}</strong><small>{{ file.sha256_prefix }} · {{ Math.ceil(file.size_bytes / 1024) }} KB</small></span>
            <button v-if="editableUploadBatch.state === 'draft'" type="button" class="text-button" @click="store.remove(file.id)">移除</button>
          </div>
        </div>
        <div v-if="store.replacementBatch" class="scan-stage__actions">
          <button type="button" class="secondary" :disabled="!store.replacementBatch.file_count || Boolean(store.busyAction)" @click="store.clear">清空新答卷</button>
          <button type="button" class="secondary" :disabled="Boolean(store.busyAction)" @click="store.cancelReplacement">取消重新上传</button>
          <button type="button" :disabled="!store.replacementBatch.file_count || Boolean(store.busyAction)" @click="confirmReplacement">确认替换并开始预检</button>
        </div>
        <div v-else-if="store.uploadBatch?.state === 'draft'" class="scan-stage__actions">
          <button type="button" class="secondary" :disabled="!store.uploadBatch.file_count || Boolean(store.busyAction)" @click="store.clear">清空</button>
          <button type="button" :disabled="!store.uploadBatch.file_count || Boolean(store.busyAction)" @click="store.analyze">开始预检</button>
        </div>
      </section>

      <section class="scan-stage" aria-labelledby="preflight-title">
        <div class="scan-stage__heading">
          <div><span>02</span><h2 id="preflight-title">扫描预检</h2></div>
          <strong v-if="store.preflight">自动匹配 {{ store.preflight.summary.auto_matched ?? 0 }} · 异常 {{ store.preflight.summary.issues ?? 0 }}</strong>
        </div>
        <div v-if="preflightActive" class="scan-progress" role="status" aria-live="polite">
          <div class="scan-progress__copy">
            <div>
              <strong>扫描预检 {{ preflightProgress }}%</strong>
              <span>{{ preflightProgressText }}</span>
            </div>
            <span>{{ store.preflightJob?.stage || '准备中' }}</span>
          </div>
          <progress :value="preflightProgress" max="100" aria-label="扫描预检进度">
            {{ preflightProgress }}%
          </progress>
          <p v-if="store.preflight">正在生成新结果，下方暂时显示上一次完整预检结果。</p>
        </div>
        <div v-if="!store.preflight" class="scan-empty">
          <p>{{ store.preflightJobId ? '预检正在后台运行，完成后这里会自动更新。' : '上传答卷后运行识别，再在这里核对学生归属和异常页。' }}</p>
          <button v-if="store.uploadBatch?.state === 'frozen' && !store.preflightJobId" data-action="retry-preflight" type="button" class="secondary" @click="store.analyze">运行或重新运行预检</button>
        </div>
        <template v-else>
          <p class="scan-page-assignment">
            本次预检按样卷页序识别：
            <strong>PDF 第 1 页为{{ preflightPageAssignment.first_page_role === 'front' ? '正面' : '反面' }}</strong>
            （正面位于{{ preflightPageAssignment.front_page_parity === 'odd' ? '奇数页' : '偶数页' }}）。
          </p>
          <p class="scan-start-summary" data-scan-reconciliation>
            扫描 {{ store.preflight.summary.scanned_papers ?? 0 }} 份 · 已匹配 {{ store.preflight.summary.matched_papers ?? 0 }} 份 ·
            对应 {{ store.preflight.summary.unique_students ?? 0 }} 名学生 · 有效可批改 {{ store.preflight.summary.ready_to_grade ?? 0 }} 份 ·
            未决 {{ pendingCount }} 份 · 无效 {{ invalidCount }} 份
          </p>
          <p v-if="matchConflicts.length" class="scan-warning" role="alert">存在答卷归属冲突，请处理下方标出的答卷后再开始批改。可以更正归属，或将重复扫描标为无效。</p>
          <p v-else-if="pendingCount" class="scan-warning"><strong>仍有 {{ pendingCount }} 份异常答卷待处理</strong>。确认跳过后，可以先批改其余学生。</p>
          <div class="scan-stage__actions scan-match-actions">
            <button type="button" class="secondary" data-match-selected :disabled="!selectedMatches.length || Boolean(store.busyAction)" @click="submitDecisions(selectedMatches)">
              {{ store.busyAction === 'decisions' ? '正在保存…' : `一键匹配（${selectedMatches.length} 项）` }}
            </button>
            <span>只提交已选好学生的完整答卷；未选择的项目继续保留。</span>
          </div>
          <p v-if="decisionNotice" class="scan-decision-state" role="status">{{ decisionNotice }}</p>
          <div v-if="lowConfidenceGroups.length" class="scan-issue-list" aria-label="低可信自动匹配">
            <div v-for="group in lowConfidenceGroups" :key="String(group.id)" class="scan-issue-row">
              <div class="scan-evidence" :aria-label="`${group.source_label || '答卷'}正反面证据`">
                <button v-if="group.front_media_url" type="button" class="scan-evidence__thumb"
                  :aria-label="`查看${group.source_label || '答卷'}正面大图`"
                  @click="openViewer('group', group, 'front')">
                  <img :src="String(group.front_media_url)"
                    :alt="`${group.source_label || '答卷'}正面`" loading="lazy" decoding="async">
                  <span>查看正面</span>
                </button>
                <button v-if="group.back_media_url" type="button" class="scan-evidence__thumb"
                  :aria-label="`查看${group.source_label || '答卷'}反面大图`"
                  @click="openViewer('group', group, 'back')">
                  <img :src="String(group.back_media_url)"
                    :alt="`${group.source_label || '答卷'}反面`" loading="lazy" decoding="async">
                  <span>查看反面</span>
                </button>
              </div>
              <span class="scan-item-copy">
                <strong>{{ group.student_name || group.detected_name || '待核对姓名' }}</strong>
                <small>{{ group.source_label }} · {{ group.match_method }}</small>
                <small v-if="group.detected_name">识别姓名：{{ group.detected_name }}</small>
                <small v-if="classEvidence(group, 'group')">{{ classEvidence(group, 'group') }}</small>
                <small v-if="conflictMessages('group', String(group.id))" class="scan-match-conflict">{{ conflictMessages('group', String(group.id)) }}</small>
                <small v-if="decisionFor('group', String(group.id))"
                  :data-saved-decision="`group:${String(group.id)}`" class="scan-decision-state">
                  {{ decisionStatus(decisionFor('group', String(group.id))) }}
                </small>
              </span>
              <div class="scan-issue-controls">
              <StudentMatchSelect
                v-model="selectedStudents[String(group.id)]"
                :students="store.students"
                placeholder="姓名、学号或拼音"
                aria-label="重新选择学生"
              />
              <div class="scan-issue-controls__actions">
              <button type="button" class="secondary" :disabled="!selectedStudents[String(group.id)] || !group.back_media_url || Boolean(store.busyAction)" @click="saveDecision('group', String(group.id), 'match')">确认归属</button>
              <button type="button" class="text-button" :disabled="Boolean(store.busyAction)" @click="saveDecision('group', String(group.id), 'invalid')">标记无效</button>
              <button type="button" class="text-button" :disabled="Boolean(store.busyAction)" @click="saveDecision('group', String(group.id), 'pending')">稍后处理</button>
              </div>
              </div>
            </div>
          </div>
          <div v-if="store.preflight.issues.length" class="scan-issue-list">
            <div v-for="issue in store.preflight.issues" :key="String(issue.id)" class="scan-issue-row">
              <div class="scan-evidence" :aria-label="`${issue.source_label || '异常答卷'}正反面证据`">
                <button v-if="issue.front_media_url" type="button" class="scan-evidence__thumb"
                  :aria-label="`查看${issue.source_label || '异常答卷'}正面大图`"
                  @click="openViewer('issue', issue, 'front')">
                  <img :src="String(issue.front_media_url)"
                    :alt="`${issue.source_label || '异常答卷'}正面`" loading="lazy" decoding="async">
                  <span>查看正面</span>
                </button>
                <button v-if="issue.back_media_url" type="button" class="scan-evidence__thumb"
                  :aria-label="`查看${issue.source_label || '异常答卷'}反面大图`"
                  @click="openViewer('issue', issue, 'back')">
                  <img :src="String(issue.back_media_url)"
                    :alt="`${issue.source_label || '异常答卷'}反面`" loading="lazy" decoding="async">
                  <span>查看反面</span>
                </button>
                <span v-else class="scan-evidence__missing">无反面</span>
              </div>
              <span class="scan-item-copy">
                <strong>{{ issue.detected_name || '未识别姓名' }}</strong>
                <small>{{ issue.source_label || '异常答卷' }}</small>
                <small v-if="issue.issue_type === 'ambiguous_name'">名单中有重名，请按学号和班级选择。</small>
                <small v-if="classEvidence(issue, 'issue')">{{ classEvidence(issue, 'issue') }}</small>
                <small v-if="conflictMessages('issue', String(issue.id))" class="scan-match-conflict">{{ conflictMessages('issue', String(issue.id)) }}</small>
                <small v-if="decisionFor('issue', String(issue.id))"
                  :data-saved-decision="`issue:${String(issue.id)}`" class="scan-decision-state">
                  {{ decisionStatus(decisionFor('issue', String(issue.id))) }}
                </small>
              </span>
              <div class="scan-issue-controls">
              <StudentMatchSelect
                v-model="selectedStudents[String(issue.id)]"
                :students="store.students"
                placeholder="姓名、学号或拼音"
                aria-label="选择学生"
              />
              <div class="scan-issue-controls__actions">
              <button type="button" class="secondary" :disabled="!selectedStudents[String(issue.id)] || !issue.back_media_url || Boolean(store.busyAction)" @click="saveDecision('issue', String(issue.id), 'match')">匹配</button>
              <button type="button" class="text-button" :disabled="Boolean(store.busyAction)" @click="saveDecision('issue', String(issue.id), 'invalid')">标记无效</button>
              <button type="button" class="text-button" :disabled="Boolean(store.busyAction)" @click="saveDecision('issue', String(issue.id), 'pending')">稍后处理</button>
              </div>
              </div>
            </div>
          </div>
          <details v-if="confirmedDecisions.length" class="scan-confirmed-decisions">
            <summary>已确认 {{ confirmedDecisions.length }} 项</summary>
            <ul>
              <li v-for="decision in confirmedDecisions"
                :key="`${decision.target_type}:${decision.target_id}`">
                <span>{{ decisionTargetLabel(decision) }}</span>
                <strong>{{ decisionStatus(decision) }}</strong>
              </li>
            </ul>
          </details>
        </template>
      </section>

      <section class="scan-stage" aria-labelledby="start-title">
        <div class="scan-stage__heading"><div><span>03</span><h2 id="start-title">开始批改</h2></div></div>
        <label v-if="pendingCount" class="scan-confirm">
          <input v-model="confirmPending" data-confirm-pending type="checkbox">
          我已知晓：{{ pendingCount }} 份异常答卷本轮会跳过，之后可继续匹配和补批。
        </label>
        <p v-if="store.preflight" class="scan-start-summary">
          本轮可批改 {{ store.preflight.summary.ready_to_grade ?? 0 }} 份；待处理异常 {{ pendingCount }} 份；
          已标无效 {{ invalidCount }} 份；缺反面/孤页 {{ missingBackCount }} 份；缺考候选 {{ store.preflight.summary.absent_candidates ?? 0 }} 人。
        </p>
        <div class="grading-modes" aria-label="选择批改方式">
          <button v-for="option in gradingModes" :key="option.mode" type="button"
            :data-grading-mode="option.mode" :data-selected="store.selectedMode === option.mode"
            :aria-pressed="store.selectedMode === option.mode" :disabled="!canPreviewPlan"
            @click="chooseMode(option.mode)">
            <span>{{ option.label }}</span>
            <strong>{{ option.title }}</strong>
            <small>{{ option.description }}</small>
            <em>{{ option.note }}</em>
          </button>
        </div>
        <section v-if="store.selectedMode" class="grading-plan"
          :data-status="store.gradingPlan?.status ?? store.planState"
          aria-labelledby="grading-plan-title" aria-live="polite">
          <header class="grading-plan__header">
            <div>
              <span>执行前确认</span>
              <h3 id="grading-plan-title">{{ selectedModeOption?.label }}计划</h3>
            </div>
            <strong v-if="store.planState === 'loading'">正在计算</strong>
            <strong v-else-if="store.gradingPlan?.status === 'ready'">可以执行</strong>
            <strong v-else-if="store.gradingPlan?.status === 'blocked'">暂不可执行</strong>
            <strong v-else-if="store.planState === 'error'">读取失败</strong>
            <strong v-else>需要重新预览</strong>
          </header>

          <p v-if="store.planState === 'loading'" class="grading-plan__state" role="status">
            正在核对可处理答卷、教师已完成项目和预计 AI 请求…
          </p>
          <div v-else-if="store.planState === 'error'" class="grading-plan__state grading-plan__state--error">
            <p>{{ store.planErrorMessage || '计划没有读取成功，请重新预览。' }}</p>
            <button type="button" class="secondary" :disabled="!canPreviewPlan" @click="retryPlan">重新预览</button>
          </div>
          <div v-else-if="store.planState === 'idle'" class="grading-plan__state">
            <p>答卷或核对结果已经变化，执行前需要按最新数据重新计算。</p>
            <button type="button" class="secondary" :disabled="!canPreviewPlan" @click="retryPlan">重新预览</button>
          </div>

          <template v-else-if="store.gradingPlan">
            <div class="grading-plan__metrics">
              <div>
                <span>可处理答卷</span>
                <strong>{{ formatPlanNumber(store.gradingPlan.counts, ['eligible_papers', 'ready_to_grade', 'matched_papers']) }}</strong>
                <small>份</small>
              </div>
              <div>
                <span>教师已完成</span>
                <strong>{{ formatPlanNumber(store.gradingPlan.counts, ['teacher_locked_items', 'teacher_final_items']) }}</strong>
                <small>题项，不会覆盖</small>
              </div>
              <div>
                <span>{{ store.selectedMode === 'manual' ? '待人工评分' : '本轮 AI 评分' }}</span>
                <strong>{{ formatPlanNumber(store.gradingPlan.counts,
                  store.selectedMode === 'manual'
                    ? ['manual_target_items', 'pending_items', 'total_score_items']
                    : ['ai_target_items', 'pending_items', 'total_score_items']) }}</strong>
                <small>题项</small>
              </div>
              <div>
                <span>预计 AI 请求</span>
                <strong>{{ requestTotal === null ? '—' : requestTotal.toLocaleString('zh-CN') }}</strong>
                <small>次</small>
              </div>
            </div>

            <p v-if="store.selectedMode === 'manual'" class="grading-plan__batching">
              人工模式不会调用模型。进入后按题查看全班答题区域，教师保存的分数作为最终结果。
            </p>
            <p v-else-if="store.selectedMode === 'full_paper'" class="grading-plan__batching">
              整卷请求 {{ formatPlanNumber(store.gradingPlan.requests, ['full_paper', 'full_paper_requests']) }} 次；
              每位考生的整份答卷保持在同一请求中。
            </p>
            <p v-else class="grading-plan__batching">
              客观题整图 {{ formatPlanNumber(store.gradingPlan.requests, ['objective_sheet', 'objective_requests']) }} 次；
              解答题分组 {{ formatPlanNumber(store.gradingPlan.requests, ['subjective_batches', 'subjective_requests']) }} 次；
              每组 {{ formatPlanNumber(store.gradingPlan.batching, ['subjective_group_min', 'group_min']) }}–{{ formatPlanNumber(store.gradingPlan.batching, ['subjective_group_max', 'group_max']) }} 位考生。
            </p>
            <p v-if="store.selectedMode !== 'manual'" class="grading-plan__batching" data-teacher-score-priority-note>
              已人工确认的分数始终有效：AI 会照常批改所有题目，但不会替代人工分。
            </p>

            <ul v-if="store.gradingPlan.warnings.length" class="grading-plan__issues grading-plan__issues--warning">
              <li v-for="issue in store.gradingPlan.warnings" :key="`warning:${issue.code}:${issue.message}`">
                {{ issueText(issue.message, issue.count) }}
              </li>
            </ul>
            <ul v-if="store.gradingPlan.blockers.length" class="grading-plan__issues grading-plan__issues--blocked">
              <li v-for="issue in store.gradingPlan.blockers" :key="`blocker:${issue.code}:${issue.message}`">
                {{ issueText(issue.message, issue.count) }}
              </li>
            </ul>

            <div class="grading-plan__confirm">
              <p v-if="store.gradingPlan.status === 'blocked'">请先处理上方问题，再重新预览计划。</p>
              <p v-else-if="pendingCount && !confirmPending">请先勾选上方确认，未匹配的异常答卷才会在本轮安全跳过。</p>
              <p v-else-if="store.selectedMode === 'manual'">确认后只会打开人工评分工作台，不会产生 AI 请求。</p>
              <p v-else>确认后才会正式创建批改任务，并按上方估算调用 AI。</p>
              <AppButton
                variant="primary"
                data-confirm-grading-plan
                :disabled="!canConfirmPlan"
                ripple
                @click="confirmPlan"
              >
                {{ store.gradingPlan.status === 'blocked' ? '当前计划不可执行' : confirmPlanLabel }}
              </AppButton>
            </div>
          </template>
        </section>
        <p v-if="gradingStarting" data-grading-submit-status class="scan-grading__notice" role="status">
          批改任务已提交，正在后台启动；进度出现前请勿重复点击。
        </p>
      </section>

      <section ref="runSection" class="scan-stage" aria-labelledby="run-title">
        <div class="scan-stage__heading"><div><span>04</span><h2 id="run-title">运行与补批</h2></div><strong v-if="store.gradingRun">{{ runStateLabel }}</strong><strong v-else-if="gradingCompletedWithoutRun">批改处理已结束</strong></div>
        <div v-if="store.gradingRun" class="run-console">
          <div class="scan-progress scan-progress--run" role="status" aria-live="polite">
            <BorderBeam v-if="gradingRunActive" :size="120" :duration="4" />
            <div class="scan-progress__copy">
              <div>
                <strong>{{ runProgressText }}（{{ runProgress }}%）</strong>
                <span>{{ runProgressDetail }}</span>
              </div>
              <span v-if="store.gradingRun.counts.conflict">
                冲突 {{ store.gradingRun.counts.conflict }}
              </span>
            </div>
            <progress :value="runProgress" max="100" aria-label="批改运行进度">
              {{ runProgress }}%
            </progress>
          </div>
          <section
            v-if="store.gradingRun.mode === 'hybrid_batch'"
            class="hybrid-run-board"
            aria-label="混合批改统计"
          >
            <header>
              <div><strong>混合批改流水</strong><span>客观题先识别，解答题再按题分批，最后集中交给老师复核。</span></div>
              <b>{{ runStateLabel }}</b>
            </header>
            <ol class="hybrid-run-board__phases">
              <li v-for="(label, index) in ['答卷入队', '客观题识别', '解答题分批', '教师交接']" :key="label" :class="{ 'is-current': hybridPhase === index + 1, 'is-done': hybridPhase > index + 1 }">
                <span>{{ index + 1 }}</span><strong>{{ label }}</strong>
              </li>
            </ol>
            <div class="hybrid-run-board__metrics">
              <div><span>本轮答卷</span><strong>{{ store.gradingRun.counts.total }}</strong><small>已进入队列</small></div>
              <div><span>批改单元</span><strong>已完成 {{ store.gradingRun.counts.graded }}</strong><small>已保存，可随时查看</small></div>
              <div><span>等待 AI</span><strong>{{ hybridAiPending }}</strong><small>未评分或处理失败</small></div>
            </div>
          </section>
          <div v-else class="run-counts">
            <span><strong>已完成 {{ store.gradingRun.counts.graded }}</strong></span>
            <span>处理中 {{ store.gradingRun.counts.grading }}</span><span>待处理 {{ store.gradingRun.counts.pending }}</span>
            <span>跳过 {{ store.gradingRun.counts.skipped }}</span><span>失败 {{ store.gradingRun.counts.failed }}</span>
            <span>冲突 {{ store.gradingRun.counts.conflict }}</span>
          </div>
          <div
            v-if="(store.gradingRun.incomplete_item_count ?? 0) > 0"
            class="scan-warning"
            role="alert"
            data-incomplete-warning
          >
            <strong>有 {{ store.gradingRun.incomplete_item_count }} 个小题没有 AI 评分结果。</strong>
            AI 返回的内容未通过校验或缺失，这些题已按「未评分」保留，不会自动给分。可以点「仅重试失败项」让 AI 只重跑受影响的题目，也可以到下方「人工干预」直接评分。
          </div>
          <div
            v-if="store.gradingRun.allowed_actions.includes('pause') || store.gradingRun.allowed_actions.includes('cancel')"
            class="run-control-help"
          >
            <p>
              <strong>安全暂停（可继续）</strong>
              停止领取新答卷；正在处理的几份先安全收尾。已保存成绩保留，之后可从剩余答卷继续。
            </p>
            <p>
              <strong>取消本次运行（不可继续）</strong>
              终结这一轮；已正式保存的成绩保留，尚未完成的答卷需要重新发起批改。两种操作都可能需要短暂等待。
            </p>
          </div>
          <div class="scan-stage__actions">
            <button v-if="store.gradingRun.allowed_actions.includes('pause')" data-action="pause" type="button" @click="store.control('pause')">安全暂停（可继续）</button>
            <button v-if="store.gradingRun.allowed_actions.includes('resume')" data-action="resume" type="button" @click="store.control('resume')">继续本次运行</button>
            <button v-if="store.gradingRun.allowed_actions.includes('retry_failed')" data-action="retry-failed" type="button" class="secondary" @click="store.control('retry-failed')">仅重试失败项</button>
            <button v-if="store.gradingRun.allowed_actions.includes('supplement_new_matches')" data-action="supplement" type="button" class="secondary" @click="store.supplement">补批后来匹配的答卷</button>
            <button v-if="store.gradingRun.allowed_actions.includes('cancel')" data-action="cancel" type="button" class="danger" @click="cancelRun">取消本次运行（不可继续）</button>
          </div>
        </div>
        <p v-else-if="gradingStarting" data-grading-starting class="scan-empty" role="status">
          启动请求已接收，正在建立本次批改进度。可以留在本页等待，刷新后也会自动恢复。
        </p>
        <div v-else-if="gradingCompletedWithoutRun" data-grading-completed-without-run class="scan-empty" role="status">
          <p>批改处理已结束，但本次运行进度记录没有生成。任务结束不代表每份答卷都成功，请先核对完成与失败数量；这里不会开放重复提交。</p>
          <div class="scan-stage__actions">
            <button type="button" data-open-grading-results @click="openResults">查看成绩</button>
          </div>
        </div>
        <p v-else class="scan-empty">尚未开始批改。启动后，刷新页面仍可恢复这里的运行状态。</p>
      </section>

      <section class="scan-stage scan-stage--intervention" aria-labelledby="intervention-title">
        <div class="scan-stage__heading">
          <div><span>05</span><h2 id="intervention-title">人工干预</h2></div>
          <strong v-if="interventionState === 'ready'">
            需处理 {{ interventionSummary.ungraded + interventionSummary.failed + interventionSummary.review }} 项
          </strong>
        </div>
        <p class="intervention-intro">
          没有 AI 结果时，可以在这里直接人工评分；已有 AI 结果时，未评分、失败和待复核答卷会排在前面，高置信结果仍可查看和修改。
        </p>
        <div v-if="interventionState === 'ready' && interventionSummary.total > 0" class="intervention-ledger">
          <span><strong>{{ interventionSummary.ungraded }}</strong>未评分</span>
          <span><strong>{{ interventionSummary.failed }}</strong>处理失败</span>
          <span><strong>{{ interventionSummary.review }}</strong>AI 待复核</span>
          <span><strong>{{ interventionSummary.aiReady }}</strong>AI 已完成</span>
          <span><strong>{{ interventionSummary.teacher }}</strong>教师已确认</span>
        </div>
        <p v-else-if="interventionState === 'loading'" class="scan-empty">正在读取人工干预摘要…</p>
        <p v-else-if="interventionState === 'error'" class="scan-empty">摘要暂时无法读取，仍可进入工作台查看完整队列。</p>
        <p v-else-if="store.preflight" class="scan-empty">当前还没有评分题项，可以先进入工作台开始纯人工评分。</p>
        <p v-else class="scan-empty">完成扫描预检后，这里会建立本场考试的人工干预队列。</p>
        <div class="scan-stage__actions">
          <button type="button" :disabled="!store.preflight" @click="openManualIntervention">进入人工干预工作台</button>
          <button v-if="interventionSummary.total > 0" type="button" class="secondary" @click="openResults">查看成绩中心</button>
        </div>
      </section>
    </template>
      </main>
    </div>

    <div
      v-if="activeViewerTarget"
      class="scan-viewer"
      role="dialog"
      aria-modal="true"
      aria-labelledby="scan-viewer-title"
    >
      <div class="scan-viewer__shell">
        <header class="scan-viewer__header">
          <div>
            <span>原卷核对</span>
            <h2 id="scan-viewer-title">{{ activeViewerTarget.title }}</h2>
          </div>
          <button type="button" class="secondary" aria-label="关闭原卷大图" @click="closeViewer">关闭</button>
        </header>
        <div class="scan-viewer__toolbar" aria-label="原卷查看工具">
          <button type="button" :disabled="activeViewerIndex <= 0" @click="moveViewer(-1)">上一份</button>
          <button type="button" :aria-pressed="viewerSide === 'front'" @click="setViewerSide('front')">正面</button>
          <button type="button" :disabled="!activeViewerTarget.backUrl" :aria-pressed="viewerSide === 'back'" @click="setViewerSide('back')">反面</button>
          <button type="button" @click="changeViewerZoom(-0.25)">缩小</button>
          <span>{{ Math.round(viewerZoom * 100) }}%</span>
          <button type="button" @click="changeViewerZoom(0.25)">放大</button>
          <button type="button" :disabled="activeViewerIndex >= viewerTargets.length - 1" @click="moveViewer(1)">下一份</button>
        </div>
        <div class="scan-viewer__body">
          <div
            ref="viewerCanvas"
            class="scan-viewer__canvas"
            :data-pannable="viewerZoom > 1"
            @pointerdown="beginViewerPan"
            @pointermove="moveViewerPan"
            @pointerup="endViewerPan"
            @pointercancel="endViewerPan"
          >
            <img
              :src="activeViewerUrl"
              :alt="`${activeViewerTarget.title}${viewerSide === 'front' ? '正面' : '反面'}整页原卷`"
              :style="{ width: `${viewerZoom * 100}%` }"
            >
          </div>
          <aside class="scan-viewer__decision">
            <span>识别结果</span>
            <strong>{{ activeViewerTarget.detectedName }}</strong>
            <small>{{ activeViewerTarget.title }}</small>
            <StudentMatchSelect
              v-model="selectedStudents[activeViewerTarget.targetId]"
              :students="store.students"
              placeholder="姓名、学号或拼音"
              aria-label="为当前原卷选择学生"
            />
            <button
              type="button"
              :disabled="!selectedStudents[activeViewerTarget.targetId] || !activeViewerTarget.backUrl || Boolean(store.busyAction)"
              @click="saveDecision(activeViewerTarget.targetType, activeViewerTarget.targetId, 'match')"
            >
              {{ activeViewerTarget.targetType === 'group' ? '确认归属' : '匹配为该学生' }}
            </button>
            <button type="button" class="secondary" :disabled="Boolean(store.busyAction)" @click="saveDecision(activeViewerTarget.targetType, activeViewerTarget.targetId, 'invalid')">标记无效</button>
            <button type="button" class="text-button" :disabled="Boolean(store.busyAction)" @click="saveDecision(activeViewerTarget.targetType, activeViewerTarget.targetId, 'pending')">稍后处理</button>
            <p v-if="decisionFor(activeViewerTarget.targetType, activeViewerTarget.targetId)" class="scan-decision-state">
              {{ decisionStatus(decisionFor(activeViewerTarget.targetType, activeViewerTarget.targetId)) }}
            </p>
          </aside>
        </div>
      </div>
    </div>
  </article>
</template>
