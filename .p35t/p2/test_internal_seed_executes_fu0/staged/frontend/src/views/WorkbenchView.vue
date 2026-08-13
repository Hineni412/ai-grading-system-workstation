<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import QuestionAnalysisPanel from '../components/analysis/QuestionAnalysisPanel.vue'
import StudentAnalysisDetail from '../components/analysis/StudentAnalysisDetail.vue'
import TagCoverageSummary from '../components/analysis/TagCoverageSummary.vue'
import RecentSessions from '../components/workbench/RecentSessions.vue'
import WorkbenchProgressRail from '../components/workbench/WorkbenchProgressRail.vue'
import { useAnalysisStore } from '../stores/analysis'
import { useSessionStore } from '../stores/session'
import { useWorkbenchStore } from '../stores/workbench'

const router = useRouter()
const sessionStore = useSessionStore()
const workbenchStore = useWorkbenchStore()
const analysisStore = useAnalysisStore()

const selectedClass = ref<string | null>(null)
const selectedQuestionId = ref<string | null>(null)
const showAnomalies = ref(false)
const anomaliesSection = ref<HTMLElement | null>(null)

const anomalyCount = computed(() => {
  const value = workbenchStore.overview?.anomalies
  return value === null || value === undefined
    ? null
    : value.unmatched_papers + value.scan_issue_students + value.failed_papers
})

const progressValue = computed(() => {
  const value = workbenchStore.overview?.progress
  return value ? `${value.graded_papers} / ${value.total_papers} 份` : null
})

const progressStatus = computed(() => {
  const value = workbenchStore.overview?.progress
  return value ? `${value.progress_percent}% 已批改` : '批改数量暂不可用'
})

const reviewValue = computed(() => {
  const value = workbenchStore.overview?.review
  return value ? `${value.item_count} 项` : null
})

const reviewStatus = computed(() => {
  const value = workbenchStore.overview?.review
  if (value === null || value === undefined) return '复核数量暂不可用'
  return value.item_count === 0 ? '当前没有待复核项目' : `${value.question_count} 道题需要核对`
})

const anomalyValue = computed(() => anomalyCount.value === null ? null : `${anomalyCount.value} 条`)
const anomalyStatus = computed(() => {
  if (anomalyCount.value === null) return '异常数量暂不可用'
  return anomalyCount.value === 0 ? '当前没有异常记录' : '可查看只读异常清单'
})

watch(
  () => sessionStore.selectedSessionId,
  (sessionId) => {
    selectedClass.value = null
    selectedQuestionId.value = null
    showAnomalies.value = false
    workbenchStore.resetForSession(sessionId)
    analysisStore.resetForSession(sessionId)
    const loads: Promise<void>[] = [workbenchStore.loadOverview(sessionId)]
    if (sessionId !== null) loads.push(analysisStore.loadQuestions(sessionId, null))
    void Promise.all(loads)
  },
  { immediate: true },
)

watch(
  () => analysisStore.questions,
  (items) => {
    if (selectedQuestionId.value !== null && items.some((item) => item.question_id === selectedQuestionId.value)) return
    const nextQuestionId = items[0]?.question_id ?? null
    selectedQuestionId.value = nextQuestionId
    const sessionId = sessionStore.selectedSessionId
    if (sessionId !== null && nextQuestionId !== null) {
      void analysisStore.loadStudents(sessionId, nextQuestionId, selectedClass.value)
    }
  },
  { deep: true, immediate: true },
)

function formatTime(value: string | null): string {
  return value?.replace('T', ' ').replace('Z', '') ?? '时间暂不可用'
}

function openGrading(questionId?: string): void {
  void router.push(questionId ? { path: '/grading', query: { question: questionId } } : '/grading')
}

function openGradingRun(): void {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId !== null) void router.push(`/sessions/${sessionId}/grading-run`)
}

function openStudents(): void {
  void router.push('/students')
}

async function openAnomalies(): Promise<void> {
  showAnomalies.value = true
  const sessionId = sessionStore.selectedSessionId
  if (sessionId !== null) await workbenchStore.loadAnomalies(sessionId)
  await nextTick()
  anomaliesSection.value?.focus()
}

function selectRecentSession(sessionId: number): void {
  sessionStore.selectSession(sessionId)
}

function archivedRecentSession(): void {
  void workbenchStore.loadOverview(sessionStore.selectedSessionId)
}

async function selectClass(className: string | null): Promise<void> {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId === null) return
  selectedClass.value = className
  selectedQuestionId.value = null
  analysisStore.resetStudents()
  const loads: Promise<void>[] = [analysisStore.loadQuestions(sessionId, className)]
  if (className !== null) loads.push(analysisStore.loadGraph(sessionId, className))
  else await analysisStore.loadGraph(sessionId, '')
  await Promise.all(loads)
}

async function selectQuestion(questionId: string): Promise<void> {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId === null) return
  selectedQuestionId.value = questionId
  await analysisStore.loadStudents(sessionId, questionId, selectedClass.value)
}

function retryOverview(): void {
  void workbenchStore.loadOverview(sessionStore.selectedSessionId)
}

function retryQuestions(): void {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId !== null) void analysisStore.loadQuestions(sessionId, selectedClass.value)
}

function retryStudents(): void {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId !== null && selectedQuestionId.value !== null) {
    void analysisStore.loadStudents(sessionId, selectedQuestionId.value, selectedClass.value)
  }
}

function retryGraph(): void {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId !== null && selectedClass.value !== null) {
    void analysisStore.loadGraph(sessionId, selectedClass.value)
  }
}

function loadMoreQuestions(): void {
  void analysisStore.loadMoreQuestions()
}

function loadMoreStudents(): void {
  void analysisStore.loadMoreStudents()
}

function loadMoreAnomalies(): void {
  void workbenchStore.loadMoreAnomalies()
}
</script>

<template>
  <section class="workbench-view" aria-labelledby="workbench-title">
    <header class="workbench-hero">
      <div>
        <p class="workbench-eyebrow">当前考试只读总览</p>
        <h1 id="workbench-title" tabindex="-1">工作台</h1>
        <p class="workbench-hero__session">
          {{ workbenchStore.overview?.current_session?.name ?? sessionStore.currentSession?.name ?? '尚未选择考试' }}
        </p>
      </div>
      <div class="workbench-hero__meta">
        <span>数据更新 {{ formatTime(workbenchStore.overview?.updated_at ?? null) }}</span>
        <button type="button" class="workbench-primary-button" @click="openGradingRun">进入批改执行</button>
        <button
          type="button"
          class="workbench-secondary-button"
          data-testid="workbench-students"
          @click="openStudents"
        >
          管理学生名单
        </button>
        <button type="button" class="workbench-secondary-button" @click="retryOverview">刷新工作台</button>
      </div>
    </header>

    <p v-if="sessionStore.selectedSessionId === null" class="workbench-empty-copy workbench-empty-copy--page">
      请选择考试后查看工作台
    </p>
    <div v-else class="workbench-view__content">
      <div v-if="workbenchStore.overviewState === 'stale-error'" class="workbench-stale" role="alert">
        <span>数据可能不是最新 · 上次更新 {{ formatTime(workbenchStore.overviewUpdatedAt) }}</span>
        <button type="button" class="workbench-link-button" @click="retryOverview">重新加载工作台</button>
      </div>
      <div v-else-if="workbenchStore.overviewState === 'error'" class="workbench-inline-error" role="alert">
        <p>工作台数据暂时无法读取</p>
        <button type="button" class="workbench-secondary-button" @click="retryOverview">重新加载工作台</button>
      </div>

      <WorkbenchProgressRail
        :progress-value="progressValue"
        :progress-status="progressStatus"
        :review-value="reviewValue"
        :review-status="reviewStatus"
        :anomaly-value="anomalyValue"
        :anomaly-status="anomalyStatus"
        @open-grading="openGrading()"
        @open-review="openGrading()"
        @open-anomalies="openAnomalies"
      />

      <div class="workbench-primary-grid">
        <QuestionAnalysisPanel
          :classes="analysisStore.classes"
          :items="analysisStore.questions"
          :state="analysisStore.questionsState"
          :error="analysisStore.questionsError"
          :updated-at="analysisStore.questionsUpdatedAt"
          :selected-class="selectedClass"
          :selected-question-id="selectedQuestionId"
          :total="analysisStore.questionsTotal"
          :page="analysisStore.questionsPage"
          :total-pages="analysisStore.questionsTotalPages"
          @select-class="selectClass"
          @select-question="selectQuestion"
          @retry="retryQuestions"
          @load-more="loadMoreQuestions"
        />
        <RecentSessions
          :sessions="workbenchStore.overview?.recent_sessions ?? []"
          :current-session-id="sessionStore.selectedSessionId"
          @select="selectRecentSession"
          @archived="archivedRecentSession"
        />
      </div>

      <div class="workbench-secondary-grid">
        <StudentAnalysisDetail
          :question-id="selectedQuestionId"
          :items="analysisStore.students"
          :state="analysisStore.studentsState"
          :updated-at="analysisStore.studentsUpdatedAt"
          :total="analysisStore.studentsTotal"
          :page="analysisStore.studentsPage"
          :total-pages="analysisStore.studentsTotalPages"
          @retry="retryStudents"
          @open-review="openGrading"
          @load-more="loadMoreStudents"
        />
        <TagCoverageSummary
          :session-id="sessionStore.selectedSessionId"
          :class-name="selectedClass"
          :graph="analysisStore.graph"
          :state="analysisStore.graphState"
          :updated-at="analysisStore.graphUpdatedAt"
          @retry="retryGraph"
        />
      </div>

      <section
        v-if="showAnomalies"
        ref="anomaliesSection"
        class="workbench-section workbench-disclosure"
        aria-labelledby="anomaly-list-title"
        tabindex="-1"
      >
        <header class="workbench-section__heading">
          <h2 id="anomaly-list-title">异常记录</h2>
        </header>
        <p
          v-if="workbenchStore.anomaliesState === 'idle' || (workbenchStore.anomaliesState === 'loading' && workbenchStore.anomaliesUpdatedAt === null)"
          class="workbench-state-copy"
          role="status"
        >
          正在读取异常记录…
        </p>
        <div v-else-if="workbenchStore.anomaliesState === 'error'" class="workbench-inline-error" role="alert">
          <p>异常清单暂时无法读取</p>
          <button type="button" class="workbench-secondary-button" @click="openAnomalies">重新加载异常记录</button>
        </div>
        <template v-else>
          <p v-if="workbenchStore.anomaliesState === 'loading'" class="workbench-state-copy" role="status">
            正在更新异常记录…
          </p>
          <div v-if="workbenchStore.anomaliesState === 'stale-error'" class="workbench-stale" role="alert">
            <span>异常记录可能不是最新 · 上次更新 {{ formatTime(workbenchStore.anomaliesUpdatedAt) }}</span>
            <button type="button" class="workbench-link-button" @click="openAnomalies">重新加载异常记录</button>
          </div>
          <p v-if="workbenchStore.anomalies.length === 0" class="workbench-empty-copy">
            {{ workbenchStore.anomaliesUpdatedAt !== null && (workbenchStore.anomaliesState === 'loading' || workbenchStore.anomaliesState === 'stale-error')
              ? '上次检查未发现异常'
              : '当前没有异常记录' }}
          </p>
          <ul v-else class="workbench-readonly-list">
            <li v-for="item in workbenchStore.anomalies" :key="item.anomaly_id">
              <strong>{{ item.display_name }}</strong>
              <span>{{ item.class_name ?? '班级暂不可用' }} · {{ item.status }}</span>
              <span>{{ item.detail ?? '暂无补充说明' }}</span>
            </li>
          </ul>
          <p v-if="workbenchStore.anomalies.length > 0" class="analysis-summary">
            当前显示 {{ workbenchStore.anomalies.length }} / {{ workbenchStore.anomaliesTotal }} 条异常
          </p>
          <button
            v-if="workbenchStore.anomaliesPage < workbenchStore.anomaliesTotalPages"
            type="button"
            class="workbench-secondary-button"
            :disabled="workbenchStore.anomaliesState === 'loading'"
            @click="loadMoreAnomalies"
          >
            {{ workbenchStore.anomaliesState === 'loading' ? '正在加载更多异常…' : '加载更多异常' }}
          </button>
        </template>
      </section>

    </div>
  </section>
</template>
