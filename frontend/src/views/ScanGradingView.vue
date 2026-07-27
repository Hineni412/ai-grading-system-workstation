<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { TERMINAL_JOB_STATUSES } from '../api/jobs'
import type {
  AutomatedGradingMode,
  GradingMode,
  GradingPlanMetrics,
  ScanDecision,
} from '../api/scan-grading'
import StudentMatchSelect from '../components/scan-grading/StudentMatchSelect.vue'
import { useScanGradingStore } from '../stores/scan-grading'

const route = useRoute()
const router = useRouter()
const store = useScanGradingStore()
const sessionId = computed(() => Number(route.params.sessionId))
const confirmPending = ref(false)
const selectedStudents = ref<Record<string, number | undefined>>({})
const gradingSubmissionPending = ref(false)
const runSection = ref<HTMLElement | null>(null)
const gradingStarting = computed(() => !store.gradingRun && (
  gradingSubmissionPending.value
  || store.busyAction === 'grading'
  || store.activeJobId !== null
))
const gradingCompletedWithoutRun = computed(() => !store.gradingRun
  && store.workspace?.grading_job?.status === 'succeeded')

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
  if (store.gradingRun || gradingStarting.value || gradingCompletedWithoutRun.value) return 4
  if (store.preflight) return 3
  if (store.uploadBatch?.state === 'frozen') return 2
  return 1
})
const pendingCount = computed(() => store.preflight?.pending_issue_count ?? 0)
const canPreviewPlan = computed(() => Boolean(store.preflight)
  && !store.gradingRun && !gradingStarting.value && !gradingCompletedWithoutRun.value
  && !store.busyAction && store.planState !== 'loading')
const canConfirmPlan = computed(() => Boolean(
  store.gradingPlan
  && store.gradingPlan.mode === store.selectedMode
  && store.gradingPlan.status === 'ready'
  && store.planState === 'ready'
  && (pendingCount.value === 0 || confirmPending.value)
  && !store.busyAction
  && !gradingStarting.value,
))
const invalidCount = computed(() => store.preflight?.decisions
  .filter((item) => item.target_type === 'issue' && item.action === 'invalid').length ?? 0)
const missingBackCount = computed(() => store.preflight?.issues
  .filter((item) => item.issue_type === 'missing_back' || item.issue_type === 'orphan_page').length ?? 0)
const preflightPageAssignment = computed(() => (
  store.preflight?.page_assignment
  ?? { first_page_role: 'front' as const, front_page_parity: 'odd' as const }
))
const lowConfidenceGroups = computed(() => store.preflight?.groups.filter((item) => (
  item.match_method !== 'exact' || Number(item.match_score ?? 0) < 1
)) ?? [])
const confirmedDecisions = computed(() => store.preflight?.decisions
  .filter((item) => item.action !== 'pending') ?? [])
const runStateLabel = computed(() => ({
  running: '批改运行中', pause_requested: '正在安全暂停', paused: '已安全暂停',
  starting: '批改任务正在启动',
  interrupted: '上次运行已中断', cancel_requested: '正在安全取消', cancelled: '本次运行已取消',
  completed: '本次运行已完成', failed: '本次运行有失败项',
}[store.gradingRun?.state ?? ''] ?? store.gradingRun?.state ?? ''))
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

function loadRoute(): void {
  if (Number.isSafeInteger(sessionId.value) && sessionId.value > 0) void store.load(sessionId.value)
}
function openManualScoring(): void {
  void router.push({
    path: '/grading',
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
        scope: 'teacher_pending',
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
  return `已保存：匹配至 ${student?.name ?? '已选学生'}`
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
function saveDecision(targetType: 'group' | 'issue', targetId: string, action: 'match' | 'invalid' | 'pending'): void {
  const decisions: ScanDecision[] = [...(store.preflight?.decisions ?? [])
    .filter((item) => !(item.target_type === targetType && item.target_id === targetId))]
  decisions.push({ target_type: targetType, target_id: targetId, action,
    ...(action === 'match' ? { student_id: selectedStudents.value[targetId] } : {}) })
  void store.saveDecisions(decisions)
}
function cancelRun(): void {
  if (window.confirm('取消后，本次运行会结束；已经完成的成绩会保留，未完成部分以后可重新发起。确认取消吗？')) {
    void store.cancel()
  }
}
function beginNewBatch(): void {
  if (window.confirm('开始新批次会保留既有成绩和运行记录，并建立一份空的上传队列。确认继续吗？')) {
    void store.newBatch()
  }
}

onMounted(loadRoute)
watch(sessionId, () => {
  gradingSubmissionPending.value = false
  loadRoute()
})
watch(() => store.gradingRun, (run) => {
  if (run) gradingSubmissionPending.value = false
})
watch(() => store.workspace?.grading_job?.status ?? null, (status) => {
  if (status && TERMINAL_JOB_STATUSES.has(status) && !store.gradingRun) {
    gradingSubmissionPending.value = false
  }
})
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
        <button type="button" @click="openManualScoring">人工评分</button>
        <button type="button" class="secondary" @click="router.push('/sessions')">返回考试配置</button>
      </div>
    </header>

    <div class="scan-grading__body">
      <ol class="scan-run-rail" aria-label="批改执行阶段">
        <li v-for="(label, index) in ['上传答卷', '扫描预检', '开始批改', '运行与补批']" :key="label"
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
          <strong>{{ store.uploadBatch?.file_count ?? 0 }} 个文件 · {{ Math.ceil((store.uploadBatch?.total_bytes ?? 0) / 1024) }} KB</strong>
        </div>
        <label class="scan-drop" :data-disabled="store.uploadBatch?.state === 'frozen'">
          <input type="file" multiple accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
            :disabled="store.uploadBatch?.state === 'frozen' || Boolean(store.busyAction)" @change="chooseFiles">
          <strong>{{ store.uploadBatch?.state === 'frozen' ? '上传批次已冻结' : '选择或继续添加答卷' }}</strong>
          <span>支持 PDF、JPG、PNG；重复内容会自动跳过。</span>
        </label>
        <div v-if="store.uploadBatch?.files.length" class="scan-file-list">
          <div v-for="file in store.uploadBatch.files" :key="file.id">
            <span><strong>{{ file.name }}</strong><small>{{ file.sha256_prefix }} · {{ Math.ceil(file.size_bytes / 1024) }} KB</small></span>
            <button v-if="store.uploadBatch.state === 'draft'" type="button" class="text-button" @click="store.remove(file.id)">移除</button>
          </div>
        </div>
        <div v-if="store.uploadBatch?.state === 'draft'" class="scan-stage__actions">
          <button type="button" class="secondary" :disabled="!store.uploadBatch.file_count || Boolean(store.busyAction)" @click="store.clear">清空</button>
          <button type="button" :disabled="!store.uploadBatch.file_count || Boolean(store.busyAction)" @click="store.analyze">冻结并开始预检</button>
        </div>
      </section>

      <section class="scan-stage" aria-labelledby="preflight-title">
        <div class="scan-stage__heading">
          <div><span>02</span><h2 id="preflight-title">扫描预检</h2></div>
          <strong v-if="store.preflight">自动匹配 {{ store.preflight.summary.auto_matched ?? 0 }} · 异常 {{ store.preflight.summary.issues ?? 0 }}</strong>
        </div>
        <div v-if="!store.preflight" class="scan-empty">
          <p>{{ store.preflightJobId ? '预检正在后台运行，完成后这里会自动更新。' : '冻结答卷后运行识别，再在这里核对学生归属和异常页。' }}</p>
          <button v-if="store.uploadBatch?.state === 'frozen' && !store.preflightJobId" data-action="retry-preflight" type="button" class="secondary" @click="store.analyze">运行或重新运行预检</button>
        </div>
        <template v-else>
          <p class="scan-page-assignment">
            本次预检按样卷页序识别：
            <strong>PDF 第 1 页为{{ preflightPageAssignment.first_page_role === 'front' ? '正面' : '反面' }}</strong>
            （正面位于{{ preflightPageAssignment.front_page_parity === 'odd' ? '奇数页' : '偶数页' }}）。
          </p>
          <p v-if="pendingCount" class="scan-warning"><strong>仍有 {{ pendingCount }} 份异常答卷待处理</strong>。它们可以暂时跳过，不会阻塞其余学生批改。</p>
          <div v-if="lowConfidenceGroups.length" class="scan-issue-list" aria-label="低可信自动匹配">
            <div v-for="group in lowConfidenceGroups" :key="String(group.id)" class="scan-issue-row">
              <div class="scan-evidence" :aria-label="`${group.source_label || '答卷'}正反面证据`">
                <img v-if="group.front_media_url" :src="String(group.front_media_url)"
                  :alt="`${group.source_label || '答卷'}正面`" loading="lazy" decoding="async">
                <img v-if="group.back_media_url" :src="String(group.back_media_url)"
                  :alt="`${group.source_label || '答卷'}反面`" loading="lazy" decoding="async">
              </div>
              <span class="scan-item-copy">
                <strong>{{ group.student_name || group.detected_name || '待核对姓名' }}</strong>
                <small>{{ group.source_label }} · {{ group.match_method }}</small>
                <small v-if="decisionFor('group', String(group.id))"
                  :data-saved-decision="`group:${String(group.id)}`" class="scan-decision-state">
                  {{ decisionStatus(decisionFor('group', String(group.id))) }}
                </small>
              </span>
              <StudentMatchSelect
                v-model="selectedStudents[String(group.id)]"
                :students="store.students"
                placeholder="姓名、学号或拼音"
                aria-label="重新选择学生"
              />
              <button type="button" class="secondary" :disabled="!selectedStudents[String(group.id)]" @click="saveDecision('group', String(group.id), 'match')">确认改绑</button>
            </div>
          </div>
          <div v-if="store.preflight.issues.length" class="scan-issue-list">
            <div v-for="issue in store.preflight.issues" :key="String(issue.id)" class="scan-issue-row">
              <div class="scan-evidence" :aria-label="`${issue.source_label || '异常答卷'}正反面证据`">
                <img v-if="issue.front_media_url" :src="String(issue.front_media_url)"
                  :alt="`${issue.source_label || '异常答卷'}正面`" loading="lazy" decoding="async">
                <img v-if="issue.back_media_url" :src="String(issue.back_media_url)"
                  :alt="`${issue.source_label || '异常答卷'}反面`" loading="lazy" decoding="async">
                <span v-else class="scan-evidence__missing">无反面</span>
              </div>
              <span class="scan-item-copy">
                <strong>{{ issue.detected_name || '未识别姓名' }}</strong>
                <small>{{ issue.source_label || '异常答卷' }}</small>
                <small v-if="decisionFor('issue', String(issue.id))"
                  :data-saved-decision="`issue:${String(issue.id)}`" class="scan-decision-state">
                  {{ decisionStatus(decisionFor('issue', String(issue.id))) }}
                </small>
              </span>
              <StudentMatchSelect
                v-model="selectedStudents[String(issue.id)]"
                :students="store.students"
                placeholder="姓名、学号或拼音"
                aria-label="选择学生"
              />
              <button type="button" class="secondary" :disabled="!selectedStudents[String(issue.id)]" @click="saveDecision('issue', String(issue.id), 'match')">匹配</button>
              <button type="button" class="text-button" @click="saveDecision('issue', String(issue.id), 'invalid')">标记无效</button>
              <button type="button" class="text-button" @click="saveDecision('issue', String(issue.id), 'pending')">稍后处理</button>
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
              <button type="button" data-confirm-grading-plan :disabled="!canConfirmPlan" @click="confirmPlan">
                {{ store.gradingPlan.status === 'blocked' ? '当前计划不可执行' : confirmPlanLabel }}
              </button>
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
          <div class="run-counts">
            <span><strong>已完成 {{ store.gradingRun.counts.graded }}</strong></span>
            <span>处理中 {{ store.gradingRun.counts.grading }}</span><span>待处理 {{ store.gradingRun.counts.pending }}</span>
            <span>跳过 {{ store.gradingRun.counts.skipped }}</span><span>失败 {{ store.gradingRun.counts.failed }}</span>
          </div>
          <div class="scan-stage__actions">
            <button v-if="store.gradingRun.allowed_actions.includes('pause')" data-action="pause" type="button" @click="store.control('pause')">安全暂停</button>
            <button v-if="store.gradingRun.allowed_actions.includes('resume')" data-action="resume" type="button" @click="store.control('resume')">继续本次运行</button>
            <button v-if="store.gradingRun.allowed_actions.includes('retry_failed')" data-action="retry-failed" type="button" class="secondary" @click="store.control('retry-failed')">仅重试失败项</button>
            <button v-if="store.gradingRun.allowed_actions.includes('supplement_new_matches')" data-action="supplement" type="button" class="secondary" @click="store.supplement">补批后来匹配的答卷</button>
            <button v-if="store.gradingRun.allowed_actions.includes('cancel')" data-action="cancel" type="button" class="danger" @click="cancelRun">取消本次运行</button>
            <button v-if="['cancelled', 'completed', 'failed'].includes(store.gradingRun.state)" data-action="new-batch" type="button" class="secondary" @click="beginNewBatch">开始新批次</button>
          </div>
        </div>
        <p v-else-if="gradingStarting" data-grading-starting class="scan-empty" role="status">
          启动请求已接收，正在建立本次批改进度。可以留在本页等待，刷新后也会自动恢复。
        </p>
        <div v-else-if="gradingCompletedWithoutRun" data-grading-completed-without-run class="scan-empty" role="status">
          <p>批改处理已结束，但本次运行进度记录没有生成。任务结束不代表每份答卷都成功，请先核对完成与失败数量；这里不会开放重复提交。</p>
          <div class="scan-stage__actions">
            <button type="button" data-open-workbench @click="router.push('/workbench')">查看工作台状态</button>
            <button type="button" data-open-grading-results class="secondary" @click="router.push('/grading')">进入评分复核</button>
          </div>
        </div>
        <p v-else class="scan-empty">尚未开始批改。启动后，刷新页面仍可恢复这里的运行状态。</p>
      </section>
    </template>
      </main>
    </div>
  </article>
</template>
