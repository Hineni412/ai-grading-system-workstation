<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import type {
  GradingPlanMetrics,
  SelectableGradingMode,
} from '../../api/scan-grading'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
import { BorderBeam } from '@/components/ui/border-beam'
import { useScanGradingStore } from '../../stores/scan-grading'
import type { InterventionSummary, ScanStageId } from './scan-stage'

const props = defineProps<{
  sessionId: number
  gradingStarting: boolean
  interventionSummary: InterventionSummary
}>()
const emit = defineEmits<{
  'select-stage': [id: ScanStageId]
  'submission-pending': []
  'submission-failed': []
}>()

const router = useRouter()
const store = useScanGradingStore()

const confirmPending = ref(false)

interface GradingModeOption {
  mode: SelectableGradingMode
  label: string
  title: string
  note: string
}

const gradingModes: GradingModeOption[] = [
  {
    mode: 'ai',
    label: 'AI 批改',
    title: '整页原图，按题批改',
    note: '会调用 AI · 执行前显示请求估算',
  },
  {
    mode: 'manual',
    label: '人工批改',
    title: '直接进入按题评分',
    note: 'AI 请求 0 · 教师分数为最终结果',
  },
]

const pendingCount = computed(() => store.preflight?.pending_issue_count ?? 0)
const matchConflicts = computed(() => store.preflight?.match_conflicts ?? [])
const invalidCount = computed(() => store.preflight?.decisions
  .filter((item) => item.action === 'invalid').length ?? 0)
const missingBackCount = computed(() => store.preflight?.issues
  .filter((item) => item.issue_type === 'missing_back' || item.issue_type === 'orphan_page').length ?? 0)
const runTerminal = computed(() => Boolean(
  store.gradingRun && ['completed', 'failed', 'cancelled'].includes(store.gradingRun.state),
))
const gradingCompletedWithoutRun = computed(() => !store.gradingRun
  && store.workspace?.grading_job?.status === 'succeeded'
  && store.workspace.grading_job.scan_batch_id === store.uploadBatch?.batch_id)

const canPreviewPlan = computed(() => Boolean(store.preflight)
  && !store.gradingRun && !props.gradingStarting && !gradingCompletedWithoutRun.value
  && !store.busyAction && store.planState !== 'loading')
const canConfirmPlan = computed(() => Boolean(
  store.gradingPlan
  && store.gradingPlan.mode === store.selectedMode
  && store.gradingPlan.status === 'ready'
  && store.planState === 'ready'
  && matchConflicts.value.length === 0
  && (pendingCount.value === 0 || confirmPending.value)
  && !store.busyAction
  && !props.gradingStarting,
))
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
  if (!job && runTerminal.value) {
    return {
      completed: '本次批改已完成',
      failed: '本次运行有失败项',
      cancelled: '本次运行已取消',
    }[store.gradingRun?.state ?? ''] ?? '本次批改处理已结束'
  }
  if (/[\u3400-\u9fff]/u.test(detail)) return detail
  return '正在处理本次批改任务'
})
const hybridPhase = computed(() => {
  const stage = store.gradingJob?.stage ?? ''
  if (['grading_saving', 'grading_completed', 'grading_finished'].includes(stage)) return 4
  if (stage === 'grading_subjective') return 3
  if (stage === 'grading_objective') return 2
  if (!store.gradingJob && runTerminal.value) return 5
  return 1
})
const hybridAiPending = computed(() => (
  props.interventionSummary.ungraded + props.interventionSummary.failed
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
  return '确认并开始 AI 批改'
})
const requestTotal = computed(() => {
  if (store.selectedMode === 'manual') return 0
  return planNumber(store.gradingPlan?.requests, ['total', 'total_requests', 'request_count'])
})

function chooseMode(mode: SelectableGradingMode): void {
  if (!canPreviewPlan.value) return
  void store.previewPlan(mode)
}
function retryPlan(): void {
  if (store.selectedMode && canPreviewPlan.value) void store.previewPlan(store.selectedMode)
}
async function startAutomated(mode: 'ai'): Promise<void> {
  emit('submission-pending')
  const submission = store.begin(mode, confirmPending.value)
  await nextTick()
  emit('select-stage', 'grade')
  await submission
  if (store.errorMessage && !store.gradingRun && store.activeJobId === null) {
    emit('submission-failed')
  }
}
async function confirmPlan(): Promise<void> {
  const plan = store.gradingPlan
  if (!plan || !canConfirmPlan.value) return
  if (plan.mode === 'manual') {
    await router.push({
      path: '/grading',
      query: {
        session: String(props.sessionId),
        scope: 'all',
        entry: 'manual',
      },
    })
    return
  }
  if (plan.mode === 'ai') await startAutomated(plan.mode)
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
function cancelRun(): void {
  if (window.confirm('取消后，本次运行会结束；已经完成的成绩会保留，未完成部分以后可重新发起。确认取消吗？')) {
    void store.cancel()
  }
}
function openResults(): void {
  void router.push({
    path: '/results',
    query: { session: String(props.sessionId) },
  })
}
function selectStage(id: ScanStageId): void {
  emit('select-stage', id)
}

watch(
  () => `${store.uploadBatch?.batch_id ?? ''}:${store.uploadBatch?.revision ?? -1}:${store.preflight?.revision ?? -1}`,
  () => { confirmPending.value = false },
)
</script>

<template>
  <section class="scan-stage" aria-labelledby="grade-title">
    <div class="scan-stage__heading">
      <h2 id="grade-title">批改</h2>
      <strong v-if="store.gradingRun">{{ runStateLabel }}</strong>
      <strong v-else-if="gradingCompletedWithoutRun">批改处理已结束</strong>
    </div>
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
        v-if="['hybrid_batch', 'ai'].includes(store.gradingRun.mode)"
        class="hybrid-run-board"
        aria-label="AI 批改统计"
      >
        <header>
          <strong>AI 批改流水</strong>
          <b>{{ runStateLabel }}</b>
        </header>
        <ol class="hybrid-run-board__phases">
          <li v-for="(label, index) in ['答卷入队', '客观题识别', '解答题分批', '教师交接']" :key="label" :class="{ 'is-current': hybridPhase === index + 1, 'is-done': hybridPhase > index + 1 }">
            <span>{{ index + 1 }}</span><strong>{{ label }}</strong>
          </li>
        </ol>
        <div class="hybrid-run-board__metrics">
          <span>本轮答卷 <strong>{{ store.gradingRun.counts.total }}</strong></span>
          <span>已完成 <strong>{{ store.gradingRun.counts.graded }}</strong> 个批改单元</span>
          <span>等待 AI <strong>{{ hybridAiPending }}</strong></span>
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
        这些题保留为未评分，不会自动给分；可「仅重试失败项」或在下方人工干预中评分。
      </div>
      <p
        v-if="store.gradingRun.allowed_actions.includes('pause') || store.gradingRun.allowed_actions.includes('cancel')"
        class="run-control-help"
      >安全暂停后可继续；取消后未完成的答卷需重新发起。已保存的成绩都会保留。</p>
      <div class="scan-stage__actions">
        <AppButton v-if="runTerminal" variant="primary" data-action="go-review" @click="selectStage('review')">去复核</AppButton>
        <AppButton v-if="store.gradingRun.allowed_actions.includes('pause')" data-action="pause" variant="secondary" @click="store.control('pause')">安全暂停（可继续）</AppButton>
        <AppButton v-if="store.gradingRun.allowed_actions.includes('resume')" data-action="resume" variant="secondary" @click="store.control('resume')">继续本次运行</AppButton>
        <AppButton v-if="store.gradingRun.allowed_actions.includes('retry_failed')" data-action="retry-failed" variant="secondary" @click="store.control('retry-failed')">仅重试失败项</AppButton>
        <AppButton v-if="store.gradingRun.allowed_actions.includes('supplement_new_matches')" data-action="supplement" variant="secondary" @click="store.supplement">补批后来匹配的答卷</AppButton>
        <AppButton v-if="store.gradingRun.allowed_actions.includes('cancel')" data-action="cancel" variant="danger" @click="cancelRun">取消本次运行（不可继续）</AppButton>
      </div>
    </div>
    <StatePanel v-else-if="gradingStarting" data-grading-starting kind="loading" title="启动请求已接收" description="正在建立本次批改进度。可以留在本页等待，刷新后也会自动恢复。" />
    <StatePanel v-else-if="gradingCompletedWithoutRun" data-grading-completed-without-run kind="empty" title="批改处理已结束" description="本次运行进度记录没有生成。任务结束不代表每份答卷都成功，请先核对完成与失败数量；这里不会开放重复提交。">
      <template #actions><AppButton variant="primary" data-open-grading-results @click="openResults">查看成绩</AppButton></template>
    </StatePanel>
    <div v-else class="scan-grade-start" :class="{ 'scan-grade-start--split': Boolean(store.selectedMode) }">
    <div class="scan-grade-start__intro">
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
        <em>{{ option.note }}</em>
      </button>
    </div>
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
        <AppButton variant="secondary" :disabled="!canPreviewPlan" @click="retryPlan">重新预览</AppButton>
      </div>
      <div v-else-if="store.planState === 'idle'" class="grading-plan__state">
        <AppButton variant="secondary" :disabled="!canPreviewPlan" @click="retryPlan">重新预览</AppButton>
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
        <p v-else class="grading-plan__batching">
          客观题整区 {{ formatPlanNumber(store.gradingPlan.requests, ['objective_sheet', 'objective_requests']) }} 次 ·
          解答题 {{ formatPlanNumber(store.gradingPlan.requests, ['subjective_batches', 'subjective_requests']) }} 次
          （每位考生每道解答题一次，整页原图）。<span data-teacher-score-priority-note>已人工确认的分数始终有效，AI 不会替代人工分。</span>
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
    </div>
  </section>
</template>
