<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'

import type {
  ResultsCenterItem,
  ResultsCenterStudent,
  ResultsScoreStatus,
  ResultsStudentStatus,
} from '../api/results-center'
import ClassAnalysisPanel from '../components/results-center/ClassAnalysisPanel.vue'
import ResultsOverviewPanel from '../components/results-center/ResultsOverviewPanel.vue'
import AppButton from '../components/design-system/AppButton.vue'
import PageHeader from '../components/design-system/PageHeader.vue'
import { Input } from '../components/ui/input'
import { useResultsCenterStore, type ResultsViewState } from '../stores/results-center'
import { useSessionStore } from '../stores/session'
import { translateGradingReason } from '../utils/grading-reasons'
import FileCenterView from './FileCenterView.vue'

type ResultsTab = 'overview' | 'details' | 'analysis' | 'exports'
type DetailFilter = 'all' | 'attention' | ResultsStudentStatus
type MatrixSortKey = 'student' | 'total' | 'question'
type SortDirection = 'ascending' | 'descending'

const route = useRoute()
const router = useRouter()
const sessionStore = useSessionStore()
const resultsStore = useResultsCenterStore()
const searchQuery = ref('')
const selectedClass = ref<string | null>(null)
const resultsPage = ref<HTMLElement | null>(null)
const matrixScroller = ref<HTMLElement | null>(null)
let pendingRestoration: ResultsViewState | null = null
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
  { id: 'overview', label: '考情总览' },
  { id: 'details', label: '成绩明细' },
  { id: 'analysis', label: '试题诊断' },
  { id: 'exports', label: '导出文件' },
]

const DETAIL_FILTER_LABELS: Record<DetailFilter, string> = {
  all: '全部学生',
  attention: '待处理',
  complete: '成绩完整',
  needs_review: '待复核',
  incomplete: '未评分',
  failed: '处理失败',
}

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

const analysisFocusQuestion = computed(() => (
  activeTab.value === 'analysis' ? stringQuery(route.query.question) : null
))
const analysisInitialClass = computed(() => (
  activeTab.value === 'analysis' ? stringQuery(route.query.class) : null
))
// 总览范围与诊断页共用 class 参数：从诊断返回时总览保持同一班级。
const overviewScope = computed(() => stringQuery(route.query.class))

const activeFilter = computed<DetailFilter>(() => {
  const candidate = stringQuery(route.query.filter)
  return candidate === 'attention'
    || candidate === 'complete'
    || candidate === 'needs_review'
    || candidate === 'incomplete'
    || candidate === 'failed'
    ? candidate
    : 'all'
})

const results = computed(() => resultsStore.results)
const classOptions = computed(() => [...new Set(
  (results.value?.students ?? []).map((student) => student.class_name ?? ''),
)].sort((left, right) => left.localeCompare(right, 'zh-CN', { numeric: true })))
const studentItemIndex = computed(() => new Map(
  (results.value?.students ?? []).map((student) => [
    student.student_id,
    new Map(student.items.map((item) => [item.question_id, item])),
  ]),
))

const studentsInClass = computed(() => (results.value?.students ?? []).filter(
  (student) => selectedClass.value === null || (student.class_name ?? '') === selectedClass.value,
))

const visibleStudents = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase()
  return studentsInClass.value.filter((student) => {
    if (!matchesFilter(student, activeFilter.value)) return false
    if (!query) return true
    return [
      student.student_name,
      student.student_code,
      student.class_name,
      student.pinyin_initials,
      student.pinyin_full,
    ].some((value) => value?.toLocaleLowerCase().includes(query))
  })
})

const classRanks = computed(() => {
  const groups = new Map<string, ResultsCenterStudent[]>()
  for (const student of results.value?.students ?? []) {
    const key = student.class_name ?? ''
    const group = groups.get(key) ?? []
    group.push(student)
    groups.set(key, group)
  }
  const ranks = new Map<number, { rank: number; size: number }>()
  for (const group of groups.values()) {
    const ranked = group
      .filter((student) => student.ungraded_count === 0 && student.failed_count === 0)
      .sort((left, right) => (
        right.current_score - left.current_score || left.student_id - right.student_id
      ))
    let previous: number | null = null
    let rank = 0
    ranked.forEach((student, index) => {
      if (previous === null || student.current_score !== previous) rank = index + 1
      previous = student.current_score
      ranks.set(student.student_id, { rank, size: ranked.length })
    })
  }
  return ranks
})

function studentRankText(student: ResultsCenterStudent): string | null {
  const entry = classRanks.value.get(student.student_id)
  return entry === null || entry === undefined
    ? null
    : `班内第 ${entry.rank} 名`
}

function studentRankTitle(student: ResultsCenterStudent): string | null {
  const entry = classRanks.value.get(student.student_id)
  return entry === null || entry === undefined
    ? null
    : `按当前完整成绩排名，班内共 ${entry.size} 人有名次`
}

const summary = computed(() => {
  const original = results.value?.summary
  if (!original || activeTab.value !== 'details') return original ?? null
  const students = visibleStudents.value
  const complete = students.filter((student) => matchesFilter(student, 'complete'))
  const scores = complete.map((student) => student.current_score)
  const items = students.flatMap((student) => student.items)
  return {
    ...original,
    student_count: students.length,
    complete_student_count: complete.length,
    average_sample_count: complete.length,
    average_score: scores.length ? scores.reduce((total, score) => total + score, 0) / scores.length : null,
    highest_score: scores.length ? Math.max(...scores) : null,
    lowest_score: scores.length ? Math.min(...scores) : null,
    ungraded_item_count: items.filter((item) => item.score_status === 'ungraded').length,
    failed_item_count: items.filter((item) => item.score_status === 'failed').length,
    needs_review_item_count: items.filter((item) => item.score_status === 'ai_review').length,
    ai_ready_item_count: items.filter((item) => item.score_status === 'ai_ready').length,
    teacher_final_item_count: items.filter((item) => item.score_status === 'teacher_final').length,
  }
})

const matrixStudents = computed(() => [...visibleStudents.value].sort(
  (left, right) => compareMatrixStudents(left, right),
))

const matrixQuestions = computed(() => (results.value?.questions ?? []).map((question) => {
  const scores = visibleStudents.value
    .map((student) => itemFor(student, question.question_id))
    .filter((item) => item !== null && item.score_awarded !== null && !['ungraded', 'failed'].includes(item.score_status))
    .map((item) => item!.score_awarded!)
  return { ...question, average_score: scores.length ? scores.reduce((total, score) => total + score, 0) / scores.length : null }
}))

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
    const saved = resultsStore.viewState
    pendingRestoration = saved?.sessionId === sessionId && saved.fullPath === route.fullPath ? saved : null
    searchQuery.value = pendingRestoration?.searchQuery ?? ''
    selectedClass.value = pendingRestoration?.selectedClass ?? null
    matrixSort.value = pendingRestoration
      ? { ...pendingRestoration.matrixSort }
      : { key: 'student', direction: 'ascending', questionId: null }
    if (sessionId === null) {
      resultsStore.reset()
      return
    }
    void resultsStore.load(sessionId)
  },
  { immediate: true },
)

function rememberView(): void {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId === null || activeTab.value !== 'details') return
  const scroller = resultsPage.value?.parentElement
  resultsStore.viewState = {
    sessionId,
    fullPath: route.fullPath,
    searchQuery: searchQuery.value,
    selectedClass: selectedClass.value,
    matrixSort: { ...matrixSort.value },
    scrollTop: scroller?.scrollTop ?? 0,
    scrollLeft: scroller?.scrollLeft ?? 0,
    matrixScrollTop: matrixScroller.value?.scrollTop ?? 0,
    matrixScrollLeft: matrixScroller.value?.scrollLeft ?? 0,
  }
}

async function restoreViewPosition(): Promise<void> {
  const saved = pendingRestoration
  if (!saved || !results.value) return
  await nextTick()
  // 页面标题的路由聚焦完成后，再恢复外层页面和表格的滚动位置。
  requestAnimationFrame(() => {
    if (!resultsPage.value || pendingRestoration !== saved) return
    const scroller = resultsPage.value.parentElement
    if (scroller) {
      scroller.scrollTop = saved.scrollTop
      scroller.scrollLeft = saved.scrollLeft
    }
    if (matrixScroller.value) {
      matrixScroller.value.scrollTop = saved.matrixScrollTop
      matrixScroller.value.scrollLeft = saved.matrixScrollLeft
    }
    pendingRestoration = null
  })
}

onBeforeRouteLeave(rememberView)
onMounted(() => { void restoreViewPosition() })
watch(results, () => { void restoreViewPosition() }, { flush: 'post' })

function matchesFilter(
  student: ResultsCenterStudent,
  filter: DetailFilter,
): boolean {
  if (filter === 'all') return true
  if (filter === 'attention') {
    return student.ungraded_count > 0
      || student.failed_count > 0
      || student.needs_review_count > 0
  }
  if (filter === 'complete') {
    return student.ungraded_count === 0 && student.failed_count === 0
  }
  if (filter === 'needs_review') return student.needs_review_count > 0
  if (filter === 'incomplete') return student.ungraded_count > 0
  return student.failed_count > 0
}

function selectTab(tab: ResultsTab): void {
  const query: Record<string, string> = {
    tab,
    ...selectedSessionQuery(),
  }
  void router.replace({ path: '/results', query })
}

function selectDetailFilter(filter: DetailFilter): void {
  const next = filter !== 'all' && filter === activeFilter.value ? 'all' : filter
  void router.replace({
    path: '/results',
    query: {
      tab: 'details',
      ...selectedSessionQuery(),
      ...(next === 'all' ? {} : { filter: next }),
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

function studentIssueText(student: ResultsCenterStudent): string {
  const issues: string[] = []
  if (student.failed_count > 0) issues.push(`失败 ${student.failed_count} 题`)
  if (student.ungraded_count > 0) issues.push(`未评分 ${student.ungraded_count} 题`)
  if (student.needs_review_count > 0) issues.push(`待复核 ${student.needs_review_count} 题`)
  return issues.length > 0 ? issues.join(' · ') : '全部题目已有成绩'
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

function navigateToReview(item: ResultsCenterItem, student: ResultsCenterStudent): void {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId === null) return
  void router.push({
    path: '/grading',
    query: {
      session: String(sessionId),
      scope: 'all',
      question: item.question_id,
      item: item.review_item_id,
      student: String(student.student_id),
      entry: 'results',
    },
  })
}

function openStudentDrawer(
  student: ResultsCenterStudent,
  event?: MouseEvent,
): void {
  drawerTrigger = event?.currentTarget instanceof HTMLElement
    ? event.currentTarget
    : null
  selectedStudent.value = student
  void nextTick(() => drawerCloseButton.value?.focus())
}

function openStudentById(studentId: number): void {
  const student = results.value?.students.find(
    (entry) => entry.student_id === studentId,
  )
  if (student) openStudentDrawer(student)
}

function openQuestionInAnalysis(
  questionId: string,
  className: string | null,
): void {
  const query: Record<string, string> = {
    tab: 'analysis',
    ...selectedSessionQuery(),
  }
  if (questionId) query.question = questionId
  if (className) query.class = className
  void router.replace({ path: '/results', query })
}

function openAttentionFilter(): void {
  selectDetailFilter('attention')
}

function setOverviewScope(className: string | null): void {
  const query: Record<string, string> = {
    tab: 'overview',
    ...selectedSessionQuery(),
    ...(className ? { class: className } : {}),
  }
  void router.replace({ path: '/results', query })
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
  <section ref="resultsPage" class="results-center" aria-labelledby="results-center-title">
    <PageHeader title="成绩中心" title-id="results-center-title">
      <template #meta>
        <span v-if="results">{{ results.session_name }}</span>
        <span v-if="resultsStore.updatedAt">
          已更新 {{ resultsStore.updatedAt.replace('T', ' ').slice(0, 19) }}
        </span>
      </template>
      <template
        v-if="activeTab === 'overview' || activeTab === 'details'"
        #actions
      >
        <AppButton class="results-button results-button--secondary" @click="refresh">
          刷新成绩
        </AppButton>
      </template>
    </PageHeader>

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
      :focus-question="analysisFocusQuestion"
      :initial-class="analysisInitialClass"
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

        <div v-if="results.students.length === 0" class="results-state-panel">
          <strong>当前考试还没有可展示的成绩</strong>
          <span>开始批改或人工评分后，成绩会出现在这里。</span>
        </div>

        <ResultsOverviewPanel
          v-else-if="activeTab === 'overview'"
          :results="results"
          :session-id="results.session_id"
          :scope="overviewScope"
          @open-student="openStudentById"
          @open-question="openQuestionInAnalysis"
          @open-filter="openAttentionFilter"
          @update:scope="setOverviewScope"
        />

        <template v-else>
        <section class="results-conclusion" aria-label="成绩概况">
          <div class="results-conclusion__line">
            <button
              type="button"
              class="results-chip"
              :aria-pressed="activeFilter === 'complete'"
              @click="selectDetailFilter('complete')"
            >
              成绩完整 {{ summary?.complete_student_count }}/{{ summary?.student_count }} 人
            </button>
            <span class="results-conclusion__stats">
              平均 {{ formatScore(summary?.average_score ?? null) }} ·
              最高 {{ formatScore(summary?.highest_score ?? null) }} ·
              最低 {{ formatScore(summary?.lowest_score ?? null) }}
            </span>
            <template
              v-if="(summary?.needs_review_item_count ?? 0)
                + (summary?.ungraded_item_count ?? 0)
                + (summary?.failed_item_count ?? 0) > 0"
            >
              <button
                v-if="(summary?.needs_review_item_count ?? 0) > 0"
                type="button"
                class="results-chip results-chip--warning"
                :aria-pressed="activeFilter === 'needs_review'"
                @click="selectDetailFilter('needs_review')"
              >
                待复核 {{ summary?.needs_review_item_count }} 题
              </button>
              <button
                v-if="(summary?.ungraded_item_count ?? 0) > 0"
                type="button"
                class="results-chip results-chip--info"
                :aria-pressed="activeFilter === 'incomplete'"
                @click="selectDetailFilter('incomplete')"
              >
                未评分 {{ summary?.ungraded_item_count }} 题
              </button>
              <button
                v-if="(summary?.failed_item_count ?? 0) > 0"
                type="button"
                class="results-chip results-chip--danger"
                :aria-pressed="activeFilter === 'failed'"
                @click="selectDetailFilter('failed')"
              >
                处理失败 {{ summary?.failed_item_count }} 题
              </button>
            </template>
            <span v-else class="results-conclusion__done">阅卷已全部完成</span>
          </div>
        </section>

        <section
          class="results-panel results-panel--matrix"
          aria-labelledby="score-matrix-title"
        >
          <div class="results-panel__heading results-panel__heading--matrix">
            <div>
              <p class="results-center__eyebrow">逐题明细</p>
              <h2 id="score-matrix-title">学生 × 题号</h2>
              <p>未评分以“—”显示，不按零分计算；点击任一分数查看学生作答。</p>
            </div>
            <div class="results-detail-filters">
              <label class="results-class-filter">
                <span>班级</span>
                <select v-model="selectedClass" aria-label="成绩明细班级">
                  <option :value="null">全部班级</option>
                  <option v-for="className in classOptions" :key="className" :value="className">{{ className || '未填写班级' }}</option>
                </select>
              </label>
              <label class="results-search">
                <span>搜索学生</span>
                <Input v-model="searchQuery" type="search" placeholder="姓名、学号、班级或拼音首字母" />
              </label>
            </div>
          </div>

          <div class="results-filter-bar" aria-label="学生成绩筛选">
            <button
              type="button"
              :aria-pressed="activeFilter === 'attention'"
              :class="{ 'is-active': activeFilter === 'attention' }"
              @click="selectDetailFilter('attention')"
            >
              只看待处理学生
            </button>
            <button
              v-if="activeFilter !== 'all'"
              type="button"
              class="results-filter-chip"
              :aria-label="`清除筛选：${DETAIL_FILTER_LABELS[activeFilter]}`"
              @click="selectDetailFilter('all')"
            >
              已筛选：{{ DETAIL_FILTER_LABELS[activeFilter] }} ×
            </button>
            <span>显示 {{ visibleStudents.length }} / {{ studentsInClass.length }} 人</span>
          </div>

          <div ref="matrixScroller" class="results-matrix-wrap" tabindex="0" aria-label="逐题成绩表，可横向滚动">
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
                    v-for="question in matrixQuestions"
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
                        <template v-if="studentRankText(student)">
                          · <em class="results-matrix__rank" :title="studentRankTitle(student) ?? undefined">{{ studentRankText(student) }}</em>
                        </template>
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
                    v-for="question in matrixQuestions"
                    :key="question.question_id"
                    class="results-matrix__score"
                    :style="questionHeatStyle(student, question.question_id)"
                  >
                    <button
                      v-if="itemFor(student, question.question_id)"
                      type="button"
                      :data-status="itemFor(student, question.question_id)!.score_status"
                      :aria-label="`${student.student_name}，${question.question_id}，${scoreStatusLabel(itemFor(student, question.question_id)!.score_status)}，得分 ${formatScore(itemFor(student, question.question_id)!.score_awarded)}，查看作答`"
                      @click="navigateToReview(itemFor(student, question.question_id)!, student)"
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
            @click="navigateToReview(item, selectedStudent)"
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
