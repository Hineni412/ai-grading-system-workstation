<script lang="ts">
import type { AnalysisReviewNoteItem } from '../../api/exports'

// 报告提示按考试保留最近一次清单：同次会话内重进总览先展示缓存，再静默刷新。
const reviewNotesCache = new Map<number, AnalysisReviewNoteItem[]>()
</script>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  classAnalysisApi,
  type ClassAnalysisResponse,
} from '../../api/class-analysis'
import type { ResultsCenterResponse } from '../../api/results-center'
import type { SessionSummary } from '../../api/sessions'
import { exportsApi } from '../../api/exports'
import { useJobStore } from '../../stores/jobs'
import { useSessionStore } from '../../stores/session'
import AppButton from '../design-system/AppButton.vue'
import { Skeleton } from '../ui/skeleton'
import QuestionHtmlBlock from '../question-bank/QuestionHtmlBlock.vue'
import {
  cachedClassAnalysis,
  rememberClassAnalysis,
} from './class-analysis-cache'
import { loadComparisonResults } from './comparison-results-cache'
import PaperWalkthrough from './PaperWalkthrough.vue'
import { walkthroughDataFor } from './paper-walkthrough'
import {
  OVERVIEW_BANDS,
  classDelta,
  classDisplayLabel,
  classKeysOf,
  classRateGapOf,
  compareByClassGap,
  compareByRate,
  compareByZeroShare,
  comparisonCandidates,
  criticalStudents,
  defaultComparison,
  displayAnswer,
  formatRate,
  formatScore,
  isCompleteStudent,
  questionRatesFor,
  rankChanges,
  rankTierChanges,
  consecutiveRankChanges,
  scoreStructureFor,
  subQuestionLabelFor,
  summarizeStudents,
  zeroShareOf,
  type ClassDelta,
  type ClassSummary,
  type ConsecutiveRankEntry,
  type CriticalStudentEntry,
  type OverviewBand,
  type QuestionSort,
  type RankChange,
  type RankTierEntry,
} from './results-overview'

const props = defineProps<{
  results: ResultsCenterResponse
  sessionId: number
  scope?: string | null
}>()

const emit = defineEmits<{
  'open-student': [studentId: number]
  'open-question': [questionId: string, className: string | null]
  'open-review': [scope: 'teacher_pending' | 'all']
  'update:scope': [scope: string | null]
}>()

const router = useRouter()
const route = useRoute()
const sessionStore = useSessionStore()
const jobStore = useJobStore()

const classKeys = computed(() => classKeysOf(props.results.students))

const scope = computed<string | null>({
  get: () => (props.scope !== null && props.scope !== undefined
    && classKeys.value.includes(props.scope)
    ? props.scope
    : null),
  set: (value) => emit('update:scope', value),
})

const sortBy = ref<QuestionSort>('rate')
const analysis = ref<ClassAnalysisResponse | null>(null)
const analysisState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const mergedAnalysis = ref<ClassAnalysisResponse | null>(null)

let loadGeneration = 0
let shownAnalysisKey: string | null = null
let scopeController: AbortController | null = null
let mergedController: AbortController | null = null
let notesController: AbortController | null = null

const reviewNotes = ref<AnalysisReviewNoteItem[]>([])
const reviewNotesExpanded = ref(false)
const pendingReviewNotes = computed(() =>
  reviewNotes.value.filter((item) => item.status === 'pending'))

async function loadReviewNotes(sessionId: number): Promise<void> {
  notesController?.abort()
  const controller = new AbortController()
  notesController = controller
  const cached = reviewNotesCache.get(sessionId)
  if (cached) reviewNotes.value = cached
  try {
    const result = await exportsApi.getAnalysisReviewNotes(
      sessionId,
      controller.signal,
    )
    if (controller.signal.aborted || props.sessionId !== sessionId) return
    reviewNotes.value = result.items
    reviewNotesCache.set(sessionId, result.items)
  } catch {
    if (!controller.signal.aborted && props.sessionId === sessionId) {
      reviewNotes.value = cached ?? []
    }
  }
}

watch(
  [scope, () => props.sessionId, () => props.results],
  ([scopeKey, sessionId], previous) => {
    if (previous?.[1] !== sessionId) {
      mergedController?.abort()
      mergedController = null
      mergedAnalysis.value = cachedClassAnalysis(sessionId, '')
      void loadReviewNotes(sessionId)
    }
    void loadScope(scopeKey, sessionId)
    if (scopeKey !== null) void ensureMerged(sessionId)
  },
  { immediate: true },
)

// 个人报告导出完成后提示清单会重写；跟踪中的导出任务结束时刷新一次。
const succeededReportJobs = computed(() => Object.values(jobStore.jobs)
  .filter((job) => job.job_type === 'report_export'
    && job.status === 'succeeded'
    && Number(job.result.session_id ?? job.payload.session_id ?? 0)
      === props.sessionId)
  .map((job) => job.id)
  .sort((left, right) => left - right)
  .join(','))

watch(succeededReportJobs, (value, previous) => {
  if (previous === undefined || value === previous) return
  void loadReviewNotes(props.sessionId)
})

async function loadScope(
  scopeKey: string | null,
  sessionId: number,
): Promise<void> {
  // '' 的 class_name 在后端表示合并全部班级；范围 null 与未分班都落到同一请求。
  const apiScope = scopeKey ?? ''
  const analysisKey = `${sessionId}::${apiScope}`
  const generation = ++loadGeneration
  scopeController?.abort()
  const cached = cachedClassAnalysis(sessionId, apiScope)
  if (cached) {
    analysis.value = cached
    analysisState.value = 'ready'
    shownAnalysisKey = analysisKey
    if (apiScope === '') mergedAnalysis.value = cached
    return
  }
  // 缓存失效但旧数据仍展示同一范围时保留旧数据，等新结果回来再替换。
  const keepStale = shownAnalysisKey === analysisKey
    && analysisState.value === 'ready'
  if (!keepStale) {
    analysis.value = null
    analysisState.value = 'loading'
    shownAnalysisKey = null
  }
  const controller = new AbortController()
  scopeController = controller
  try {
    const result = await classAnalysisApi.getClassAnalysis(
      sessionId,
      controller.signal,
      apiScope,
      'summary',
    )
    if (generation !== loadGeneration || props.sessionId !== sessionId) return
    rememberClassAnalysis(sessionId, apiScope, result)
    analysis.value = result
    analysisState.value = 'ready'
    shownAnalysisKey = analysisKey
    if (apiScope === '') mergedAnalysis.value = result
  } catch {
    if (generation !== loadGeneration || props.sessionId !== sessionId) return
    if (keepStale) return
    analysisState.value = 'error'
  }
}

async function ensureMerged(sessionId: number): Promise<void> {
  if (mergedAnalysis.value || mergedController) return
  const cached = cachedClassAnalysis(sessionId, '')
  if (cached) {
    mergedAnalysis.value = cached
    return
  }
  const controller = new AbortController()
  mergedController = controller
  try {
    const result = await classAnalysisApi.getClassAnalysis(
      sessionId,
      controller.signal,
      '',
      'summary',
    )
    rememberClassAnalysis(sessionId, '', result)
    if (!controller.signal.aborted && props.sessionId === sessionId) {
      mergedAnalysis.value = result
    }
  } catch {
    // 缺考列保持「—」，不阻塞其余内容。
  } finally {
    if (mergedController === controller) mergedController = null
  }
}

onBeforeUnmount(() => {
  loadGeneration += 1
  scopeController?.abort()
  mergedController?.abort()
  comparisonController?.abort()
  notesController?.abort()
})

const studentsInScope = computed(() => (scope.value === null
  ? props.results.students
  : props.results.students.filter(
    (student) => (student.class_name ?? '') === scope.value,
  )))

const absentCounts = computed(() => {
  const counts = new Map<string, number>()
  for (const item of mergedAnalysis.value?.data?.skipped ?? []) {
    if (item.reason !== '缺考') continue
    counts.set(item.class_name, (counts.get(item.class_name) ?? 0) + 1)
  }
  return counts
})

const classSummaries = computed<ClassSummary[]>(() => classKeys.value.map(
  (key) => summarizeStudents(
    props.results.students.filter(
      (student) => (student.class_name ?? '') === key,
    ),
    key,
    mergedAnalysis.value ? (absentCounts.value.get(key) ?? 0) : null,
  ),
))

const classRows = computed<ClassSummary[]>(() => {
  const rows = [...classSummaries.value]
  if (rows.length >= 2) {
    rows.push(summarizeStudents(
      props.results.students,
      null,
      mergedAnalysis.value
        ? (mergedAnalysis.value.data?.skipped ?? [])
          .filter((item) => item.reason === '缺考').length
        : null,
    ))
  }
  return rows
})

const questionIds = computed(() => props.results.questions.map(
  (question) => question.question_id,
))

const scopeQuestionRates = computed(() => questionRatesFor(
  studentsInScope.value,
  questionIds.value,
))

const scopeScoreStructure = computed(() => scoreStructureFor(
  studentsInScope.value,
  questionIds.value,
))

const classQuestionRates = computed(() => new Map(
  classKeys.value.map((key) => [
    key,
    questionRatesFor(
      props.results.students.filter(
        (student) => (student.class_name ?? '') === key,
      ),
      questionIds.value,
    ),
  ]),
))

const scopeAnalysisQuestions = computed(() => (
  analysisState.value === 'ready'
    ? analysis.value?.data?.questions ?? []
    : null
))

const critical = computed(() => criticalStudents(studentsInScope.value))

const criticalGroups = computed(() => {
  const maxScore = props.results.summary.max_score
  const groups: { key: string; title: string; rows: CriticalStudentEntry[] }[] = []
  if (critical.value.passing.length > 0) {
    groups.push({
      key: 'passing',
      title: `差 5 分内及格（及格线 ${formatScore(maxScore * 0.6)} 分）`,
      rows: critical.value.passing,
    })
  }
  if (critical.value.excellent.length > 0) {
    groups.push({
      key: 'excellent',
      title: `差 5 分内优秀（优秀线 ${formatScore(maxScore * 0.85)} 分）`,
      rows: critical.value.excellent,
    })
  }
  return groups
})

interface QuestionRow {
  questionId: string
  maxScore: number
  rate: number | null
  stem: string | null
  stemTitle: string | null
  subIndex: number | null
  answer: string | null
  answerFull: string | null
  structure: { full: number; partial: number; zero: number; resolved: number }
  zeroShare: number | null
  classRates: { key: string; label: string; rate: number | null; isLow: boolean }[]
  classRateGap: number | null
}

const CLASS_RATE_GAP = 0.15

const gapSortAvailable = computed(() => (
  scope.value === null && classKeys.value.length >= 2
))
watch(gapSortAvailable, (available) => {
  if (!available && sortBy.value === 'gap') sortBy.value = 'rate'
})

const sortOptions = computed<{ id: QuestionSort; label: string }[]>(() => {
  const options: { id: QuestionSort; label: string }[] = [
    { id: 'rate', label: '按得分率' },
    { id: 'number', label: '按题号' },
    { id: 'zero', label: '按 0 分占比' },
  ]
  if (gapSortAvailable.value) options.push({ id: 'gap', label: '按班级差距' })
  return options
})

const questionRows = computed<QuestionRow[]>(() => {
  const analysisById = new Map(
    (scopeAnalysisQuestions.value ?? []).map(
      (question) => [question.question_id, question] as const,
    ),
  )
  const perClass = scope.value === null && classKeys.value.length >= 2
    ? classKeys.value.map((key) => ({
      key,
      label: classDisplayLabel(key),
      rates: classQuestionRates.value.get(key)!,
    }))
    : []
  const stemList = props.results.questions.map((question) => ({
    question_id: question.question_id,
    stem_summary: analysisById.get(question.question_id)?.stem_summary ?? null,
  }))
  const rows = props.results.questions.map((question) => {
    const analysisQuestion = analysisById.get(question.question_id)
    const classRates = perClass.map((entry) => ({
      key: entry.key,
      label: entry.label,
      rate: entry.rates.get(question.question_id) ?? null,
      isLow: false,
    }))
    const validRates = classRates
      .map((entry) => entry.rate)
      .filter((rate): rate is number => rate !== null)
    const minRate = validRates.length ? Math.min(...validRates) : null
    const maxRate = validRates.length ? Math.max(...validRates) : null
    if (minRate !== null && maxRate !== null && maxRate - minRate >= CLASS_RATE_GAP) {
      for (const entry of classRates) entry.isLow = entry.rate === minRate
    }
    const stem = analysisQuestion?.stem_summary ?? null
    const sub = subQuestionLabelFor(question.question_id, stem, stemList)
    const structure = scopeScoreStructure.value.get(question.question_id)
      ?? { full: 0, partial: 0, zero: 0, resolved: 0 }
    return {
      questionId: question.question_id,
      maxScore: question.max_score,
      rate: scopeQuestionRates.value.get(question.question_id) ?? null,
      stem,
      stemTitle: sub?.parentStem ?? stem,
      subIndex: sub?.index ?? null,
      answer: sub ? displayAnswer(analysisQuestion?.canonical_answer ?? null) : null,
      answerFull: sub ? (analysisQuestion?.canonical_answer ?? null) : null,
      structure,
      zeroShare: zeroShareOf(structure),
      classRates,
      classRateGap: classRateGapOf(classRates.map((entry) => entry.rate)),
    }
  })
  if (sortBy.value === 'rate') {
    rows.sort(compareByRate)
  } else if (sortBy.value === 'zero') {
    rows.sort(compareByZeroShare)
  } else if (sortBy.value === 'gap') {
    rows.sort(compareByClassGap)
  }
  return rows
})

const reviewPending = computed(() => {
  const summary = props.results.summary
  return summary.needs_review_item_count
    + summary.ungraded_item_count
    + summary.failed_item_count
})

const reviewBreakdown = computed(() => {
  const summary = props.results.summary
  const parts: string[] = []
  if (summary.needs_review_item_count > 0) {
    parts.push(`待复核 ${summary.needs_review_item_count}`)
  }
  if (summary.ungraded_item_count > 0) {
    parts.push(`未评分 ${summary.ungraded_item_count}`)
  }
  if (summary.failed_item_count > 0) {
    parts.push(`失败 ${summary.failed_item_count}`)
  }
  return parts.join(' · ')
})

const currentSession = computed(() => (
  sessionStore.sessions.find((session) => session.id === props.sessionId) ?? null
))

const comparisonOptions = computed(() => comparisonCandidates(
  currentSession.value,
  sessionStore.sessions,
))

const comparisonId = ref<number | null>(null)
const previousResults = ref<ResultsCenterResponse | null>(null)
const previousState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
let comparisonController: AbortController | null = null

watch(
  [comparisonOptions, () => props.sessionId],
  () => {
    const fallback = defaultComparison(currentSession.value, sessionStore.sessions)
    comparisonId.value = comparisonOptions.value.some(
      (session) => session.id === comparisonId.value,
    ) ? comparisonId.value : (fallback?.id ?? null)
  },
  { immediate: true },
)

watch(comparisonId, (sessionId) => {
  comparisonController?.abort()
  previousResults.value = null
  if (sessionId === null) {
    previousState.value = 'idle'
    return
  }
  previousState.value = 'loading'
  const controller = new AbortController()
  comparisonController = controller
  void loadComparisonResults(sessionId)
    .then((result) => {
      if (controller.signal.aborted || comparisonId.value !== sessionId) return
      previousResults.value = result
      previousState.value = 'ready'
    })
    .catch(() => {
      if (!controller.signal.aborted && comparisonId.value === sessionId) {
        previousState.value = 'error'
      }
    })
}, { immediate: true })

const rankEntries = computed<RankChange[]>(() => {
  const previous = previousScopeStudents.value
  if (!previous) return []
  return rankChanges(studentsInScope.value, previous)
})

const tierGroups = computed(() => {
  const previous = previousScopeStudents.value
  if (!previous) {
    return {
      enteringTop: [] as RankTierEntry[],
      leavingTop: [] as RankTierEntry[],
      leavingBottom: [] as RankTierEntry[],
      enteringBottom: [] as RankTierEntry[],
    }
  }
  return rankTierChanges(studentsInScope.value, previous)
})

const tierHint = computed(() => {
  const sizes = new Set<number>()
  for (const entry of rankEntries.value) {
    sizes.add(Math.ceil(entry.currentSize / 4))
    sizes.add(Math.ceil(entry.previousSize / 4))
  }
  return sizes.size === 1
    ? `前 1/4 = 本班前 ${[...sizes][0]} 名`
    : '前 1/4 = 各班前 1/4'
})

// 第三场 = 同卷别候选中早于所选对比考试的最近一场；顺序要求 第三场 < 对比 < 本场。
const earlierSession = computed<SessionSummary | null>(() => {
  const comparison = comparisonOptions.value.find(
    (option) => option.id === comparisonId.value,
  )
  const created = currentSession.value?.created_at
  if (!comparison?.created_at || !created) return null
  if (!(comparison.created_at < created)) return null
  return comparisonOptions.value.find((option) => (
    option.id !== comparison.id
    && option.created_at !== null
    && option.created_at < comparison.created_at!
  )) ?? null
})

const earlierResults = ref<ResultsCenterResponse | null>(null)
const earlierState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
let earlierController: AbortController | null = null

watch(() => earlierSession.value?.id ?? null, (sessionId) => {
  earlierController?.abort()
  earlierResults.value = null
  if (sessionId === null) {
    earlierState.value = 'idle'
    return
  }
  earlierState.value = 'loading'
  const controller = new AbortController()
  earlierController = controller
  void loadComparisonResults(sessionId)
    .then((result) => {
      if (controller.signal.aborted || earlierSession.value?.id !== sessionId) return
      earlierResults.value = result
      earlierState.value = 'ready'
    })
    .catch(() => {
      if (!controller.signal.aborted && earlierSession.value?.id === sessionId) {
        earlierState.value = 'error'
      }
    })
}, { immediate: true })

const earlierScopeStudents = computed(() => {
  if (!earlierResults.value) return null
  return scope.value === null
    ? earlierResults.value.students
    : earlierResults.value.students.filter(
      (student) => (student.class_name ?? '') === scope.value,
    )
})

const consecGroups = computed(() => {
  const previous = previousScopeStudents.value
  const earlier = earlierScopeStudents.value
  if (!previous || !earlier) {
    return {
      improving: [] as ConsecutiveRankEntry[],
      declining: [] as ConsecutiveRankEntry[],
    }
  }
  return consecutiveRankChanges(studentsInScope.value, previous, earlier)
})

const RANK_LIST_LIMIT = 8
const rankListExpanded = ref<Record<string, boolean>>({})

interface RankSection<E> {
  key: string
  title: string
  rows: E[]
}

const tierSections = computed<RankSection<RankTierEntry>[]>(() => {
  const groups = tierGroups.value
  return [
    { key: 'enteringTop', title: '进入前 1/4', rows: groups.enteringTop },
    { key: 'leavingTop', title: '跌出前 1/4', rows: groups.leavingTop },
    { key: 'leavingBottom', title: '走出后 1/4', rows: groups.leavingBottom },
    { key: 'enteringBottom', title: '跌入后 1/4', rows: groups.enteringBottom },
  ]
})

const consecSections = computed<RankSection<ConsecutiveRankEntry>[]>(() => {
  const sequence = earlierSession.value !== null && comparisonName.value !== null
    ? `（「${earlierSession.value.name}」→「${comparisonName.value}」→本场，每次 ≥3 名）`
    : ''
  const groups = consecGroups.value
  return [
    { key: 'improving', title: `连续进步${sequence}`, rows: groups.improving },
    { key: 'declining', title: `连续退步${sequence}`, rows: groups.declining },
  ]
})

function shownRankRows<E>(section: RankSection<E>): E[] {
  return rankListExpanded.value[section.key]
    ? section.rows
    : section.rows.slice(0, RANK_LIST_LIMIT)
}

function toggleRankSection(key: string): void {
  rankListExpanded.value = {
    ...rankListExpanded.value,
    [key]: !rankListExpanded.value[key],
  }
}

/** 分数分布条只渲染有人数的分数段；段宽按人数 flex-grow。 */
function visibleBands(summary: ClassSummary): (OverviewBand & { count: number })[] {
  return OVERVIEW_BANDS
    .map((band) => ({ ...band, count: summary.bandCounts[band.id] }))
    .filter((band) => band.count > 0)
}

function bandAriaLabel(summary: ClassSummary): string {
  return OVERVIEW_BANDS.map(
    (band) => `${band.label} ${summary.bandCounts[band.id]} 人`,
  ).join('，')
}

function isActiveRow(row: ClassSummary): boolean {
  return row.key === scope.value
}

const previousClassSummaries = computed<Map<string | null, ClassSummary> | null>(() => {
  if (previousState.value !== 'ready' || !previousResults.value) return null
  const map = new Map<string | null, ClassSummary>()
  for (const key of classKeys.value) {
    map.set(key, summarizeStudents(
      previousResults.value.students.filter(
        (student) => (student.class_name ?? '') === key,
      ),
      key,
    ))
  }
  map.set(null, summarizeStudents(previousResults.value.students, null))
  return map
})

const comparisonName = computed(() => (
  comparisonOptions.value.find((option) => option.id === comparisonId.value)?.name
  ?? null
))

// 平均分只在两次满分相同且都 >0 时比较；两率始终按百分点比较。
const sameFullScore = computed(() => (
  previousResults.value !== null
  && props.results.summary.max_score > 0
  && previousResults.value.summary.max_score === props.results.summary.max_score
))

function rowDelta(row: ClassSummary): ClassDelta | null {
  const previous = previousClassSummaries.value
  if (previous === null) return null
  return classDelta(row, previous.get(row.key) ?? null, sameFullScore.value)
}

function deltaText(delta: number | null, mode: 'score' | 'rate'): string | null {
  if (delta === null) return null
  const text = mode === 'rate'
    ? String(Math.round(Math.abs(delta) * 100))
    : formatScore(Math.abs(delta))
  if (text === '0') return null
  return `${delta > 0 ? '↑' : '↓'}${text}`
}

function deltaEntry(
  delta: number | null,
  mode: 'score' | 'rate',
): { text: string; up: boolean } | null {
  const text = deltaText(delta, mode)
  return text === null ? null : { text, up: (delta ?? 0) > 0 }
}

const tableRows = computed(() => classRows.value.map((row) => {
  const delta = rowDelta(row)
  return {
    row,
    averageDelta: deltaEntry(delta?.average ?? null, 'score'),
    passDelta: deltaEntry(delta?.passRate ?? null, 'rate'),
    excellentDelta: deltaEntry(delta?.excellentRate ?? null, 'rate'),
  }
}))

function deltaTitle(): string | undefined {
  return comparisonName.value === null
    ? undefined
    : `较「${comparisonName.value}」`
}

function structureAria(structure: {
  full: number
  partial: number
  zero: number
  resolved: number
}): string {
  return `满分 ${structure.full} 人，部分得分 ${structure.partial} 人，0 分 ${structure.zero} 人`
}

function structureWidth(count: number, resolved: number): string {
  return resolved === 0 ? '0%' : `${(count / resolved) * 100}%`
}

function openQuestion(questionId: string): void {
  emit('open-question', questionId, scope.value)
}

// 与成绩明细的复核跳转一致：定位到该学生该题的复核条目。
function openReviewNote(note: AnalysisReviewNoteItem): void {
  void router.push({
    path: '/grading',
    query: {
      session: String(props.sessionId),
      scope: 'all',
      question: note.question_id,
      ...(note.review_item_id ? { item: note.review_item_id } : {}),
      student: String(note.student_id),
      entry: 'results',
    },
  })
}

// ---- 看卷 10 分钟 ----
const WALKTHROUGH_RESUME_KEY = 'ai-grading:paper-walkthrough:v1'
const walkthroughOpen = ref(false)
const walkthroughData = computed(() => walkthroughDataFor(props.results))
const walkthroughResumeIndex = ref<number | undefined>(undefined)
const walkthroughAvailable = computed(() => (
  studentsInScope.value.some(isCompleteStudent)
))
const previousScopeStudents = computed(() => {
  if (!previousResults.value) return null
  return scope.value === null
    ? previousResults.value.students
    : previousResults.value.students.filter(
      (student) => (student.class_name ?? '') === scope.value,
    )
})
const walkthroughScopeLabel = computed(() => (
  scope.value === null ? '全部班级' : classDisplayLabel(scope.value)
))
// 班级分析与对比考试数据到位前不允许开看卷，否则错法分组/名次变化会缺类
const walkthroughReady = computed(() => (
  (analysisState.value === 'ready' || analysisState.value === 'error')
  && previousState.value !== 'loading'
))

function openWalkthrough(): void {
  walkthroughResumeIndex.value = undefined
  walkthroughOpen.value = true
}

// 首页入口只在本次参数请求就绪后打开一次；关闭后不再次自动打开。
watch([() => route.query.open, walkthroughAvailable, walkthroughReady], ([open, available, ready]) => {
  if (open !== 'walkthrough' || !available || !ready) return
  openWalkthrough()
  const query = { ...route.query }
  delete query.open
  void router.replace({ query })
}, { immediate: true })

onMounted(() => {
  let saved: { sessionId?: number; scope?: string | null; index?: number } | null = null
  try {
    const raw = sessionStorage.getItem(WALKTHROUGH_RESUME_KEY)
    if (!raw) return
    saved = JSON.parse(raw) as { sessionId?: number; scope?: string | null; index?: number }
    sessionStorage.removeItem(WALKTHROUGH_RESUME_KEY)
  } catch { return }
  if (!saved || saved.sessionId !== props.sessionId) return
  const targetScope = saved.scope === undefined ? null : saved.scope
  if (targetScope !== null && !classKeys.value.includes(targetScope)) return
  if (targetScope !== scope.value) emit('update:scope', targetScope)
  // 等该范围的分析与对比数据就绪后再恢复；考试切换则放弃
  const tryResume = (): boolean => {
    if (props.sessionId !== saved!.sessionId) return true
    if (!walkthroughReady.value || scope.value !== targetScope) return false
    if (!walkthroughAvailable.value) return true
    walkthroughResumeIndex.value = typeof saved!.index === 'number'
      ? saved!.index
      : undefined
    walkthroughOpen.value = true
    return true
  }
  if (tryResume()) return
  const stop = watch(
    [walkthroughReady, scope, () => props.sessionId],
    () => { if (tryResume()) stop() },
  )
})
</script>

<template>
  <div class="overview" data-testid="results-overview">
    <div class="overview__controls">
      <div class="overview__scope" role="group" aria-label="统计范围">
        <button
          type="button"
          :class="{ 'is-active': scope === null }"
          :aria-pressed="scope === null"
          @click="scope = null"
        >全部</button>
        <button
          v-for="key in classKeys"
          :key="key"
          type="button"
          :class="{ 'is-active': scope === key }"
          :aria-pressed="scope === key"
          @click="scope = key"
        >{{ classDisplayLabel(key) }}</button>
      </div>
      <label v-if="comparisonOptions.length" class="overview__compare">
        <span>对比：</span>
        <select class="app-input" v-model.number="comparisonId" aria-label="对比考试">
          <option
            v-for="option in comparisonOptions"
            :key="option.id"
            :value="option.id"
          >{{ option.name }}</option>
        </select>
      </label>
    </div>

    <section class="overview__section overview__todo-section" aria-label="待办">
      <div class="overview__todo">
        <div class="overview__todo-item">
          <template v-if="reviewPending > 0">
            <span class="overview__todo-text">
              复核 · {{ reviewPending }} 题待处理<template v-if="reviewBreakdown">（{{ reviewBreakdown }}）</template>
            </span>
            <AppButton
              variant="ghost"
              @click="emit('open-review', 'teacher_pending')"
            >去复核</AppButton>
            <AppButton
              v-if="walkthroughAvailable"
              variant="ghost"
              :disabled="!walkthroughReady"
              :title="walkthroughReady ? undefined : '正在准备数据…'"
              @click="openWalkthrough"
            >看卷 10 分钟</AppButton>
          </template>
          <template v-else>
            <span class="overview__todo-text">
              复核 · 已全部确认（教师确认 {{ results.summary.teacher_final_item_count }} · AI 评分 {{ results.summary.ai_ready_item_count }}）
            </span>
            <AppButton
              v-if="walkthroughAvailable"
              variant="primary"
              :disabled="!walkthroughReady"
              :title="walkthroughReady ? undefined : '正在准备数据…'"
              @click="openWalkthrough"
            >看卷 10 分钟</AppButton>
            <AppButton
              variant="ghost"
              @click="emit('open-review', 'all')"
            >抽查复核</AppButton>
          </template>
        </div>
        <div
          v-if="pendingReviewNotes.length > 0"
          class="overview__todo-item overview__todo-item--end"
        >
          <span class="overview__todo-text">
            报告提示 · {{ pendingReviewNotes.length }} 条待核对
          </span>
          <AppButton
            variant="ghost"
            :aria-expanded="reviewNotesExpanded"
            @click="reviewNotesExpanded = !reviewNotesExpanded"
          >{{ reviewNotesExpanded ? '收起' : '查看明细' }}</AppButton>
        </div>
      </div>
      <div
        v-if="reviewNotesExpanded && reviewNotes.length > 0"
        class="overview__notes"
        data-testid="report-review-notes"
      >
        <ul class="overview__notes-list">
          <li
            v-for="(note, index) in reviewNotes"
            :key="`${note.student_id}:${note.question_id}:${index}`"
            class="overview__notes-item"
            :class="{ 'is-confirmed': note.status === 'confirmed' }"
          >
            <span class="overview__notes-student">
              {{ note.student_name }}<small v-if="note.class_name">
                {{ classDisplayLabel(note.class_name) }}
              </small>
            </span>
            <span class="overview__notes-question">{{ note.display_label }}</span>
            <span class="overview__notes-text">{{ note.note }}</span>
            <span
              v-if="note.status === 'confirmed'"
              class="overview__notes-status"
            >已核对</span>
            <AppButton
              v-else
              variant="ghost"
              size="small"
              class="overview__notes-action"
              @click="openReviewNote(note)"
            >去核对</AppButton>
          </li>
        </ul>
      </div>
    </section>

    <div class="overview__duo">
    <section class="overview__section" aria-labelledby="overview-classes-title">
      <h2 id="overview-classes-title" class="overview__title">班级对比</h2>
      <div class="overview__table-wrap">
        <table class="app-table overview__table">
          <thead>
            <tr>
              <th scope="col">班级</th>
              <th scope="col" class="is-num">参考</th>
              <th scope="col" class="is-num">缺考</th>
              <th scope="col" class="is-num">平均</th>
              <th scope="col" class="is-num">中位</th>
              <th scope="col" class="is-num">最高/最低</th>
              <th scope="col" class="is-num">及格率</th>
              <th scope="col" class="is-num">优秀率</th>
              <th scope="col">分数分布</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="({ row, averageDelta, passDelta, excellentDelta }) in tableRows"
              :key="row.key ?? '__all__'"
              :class="{ 'is-active': isActiveRow(row), 'is-total': row.key === null }"
            >
              <th scope="row">
                <button
                  type="button"
                  class="overview__link overview__class-link"
                  :aria-pressed="isActiveRow(row)"
                  @click="scope = row.key"
                >{{ row.label }}</button>
              </th>
              <td class="is-num">{{ row.studentCount }}</td>
              <td class="is-num">{{ row.absentCount === null ? '—' : row.absentCount }}</td>
              <td class="is-num">{{ formatScore(row.average) }}<small
                v-if="averageDelta"
                class="overview__delta"
                :class="averageDelta.up ? 'is-up' : 'is-down'"
                :title="deltaTitle()"
              >{{ averageDelta.text }}</small></td>
              <td class="is-num">{{ formatScore(row.median) }}</td>
              <td class="is-num">{{ formatScore(row.highest) }} / {{ formatScore(row.lowest) }}</td>
              <td class="is-num">{{ formatRate(row.passRate) }}<small
                v-if="passDelta"
                class="overview__delta"
                :class="passDelta.up ? 'is-up' : 'is-down'"
                :title="deltaTitle()"
              >{{ passDelta.text }}</small></td>
              <td class="is-num">{{ formatRate(row.excellentRate) }}<small
                v-if="excellentDelta"
                class="overview__delta"
                :class="excellentDelta.up ? 'is-up' : 'is-down'"
                :title="deltaTitle()"
              >{{ excellentDelta.text }}</small></td>
              <td>
                <span
                  class="overview__bandbar"
                  role="img"
                  :aria-label="bandAriaLabel(row)"
                >
                  <i
                    v-for="band in visibleBands(row)"
                    :key="band.id"
                    :data-band="band.id"
                    :style="{ flexGrow: band.count }"
                    :title="`${band.label} ${band.count} 人`"
                  >{{ band.count }}</i>
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="overview__legend">
        <span v-for="band in OVERVIEW_BANDS" :key="band.id">
          <i :data-band="band.id" aria-hidden="true"></i>{{ band.label }} {{ band.range }}
        </span>
        <span
          v-if="previousState === 'ready' && comparisonName !== null"
          class="overview__legend-note"
        >↑↓ 为较「{{ comparisonName }}」的变化；平均分仅在两次满分相同时比较，及格率、优秀率按百分点。</span>
      </p>
    </section>
      <section
        class="overview__section overview__critical-card"
        aria-labelledby="overview-critical-title"
        data-testid="critical-students"
      >
        <h2 id="overview-critical-title" class="overview__title">临界学生</h2>
        <div class="overview__critical-body">
          <div
            v-for="group in criticalGroups"
            :key="group.key"
            class="overview__critical-group"
          >
            <h3 class="overview__critical-title">{{ group.title }}</h3>
            <ul class="overview__notes-list overview__critical-list">
              <li
                v-for="entry in group.rows"
                :key="entry.student.student_id"
                class="overview__notes-item"
              >
                <button
                  type="button"
                  class="overview__link overview__notes-student"
                  @click="emit('open-student', entry.student.student_id)"
                >
                  {{ entry.student.student_name }}<small v-if="scope === null">
                    {{ classDisplayLabel(entry.student.class_name) }}
                  </small>
                </button>
                <span class="overview__notes-text">
                  {{ formatScore(entry.student.current_score) }} 分 · 差 {{ formatScore(entry.gap) }} 分
                </span>
              </li>
            </ul>
          </div>
          <p v-if="criticalGroups.length === 0" class="overview__tile-sub">
            没有差 5 分内及格或优秀的学生
          </p>
        </div>
      </section>
    </div>

    <section class="overview__section" aria-labelledby="overview-ranks-title">
      <div class="overview__tile overview__tile--ranks">
        <div class="overview__tile-head">
          <h3 id="overview-ranks-title">进退步</h3>
          <span
            v-if="previousState === 'ready' && comparisonName !== null"
            class="overview__tile-sub"
          >对比「{{ comparisonName }}」 · 两场都有完整成绩且同班的 {{ rankEntries.length }} 人</span>
        </div>
        <p v-if="!comparisonOptions.length" class="overview__tile-sub">
          没有可对比的上一场考试
        </p>
        <Skeleton
          v-else-if="previousState === 'loading'"
          class="overview__skeleton-block"
          aria-hidden="true"
        />
        <p v-else-if="previousState === 'error'" class="overview__tile-sub">
          对比考试的成绩暂时无法读取。
        </p>
        <template v-else-if="previousState === 'ready'">
          <div class="overview__tier-grid">
            <div
              v-for="section in tierSections"
              :key="section.key"
              class="overview__rank-group"
            >
              <h4 :title="tierHint">{{ section.title }} · {{ section.rows.length }} 人</h4>
              <p v-if="section.rows.length === 0" class="overview__tile-sub">无</p>
              <template v-else>
                <ul class="overview__notes-list overview__tier-list">
                  <li
                    v-for="entry in shownRankRows(section)"
                    :key="entry.student.student_id"
                    class="overview__notes-item"
                  >
                    <button
                      type="button"
                      class="overview__link overview__notes-student"
                      @click="emit('open-student', entry.student.student_id)"
                    >
                      {{ entry.student.student_name }}<small v-if="scope === null">
                        {{ classDisplayLabel(entry.student.class_name) }}
                      </small>
                    </button>
                    <span class="overview__notes-text">
                      <b
                        class="overview__tier-ranks"
                        :class="entry.change >= 0 ? 'is-up' : 'is-down'"
                      >{{ entry.previousRank }}→{{ entry.currentRank }}</b>
                    </span>
                  </li>
                </ul>
                <button
                  v-if="section.rows.length > RANK_LIST_LIMIT"
                  type="button"
                  class="overview__link overview__tier-more"
                  @click="toggleRankSection(section.key)"
                >{{ rankListExpanded[section.key] ? '收起' : '展开' }}</button>
              </template>
            </div>
          </div>
          <div class="overview__consec">
            <h4 class="overview__consec-heading">连续变化（近三场）</h4>
            <p v-if="earlierSession === null" class="overview__tile-sub">
              需要三场同卷别考试才能看连续变化
            </p>
            <Skeleton
              v-else-if="earlierState !== 'ready' && earlierState !== 'error'"
              class="overview__skeleton-block"
              aria-hidden="true"
            />
            <p v-else-if="earlierState === 'error'" class="overview__tile-sub">
              第三场考试的成绩暂时无法读取。
            </p>
            <div v-else class="overview__tier-grid">
              <div
                v-for="section in consecSections"
                :key="section.key"
                class="overview__rank-group"
              >
                <h4>{{ section.title }} · {{ section.rows.length }} 人</h4>
                <p v-if="section.rows.length === 0" class="overview__tile-sub">无</p>
                <template v-else>
                  <ul class="overview__notes-list overview__tier-list">
                    <li
                      v-for="entry in shownRankRows(section)"
                      :key="entry.student.student_id"
                      class="overview__notes-item"
                    >
                      <button
                        type="button"
                        class="overview__link overview__notes-student"
                        @click="emit('open-student', entry.student.student_id)"
                      >
                        {{ entry.student.student_name }}<small v-if="scope === null">
                          {{ classDisplayLabel(entry.student.class_name) }}
                        </small>
                      </button>
                      <span class="overview__notes-text">
                        <b
                          class="overview__tier-ranks"
                          :class="entry.totalChange >= 0 ? 'is-up' : 'is-down'"
                        >{{ entry.earlierRank }}→{{ entry.previousRank }}→{{ entry.currentRank }}</b>
                      </span>
                    </li>
                  </ul>
                  <button
                    v-if="section.rows.length > RANK_LIST_LIMIT"
                    type="button"
                    class="overview__link overview__tier-more"
                    @click="toggleRankSection(section.key)"
                  >{{ rankListExpanded[section.key] ? '收起' : '展开' }}</button>
                </template>
              </div>
            </div>
          </div>
        </template>
      </div>
    </section>

    <section class="overview__section" aria-labelledby="overview-questions-title">
      <div class="overview__section-head">
        <h2 id="overview-questions-title" class="overview__title">题目得分率</h2>
        <div class="overview__sort" role="group" aria-label="题目排序">
          <button
            v-for="option in sortOptions"
            :key="option.id"
            type="button"
            :class="{ 'is-active': sortBy === option.id }"
            :aria-pressed="sortBy === option.id"
            @click="sortBy = option.id"
          >{{ option.label }}</button>
        </div>
      </div>
      <ol class="overview__questions">
        <li v-for="row in questionRows" :key="row.questionId">
          <button
            type="button"
            class="overview__question"
            :data-question-id="row.questionId"
            @click="openQuestion(row.questionId)"
          >
            <span class="overview__qid">{{ row.questionId }}</span>
            <span class="overview__stem" :title="row.stemTitle ?? undefined">
              <Skeleton
                v-if="analysisState === 'loading'"
                class="overview__skeleton-text"
                aria-hidden="true"
              />
              <template v-else-if="row.subIndex !== null">
                第 {{ row.subIndex }} 小问<template v-if="row.answer">
                  · 答案 <span :title="row.answerFull ?? undefined"><QuestionHtmlBlock :text="row.answer" inline typeset-text /></span>
                </template>
              </template>
              <QuestionHtmlBlock
                v-else-if="row.stem"
                :text="row.stem"
                inline
                typeset-text
              />
              <template v-else>—</template>
            </span>
            <span class="overview__rate">
              <span
                class="overview__structure"
                role="img"
                :aria-label="structureAria(row.structure)"
              >
                <i
                  class="is-full"
                  :style="{ width: structureWidth(row.structure.full, row.structure.resolved) }"
                  :title="`满分 ${row.structure.full} 人`"
                ></i>
                <i
                  class="is-partial"
                  :style="{ width: structureWidth(row.structure.partial, row.structure.resolved) }"
                  :title="`部分得分 ${row.structure.partial} 人`"
                ></i>
                <i
                  class="is-zero"
                  :style="{ width: structureWidth(row.structure.zero, row.structure.resolved) }"
                  :title="`0 分 ${row.structure.zero} 人`"
                ></i>
              </span>
              <strong>{{ formatRate(row.rate) }}</strong>
              <span
                v-if="sortBy === 'zero' && row.zeroShare !== null"
                class="overview__zero-share"
              >0 分 {{ formatRate(row.zeroShare) }}</span>
              <span
                v-if="row.classRates.length"
                class="overview__classrates"
              ><template
                v-for="(entry, index) in row.classRates"
                :key="entry.key"
              ><template v-if="index"> · </template><span
                :class="{ 'is-gap': entry.isLow }"
              >{{ entry.label }} {{ formatRate(entry.rate) }}</span></template></span>
            </span>
          </button>
        </li>
      </ol>
    </section>

    <PaperWalkthrough
      v-if="walkthroughOpen"
      :session-id="sessionId"
      :scope-key="scope"
      :scope-label="walkthroughScopeLabel"
      :students="studentsInScope"
      :question-ids="questionIds"
      :analysis-questions="scopeAnalysisQuestions"
      :previous-students="previousScopeStudents"
      :pending-count="reviewPending"
      :initial-index="walkthroughResumeIndex"
      :data-cache="walkthroughData"
      @close="walkthroughOpen = false"
    />

  </div>
</template>
