<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type {
  ResultsCenterItem,
  ResultsCenterQuestion,
  ResultsCenterStudent,
  ResultsScoreStatus,
  ResultsStudentStatus,
} from '../api/results-center'
import ClassAnalysisPanel from '../components/results-center/ClassAnalysisPanel.vue'
import AppButton from '../components/design-system/AppButton.vue'
import { Input } from '../components/ui/input'
import { useResultsCenterStore } from '../stores/results-center'
import { useSessionStore } from '../stores/session'
import { translateGradingReason } from '../utils/grading-reasons'
import FileCenterView from './FileCenterView.vue'

type ResultsTab = 'overview' | 'details' | 'analysis' | 'exports'
type DetailFilter = 'all' | ResultsStudentStatus
type MatrixSortKey = 'student' | 'total' | 'question'
type SortDirection = 'ascending' | 'descending'

const route = useRoute()
const router = useRouter()
const sessionStore = useSessionStore()
const resultsStore = useResultsCenterStore()
const searchQuery = ref('')
const selectedStudent = ref<ResultsCenterStudent | null>(null)
const matrixSort = ref<{
  key: MatrixSortKey
  direction: SortDirection
  questionId: string | null
}>({
  key: 'student',
  direction: 'ascending',
  questionId: null,
})
const drawer = ref<HTMLElement | null>(null)
const drawerCloseButton = ref<HTMLButtonElement | null>(null)
let drawerTrigger: HTMLElement | null = null

const tabs: Array<{ id: ResultsTab; label: string }> = [
  { id: 'overview', label: '成绩总览' },
  { id: 'details', label: '成绩明细' },
  { id: 'analysis', label: '班级分析' },
  { id: 'exports', label: '导出文件' },
]

const detailFilters: Array<{ id: DetailFilter; label: string }> = [
  { id: 'all', label: '全部学生' },
  { id: 'complete', label: '成绩完整' },
  { id: 'needs_review', label: '待复核' },
  { id: 'incomplete', label: '未评分' },
  { id: 'failed', label: '处理失败' },
]

function stringQuery(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function positiveIntegerQuery(value: unknown): number | null {
  if (typeof value !== 'string' || !/^\d+$/.test(value)) return null
  const parsed = Number(value)
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null
}

const activeTab = computed<ResultsTab>(() => {
  const candidate = stringQuery(route.query.tab)
  return candidate === 'details' || candidate === 'analysis' || candidate === 'exports'
    ? candidate
    : 'overview'
})

const activeFilter = computed<DetailFilter>(() => {
  const candidate = stringQuery(route.query.filter)
  return candidate === 'complete'
    || candidate === 'needs_review'
    || candidate === 'incomplete'
    || candidate === 'failed'
    ? candidate
    : 'all'
})

const results = computed(() => resultsStore.results)
const summary = computed(() => results.value?.summary ?? null)
const studentItemIndex = computed(() => new Map(
  (results.value?.students ?? []).map((student) => [
    student.student_id,
    new Map(student.items.map((item) => [item.question_id, item])),
  ]),
))

const visibleStudents = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase()
  return (results.value?.students ?? []).filter((student) => {
    if (!matchesFilter(student, activeFilter.value)) return false
    if (!query) return true
    return [
      student.student_name,
      student.student_code,
      student.class_name,
    ].some((value) => value?.toLocaleLowerCase().includes(query))
  })
})

const overviewStudents = computed(() => [...visibleStudents.value].sort(
  (left, right) => (
    studentRiskRank(left) - studentRiskRank(right)
    || left.student_name.localeCompare(right.student_name, 'zh-CN')
    || left.student_id - right.student_id
  ),
))

const matrixStudents = computed(() => [...visibleStudents.value].sort(
  (left, right) => compareMatrixStudents(left, right),
))

const scoreBands = computed(() => {
  const bands = [
    { id: 'excellent', label: '90–100%', minimum: 0.9, maximum: 1 },
    { id: 'good', label: '80–89%', minimum: 0.8, maximum: 0.9 },
    { id: 'middle', label: '70–79%', minimum: 0.7, maximum: 0.8 },
    { id: 'pass', label: '60–69%', minimum: 0.6, maximum: 0.7 },
    { id: 'low', label: '低于 60%', minimum: 0, maximum: 0.6 },
  ]
  const complete = (results.value?.students ?? []).filter(
    (student) => student.ungraded_count === 0
      && student.failed_count === 0
      && student.max_score > 0,
  )
  const resolved = bands.map((band) => ({
    ...band,
    count: complete.filter((student) => {
      const rate = student.current_score / student.max_score
      return rate >= band.minimum
        && (band.maximum === 1 ? rate <= 1 : rate < band.maximum)
    }).length,
  }))
  const largest = Math.max(1, ...resolved.map((band) => band.count))
  return resolved.map((band) => ({
    ...band,
    width: `${Math.round((band.count / largest) * 100)}%`,
  }))
})

const questionInsights = computed(() => [...(results.value?.questions ?? [])].sort(
  (left, right) => (
    questionRiskCount(right) - questionRiskCount(left)
    || questionScoreRate(left) - questionScoreRate(right)
    || left.question_id.localeCompare(right.question_id, 'zh-CN')
  ),
))

const orderedDrawerItems = computed(() => {
  if (!selectedStudent.value || !results.value) return []
  const positions = new Map(
    results.value.questions.map((question, index) => [question.question_id, index]),
  )
  return [...selectedStudent.value.items].sort(
    (left, right) => (
      (positions.get(left.question_id) ?? Number.MAX_SAFE_INTEGER)
      - (positions.get(right.question_id) ?? Number.MAX_SAFE_INTEGER)
    ),
  )
})

watch(
  [
    () => route.query.session,
    () => sessionStore.loadState,
    () => sessionStore.sessions.map((session) => session.id).join(','),
  ],
  ([querySession, loadState]) => {
    if (loadState !== 'ready') return
    const requestedSessionId = positiveIntegerQuery(querySession)
    if (
      requestedSessionId === null
      || requestedSessionId === sessionStore.selectedSessionId
      || !sessionStore.sessions.some((session) => session.id === requestedSessionId)
    ) return
    sessionStore.selectSession(requestedSessionId)
  },
  { immediate: true },
)

watch(
  () => sessionStore.selectedSessionId,
  (sessionId) => {
    closeStudentDrawer(false)
    searchQuery.value = ''
    if (sessionId === null) {
      resultsStore.reset()
      return
    }
    void resultsStore.load(sessionId)
  },
  { immediate: true },
)

function matchesFilter(
  student: ResultsCenterStudent,
  filter: DetailFilter,
): boolean {
  if (filter === 'all') return true
  if (filter === 'complete') {
    return student.ungraded_count === 0 && student.failed_count === 0
  }
  if (filter === 'needs_review') return student.needs_review_count > 0
  if (filter === 'incomplete') return student.ungraded_count > 0
  return student.failed_count > 0
}

function selectTab(tab: ResultsTab, filter: DetailFilter = 'all'): void {
  const query: Record<string, string> = {
    tab,
    ...selectedSessionQuery(),
  }
  if (tab === 'details' && filter !== 'all') query.filter = filter
  void router.replace({ path: '/results', query })
}

function selectDetailFilter(filter: DetailFilter): void {
  void router.replace({
    path: '/results',
    query: {
      tab: 'details',
      ...selectedSessionQuery(),
      ...(filter === 'all' ? {} : { filter }),
    },
  })
}

function selectedSessionQuery(): Record<string, string> {
  return sessionStore.selectedSessionId === null
    ? {}
    : { session: String(sessionStore.selectedSessionId) }
}

function refresh(): void {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId !== null) void resultsStore.load(sessionId)
}

function formatScore(value: number | null): string {
  if (value === null) return '—'
  return Number.isInteger(value)
    ? String(value)
    : value.toFixed(1).replace(/\.0$/, '')
}

function scoreStatusLabel(status: ResultsScoreStatus): string {
  return {
    ungraded: '未评分',
    failed: '处理失败',
    ai_review: 'AI 待复核',
    ai_ready: 'AI 已评分',
    teacher_final: '教师已确认',
  }[status]
}

function shortScoreStatusLabel(status: ResultsScoreStatus): string {
  return {
    ungraded: '未评',
    failed: '失败',
    ai_review: '待复核',
    ai_ready: 'AI',
    teacher_final: '教师',
  }[status]
}

function studentStatusLabel(status: ResultsStudentStatus): string {
  return {
    complete: '成绩完整',
    needs_review: '含待复核项',
    incomplete: '尚有未评分',
    failed: '存在处理失败',
  }[status]
}

function studentIssueText(student: ResultsCenterStudent): string {
  const issues: string[] = []
  if (student.failed_count > 0) issues.push(`失败 ${student.failed_count} 题`)
  if (student.ungraded_count > 0) issues.push(`未评分 ${student.ungraded_count} 题`)
  if (student.needs_review_count > 0) issues.push(`待复核 ${student.needs_review_count} 题`)
  return issues.length > 0 ? issues.join(' · ') : '全部题目已有成绩'
}

function studentRiskRank(student: ResultsCenterStudent): number {
  if (student.failed_count > 0) return 0
  if (student.ungraded_count > 0) return 1
  if (student.needs_review_count > 0) return 2
  return 3
}

function questionRiskCount(question: ResultsCenterQuestion): number {
  return question.failed_count + question.ungraded_count + question.needs_review_count
}

function questionScoreRate(question: ResultsCenterQuestion): number {
  if (question.average_score === null || question.max_score <= 0) return 1
  return question.average_score / question.max_score
}

function questionIssueText(question: ResultsCenterQuestion): string {
  const issues: string[] = []
  if (question.failed_count > 0) issues.push(`失败 ${question.failed_count}`)
  if (question.ungraded_count > 0) issues.push(`未评 ${question.ungraded_count}`)
  if (question.needs_review_count > 0) issues.push(`复核 ${question.needs_review_count}`)
  return issues.length > 0 ? issues.join(' · ') : '无需额外处理'
}

function currentTotalPrefix(student: ResultsCenterStudent): string {
  return student.ungraded_count > 0 || student.failed_count > 0 ? '当前 ' : ''
}

function setMatrixSort(key: MatrixSortKey, questionId: string | null = null): void {
  const sameColumn = matrixSort.value.key === key
    && matrixSort.value.questionId === questionId
  matrixSort.value = {
    key,
    questionId,
    direction: sameColumn && matrixSort.value.direction === 'ascending'
      ? 'descending'
      : 'ascending',
  }
}

function matrixAriaSort(
  key: MatrixSortKey,
  questionId: string | null = null,
): SortDirection | 'none' {
  return matrixSort.value.key === key && matrixSort.value.questionId === questionId
    ? matrixSort.value.direction
    : 'none'
}

function matrixSortArrow(
  key: MatrixSortKey,
  questionId: string | null = null,
): string {
  const state = matrixAriaSort(key, questionId)
  return state === 'ascending' ? '↑' : state === 'descending' ? '↓' : '↕'
}

function compareMatrixStudents(
  left: ResultsCenterStudent,
  right: ResultsCenterStudent,
): number {
  const sort = matrixSort.value
  let result = 0
  if (sort.key === 'student') {
    result = (left.student_code ?? '').localeCompare(
      right.student_code ?? '',
      'zh-CN',
      { numeric: true, sensitivity: 'base' },
    )
      || left.student_name.localeCompare(right.student_name, 'zh-CN')
      || left.student_id - right.student_id
  } else if (sort.key === 'total') {
    return compareOptionalRates(
      studentTotalRate(left),
      studentTotalRate(right),
      sort.direction,
    )
  } else {
    return compareOptionalRates(
      resolvedItemRate(itemFor(left, sort.questionId ?? '')),
      resolvedItemRate(itemFor(right, sort.questionId ?? '')),
      sort.direction,
    )
  }
  return sort.direction === 'ascending' ? result : -result
}

function compareOptionalRates(
  left: number | null,
  right: number | null,
  direction: SortDirection,
): number {
  if (left === null && right === null) return 0
  if (left === null) return 1
  if (right === null) return -1
  return direction === 'ascending' ? left - right : right - left
}

function studentTotalRate(student: ResultsCenterStudent): number | null {
  return student.max_score > 0 ? student.current_score / student.max_score : null
}

function resolvedItemRate(item: ResultsCenterItem | null): number | null {
  if (
    !item
    || !['ai_ready', 'teacher_final'].includes(item.score_status)
    || item.score_awarded === null
    || item.max_score <= 0
  ) return null
  return item.score_awarded / item.max_score
}

function heatStyle(rate: number | null): Record<string, string> | undefined {
  if (rate === null) return undefined
  const bounded = Math.max(0, Math.min(1, rate))
  const hue = Math.round(7 + bounded * 126)
  return {
    backgroundColor: `hsl(${hue} 52% 88%)`,
    color: '#172333',
  }
}

function totalHeatStyle(
  student: ResultsCenterStudent,
): Record<string, string> | undefined {
  if (student.ungraded_count > 0 || student.failed_count > 0) return undefined
  return heatStyle(studentTotalRate(student))
}

function questionHeatStyle(
  student: ResultsCenterStudent,
  questionId: string,
): Record<string, string> | undefined {
  return heatStyle(resolvedItemRate(itemFor(student, questionId)))
}

function itemFor(
  student: ResultsCenterStudent,
  questionId: string,
): ResultsCenterItem | null {
  return studentItemIndex.value.get(student.student_id)?.get(questionId) ?? null
}

function confidenceLabel(value: number | null): string | null {
  if (value === null) return null
  const percent = value <= 1 ? value * 100 : value
  return `置信度 ${Math.round(percent)}%`
}

function navigateToReview(item: ResultsCenterItem): void {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId === null) return
  void router.push({
    path: '/grading',
    query: {
      session: String(sessionId),
      scope: 'all',
      question: item.question_id,
      item: item.review_item_id,
      entry: 'results',
    },
  })
}

function navigateQuestionToReview(questionId: string): void {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId === null) return
  void router.push({
    path: '/grading',
    query: {
      session: String(sessionId),
      scope: 'all',
      question: questionId,
      entry: 'results',
    },
  })
}

function openStudentDrawer(
  student: ResultsCenterStudent,
  event: MouseEvent,
): void {
  drawerTrigger = event.currentTarget instanceof HTMLElement
    ? event.currentTarget
    : null
  selectedStudent.value = student
  void nextTick(() => drawerCloseButton.value?.focus())
}

function closeStudentDrawer(restoreFocus = true): void {
  if (selectedStudent.value === null) return
  const trigger = drawerTrigger
  selectedStudent.value = null
  drawerTrigger = null
  if (restoreFocus && trigger) void nextTick(() => trigger.focus())
}

function handleDrawerKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeStudentDrawer()
    return
  }
  if (event.key !== 'Tab' || !drawer.value) return
  const focusable = [...drawer.value.querySelectorAll<HTMLElement>(
    'button:not(:disabled), [href], input:not(:disabled), [tabindex]:not([tabindex="-1"])',
  )]
  if (focusable.length === 0) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}
</script>

<template>
  <section class="results-center" aria-labelledby="results-center-title">
    <header class="results-center__hero">
      <div>
        <p class="results-center__eyebrow">当前考试 · 成绩与出件</p>
        <h1 id="results-center-title" tabindex="-1">成绩中心</h1>
        <p>集中查看班级成绩、定位需要处理的题目，并导出正式文件。</p>
      </div>
      <div
        v-if="activeTab === 'overview' || activeTab === 'details'"
        class="results-center__hero-actions"
      >
        <span v-if="resultsStore.updatedAt">
          已更新 {{ resultsStore.updatedAt.replace('T', ' ').slice(0, 19) }}
        </span>
        <AppButton class="results-button results-button--secondary" @click="refresh">
          刷新成绩
        </AppButton>
      </div>
    </header>

    <nav class="results-tabs" aria-label="成绩中心页面">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        type="button"
        :aria-current="activeTab === tab.id ? 'page' : undefined"
        :class="{ 'is-active': activeTab === tab.id }"
        @click="selectTab(tab.id)"
      >
        {{ tab.label }}
      </button>
    </nav>

    <FileCenterView v-if="activeTab === 'exports'" embedded />

    <ClassAnalysisPanel
      v-else-if="activeTab === 'analysis'"
      :session-id="sessionStore.selectedSessionId"
    />

    <template v-else>
      <div v-if="sessionStore.selectedSessionId === null" class="results-state-panel">
        <strong>请先在顶部选择考试</strong>
        <span>选择后，这里会显示该考试的成绩和需要复核的题目。</span>
      </div>

      <div
        v-else-if="resultsStore.state === 'loading' && !results"
        class="results-state-panel"
        role="status"
      >
        <strong>正在整理当前考试成绩</strong>
        <span>学生较多时可能需要片刻。</span>
      </div>

      <div
        v-else-if="resultsStore.state === 'error'"
        class="results-state-panel results-state-panel--error"
        role="alert"
      >
        <strong>成绩暂时无法读取</strong>
        <span>{{ resultsStore.errorMessage }}</span>
        <AppButton class="results-button results-button--secondary" @click="refresh">
          重新加载
        </AppButton>
      </div>

      <template v-else-if="results">
        <div
          v-if="resultsStore.state === 'stale-error'"
          class="results-inline-warning"
          role="alert"
        >
          <span>当前显示上次成功读取的成绩，最新数据暂时无法取得。</span>
          <button type="button" @click="refresh">重新加载</button>
        </div>

        <section class="results-conclusion" aria-labelledby="results-conclusion-title">
          <div class="results-conclusion__heading">
            <div>
              <p class="results-center__eyebrow">阅卷结论</p>
              <h2 id="results-conclusion-title">{{ results.session_name }}</h2>
            </div>
            <span>点击任一数字查看对应学生</span>
          </div>
          <div class="results-conclusion__strip">
            <button type="button" @click="selectTab('details', 'complete')">
              <span>成绩完整</span>
              <strong>{{ summary?.complete_student_count }}<small> / {{ summary?.student_count }} 人</small></strong>
              <em>所有题目均已有成绩</em>
            </button>
            <button type="button" @click="selectTab('details', 'complete')">
              <span>当前平均分</span>
              <strong>
                {{ formatScore(summary?.average_score ?? null) }}
                <small> / {{ formatScore(summary?.max_score ?? null) }}</small>
              </strong>
              <em>
                最高 {{ formatScore(summary?.highest_score ?? null) }} ·
                最低 {{ formatScore(summary?.lowest_score ?? null) }} ·
                计入 {{ summary?.average_sample_count }} 人
              </em>
            </button>
            <button
              type="button"
              class="results-conclusion__risk"
              @click="selectTab('details', 'needs_review')"
            >
              <span>待复核</span>
              <strong>{{ summary?.needs_review_item_count }}<small> 题</small></strong>
              <em>AI 已给分，建议教师核对</em>
            </button>
            <button
              type="button"
              class="results-conclusion__pending"
              @click="selectTab('details', 'incomplete')"
            >
              <span>未评分</span>
              <strong>{{ summary?.ungraded_item_count }}<small> 题</small></strong>
              <em>不计入当前总分与平均分</em>
            </button>
            <button
              type="button"
              class="results-conclusion__danger"
              @click="selectTab('details', 'failed')"
            >
              <span>处理失败</span>
              <strong>{{ summary?.failed_item_count }}<small> 题</small></strong>
              <em>需要重新处理或人工评分</em>
            </button>
          </div>
        </section>

        <div v-if="results.students.length === 0" class="results-state-panel">
          <strong>当前考试还没有可展示的成绩</strong>
          <span>开始批改或人工评分后，成绩会出现在这里。</span>
        </div>

        <template v-else-if="activeTab === 'overview'">
          <div class="results-overview-insights">
            <section class="results-insight" aria-labelledby="score-distribution-title">
              <div class="results-insight__heading">
                <div>
                  <p class="results-center__eyebrow">分数分布</p>
                  <h2 id="score-distribution-title">完整成绩样本</h2>
                </div>
                <span>{{ summary?.average_sample_count }} 人</span>
              </div>
              <div class="results-distribution">
                <div v-for="band in scoreBands" :key="band.id">
                  <span>{{ band.label }}</span>
                  <div aria-hidden="true">
                    <i :style="{ width: band.width }"></i>
                  </div>
                  <strong>{{ band.count }} 人</strong>
                </div>
              </div>
            </section>

            <section class="results-insight" aria-labelledby="question-insights-title">
              <div class="results-insight__heading">
                <div>
                  <p class="results-center__eyebrow">题目情况</p>
                  <h2 id="question-insights-title">需处理题目优先</h2>
                </div>
                <span>{{ results.questions.length }} 题</span>
              </div>
              <div class="results-question-insights">
                <button
                  v-for="question in questionInsights"
                  :key="question.question_id"
                  type="button"
                  @click="navigateQuestionToReview(question.question_id)"
                >
                  <strong>{{ question.question_id }}</strong>
                  <span>
                    均分 {{ formatScore(question.average_score) }}
                    / {{ formatScore(question.max_score) }}
                  </span>
                  <em :class="{ 'has-risk': questionRiskCount(question) > 0 }">
                    {{ questionIssueText(question) }}
                  </em>
                  <span aria-hidden="true">›</span>
                </button>
              </div>
            </section>
          </div>

          <section
            class="results-panel"
            aria-labelledby="overview-students-title"
          >
            <div class="results-panel__heading">
              <div>
                <p class="results-center__eyebrow">学生清单</p>
                <h2 id="overview-students-title">逐人查看</h2>
                <p>需要处理的学生排在前面。</p>
              </div>
              <label class="results-search">
                <span>搜索学生</span>
                <Input
                  v-model="searchQuery"
                  type="search"
                  placeholder="姓名、学号或班级"
                />
              </label>
            </div>
            <div class="results-table-wrap">
              <table class="results-overview-table">
                <thead>
                  <tr>
                    <th scope="col">学生</th>
                    <th scope="col">班级</th>
                    <th scope="col">当前总分</th>
                    <th scope="col">成绩状态</th>
                    <th scope="col">需要处理</th>
                    <th scope="col"><span class="visually-hidden">操作</span></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="student in overviewStudents" :key="student.student_id">
                    <th scope="row">
                      <button type="button" @click="openStudentDrawer(student, $event)">
                        <strong>{{ student.student_name }}</strong>
                        <span>{{ student.student_code || '未填写学号' }}</span>
                      </button>
                    </th>
                    <td>{{ student.class_name || '未填写班级' }}</td>
                    <td>
                      <strong>
                        {{ currentTotalPrefix(student) }}{{ formatScore(student.current_score) }}
                        <small>/ {{ formatScore(student.max_score) }}</small>
                      </strong>
                    </td>
                    <td>
                      <span class="results-status" :data-status="student.status">
                        {{ studentStatusLabel(student.status) }}
                      </span>
                    </td>
                    <td>{{ studentIssueText(student) }}</td>
                    <td>
                      <button
                        type="button"
                        class="results-link-button"
                        @click="openStudentDrawer(student, $event)"
                      >
                        查看逐题
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-if="overviewStudents.length === 0" class="results-table-empty">
              没有符合当前搜索条件的学生。
            </p>
          </section>
        </template>

        <section
          v-else
          class="results-panel results-panel--matrix"
          aria-labelledby="score-matrix-title"
        >
          <div class="results-panel__heading results-panel__heading--matrix">
            <div>
              <p class="results-center__eyebrow">逐题明细</p>
              <h2 id="score-matrix-title">学生 × 题号</h2>
              <p>未评分以“—”显示，不按零分计算；点击任一分数可进入人工干预。</p>
            </div>
            <label class="results-search">
              <span>搜索学生</span>
              <Input
                v-model="searchQuery"
                type="search"
                placeholder="姓名、学号或班级"
              />
            </label>
          </div>

          <div class="results-filter-bar" aria-label="学生成绩筛选">
            <button
              v-for="filter in detailFilters"
              :key="filter.id"
              type="button"
              :aria-pressed="activeFilter === filter.id"
              :class="{ 'is-active': activeFilter === filter.id }"
              @click="selectDetailFilter(filter.id)"
            >
              {{ filter.label }}
            </button>
            <span>显示 {{ visibleStudents.length }} / {{ results.students.length }} 人</span>
          </div>

          <div class="results-matrix-wrap" tabindex="0" aria-label="逐题成绩表，可横向滚动">
            <table class="results-matrix">
              <thead>
                <tr>
                  <th
                    scope="col"
                    class="results-matrix__identity"
                    :aria-sort="matrixAriaSort('student')"
                  >
                    <button type="button" class="results-matrix__sort" @click="setMatrixSort('student')">
                      学生 <span aria-hidden="true">{{ matrixSortArrow('student') }}</span>
                    </button>
                  </th>
                  <th
                    scope="col"
                    class="results-matrix__total"
                    :aria-sort="matrixAriaSort('total')"
                  >
                    <button type="button" class="results-matrix__sort" @click="setMatrixSort('total')">
                      当前总分 <span aria-hidden="true">{{ matrixSortArrow('total') }}</span>
                    </button>
                  </th>
                  <th
                    v-for="question in results.questions"
                    :key="question.question_id"
                    scope="col"
                    :aria-sort="matrixAriaSort('question', question.question_id)"
                  >
                    <button
                      type="button"
                      class="results-matrix__sort"
                      @click="setMatrixSort('question', question.question_id)"
                    >
                      <strong>{{ question.question_id }}</strong>
                      <i aria-hidden="true">{{ matrixSortArrow('question', question.question_id) }}</i>
                    </button>
                    <span>
                      均分 {{ formatScore(question.average_score) }}
                      / {{ formatScore(question.max_score) }}
                    </span>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="student in matrixStudents" :key="student.student_id">
                  <th scope="row" class="results-matrix__identity">
                    <button type="button" @click="openStudentDrawer(student, $event)">
                      <strong>{{ student.student_name }}</strong>
                      <span>
                        {{ student.student_code || '无学号' }}
                        <template v-if="student.class_name"> · {{ student.class_name }}</template>
                      </span>
                    </button>
                  </th>
                  <td
                    class="results-matrix__total"
                    :style="totalHeatStyle(student)"
                  >
                    <strong>
                      {{ currentTotalPrefix(student) }}{{ formatScore(student.current_score) }}
                      <small>/ {{ formatScore(student.max_score) }}</small>
                    </strong>
                    <span>{{ studentIssueText(student) }}</span>
                  </td>
                  <td
                    v-for="question in results.questions"
                    :key="question.question_id"
                    class="results-matrix__score"
                    :style="questionHeatStyle(student, question.question_id)"
                  >
                    <button
                      v-if="itemFor(student, question.question_id)"
                      type="button"
                      :data-status="itemFor(student, question.question_id)!.score_status"
                      :aria-label="`${student.student_name}，${question.question_id}，${scoreStatusLabel(itemFor(student, question.question_id)!.score_status)}，得分 ${formatScore(itemFor(student, question.question_id)!.score_awarded)}，进入人工干预`"
                      @click="navigateToReview(itemFor(student, question.question_id)!)"
                    >
                      <strong>{{ formatScore(itemFor(student, question.question_id)!.score_awarded) }}</strong>
                      <span>{{ shortScoreStatusLabel(itemFor(student, question.question_id)!.score_status) }}</span>
                    </button>
                    <span v-else class="results-matrix__missing">—<small>无记录</small></span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-if="visibleStudents.length === 0" class="results-table-empty">
            没有符合当前筛选条件的学生。
          </p>
        </section>
      </template>
    </template>
  </section>

  <Teleport to="body">
    <div
      v-if="selectedStudent"
      class="results-drawer-backdrop"
      @mousedown.self="closeStudentDrawer()"
    >
      <aside
        ref="drawer"
        class="results-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="student-result-title"
        @keydown="handleDrawerKeydown"
      >
        <header class="results-drawer__header">
          <div>
            <p class="results-center__eyebrow">学生成绩详情</p>
            <h2 id="student-result-title">{{ selectedStudent.student_name }}</h2>
            <p>
              {{ selectedStudent.student_code || '未填写学号' }}
              <template v-if="selectedStudent.class_name"> · {{ selectedStudent.class_name }}</template>
            </p>
          </div>
          <button
            ref="drawerCloseButton"
            type="button"
            class="results-drawer__close"
            aria-label="关闭学生成绩详情"
            @click="closeStudentDrawer()"
          >
            关闭
          </button>
        </header>

        <div class="results-drawer__total">
          <span>{{ currentTotalPrefix(selectedStudent) }}总分</span>
          <strong>
            {{ formatScore(selectedStudent.current_score) }}
            <small>/ {{ formatScore(selectedStudent.max_score) }}</small>
          </strong>
          <em>{{ studentIssueText(selectedStudent) }}</em>
        </div>

        <div class="results-drawer__items">
          <button
            v-for="item in orderedDrawerItems"
            :key="item.review_item_id"
            type="button"
            :data-status="item.score_status"
            @click="navigateToReview(item)"
          >
            <span class="results-drawer__question">{{ item.question_id }}</span>
            <span class="results-drawer__item-score">
              <strong>{{ formatScore(item.score_awarded) }}</strong>
              / {{ formatScore(item.max_score) }}
            </span>
            <span class="results-status" :data-status="item.score_status">
              {{ scoreStatusLabel(item.score_status) }}
            </span>
            <span class="results-drawer__reason">
              {{ item.review_reason ? translateGradingReason(item.review_reason) : confidenceLabel(item.confidence_score) || '点击查看答卷与评分依据' }}
            </span>
            <span aria-hidden="true">›</span>
          </button>
        </div>
      </aside>
    </div>
  </Teleport>
</template>
