<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import PageHeader from '../components/design-system/PageHeader.vue'
import AppButton from '../components/design-system/AppButton.vue'
import FeedbackBanner from '../components/design-system/FeedbackBanner.vue'
import WorkbenchTodoList from '../components/workbench/WorkbenchTodoList.vue'
import WorkbenchCurrentExam from '../components/workbench/WorkbenchCurrentExam.vue'
import WorkbenchSemesterInsight from '../components/workbench/WorkbenchSemesterInsight.vue'
import WorkbenchRecentExams from '../components/workbench/WorkbenchRecentExams.vue'
import { activeJobs, buildTodoRows, buildExamSteps, setupPath } from '../components/workbench/workbench-home'
import { fetchRegionReadiness, type RegionReadiness } from '../api/template-regions'
import { getSessionQuestionBankAnalysisStatus, type SessionQuestionBankAnalysisStatus } from '../api/session-question-bank-sync'
import { trainingApi, type TrainingPendingSummary } from '../api/training'
import { useSessionSwitch } from '../composables/useSessionSwitch'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import { useSessionStore } from '../stores/session'
import { useWorkbenchStore } from '../stores/workbench'
import { useMasteryOverviewStore } from '../stores/mastery-overview'
import { useJobStore } from '../stores/jobs'
import '../styles/workbench.css'

const router = useRouter()
const sessionStore = useSessionStore()
const curriculumScope = useCurriculumScopeStore()
const workbenchStore = useWorkbenchStore()
const mastery = useMasteryOverviewStore()
const jobStore = useJobStore()
const { switchSession } = useSessionSwitch()
const readiness = ref<RegionReadiness | null>(null)
const analysis = ref<SessionQuestionBankAnalysisStatus | null>(null)
const training = ref<TrainingPendingSummary | null>(null)
const trainingState = ref('idle')
const readinessState = ref('idle')
const analysisState = ref('idle')
let readinessController: AbortController | null = null
let analysisController: AbortController | null = null
let trainingController: AbortController | null = null
let disposed = false
let generation = 0

const todayLabel = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(new Date())
const hour = new Date().getHours()
const greeting = hour < 11 ? '早上好' : hour < 14 ? '中午好' : hour < 18 ? '下午好' : '晚上好'
const currentTermLabel = computed(() => curriculumScope.selectedVolume?.label ?? '未限定教学学期')
const jobs = computed(() => activeJobs(Object.values(jobStore.jobs)))
const inputs = computed(() => ({ sessionId: sessionStore.selectedSessionId, overview: workbenchStore.overview,
  readiness: readiness.value, analysis: analysis.value, jobs: jobs.value, training: training.value }))
const todo = computed(() => buildTodoRows(inputs.value))
const steps = computed(() => buildExamSteps(inputs.value))
const currentStep = computed(() => steps.value.find(step => step.status !== 'done')?.id ?? 'results')
const failures = computed(() => [workbenchStore.overviewState === 'error' && '考试概况',
  readinessState.value === 'error' && '考试配置状态', analysisState.value === 'error' && '题库资料状态',
  trainingState.value === 'error' && '训练回收待办',
  workbenchStore.overview?.current_session && workbenchStore.overview.personal_reports === null && '个人报告状态']
  .filter((source): source is string => !!source))
const todoLoading = computed(() => (workbenchStore.overviewState === 'loading' && !workbenchStore.overview)
  || trainingState.value === 'loading')

async function loadTraining() {
  trainingController?.abort()
  const controller = new AbortController()
  trainingController = controller
  trainingState.value = 'loading'
  training.value = null
  try {
    const result = await trainingApi.getPendingSummary(controller.signal)
    if (controller.signal.aborted) return
    training.value = result; trainingState.value = 'ready'
  } catch {
    if (controller.signal.aborted) return
    trainingState.value = 'error'
  }
}
void nextTick().then(() => { if (!disposed) void loadTraining() })

async function loadReadiness(id: number) {
  readinessController?.abort()
  const controller = new AbortController()
  readinessController = controller
  readinessState.value = 'loading'
  try {
    const result = await fetchRegionReadiness(id, controller.signal)
    if (controller.signal.aborted || sessionStore.selectedSessionId !== id) return
    readiness.value = result; readinessState.value = 'ready'
  } catch {
    if (controller.signal.aborted) return
    readiness.value = null; readinessState.value = 'error'
  }
}
async function loadAnalysis(id: number) {
  analysisController?.abort()
  const controller = new AbortController()
  analysisController = controller
  analysisState.value = 'loading'
  try {
    const result = await getSessionQuestionBankAnalysisStatus(id, controller.signal)
    if (controller.signal.aborted || sessionStore.selectedSessionId !== id) return
    analysis.value = result; analysisState.value = 'ready'
  } catch {
    if (controller.signal.aborted) return
    analysis.value = null; analysisState.value = 'error'
  }
}
async function loadExam() {
  const id = sessionStore.selectedSessionId
  const currentGeneration = ++generation
  const overviewRequest = workbenchStore.loadOverview(id, curriculumScope.selectedVolumeId)
  const readinessRequest = id !== null ? loadReadiness(id) : Promise.resolve()
  await overviewRequest
  if (disposed || generation !== currentGeneration) return
  if (id !== null && ['ready', 'stale-error'].includes(workbenchStore.overviewState)) void loadAnalysis(id)
  await readinessRequest
}
watch(() => sessionStore.selectedSessionId, id => {
  readinessController?.abort(); analysisController?.abort()
  readiness.value = null; analysis.value = null
  readinessState.value = 'idle'; analysisState.value = 'idle'
  workbenchStore.resetForSession(id)
  void loadExam()
}, { immediate: true })
watch(() => curriculumScope.selectedVolumeId, () => { void loadExam() })
// 下一次 DOM 渲染后读取学情，首页其他内容不等待这项汇总。
watch(() => curriculumScope.selectedVolumeId, async volumeId => {
  await nextTick()
  if (!disposed && curriculumScope.selectedVolumeId === volumeId) void mastery.peek(volumeId)
}, { immediate: true, flush: 'post' })
onBeforeUnmount(() => { disposed = true; generation += 1; readinessController?.abort(); analysisController?.abort(); trainingController?.abort() })

function openPath(path: string) { void router.push(path) }
function retry(source: string) {
  const id = sessionStore.selectedSessionId
  if (source === '训练回收待办') void loadTraining()
  else if (source === '题库资料状态' && id !== null) void loadAnalysis(id)
  else if (source === '考试配置状态' && id !== null) void loadReadiness(id)
  else void loadExam()
}
function selectStep(step: string) {
  const id = sessionStore.selectedSessionId
  if (id === null) return
  openPath(step === 'setup' ? setupPath(id, readiness.value) : step === 'review' ? '/grading'
    : step === 'results' ? '/results' : `/sessions/${id}/grading-run`)
}
function selectExam(id: number, results: boolean) {
  if (!switchSession(id)) return
  if (results) openPath('/results')
}
</script>
<template>
  <section class="workbench-view fx-enter" aria-labelledby="workbench-title">
    <PageHeader title="工作台" title-id="workbench-title">
      <template #meta>{{ greeting }} · {{ todayLabel }} · {{ currentTermLabel }}</template>
      <template #actions><AppButton @click="openPath('/sessions')">新建考试</AppButton></template>
    </PageHeader>
    <div class="workbench-content">
      <FeedbackBanner v-if="workbenchStore.overviewState === 'stale-error'" tone="warning" title="考试数据可能不是最新" :description="`上次更新 ${workbenchStore.overviewUpdatedAt?.replace('T', ' ').replace('Z', '') ?? '时间暂不可用'}`" action-label="重新加载" @action="loadExam" />
      <FeedbackBanner v-else-if="workbenchStore.overviewState === 'error'" tone="warning" title="考试概况暂时无法读取，其他入口仍可使用。" action-label="重新加载" @action="loadExam" />
      <div class="workbench-grid fx-stagger">
        <WorkbenchTodoList :need="todo.need" :continued="todo.continued" :jobs="jobs" :loading="todoLoading" :failures="failures" @open="openPath" @retry="retry" />
        <aside class="workbench-sidebar fx-stagger">
          <WorkbenchCurrentExam :overview="workbenchStore.overview" :state="workbenchStore.overviewState" :session-id="sessionStore.selectedSessionId" :steps="steps" :current="currentStep" @open="openPath" @select="selectStep" @retry="loadExam" />
          <WorkbenchSemesterInsight :overview="mastery.overview" :state="mastery.loadState" :volume-id="curriculumScope.selectedVolumeId" :term-label="currentTermLabel" :scope="mastery.scopeSelection" @open="openPath" @retry="mastery.load(curriculumScope.selectedVolumeId, true, false)" />
        </aside>
        <WorkbenchRecentExams :items="workbenchStore.overview?.recent_sessions ?? []" :session-id="sessionStore.selectedSessionId" :review-count="workbenchStore.overview?.review?.item_count ?? null" :loading="workbenchStore.overviewState === 'loading'" :error="workbenchStore.overviewState === 'error'" @select="selectExam" @create="openPath('/sessions')" />
      </div>
    </div>
  </section>
</template>
