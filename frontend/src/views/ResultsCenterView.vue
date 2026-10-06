<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import { Download, LayoutDashboard, ListChecks, Table2 } from '@lucide/vue'

import type {
  ResultsCenterItem,
  ResultsCenterStudent,
  ResultsScoreStatus,
  ResultsStudentStatus,
} from '../api/results-center'
import { personalReportsApi, reportStatusText, matchesReportStudent, type PersonalReportState } from '../api/personal-reports'
import { useJobStore } from '../stores/jobs'
import { TERMINAL_JOB_STATUSES } from '../api/jobs'
import PersonalReportReader from '../components/results-center/PersonalReportReader.vue'
import ReportPipelineButton from '../components/results-center/ReportPipelineButton.vue'
import ClassAnalysisPanel from '../components/results-center/ClassAnalysisPanel.vue'
import ResultsOverviewPanel from '../components/results-center/ResultsOverviewPanel.vue'
import { invalidateClassAnalysis } from '../components/results-center/class-analysis-cache'
import { loadComparisonResults } from '../components/results-center/comparison-results-cache'
import { unexpectedLosses } from '../components/results-center/paper-walkthrough'
import {
  classRanksOf,
  defaultComparison,
  formatScore,
  isCompleteStudent,
  rankChanges,
} from '../components/results-center/results-overview'
import AppButton from '../components/design-system/AppButton.vue'
import FeedbackBanner from '../components/design-system/FeedbackBanner.vue'
import StatePanel from '../components/design-system/StatePanel.vue'
import AppIconButton from '../components/design-system/AppIconButton.vue'
import PageHeader from '../components/design-system/PageHeader.vue'
import { Input } from '../components/ui/input'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '../components/ui/sheet'
import { useResultsCenterStore, type ResultsViewState } from '../stores/results-center'
import { useSessionStore } from '../stores/session'
import { translateGradingReason } from '../utils/grading-reasons'
import FileCenterView from './FileCenterView.vue'

type ResultsTab = 'overview' | 'details' | 'analysis'
type DetailFilter = 'all' | 'attention' | 'reports' | ResultsStudentStatus
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
const matrixCollapsed = ref(false)
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
let drawerTrigger: HTMLElement | null = null

const tabs = [
  { id: 'overview', label: '考情总览', icon: LayoutDashboard },
  { id: 'details', label: '成绩明细', icon: Table2 },
  { id: 'analysis', label: '试题诊断', icon: ListChecks },
] satisfies Array<{ id: ResultsTab; label: string; icon: unknown }>

const DETAIL_FILTER_LABELS: Record<DetailFilter, string> = {
  all: '全部学生',
  attention: '待处理',
  reports: '报告未生成或需重新生成',
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

const exportOpen = ref(false)
const fileCenterRef = ref<InstanceType<typeof FileCenterView> | null>(null)

const activeTab = computed<ResultsTab>(() => {
  const candidate = stringQuery(route.query.tab)
  return candidate === 'details' || candidate === 'analysis'
    ? candidate
    : 'overview'
})

// 旧入口 tab=exports 落在总览上并自动打开导出弹层；弹层挂载后才置 open，
// 否则 PopoverRoot 初始化会把 v-model 回写成 false。
onMounted(async () => {
  await nextTick()
  if (stringQuery(route.query.tab) === 'exports') exportOpen.value = true
})
watch(
  () => route.query.tab,
  (tab) => {
    if (stringQuery(tab) === 'exports') exportOpen.value = true
  },
)

function guardExportPopoverClose(event: Event): void {
  if (fileCenterRef.value?.dialogOpen) event.preventDefault()
}

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
  return candidate === 'reports'
    || candidate === 'attention'
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
    return matchesReportStudent(student, query)
  })
})

const classRanks = computed(() => classRanksOf(results.value?.students ?? []))

function studentRankText(student: ResultsCenterStudent): string | null {
  const entry = classRanks.value.get(student.student_id)
  return entry === null || entry === undefined
    ? null
    : `第 ${entry.rank} 名`
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

const jobStore = useJobStore()
const personalStates = ref<PersonalReportState[]>([])
const personalStateError = ref('')
const readerStudentId = ref<number | null>(null)
const readerReportSessionId = ref<number | undefined>()
const readerDataOnly = ref(false)
const readerStudents = ref<ResultsCenterStudent[]>([])
const highlightedStudentId = ref<number | null>(null)
let personalController: AbortController | null = null
function reportState(student: ResultsCenterStudent) { return personalStates.value.find(s => s.student_id === student.student_id) }
function reportLabel(student: ResultsCenterStudent) {
  const state = reportState(student)
  return `查看${student.student_name}的个人报告（${state ? (state.status === 'old_prompt' ? '用旧版提示词生成' : reportStatusText[state.status]) : '状态读取中'}）`
}
const reportsNeedGeneration = computed(() => studentsInClass.value.filter(s => ['missing', 'stale'].includes(reportState(s)?.status ?? '')).length)
async function refreshPersonalStates() {
  personalController?.abort()
  const sid = sessionStore.selectedSessionId
  if (sid === null) { personalStates.value = []; return }
  const next = new AbortController()
  personalController = next
  personalStateError.value = ''
  try {
    const value = await personalReportsApi.states(sid, next.signal)
    if (!next.signal.aborted && sessionStore.selectedSessionId === sid) personalStates.value = value
  } catch { if (!next.signal.aborted) personalStateError.value = '报告状态暂时无法读取。' }
}
watch(() => sessionStore.selectedSessionId, () => {
  personalStates.value = []; readerStudentId.value = null; void refreshPersonalStates()
})
watch(() => resultsStore.updatedAt, () => { void refreshPersonalStates() })
const observedReportJobs = new Set<number>()
watch(() => Object.values(jobStore.jobs).map(j => `${j.id}:${j.status}`).join('|'), () => {
  for (const job of Object.values(jobStore.jobs)) {
    if (job.job_type !== 'report_export' || job.payload.report_type !== 'personal_analysis_html') continue
    if (!TERMINAL_JOB_STATUSES.has(job.status)) observedReportJobs.add(job.id)
    else if (observedReportJobs.delete(job.id)) void refreshPersonalStates()
  }
}, {immediate: true})
function openPersonalReport(student: ResultsCenterStudent, dataOnly = false, reportSessionId?: number) {
  readerStudents.value = [...matrixStudents.value]
  if (!readerStudents.value.some(s => s.student_id === student.student_id)) readerStudents.value.push(student)
  readerDataOnly.value = dataOnly
  readerReportSessionId.value = reportSessionId
  readerStudentId.value = student.student_id
  selectedStudent.value = null
}
function closePersonalReport(studentId: number) {
  readerStudentId.value = null
  highlightedStudentId.value = studentId
  const student = results.value?.students.find(s => s.student_id === studentId)
  if (student) openStudentDrawer(student)
}
function reviewFromPersonalReport(studentId: number, questionId: string, reportSessionId: number) {
  const student = results.value?.students.find(s => s.student_id === studentId)
  const sid = sessionStore.selectedSessionId
  if (!student || sid === null || reportSessionId !== sid) return
  const parent = questionId.match(/^Q?([0-9]+)/i)?.[1]
  const item = student.items.find(i => i.question_id === questionId) ??
    student.items.find(i => parent && i.question_id.match(/^Q?([0-9]+)/i)?.[1] === parent)
  if (!item) return
  resultsStore.reportReturn = {sessionId: sid, studentId, reportSessionId}
  navigateToReview(item, student)
}
watch(results, () => {
  const pending = resultsStore.reportReturn
  if (!pending || pending.sessionId !== sessionStore.selectedSessionId) return
  const student = results.value?.students.find(s => s.student_id === pending.studentId)
  if (!student) return
  // 能进入复核说明此前已在读正文；未生成时恢复数据版，已有叙述由阅读器切回分析版。
  openPersonalReport(student, true, pending.reportSessionId)
  resultsStore.reportReturn = null
}, {flush: 'post', immediate: true})
onMounted(() => { void refreshPersonalStates() })
onBeforeUnmount(() => { personalController?.abort() })

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

// 总览「看名单」直达：按总分升序排，覆盖默认排序但不覆盖视图恢复。
watch(
  () => route.query.sort,
  (sort) => {
    if (
      sort === 'total-asc'
      && activeTab.value === 'details'
      && !pendingRestoration
    ) {
      matrixSort.value = { key: 'total', direction: 'ascending', questionId: null }
    }
  },
  { immediate: true },
)

// 成绩刷新后班级分析摘要即过期，总览与诊断会在下次读取时重取。
watch(
  () => resultsStore.results,
  (value) => {
    const sessionId = sessionStore.selectedSessionId
    if (value !== null && sessionId !== null) invalidateClassAnalysis(sessionId)
  },
)

// 「AI 整理」管线任务（错因 → 班级 → 个人）；页面按最新一条同名 job 显示。
const pipelineJob = computed(() => {
  const sessionId = sessionStore.selectedSessionId
  if (sessionId === null) return null
  return (
    Object.values(jobStore.jobs)
      .filter((job) => (
        job.job_type === 'class_analysis_generate'
        && job.payload.session_id === sessionId
      ))
      .sort((left, right) => right.id - left.id)[0] ?? null
  )
})

// 管线结束后：班级分析缓存失效、个人报告状态重新读取。
watch(
  () => (pipelineJob.value === null ? '' : `${pipelineJob.value.id}:${pipelineJob.value.status}`),
  (key, previous) => {
    if (!key || key === previous) return
    const job = pipelineJob.value
    const sessionId = sessionStore.selectedSessionId
    if (job === null || sessionId === null || !TERMINAL_JOB_STATUSES.has(job.status)) return
    invalidateClassAnalysis(sessionId)
    void refreshPersonalStates()
  },
)

function onVisibilityChange(): void {
  if (document.visibilityState !== 'visible') return
  const sessionId = sessionStore.selectedSessionId
  const updatedAt = resultsStore.updatedAt
  if (sessionId === null || updatedAt === null) return
  if (Date.now() - new Date(updatedAt).getTime() > 60_000) {
    void resultsStore.load(sessionId)
  }
}

onMounted(() => {
  document.addEventListener('visibilitychange', onVisibilityChange)
})
onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', onVisibilityChange)
})

function matchesFilter(
  student: ResultsCenterStudent,
  filter: DetailFilter,
): boolean {
  if (filter === 'all') return true
  if (filter === 'reports') return ['missing', 'stale'].includes(reportState(student)?.status ?? '')
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



function formatUpdatedAt(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (value: number) => String(value).padStart(2, '0')
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`
  const now = new Date()
  const sameDay = date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate()
  return sameDay ? time : `${date.getMonth() + 1}月${date.getDate()}日 ${time}`
}

function onMatrixScroll(event: Event): void {
  matrixCollapsed.value = (event.target as HTMLElement).scrollTop > 8
}

function scrollToTop(): void {
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  const behavior: ScrollBehavior = reduced ? 'auto' : 'smooth'
  matrixScroller.value?.scrollTo({ top: 0, left: 0, behavior })
  resultsPage.value?.parentElement?.scrollTo({ top: 0, behavior })
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
  resultsStore.setReviewNavigation({
    sessionId,
    studentIds: matrixStudents.value.map((entry) => entry.student_id),
  })
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
}

function focusDrawerClose(event: Event): void {
  event.preventDefault()
  void nextTick(() => document.querySelector<HTMLElement>('.results-drawer .app-icon-button')?.focus())
}

function openStudentById(studentId: number): void {
  const student = results.value?.students.find(
    (entry) => entry.student_id === studentId,
  )
  if (student) openStudentDrawer(student)
}

// 学生抽屉补充行：较默认对比考试的分差/名次 + 本次意外失分题
const drawerComparisonStudents = ref<ResultsCenterStudent[] | null>(null)
let drawerComparisonRequest = 0

const drawerComparisonSession = computed(() => defaultComparison(
  sessionStore.currentSession,
  sessionStore.sessions,
))

watch(
  [selectedStudent, drawerComparisonSession],
  async ([student, comparison]) => {
    drawerComparisonRequest += 1
    const request = drawerComparisonRequest
    drawerComparisonStudents.value = null
    if (!student || !comparison) return
    try {
      const data = await loadComparisonResults(comparison.id)
      if (request === drawerComparisonRequest) {
        drawerComparisonStudents.value = data.students
      }
    } catch { /* 对比考试加载失败时省略该行 */ }
  },
  { immediate: true },
)

const drawerCompareLine = computed(() => {
  const student = selectedStudent.value
  if (!student) return null
  if (!drawerComparisonSession.value) return '暂无可对比考试'
  if (drawerComparisonStudents.value === null) return null
  const entry = rankChanges(
    results.value?.students ?? [],
    drawerComparisonStudents.value,
  ).find((e) => e.student.student_id === student.student_id)
  if (!entry) return null
  const diff = student.current_score - entry.previousScore
  const sign = diff >= 0 ? '+' : ''
  return `较上次 ${sign}${formatScore(diff)} 分 · 班内名次 ${entry.previousRank} → ${entry.currentRank}`
})

const drawerLossLine = computed(() => {
  const student = selectedStudent.value
  if (!student) return null
  const peers = (results.value?.students ?? []).filter(
    (peer) => (peer.class_name ?? '') === (student.class_name ?? '')
      && isCompleteStudent(peer),
  )
  const losses = unexpectedLosses(student, peers).slice(0, 2)
  if (!losses.length) return null
  return `这次意外失分：${losses.map((loss) => (
    `${loss.questionId}（本班 ${Math.round(loss.fullFraction * 100)}% 满分，本题 ${formatScore(loss.score)}/${formatScore(loss.maxScore)}）`
  )).join('、')}`
})

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

function openReview(scope: 'teacher_pending' | 'all'): void {
  void router.push({
    path: '/grading',
    query: { ...selectedSessionQuery(), scope, entry: 'results' },
  })
}

function openLowList(className: string | null): void {
  selectedClass.value = className !== null && classOptions.value.includes(className)
    ? className
    : null
  void router.replace({
    path: '/results',
    query: {
      tab: 'details',
      ...selectedSessionQuery(),
      sort: 'total-asc',
    },
  })
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


</script>

<template>
  <section
    ref="resultsPage"
    class="results-center"
    :class="{ 'results-center--matrix': activeTab === 'details' }"
    aria-labelledby="results-center-title"
  >
    <PageHeader title="成绩中心" title-id="results-center-title">
      <template #meta>
        <span v-if="results">{{ results.session_name }}</span>
        <span v-if="resultsStore.updatedAt">
          已更新 {{ formatUpdatedAt(resultsStore.updatedAt) }}
        </span>
      </template>
      <template #actions>
        <nav class="results-rail" aria-label="成绩中心页面">
          <button
            v-for="tab in tabs"
            :key="tab.id"
            type="button"
            :aria-current="activeTab === tab.id ? 'page' : undefined"
            :class="{ 'is-active': activeTab === tab.id }"
            @click="selectTab(tab.id)"
          >
            <component :is="tab.icon" :size="15" :stroke-width="2" aria-hidden="true" />
            <span class="results-rail__label">{{ tab.label }}</span>
          </button>
        </nav>
        <ReportPipelineButton
          v-if="sessionStore.selectedSessionId !== null"
          :session-id="sessionStore.selectedSessionId"
          :active-job="pipelineJob"
        />
        <PopoverRoot v-model:open="exportOpen">
          <PopoverTrigger as-child>
            <AppButton
              variant="secondary"
              size="small"
              class="results-export-toggle"
              aria-label="导出文件"
            >
              <template #leading>
                <Download :size="14" :stroke-width="2" aria-hidden="true" />
              </template>
              导出
            </AppButton>
          </PopoverTrigger>
          <PopoverPortal>
            <PopoverContent
              class="results-export-popover"
              align="end"
              :side-offset="8"
              :collision-padding="8"
              @interact-outside="guardExportPopoverClose"
              @escape-key-down="guardExportPopoverClose"
            >
              <FileCenterView ref="fileCenterRef" embedded variant="popover" />
            </PopoverContent>
          </PopoverPortal>
        </PopoverRoot>
      </template>
    </PageHeader>

    <ClassAnalysisPanel
      v-if="activeTab === 'analysis'"
      :session-id="sessionStore.selectedSessionId"
      :focus-question="analysisFocusQuestion"
      :initial-class="analysisInitialClass"
    />

    <template v-else>
      <StatePanel v-if="sessionStore.selectedSessionId === null" kind="empty" title="请先选择考试" description="在左侧栏“当前考试”中选择。" />

      <StatePanel
        v-else-if="resultsStore.state === 'loading' && !results"
       
        kind="loading"
        title="正在整理当前考试成绩"
        description="学生较多时可能需要片刻。"
      />

      <StatePanel
        v-else-if="resultsStore.state === 'error'"
       
        kind="error"
        title="成绩暂时无法读取"
        :description="resultsStore.errorMessage"
        retry-label="重新加载"
        @retry="refresh"
      />

      <template v-else-if="results">
        <FeedbackBanner
          v-if="resultsStore.state === 'stale-error'"
          tone="warning"
          title="当前显示上次成功读取的成绩，最新数据暂时无法取得。"
         
          action-label="重新加载"
          @action="refresh"
        />

        <StatePanel v-if="results.students.length === 0" kind="empty" title="当前考试还没有可展示的成绩" />

        <ResultsOverviewPanel
          v-else-if="activeTab === 'overview'"
          :results="results"
          :session-id="results.session_id"
          :scope="overviewScope"
          @open-student="openStudentById"
          @open-question="openQuestionInAnalysis"
          @open-low-list="openLowList"
          @open-review="openReview"
          @update:scope="setOverviewScope"
        />

        <template v-else>
        <section
          class="results-panel results-panel--matrix"
          :class="{ 'is-collapsed': matrixCollapsed }"
          aria-labelledby="score-matrix-title"
        >
          <div class="results-panel__head">
            <div class="results-matrix-collapsible">
              <h2 id="score-matrix-title" class="results-panel__title">学生 × 题号</h2>
              <div class="results-conclusion__line" aria-label="成绩概况">
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
            </div>

            <div class="results-filter-bar" aria-label="学生成绩筛选">
              <select
                v-model="selectedClass"
                class="results-filter-bar__select app-input"
                aria-label="成绩明细班级"
              >
                <option :value="null">全部班级</option>
                <option v-for="className in classOptions" :key="className" :value="className">{{ className || '未填写班级' }}</option>
              </select>
              <Input
                v-model="searchQuery"
                type="search"
                class="results-filter-bar__search"
                aria-label="搜索学生"
                placeholder="姓名、学号、班级或拼音首字母"
              />
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
              <button type="button" class="results-filter-chip" :aria-pressed="activeFilter === 'reports'"
                :class="{'is-active': activeFilter === 'reports'}" @click="selectDetailFilter('reports')">
                报告未生成或需重新生成（{{ reportsNeedGeneration }}）
              </button>
              <span>显示 {{ visibleStudents.length }} / {{ studentsInClass.length }} 人</span>
            </div>
          </div>

          <p v-if="personalStateError" class="personal-report-help">{{ personalStateError }} <AppButton variant="ghost" size="small" @click="refreshPersonalStates">重试</AppButton></p>
          <div
            ref="matrixScroller"
            class="results-matrix-wrap app-heat-table-wrap"
            tabindex="0"
            aria-label="逐题成绩表，可横向滚动"
            @scroll.passive="onMatrixScroll"
          >
            <table class="app-heat-table results-matrix">
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
                <tr v-for="student in matrixStudents" :key="student.student_id" :class="{'personal-report-highlight': highlightedStudentId === student.student_id}">
                  <th scope="row" class="results-matrix__identity">
                    <button type="button" @click="openStudentDrawer(student, $event)">
                      <span class="results-matrix__name"><strong>{{ student.student_name }}</strong><em v-if="studentRankText(student)" class="results-matrix__rank" :title="studentRankTitle(student) ?? undefined">{{ studentRankText(student) }}</em></span>
                      <span>{{ student.student_code || '无学号' }}<template v-if="student.class_name"> · {{ student.class_name }}</template></span>
                    </button>
                    <button v-if="reportState(student) && reportState(student)?.status !== 'unavailable'"
                      type="button" :class="['personal-report-status', `personal-report-status--${reportState(student)?.status}`]"
                      :aria-label="reportLabel(student)" :title="reportLabel(student)" @click="openPersonalReport(student)">
                      {{ reportState(student)?.status === 'current' || reportState(student)?.status === 'old_prompt' ? '✓' : reportState(student)?.status === 'stale' ? '↻' : '○' }}
                    </button>
                    <span v-else-if="reportState(student)?.status === 'unavailable'" class="personal-report-unavailable" :title="reportState(student)?.reason ?? ''">{{ reportState(student)?.reason === '缺考' ? '缺考' : '不可生成' }}</span>
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
          <StatePanel v-if="visibleStudents.length === 0" kind="empty" compact title="没有符合当前筛选条件的学生。" />
        </section>
        <AppIconButton
          v-if="matrixCollapsed"
          class="results-back-top"
          label="回到顶部"
          icon="arrow-up"
          variant="secondary"
          @click="scrollToTop"
        />
        </template>
      </template>
    </template>
  </section>

  <Sheet :open="selectedStudent !== null" @update:open="(value: boolean) => { if (!value) closeStudentDrawer() }">
    <SheetContent
      class="results-drawer"
      :aria-describedby="undefined"
      @open-auto-focus="focusDrawerClose"
    >
      <template v-if="selectedStudent">
        <SheetHeader class="results-drawer__header">
          <div>
            <SheetTitle as="h2" id="student-result-title">{{ selectedStudent.student_name }}</SheetTitle>
            <p>
              {{ selectedStudent.student_code || '未填写学号' }}
              <template v-if="selectedStudent.class_name"> · {{ selectedStudent.class_name }}</template>
            </p>
          </div>
        </SheetHeader>

        <div class="results-drawer__total">
          <span>{{ currentTotalPrefix(selectedStudent) }}总分</span>
          <strong>
            {{ formatScore(selectedStudent.current_score) }}
            <small>/ {{ formatScore(selectedStudent.max_score) }}</small>
          </strong>
          <em>{{ studentIssueText(selectedStudent) }}</em>
        </div>

        <p v-if="drawerCompareLine" class="results-drawer__cmp">
          {{ drawerCompareLine }}
        </p>
        <p v-if="drawerLossLine" class="results-drawer__cmp">
          {{ drawerLossLine }}
        </p>

        <div class="personal-report-drawer">
          <b>个人报告</b>
          <template v-if="reportState(selectedStudent)?.status === 'current'"><AppButton variant="ghost" size="small" @click="openPersonalReport(selectedStudent)">查看个人报告 ›</AppButton></template>
          <template v-else-if="reportState(selectedStudent)?.status === 'old_prompt'"><p>用旧版提示词生成。</p><AppButton variant="ghost" size="small" @click="openPersonalReport(selectedStudent)">查看个人报告 ›</AppButton></template>
          <template v-else-if="reportState(selectedStudent)?.status === 'stale'"><p>成绩已变化，显示上次生成的 AI 分析。</p><AppButton variant="ghost" size="small" @click="openPersonalReport(selectedStudent)">查看个人报告（上次生成） ›</AppButton></template>
          <template v-else-if="reportState(selectedStudent)?.status === 'missing'"><p>本场报告未生成</p><AppButton variant="ghost" size="small" @click="openPersonalReport(selectedStudent, true)">查看报告（数据版） ›</AppButton></template>
          <p v-else>{{ reportState(selectedStudent)?.reason ?? '正在读取报告状态…' }}</p>
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
      </template>
    </SheetContent>
  </Sheet>
  <PersonalReportReader v-if="readerStudentId !== null && sessionStore.selectedSessionId !== null"
    :students="readerStudents" :student-id="readerStudentId" :session-id="sessionStore.selectedSessionId"
    :report-session-id="readerReportSessionId" :volume-id="sessionStore.currentSession?.curriculum_volume_id"
    :initially-data-only="readerDataOnly" @close="closePersonalReport" @review="reviewFromPersonalReport" @refreshed="refreshPersonalStates" />
</template>

<style scoped>
.personal-report-status{display:inline-flex!important;width:26px!important;min-width:26px;height:26px;align-items:center;justify-content:center;vertical-align:top;margin-left:6px;border-radius:5px!important;font-size:var(--font-size-body)!important;padding:0!important}
.results-matrix tbody .results-matrix__identity > button:not(.personal-report-status){display:inline-grid;width:calc(100% - 32px);vertical-align:top}
.personal-report-status--current,.personal-report-status--old_prompt{color:#368260!important;background:#edf7ef!important}.personal-report-status--stale{color:#aa7b22!important;background:#fff3d8!important}.personal-report-status--missing{color:#87929c!important;background:#f2f4f6!important}
.personal-report-unavailable{font-size:var(--font-size-caption);color:#84919e;margin-left:4px}.personal-report-help{color:#7b8b98;font-size:var(--font-size-caption);margin:5px 20px 12px}.personal-report-highlight{outline:2px solid #6c9bb3;outline-offset:-2px}.personal-report-drawer{padding:16px 0;margin-top:12px;border-top:1px solid #e0e7ed}.personal-report-drawer b{display:block;font-size:var(--font-size-body)}.personal-report-drawer p{color:#778490;font-size:var(--font-size-caption);margin:8px 0}

.results-drawer__cmp {
  margin: 0;
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-size-caption);
  color: var(--muted-foreground);
}
.results-drawer__cmp + .results-drawer__cmp {
  padding-top: 0;
}
</style>
