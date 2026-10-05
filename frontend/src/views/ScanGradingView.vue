<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { TERMINAL_JOB_STATUSES } from '../api/jobs'
import {
  fetchReviewQuestions,
  type ReviewQuestionSummary,
} from '../api/review'
import PageHeader from '../components/design-system/PageHeader.vue'
import StepProgress, { type StepProgressStep } from '../components/design-system/StepProgress.vue'
import ScanGradeStage from '../components/scan-grading/ScanGradeStage.vue'
import ScanPreflightStage from '../components/scan-grading/ScanPreflightStage.vue'
import ScanReviewStage from '../components/scan-grading/ScanReviewStage.vue'
import ScanViewerDialog from '../components/scan-grading/ScanViewerDialog.vue'
import {
  SCAN_STAGE_ORDER,
  summarizeIntervention,
  type InterventionState,
  type ScanStageId,
} from '../components/scan-grading/scan-stage'
import { useScanPreflight } from '../components/scan-grading/useScanPreflight'
import { useResultsCenterStore } from '../stores/results-center'
import { useScanGradingStore } from '../stores/scan-grading'
import '../styles/scan-grading.css'

const route = useRoute()
const store = useScanGradingStore()
const resultsStore = useResultsCenterStore()
const sessionId = computed(() => Number(route.params.sessionId))
const gradingSubmissionPending = ref(false)
const interventionQuestions = ref<ReviewQuestionSummary[]>([])
const interventionState = ref<InterventionState>('idle')
// 答卷核对与原卷查看器的状态；组件卸载（切换阶段）时保留，换场次时由它自己重置。
const preflight = useScanPreflight(sessionId)
const gradingStarting = computed(() => !store.gradingRun && (
  gradingSubmissionPending.value
  || store.busyAction === 'grading'
  || store.activeJobId !== null
))
const gradingCompletedWithoutRun = computed(() => !store.gradingRun
  && store.workspace?.grading_job?.status === 'succeeded'
  && store.workspace.grading_job.scan_batch_id === store.uploadBatch?.batch_id)
const runTerminal = computed(() => Boolean(
  store.gradingRun && ['completed', 'failed', 'cancelled'].includes(store.gradingRun.state),
))

const activeStage = ref<ScanStageId>('prepare')
const stageTransition = ref<'fx-stage-forward' | 'fx-stage-back'>('fx-stage-forward')
const interventionSummary = computed(() => summarizeIntervention(interventionQuestions.value))
// defaultStage 只按真实进度计算；activeStage 跟随它，也允许用户手动切换面板。
const defaultStage = computed<ScanStageId>(() => {
  if (store.replacementBatch) return 'prepare'
  if (runTerminal.value || gradingCompletedWithoutRun.value) return 'review'
  if (store.gradingRun || gradingStarting.value) return 'grade'
  if (preflight.preflightActive.value) return 'prepare'
  if (store.preflight) return (preflight.matchConflicts.value.length || preflight.pendingCount.value) ? 'prepare' : 'grade'
  return 'prepare'
})
watch(defaultStage, (next, previous) => {
  // 核对完最后一份异常时留在预检页，由教师点“下一步”进入批改。
  if (previous === 'prepare' && next === 'grade' && activeStage.value === 'prepare') return
  stageTransition.value = SCAN_STAGE_ORDER.indexOf(next) >= SCAN_STAGE_ORDER.indexOf(activeStage.value)
    ? 'fx-stage-forward' : 'fx-stage-back'
  activeStage.value = next
}, { immediate: true })
const interventionPending = computed(() => (
  interventionSummary.value.ungraded + interventionSummary.value.failed + interventionSummary.value.review
))
const stageSteps = computed<StepProgressStep[]>(() => {
  const uploadState = store.uploadBatch?.state
  const preparing = (uploadState === 'draft' && (store.uploadBatch?.file_count ?? 0) > 0)
    || Boolean(store.replacementBatch) || preflight.preflightActive.value
  const prepareStatus = store.preflight && !preflight.matchConflicts.value.length && !preflight.pendingCount.value ? 'done'
    : preparing || (store.preflight && (preflight.matchConflicts.value.length || preflight.pendingCount.value))
      ? 'in_progress' : 'todo'
  const gradeStatus = runTerminal.value || gradingCompletedWithoutRun.value ? 'done'
    : store.gradingRun || gradingStarting.value ? 'in_progress' : 'todo'
  const reviewStatus = interventionState.value !== 'ready' ? 'todo'
    : interventionPending.value > 0 ? 'in_progress'
      : interventionSummary.value.total > 0 ? 'done' : 'todo'
  return [
    {
      id: 'prepare', label: '答卷与预检',
      status: prepareStatus,
      available: Boolean(store.workspace),
      hint: preflight.preflightActive.value ? '预检进行中' : prepareStatus === 'done' ? '已核对'
        : prepareStatus === 'in_progress' ? '需核对' : '待上传',
    },
    {
      id: 'grade', label: '批改',
      status: gradeStatus,
      available: Boolean(store.preflight),
      hint: gradeStatus === 'done' ? '已结束' : gradeStatus === 'in_progress' ? '进行中' : '待发起',
    },
    {
      id: 'review', label: '复核',
      status: reviewStatus,
      available: Boolean(store.preflight),
      hint: reviewStatus === 'in_progress' ? `待处理 ${interventionPending.value} 项`
        : reviewStatus === 'done' ? '已处理完' : '待建立',
    },
  ]
})
function selectStage(id: string): void {
  const next = id as ScanStageId
  if (!SCAN_STAGE_ORDER.includes(next)) return
  if (!stageSteps.value.find((step) => step.id === next)?.available) return
  stageTransition.value = SCAN_STAGE_ORDER.indexOf(next) >= SCAN_STAGE_ORDER.indexOf(activeStage.value)
    ? 'fx-stage-forward' : 'fx-stage-back'
  activeStage.value = next
}

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

onMounted(() => {
  void loadRoute()
})
watch(sessionId, () => {
  gradingSubmissionPending.value = false
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
// 复核面板显示且摘要就绪时读取成绩；摘要重载（批改完成等）会再次触发。
watch(
  () => activeStage.value === 'review'
    && interventionState.value === 'ready'
    && interventionSummary.value.total > 0,
  (showReview) => {
    if (showReview && sessionId.value > 0) void resultsStore.load(sessionId.value)
  },
)
</script>

<template>
  <article class="scan-grading">
    <PageHeader title="考试批改">
      <template #actions>
        <StepProgress
          class="scan-stage-rail"
          aria-label="批改执行阶段"
          :steps="stageSteps"
          :current="activeStage"
          @select="selectStage"
        />
      </template>
    </PageHeader>

    <div class="scan-grading__body">
      <main class="scan-grading__main">

    <p v-if="store.errorMessage" class="scan-grading__notice" role="alert">{{ store.errorMessage }}</p>
    <p v-if="store.loadState === 'loading'" class="scan-grading__notice" role="status">正在恢复本次批改工作区…</p>

    <Transition v-if="store.workspace" :name="stageTransition" mode="out-in">
      <ScanPreflightStage
        v-if="activeStage === 'prepare'" key="prepare"
        :recon="preflight"
        @select-stage="selectStage"
      />
      <ScanGradeStage
        v-else-if="activeStage === 'grade'" key="grade"
        :session-id="sessionId"
        :grading-starting="gradingStarting"
        :intervention-summary="interventionSummary"
        @select-stage="selectStage"
        @submission-pending="gradingSubmissionPending = true"
        @submission-failed="gradingSubmissionPending = false"
      />
      <ScanReviewStage
        v-else key="review"
        :session-id="sessionId"
        :questions="interventionQuestions"
        :state="interventionState"
        :summary="interventionSummary"
        @select-stage="selectStage"
      />
    </Transition>
      </main>
    </div>

    <ScanViewerDialog :recon="preflight" />
  </article>
</template>
