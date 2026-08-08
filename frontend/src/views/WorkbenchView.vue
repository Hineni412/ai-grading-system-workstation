<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import QuestionAnalysisPanel from '../components/analysis/QuestionAnalysisPanel.vue'
import StudentAnalysisDetail from '../components/analysis/StudentAnalysisDetail.vue'
import TagCoverageSummary from '../components/analysis/TagCoverageSummary.vue'
import RecentSessions from '../components/workbench/RecentSessions.vue'
import WorkbenchProgressRail from '../components/workbench/WorkbenchProgressRail.vue'
import { useAnalysisStore } from '../stores/analysis'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import { useSessionStore } from '../stores/session'
import { useWorkbenchStore } from '../stores/workbench'

const router = useRouter()
const sessionStore = useSessionStore()
const curriculumScope = useCurriculumScopeStore()
const workbenchStore = useWorkbenchStore()
const analysisStore = useAnalysisStore()

const selectedClass = ref<string | null>(null)
const selectedQuestionId = ref<string | null>(null)
const showAnomalies = ref(false)
const anomaliesSection = ref<HTMLElement | null>(null)

interface WorkbenchFocusItem {
  id: 'exam' | 'preparation' | 'class-work'
  context: string
  title: string
  detail: string
  action: string
  path: string
}

const todayLabel = new Intl.DateTimeFormat('zh-CN', {
  month: 'long',
  day: 'numeric',
  weekday: 'long',
}).format(new Date())

const greeting = (() => {
  const hour = new Date().getHours()
  if (hour < 11) return '早上好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})()

const currentTermLabel = computed(
  () => curriculumScope.selectedVolume?.label ?? '未限定教学学期',
)

const currentExamName = computed(
  () => workbenchStore.overview?.current_session?.name
    ?? sessionStore.currentSession?.name
    ?? '尚未选择考试',
)

const progressPercent = computed(
  () => workbenchStore.overview?.progress?.progress_percent ?? null,
)

const workbenchFocusItems = computed<WorkbenchFocusItem[]>(() => {
  const sessionId = sessionStore.selectedSessionId
  const progress = workbenchStore.overview?.progress
  const review = workbenchStore.overview?.review
  let examItem: WorkbenchFocusItem

  if (sessionId === null) {
    examItem = {
      id: 'exam',
      context: '考试与阅卷',
      title: '先选择或创建一场考试',
      detail: '选定考试后，这里会显示批改、复核和异常处理中最优先的一项。',
      action: '配置考试',
      path: '/sessions',
    }
  } else if (progress !== null && progress !== undefined && progress.progress_percent < 100) {
    examItem = {
      id: 'exam',
      context: `考试批改 · ${currentExamName.value}`,
      title: `继续完成 ${progress.graded_papers} / ${progress.total_papers} 份批改`,
      detail: review?.item_count
        ? `还有 ${review.item_count} 项需要教师复核，完成后再进入本次学情。`
        : '先完成剩余答卷，系统会持续汇总本次考试学情。',
      action: '继续批改',
      path: `/sessions/${sessionId}/grading-run`,
    }
  } else if ((review?.item_count ?? 0) > 0) {
    examItem = {
      id: 'exam',
      context: `教师复核 · ${currentExamName.value}`,
      title: `处理 ${review?.item_count ?? 0} 项待复核内容`,
      detail: '核对低置信度评分与异常结果，教师确认后再作为正式成绩。',
      action: '开始复核',
      path: '/grading',
    }
  } else {
    examItem = {
      id: 'exam',
      context: `考试学情 · ${currentExamName.value}`,
      title: '查看本次考试的知识与能力表现',
      detail: '批改已完成，可以从知识证据继续安排训练或下一节课。',
      action: '查看学情',
      path: '/knowledge-graph',
    }
  }

  return [
    examItem,
    {
      id: 'preparation',
      context: `备课工作台 · ${currentTermLabel.value}`,
      title: '继续准备下一节课',
      detail: '从教材课时树、题库资料和已确认的班级证据继续备课。',
      action: '进入备课',
      path: '/teaching-prep',
    },
    {
      id: 'class-work',
      context: '班主任工作台 · 今日',
      title: '查看今天需要跟进的班务',
      detail: '继续处理事务、学生关注和家校沟通草稿，最终决定仍由教师作出。',
      action: '查看班务',
      path: '/class-teacher',
    },
  ]
})

const workflowSteps = computed(() => [{
  label: '考试',
  status: sessionStore.selectedSessionId === null ? '待选择' : currentExamName.value,
  path: '/sessions',
}, {
  label: '批改',
  status: progressPercent.value === null ? '等待考试' : `${progressPercent.value}%`,
  path: sessionStore.selectedSessionId === null
    ? '/grading'
    : `/sessions/${sessionStore.selectedSessionId}/grading-run`,
}, {
  label: '学情',
  status: progressPercent.value === 100 ? '可查看' : '随批改更新',
  path: '/knowledge-graph',
}, {
  label: '训练',
  status: curriculumScope.selectedVolumeId ? '按本学期筛选' : '显示全部',
  path: '/question-assembly',
}, {
  label: '备课',
  status: curriculumScope.selectedVolumeId ? '已同步学期' : '待选择学期',
  path: '/teaching-prep',
}])

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

function openPath(path: string): void {
  void router.push(path)
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
    <header class="workbench-home-hero">
      <div class="workbench-home-hero__copy">
        <p class="workbench-home-kicker">{{ todayLabel }}</p>
        <h1 id="workbench-title" tabindex="-1">{{ greeting }}，今天先完成这三件事</h1>
        <p>{{ currentTermLabel }}{{ curriculumScope.selectedVolumeId ? '已作为当前教学学期' : ' · 当前显示全部学期内容' }}</p>
      </div>
      <div class="workbench-home-hero__summary" aria-label="今日工作概况">
        <strong>3</strong>
        <span>项优先工作</span>
        <small>按教学影响排序</small>
      </div>
    </header>

    <div v-if="workbenchStore.overviewState === 'stale-error'" class="workbench-stale" role="alert">
      <span>考试数据可能不是最新 · 上次更新 {{ formatTime(workbenchStore.overviewUpdatedAt) }}</span>
      <button type="button" class="workbench-link-button" @click="retryOverview">重新加载考试概况</button>
    </div>
    <div v-else-if="workbenchStore.overviewState === 'error'" class="workbench-inline-error" role="alert">
      <p>考试概况暂时无法读取；备课和班务入口仍可使用。</p>
      <button type="button" class="workbench-secondary-button" @click="retryOverview">重新加载考试概况</button>
    </div>

    <div class="workbench-home-dashboard">
      <section class="workbench-focus-board" aria-labelledby="workbench-focus-title">
        <header class="workbench-home-section-heading">
          <div>
            <p class="workbench-home-kicker">TEACHING FOCUS</p>
            <h2 id="workbench-focus-title">今日焦点</h2>
          </div>
          <span>按影响排序</span>
        </header>
        <ol class="workbench-focus-list">
          <li
            v-for="(item, index) in workbenchFocusItems"
            :key="item.id"
            :class="{ 'is-primary': index === 0 }"
          >
            <span class="workbench-focus-list__number">{{ String(index + 1).padStart(2, '0') }}</span>
            <div>
              <small>{{ item.context }}</small>
              <h3>{{ item.title }}</h3>
              <p>{{ item.detail }}</p>
            </div>
            <button type="button" class="workbench-focus-list__action" @click="openPath(item.path)">
              {{ item.action }} <span aria-hidden="true">→</span>
            </button>
          </li>
        </ol>
      </section>

      <aside class="workbench-pulse" aria-labelledby="workbench-pulse-title">
        <header class="workbench-home-section-heading">
          <div>
            <p class="workbench-home-kicker">CURRENT EXAM</p>
            <h2 id="workbench-pulse-title">教学脉搏</h2>
          </div>
        </header>
        <p class="workbench-pulse__exam">{{ currentExamName }}</p>
        <div class="workbench-pulse__score">
          <strong>{{ progressPercent ?? '—' }}</strong>
          <span>{{ progressPercent === null ? '尚无批改进度' : '% 已批改' }}</span>
        </div>
        <div class="workbench-pulse__track" aria-hidden="true">
          <span :style="{ width: `${progressPercent ?? 0}%` }" />
        </div>
        <dl class="workbench-pulse__stats">
          <div>
            <dt>已批改</dt>
            <dd>{{ workbenchStore.overview?.progress?.graded_papers ?? '—' }}<small>份</small></dd>
          </div>
          <div>
            <dt>待复核</dt>
            <dd>{{ workbenchStore.overview?.review?.item_count ?? '—' }}<small>项</small></dd>
          </div>
          <div>
            <dt>异常</dt>
            <dd>{{ anomalyCount ?? '—' }}<small>条</small></dd>
          </div>
        </dl>
        <button type="button" class="workbench-pulse__link" @click="openPath('/knowledge-graph')">
          查看完整学情证据 →
        </button>
      </aside>
    </div>

    <section class="workbench-workflow" aria-labelledby="workbench-workflow-title">
      <div class="workbench-workflow__intro">
        <p class="workbench-home-kicker">ONE CONTINUOUS LOOP</p>
        <h2 id="workbench-workflow-title">从一次考试，走到下一堂课</h2>
      </div>
      <ol class="workbench-workflow__steps">
        <li v-for="(step, index) in workflowSteps" :key="step.label">
          <button type="button" @click="openPath(step.path)">
            <span>{{ String(index + 1).padStart(2, '0') }}</span>
            <strong>{{ step.label }}</strong>
            <small>{{ step.status }}</small>
          </button>
        </li>
      </ol>
    </section>

    <section v-if="sessionStore.selectedSessionId !== null" class="workbench-exam-detail" aria-labelledby="workbench-exam-detail-title">
      <header class="workbench-exam-detail__heading">
        <div>
          <p class="workbench-home-kicker">CURRENT EXAM DETAIL</p>
          <h2 id="workbench-exam-detail-title">当前考试详情</h2>
          <span>{{ currentExamName }} · 数据更新 {{ formatTime(workbenchStore.overview?.updated_at ?? null) }}</span>
        </div>
        <div class="workbench-exam-detail__actions">
          <button type="button" class="workbench-primary-button" @click="openGradingRun">进入批改执行</button>
          <button
            type="button"
            class="workbench-secondary-button"
            data-testid="workbench-students"
            @click="openStudents"
          >
            管理学生名单
          </button>
          <button type="button" class="workbench-secondary-button" @click="retryOverview">刷新考试数据</button>
        </div>
      </header>

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

    </section>

    <section v-else class="workbench-no-exam" aria-labelledby="workbench-no-exam-title">
      <div>
        <p class="workbench-home-kicker">START HERE</p>
        <h2 id="workbench-no-exam-title">还没有选择考试</h2>
        <p>可以先选择最近考试，或创建一场新考试；备课和班主任工作台不受影响。</p>
        <button type="button" class="workbench-primary-button" @click="openPath('/sessions')">选择或创建考试</button>
      </div>
      <RecentSessions
        :sessions="workbenchStore.overview?.recent_sessions ?? []"
        :current-session-id="sessionStore.selectedSessionId"
        @select="selectRecentSession"
        @archived="archivedRecentSession"
      />
    </section>
  </section>
</template>
