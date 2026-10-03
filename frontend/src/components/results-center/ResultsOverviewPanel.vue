<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  classAnalysisApi,
  type ClassAnalysisResponse,
} from '../../api/class-analysis'
import type { ResultsCenterResponse } from '../../api/results-center'
import {
  exportsApi,
  type AnalysisReviewNoteItem,
} from '../../api/exports'
import { jobApi, TERMINAL_JOB_STATUSES } from '../../api/jobs'
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
  buildOverviewTiles,
  classDisplayLabel,
  classKeysOf,
  comparisonCandidates,
  defaultComparison,
  displayAnswer,
  formatCount,
  formatRate,
  formatScore,
  isCompleteStudent,
  questionRatesFor,
  rankChangeGroups,
  rankChanges,
  scoreStructureFor,
  subQuestionLabelFor,
  summarizeStudents,
  topCauseOf,
  type ClassSummary,
  type OverviewBandId,
  type OverviewTile,
  type RankChange,
} from './results-overview'

const props = defineProps<{
  results: ResultsCenterResponse
  sessionId: number
  scope?: string | null
}>()

const emit = defineEmits<{
  'open-student': [studentId: number]
  'open-question': [questionId: string, className: string | null]
  'open-low-list': [scope: string | null]
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

const sortBy = ref<'rate' | 'number'>('rate')
const analysis = ref<ClassAnalysisResponse | null>(null)
const analysisState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const mergedAnalysis = ref<ClassAnalysisResponse | null>(null)
const narrative = ref<ClassAnalysisResponse | null>(null)
const narrativeState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const narrativeCache = new Map<string, ClassAnalysisResponse>()

let loadGeneration = 0
let scopeController: AbortController | null = null
let mergedController: AbortController | null = null
let narrativeController: AbortController | null = null
let notesController: AbortController | null = null

const reviewNotes = ref<AnalysisReviewNoteItem[]>([])
const reviewNotesExpanded = ref(false)
const pendingReviewNotes = computed(() =>
  reviewNotes.value.filter((item) => item.status === 'pending'))

async function loadReviewNotes(sessionId: number): Promise<void> {
  notesController?.abort()
  const controller = new AbortController()
  notesController = controller
  try {
    const result = await exportsApi.getAnalysisReviewNotes(
      sessionId,
      controller.signal,
    )
    if (controller.signal.aborted || props.sessionId !== sessionId) return
    reviewNotes.value = result.items
  } catch {
    if (!controller.signal.aborted && props.sessionId === sessionId) {
      reviewNotes.value = []
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
    // The existing full response contains both statistics and narrative;
    // avoid assembling the same exam twice when neither is cached yet.
    void loadScope(scopeKey, sessionId, true).then(() => {
      if (scope.value === scopeKey && props.sessionId === sessionId) {
        void loadNarrative(scopeKey, sessionId)
      }
    })
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
  includeNarrative = false,
): Promise<void> {
  // '' 的 class_name 在后端表示合并全部班级；范围 null 与未分班都落到同一请求。
  const apiScope = scopeKey ?? ''
  const generation = ++loadGeneration
  scopeController?.abort()
  const cached = cachedClassAnalysis(sessionId, apiScope)
  if (cached) {
    analysis.value = cached
    analysisState.value = 'ready'
    if (apiScope === '') mergedAnalysis.value = cached
    return
  }
  analysis.value = null
  analysisState.value = 'loading'
  const controller = new AbortController()
  scopeController = controller
  try {
    const result = await classAnalysisApi.getClassAnalysis(
      sessionId,
      controller.signal,
      apiScope,
      includeNarrative ? 'full' : 'summary',
    )
    if (generation !== loadGeneration || props.sessionId !== sessionId) return
    rememberClassAnalysis(sessionId, apiScope, result)
    if (includeNarrative) narrativeCache.set(`${sessionId}::${apiScope}`, result)
    analysis.value = result
    analysisState.value = 'ready'
    if (apiScope === '') mergedAnalysis.value = result
  } catch {
    if (generation !== loadGeneration || props.sessionId !== sessionId) return
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

const narrativeActiveJob = computed(() => {
  const id = narrative.value?.active_job_id ?? null
  return id === null ? null : jobStore.jobs[id] ?? null
})
const narrativeGenerating = computed(() => (
  narrative.value?.status === 'generating'
  || (narrativeActiveJob.value !== null
    && !TERMINAL_JOB_STATUSES.has(narrativeActiveJob.value.status))
))
const narrativeFindings = computed(() => (
  narrative.value?.narrative?.key_findings.slice(0, 2) ?? []
))

watch(() => narrativeActiveJob.value?.status, (status) => {
  if (status === undefined || !TERMINAL_JOB_STATUSES.has(status)) return
  narrativeCache.delete(`${props.sessionId}::${scope.value ?? ''}`)
  void loadNarrative(scope.value, props.sessionId)
})

async function loadNarrative(
  scopeKey: string | null,
  sessionId: number,
): Promise<void> {
  const apiScope = scopeKey ?? ''
  const cacheKey = `${sessionId}::${apiScope}`
  narrativeController?.abort()
  const cached = narrativeCache.get(cacheKey)
  if (cached) {
    narrative.value = cached
    narrativeState.value = 'ready'
    return
  }
  narrative.value = null
  narrativeState.value = 'loading'
  const controller = new AbortController()
  narrativeController = controller
  try {
    const result = await classAnalysisApi.getClassAnalysis(
      sessionId,
      controller.signal,
      apiScope,
      'narrative',
    )
    if (controller.signal.aborted || props.sessionId !== sessionId) return
    narrativeCache.set(cacheKey, result)
    narrative.value = result
    narrativeState.value = 'ready'
    void ensureNarrativeTracked(result.active_job_id)
  } catch {
    if (controller.signal.aborted || props.sessionId !== sessionId) return
    narrativeState.value = 'error'
  }
}

async function ensureNarrativeTracked(id: number | null): Promise<void> {
  if (id === null) return
  const existing = jobStore.jobs[id]
  if (existing !== undefined && !TERMINAL_JOB_STATUSES.has(existing.status)) return
  try {
    const job = await jobApi.getJob(id)
    if (jobStore.jobs[id] !== undefined) return
    if (!TERMINAL_JOB_STATUSES.has(job.status)) jobStore.track(job)
  } catch {
    // 任务可能已结束或被清理；下次进入时按最新状态展示。
  }
}

function reloadNarrative(): void {
  narrativeCache.delete(`${props.sessionId}::${scope.value ?? ''}`)
  void loadNarrative(scope.value, props.sessionId)
}

function reloadAnalysis(): void {
  void loadScope(scope.value, props.sessionId)
}

function openReport(): void {
  void router.push({
    path: '/class-report',
    query: {
      session: String(props.sessionId),
      ...(scope.value ? { class: scope.value } : {}),
    },
  })
}

onBeforeUnmount(() => {
  loadGeneration += 1
  scopeController?.abort()
  mergedController?.abort()
  comparisonController?.abort()
  narrativeController?.abort()
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

const causeIssue = computed<'missing' | 'legacy' | null>(() => {
  if (analysisState.value === 'error') return 'missing'
  if (analysisState.value !== 'ready') return null
  const questions = analysis.value?.data?.questions ?? []
  const hasStructured = questions.some(
    (question) => (question.causes ?? []).some((cause) => cause.kind !== undefined),
  )
  if (hasStructured) return null
  return questions.some((question) => (question.causes ?? []).length > 0)
    ? 'legacy'
    : 'missing'
})

const tiles = computed(() => buildOverviewTiles({
  scopeKey: scope.value,
  scopeStudents: studentsInScope.value,
  classes: classSummaries.value,
  questionRates: scopeQuestionRates.value,
  classQuestionRates: scope.value === null ? classQuestionRates.value : undefined,
  analysisQuestions: scopeAnalysisQuestions.value,
  scoreStructure: scopeScoreStructure.value,
}))

interface QuestionRow {
  questionId: string
  maxScore: number
  rate: number | null
  stem: string | null
  stemTitle: string | null
  subIndex: number | null
  answer: string | null
  answerFull: string | null
  causeLabel: string | null
  causeCount: number | null
  causeLegacy: boolean
  structure: { full: number; partial: number; zero: number; resolved: number }
  classRates: { key: string; label: string; rate: number | null; isLow: boolean }[]
}

const CLASS_RATE_GAP = 0.15

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
    const topCause = analysisQuestion ? topCauseOf(analysisQuestion) : null
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
    return {
      questionId: question.question_id,
      maxScore: question.max_score,
      rate: scopeQuestionRates.value.get(question.question_id) ?? null,
      stem,
      stemTitle: sub?.parentStem ?? stem,
      subIndex: sub?.index ?? null,
      answer: sub ? displayAnswer(analysisQuestion?.canonical_answer ?? null) : null,
      answerFull: sub ? (analysisQuestion?.canonical_answer ?? null) : null,
      causeLabel: topCause !== null && topCause !== 'legacy' ? topCause.label : null,
      causeCount: topCause !== null && topCause !== 'legacy' ? topCause.count : null,
      causeLegacy: topCause === 'legacy',
      structure: scopeScoreStructure.value.get(question.question_id)
        ?? { full: 0, partial: 0, zero: 0, resolved: 0 },
      classRates,
    }
  })
  if (sortBy.value === 'rate') {
    rows.sort((left, right) => (
      left.rate === null ? 1 : right.rate === null ? -1 : left.rate - right.rate
    ))
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

type MarkerTileId =
  | 'review'
  | 'report-notes'
  | 'ai-report'
  | 'ranks'
  | 'cause-categories'
type DisplayTile = OverviewTile | { id: MarkerTileId }

interface TileSlot {
  tile: DisplayTile
  /** 行内宽度占比（4 列布局中的份数）；由数据决定，保证每行铺满无空位。 */
  span: number
}

const tileRows = computed<TileSlot[][]>(() => {
  const byId = new Map(tiles.value.map((tile) => [tile.id, tile]))
  const fillers: TileSlot[] = []
  for (const id of ['low-tail', 'weak-questions', 'zero-share'] as const) {
    const tile = byId.get(id)
    if (tile) fillers.push({ tile, span: 1 })
  }
  // 报告提示磁贴紧邻「复核」出现；行宽超出时把其余磁贴顺延到第三行。
  const row1: TileSlot[] = [
    { tile: { id: 'review' }, span: 1 },
    ...(pendingReviewNotes.value.length > 0
      ? [{ tile: { id: 'report-notes' } as DisplayTile, span: 1 }]
      : []),
    ...fillers.slice(0, pendingReviewNotes.value.length > 0 ? 2 : 3),
  ]
  const overflow = fillers.slice(pendingReviewNotes.value.length > 0 ? 2 : 3)
  // 失分类型磁贴始终占位（加载/无数据/失败态都在格内展示），保证第二行恒为 1+3。
  const categories = byId.get('cause-categories')
  const row2: TileSlot[] = [
    { tile: categories ?? { id: 'cause-categories' }, span: 1 },
    { tile: { id: 'ranks' }, span: 3 },
  ]
  const classGap = byId.get('class-gap')
  const row3: TileSlot[] = [
    ...overflow,
    ...(classGap ? [{ tile: classGap, span: 1 }] : []),
    { tile: { id: 'ai-report' }, span: 1 },
  ]
  return [row1, row2, row3]
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
  if (!previousResults.value) return []
  const current = studentsInScope.value
  const previous = scope.value === null
    ? previousResults.value.students
    : previousResults.value.students.filter(
      (student) => (student.class_name ?? '') === scope.value,
    )
  return rankChanges(current, previous)
})

const rankGroups = computed(() => rankChangeGroups(
  rankEntries.value,
  5,
  10,
))
const ranksExpanded = ref(false)

const rankRows = computed(() => ({
  improved: ranksExpanded.value
    ? rankGroups.value.improved
    : rankGroups.value.improved.slice(0, 5),
  declined: ranksExpanded.value
    ? rankGroups.value.declined
    : rankGroups.value.declined.slice(0, 5),
}))

const ranksExpandable = computed(() => (
  rankGroups.value.improved.length > 5 || rankGroups.value.declined.length > 5
))

/** 名次→横轴位置：第 1 名在右端，最后一名在左端；范围外按边缘截断。 */
function rankFraction(rank: number, size: number): number {
  if (size <= 1) return 0.5
  const clamped = Math.max(1, Math.min(size, rank))
  return 1 - (clamped - 1) / (size - 1)
}

function rankPosition(rank: number, size: number): string {
  return `${rankFraction(rank, size) * 100}%`
}

function rankSegmentStyle(entry: RankChange): Record<string, string> {
  const from = rankFraction(entry.previousRank, entry.currentSize)
  const to = rankFraction(entry.currentRank, entry.currentSize)
  return {
    left: `${Math.min(from, to) * 100}%`,
    width: `${Math.abs(from - to) * 100}%`,
  }
}

function rankAria(entry: RankChange): string {
  const parts = [entry.student.student_name]
  if (scope.value === null) parts.push(classDisplayLabel(entry.student.class_name))
  parts.push(
    `名次从第 ${entry.previousRank} 名到第 ${entry.currentRank} 名`,
    `${entry.change >= 0 ? '进步' : '退步'} ${Math.abs(entry.change)} 名`,
  )
  return parts.join('，')
}

function bandTotal(summary: ClassSummary): number {
  return OVERVIEW_BANDS.reduce(
    (total, band) => total + summary.bandCounts[band.id],
    0,
  )
}

function bandWidth(summary: ClassSummary, bandId: OverviewBandId): string {
  const total = bandTotal(summary)
  return total === 0
    ? '0%'
    : `${(summary.bandCounts[bandId] / total) * 100}%`
}

function bandAriaLabel(summary: ClassSummary): string {
  return OVERVIEW_BANDS.map(
    (band) => `${band.label} ${summary.bandCounts[band.id]} 人`,
  ).join('，')
}

function isActiveRow(row: ClassSummary): boolean {
  return row.key === scope.value
}

function rateWidth(rate: number | null): string {
  return rate === null ? '0%' : `${Math.max(0, Math.min(1, rate)) * 100}%`
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

function axisPosition(value: number, maxScore: number): string {
  return `${maxScore > 0 ? Math.max(0, Math.min(1, value / maxScore)) * 100 : 0}%`
}

function categoryColor(index: number): string {
  return `var(--chart-${(index % 5) + 1})`
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
    </div>

    <section class="overview__section" aria-labelledby="overview-classes-title">
      <h2 id="overview-classes-title" class="overview__title">班级对比</h2>
      <div class="overview__table-wrap">
        <table class="overview__table">
          <thead>
            <tr>
              <th scope="col">班级</th>
              <th scope="col">参考</th>
              <th scope="col">缺考</th>
              <th scope="col">平均</th>
              <th scope="col">中位</th>
              <th scope="col">最高/最低</th>
              <th scope="col">及格率</th>
              <th scope="col">优秀率</th>
              <th scope="col">分数分布</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in classRows"
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
              <td>{{ row.studentCount }}</td>
              <td>{{ row.absentCount === null ? '—' : row.absentCount }}</td>
              <td>{{ formatScore(row.average) }}</td>
              <td>{{ formatScore(row.median) }}</td>
              <td>{{ formatScore(row.highest) }} / {{ formatScore(row.lowest) }}</td>
              <td>{{ formatRate(row.passRate) }}</td>
              <td>{{ formatRate(row.excellentRate) }}</td>
              <td>
                <span
                  class="overview__bandbar"
                  role="img"
                  :aria-label="bandAriaLabel(row)"
                >
                  <i
                    v-for="band in OVERVIEW_BANDS"
                    :key="band.id"
                    :data-band="band.id"
                    :style="{ width: bandWidth(row, band.id) }"
                    :title="`${band.label} ${row.bandCounts[band.id]} 人`"
                  ></i>
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
      </p>
    </section>

    <section class="overview__section" aria-labelledby="overview-findings-title">
      <h2 id="overview-findings-title" class="overview__title">本次重点</h2>
      <div class="overview__tiles">
        <div
          v-for="(row, rowIndex) in tileRows"
          :key="rowIndex"
          class="overview__tile-row"
        >
        <template v-for="({ tile, span }) in row" :key="tile.id">
          <section
            v-if="tile.id === 'review'"
            class="overview__tile"
            :style="{ '--tile-span': span }"
          >
            <h3>复核</h3>
            <template v-if="reviewPending > 0">
              <strong class="overview__tile-num">{{ reviewPending }} 题待处理</strong>
              <span class="overview__tile-sub">{{ reviewBreakdown }}</span>
              <AppButton
                variant="ghost"
                class="overview__tile-action"
                @click="emit('open-review', 'teacher_pending')"
              >去复核</AppButton>
              <AppButton
                v-if="walkthroughAvailable"
                variant="ghost"
                class="overview__tile-action"
                :disabled="!walkthroughReady"
                :title="walkthroughReady ? undefined : '正在准备数据…'"
                @click="openWalkthrough"
              >看卷 10 分钟</AppButton>
            </template>
            <template v-else>
              <strong class="overview__tile-num">已全部确认</strong>
              <span class="overview__tile-sub">
                教师确认 {{ results.summary.teacher_final_item_count }} ·
                AI 评分 {{ results.summary.ai_ready_item_count }}
              </span>
              <AppButton
                v-if="walkthroughAvailable"
                variant="primary"
                class="overview__tile-action"
                :disabled="!walkthroughReady"
                :title="walkthroughReady ? undefined : '正在准备数据…'"
                @click="openWalkthrough"
              >看卷 10 分钟</AppButton>
              <AppButton
                variant="ghost"
                class="overview__tile-action"
                @click="emit('open-review', 'all')"
              >抽查复核</AppButton>
            </template>
          </section>
          <section
            v-else-if="tile.id === 'report-notes'"
            class="overview__tile overview__tile--notes"
            :style="{ '--tile-span': span }"
          >
            <h3>报告提示</h3>
            <strong class="overview__tile-num">
              {{ pendingReviewNotes.length }} 条待核对
            </strong>
            <span class="overview__tile-sub">个人报告中的建议核对点</span>
            <AppButton
              variant="ghost"
              class="overview__tile-action"
              :aria-expanded="reviewNotesExpanded"
              @click="reviewNotesExpanded = !reviewNotesExpanded"
            >{{ reviewNotesExpanded ? '收起' : '查看明细' }}</AppButton>
          </section>
          <section
            v-else-if="tile.id === 'low-tail'"
            class="overview__tile"
            :style="{ '--tile-span': span }"
          >
            <h3>低分学生</h3>
            <strong class="overview__tile-num">{{ tile.count }} 人</strong>
            <span class="overview__tile-sub">
              低于 40%<template v-if="tile.zeroAverage !== null">
                · 人均 0 分题 {{ formatCount(tile.zeroAverage) }} 道
              </template>
            </span>
            <template v-if="tile.perClass.length > 1">
              <span
                class="overview__tile-stack"
                role="img"
                :aria-label="tile.perClass.map((seg) => `${seg.label} ${seg.count} 人`).join('，')"
              >
                <i
                  v-for="seg in tile.perClass"
                  :key="seg.label"
                  :style="{ width: `${(seg.count / tile.count) * 100}%` }"
                  :title="`${seg.label} ${seg.count} 人`"
                ></i>
              </span>
              <span class="overview__tile-legend">
                {{ tile.perClass.map((seg) => `${seg.label} ${seg.count}`).join(' · ') }}
              </span>
            </template>
            <template v-if="tile.medianGap">
              <span
                class="overview__tile-axis"
                role="img"
                :aria-label="`平均 ${formatScore(tile.medianGap.average)}，中位 ${formatScore(tile.medianGap.median)}，位置按 0–100 分`"
              >
                <i
                  class="overview__tile-marker overview__tile-marker--avg"
                  :style="{ left: axisPosition(tile.medianGap.average, results.summary.max_score) }"
                  aria-hidden="true"
                ></i>
                <i
                  class="overview__tile-marker overview__tile-marker--median"
                  :style="{ left: axisPosition(tile.medianGap.median, results.summary.max_score) }"
                  aria-hidden="true"
                ></i>
              </span>
              <span class="overview__tile-legend">
                <span>
                  <i class="overview__tile-key overview__tile-key--avg" aria-hidden="true"></i>
                  平均 {{ formatScore(tile.medianGap.average) }}
                </span>
                <span>
                  <i class="overview__tile-key overview__tile-key--median" aria-hidden="true"></i>
                  中位 {{ formatScore(tile.medianGap.median) }}
                </span>
              </span>
              <p class="overview__tile-caption">低分拉低了平均</p>
            </template>
            <AppButton
              variant="ghost"
              class="overview__tile-action"
              @click="emit('open-low-list', scope)"
              >看名单</AppButton>
          </section>

          <section
            v-else-if="tile.id === 'weak-questions'"
            class="overview__tile"
            :style="{ '--tile-span': span }"
          >
            <h3>最弱题目</h3>
            <ol class="overview__weak">
              <li v-for="row in tile.rows" :key="row.questionId">
                <button
                  type="button"
                  class="overview__link overview__qid-link"
                  @click="openQuestion(row.questionId)"
                >{{ row.questionId }}</button>
                <span class="overview__mini-bar" aria-hidden="true">
                  <i :style="{ width: rateWidth(row.rate) }"></i>
                </span>
                <strong>{{ formatRate(row.rate) }}</strong>
              </li>
            </ol>
          </section>

          <section
            v-else-if="tile.id === 'zero-share'"
            class="overview__tile"
            :style="{ '--tile-span': span }"
          >
            <h3>0 分集中</h3>
            <strong class="overview__tile-num">
              <button
                type="button"
                class="overview__link overview__qid-link"
                @click="openQuestion(tile.top.questionId)"
              >{{ tile.top.questionId }}</button>
              {{ formatRate(tile.top.share) }}
            </strong>
            <span class="overview__tile-sub">已给分学生中 0 分占比最高</span>
            <span
              class="overview__columns"
              role="img"
              :aria-label="tile.bars.map((bar) => `${bar.questionId} ${formatRate(bar.share)}`).join('，')"
            >
              <i
                v-for="bar in tile.bars"
                :key="bar.questionId"
                :class="{ 'is-top': tile.topIds.includes(bar.questionId) }"
                :style="{ height: `${Math.max(4, bar.share * 100)}%` }"
                :title="`${bar.questionId} 0 分占比 ${formatRate(bar.share)}`"
              ></i>
            </span>
            <p class="overview__tile-caption">
              0 分最多：<template
                v-for="(id, index) in tile.topIds"
                :key="id"
              ><template v-if="index">、</template><button
                type="button"
                class="overview__link"
                @click="openQuestion(id)"
              >{{ id }}</button></template>
            </p>
          </section>

          <section
            v-else-if="tile.id === 'class-gap'"
            class="overview__tile"
            :style="{ '--tile-span': span }"
          >
            <h3>班级差距</h3>
            <strong class="overview__tile-num">差 {{ formatScore(tile.gap) }} 分</strong>
            <span class="overview__tile-sub">
              {{ tile.highLabel }} {{ formatScore(tile.highAverage) }} ·
              {{ tile.lowLabel }} {{ formatScore(tile.lowAverage) }}
            </span>
            <span
              class="overview__gapbars"
              role="img"
              :aria-label="`${tile.highLabel}平均 ${formatScore(tile.highAverage)}，${tile.lowLabel}平均 ${formatScore(tile.lowAverage)}`"
            >
              <span class="overview__gapbar">
                <em>{{ tile.highLabel }}</em>
                <span class="overview__mini-bar overview__mini-bar--high"><i
                  :style="{ width: axisPosition(tile.highAverage, results.summary.max_score) }"
                ></i></span>
                <b>{{ formatScore(tile.highAverage) }}</b>
              </span>
              <span class="overview__gapbar">
                <em>{{ tile.lowLabel }}</em>
                <span class="overview__mini-bar overview__mini-bar--low"><i
                  :style="{ width: axisPosition(tile.lowAverage, results.summary.max_score) }"
                ></i></span>
                <b>{{ formatScore(tile.lowAverage) }}</b>
              </span>
            </span>
            <p v-if="tile.widest" class="overview__tile-caption">
              差距最大
              <button
                type="button"
                class="overview__link"
                @click="openQuestion(tile.widest.questionId)"
              >{{ tile.widest.questionId }}</button>
              （{{ tile.highLabel }} {{ formatRate(tile.widest.highRate) }} /
              {{ tile.lowLabel }} {{ formatRate(tile.widest.lowRate) }}）
            </p>
          </section>

          <section
            v-else-if="tile.id === 'cause-categories'"
            class="overview__tile"
            :style="{ '--tile-span': span }"
          >
            <h3>失分类型 <span class="overview__ai-tag" title="来自 AI 错因整理">AI 整理</span></h3>
            <Skeleton
              v-if="analysisState === 'loading'"
              class="overview__skeleton-text"
              aria-hidden="true"
            />
            <template v-else-if="analysisState === 'error'">
              <span class="overview__tile-sub">暂时无法读取</span>
              <AppButton
                variant="ghost"
                class="overview__tile-action"
                @click="reloadAnalysis"
              >重试</AppButton>
            </template>
            <template v-else-if="causeIssue === 'missing'">
              <span class="overview__tile-sub">错因尚未整理</span>
              <AppButton
                variant="ghost"
                class="overview__tile-action"
                @click="emit('open-question', '', scope)"
              >去试题诊断查看</AppButton>
            </template>
            <template v-else-if="causeIssue === 'legacy'">
              <span class="overview__tile-sub">错因为旧版整理</span>
              <AppButton
                variant="ghost"
                class="overview__tile-action"
                @click="emit('open-question', '', scope)"
              >去试题诊断查看</AppButton>
            </template>
            <template v-else-if="'segments' in tile">
              <span
                class="overview__tile-stack overview__tile-stack--cats"
                role="img"
                :aria-label="tile.segments.map((seg) => `${seg.category} ${seg.count} 人次`).join('，')"
              >
                <i
                  v-for="(seg, index) in tile.segments"
                  :key="seg.category"
                  :style="{ width: `${(seg.count / tile.total) * 100}%`, background: categoryColor(index) }"
                ></i>
              </span>
              <ul class="overview__tile-legend overview__tile-legend--cats">
                <li v-for="(seg, index) in tile.segments" :key="seg.category">
                  <i :style="{ background: categoryColor(index) }" aria-hidden="true"></i>
                  {{ seg.category }} {{ seg.count }} 人次
                </li>
              </ul>
            </template>
            <span v-else class="overview__tile-sub">暂无错因分类</span>
          </section>

          <section
            v-else-if="tile.id === 'ai-report'"
            class="overview__tile overview__tile--ai-report"
            :style="{ '--tile-span': span }"
          >
            <h3>班级分析 <span class="overview__ai-tag" title="来自 AI 班级分析">AI</span></h3>
            <Skeleton
              v-if="narrativeState === 'loading'"
              class="overview__skeleton-text"
              aria-hidden="true"
            />
            <template v-else-if="narrativeState === 'error'">
              <span class="overview__tile-sub">暂时无法读取</span>
              <AppButton
                variant="ghost"
                class="overview__tile-action"
                @click="reloadNarrative"
              >重试</AppButton>
            </template>
            <strong v-else-if="narrativeGenerating" class="overview__tile-num">生成中…</strong>
            <template v-else-if="narrative?.narrative && !narrative.stale">
              <ul class="overview__tile-findings">
                <li
                  v-for="finding in narrativeFindings"
                  :key="finding.title"
                  :title="finding.title"
                >{{ finding.title }}</li>
              </ul>
              <AppButton
                variant="ghost"
                class="overview__tile-action"
                @click="openReport"
              >查看完整报告</AppButton>
            </template>
            <template v-else-if="narrative?.stale">
              <strong class="overview__tile-num">成绩已变化</strong>
              <span class="overview__tile-sub">报告需更新</span>
              <AppButton
                variant="ghost"
                class="overview__tile-action"
                @click="openReport"
              >去更新</AppButton>
            </template>
            <template v-else>
              <strong class="overview__tile-num">
                {{ narrative?.narrative_failed ? '上次生成失败' : '尚未生成' }}
              </strong>
              <AppButton
                variant="ghost"
                class="overview__tile-action"
                @click="openReport"
              >去生成</AppButton>
            </template>
          </section>

          <section
            v-else-if="tile.id === 'ranks'"
            class="overview__tile overview__tile--ranks"
            :style="{ '--tile-span': span }"
          >
            <div class="overview__tile-head">
              <h3 id="overview-ranks-title">排名变化</h3>
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
            <div v-else-if="previousState === 'ready'" class="overview__ranks">
              <div
                v-for="group in [
                  { key: 'improved', title: '进步明显', rows: rankRows.improved },
                  { key: 'declined', title: '退步明显', rows: rankRows.declined },
                ]"
                :key="group.key"
                class="overview__rank-group"
                :data-direction="group.key"
              >
                <h4>{{ group.title }}</h4>
                <ol v-if="group.rows.length" class="overview__rank-list">
                  <li v-for="entry in group.rows" :key="entry.student.student_id">
                    <button
                      type="button"
                      class="overview__rank-row"
                      :aria-label="rankAria(entry)"
                      @click="emit('open-student', entry.student.student_id)"
                    >
                      <span class="overview__rank-name">
                        {{ entry.student.student_name }}<small v-if="scope === null">
                          {{ classDisplayLabel(entry.student.class_name) }}
                        </small>
                      </span>
                      <span
                        class="overview__ranktrack"
                        :class="entry.change >= 0 ? 'is-up' : 'is-down'"
                        aria-hidden="true"
                      >
                        <i class="overview__ranktrack-seg" :style="rankSegmentStyle(entry)"></i>
                        <i
                          class="overview__ranktrack-dot is-prev"
                          :style="{ left: rankPosition(entry.previousRank, entry.currentSize) }"
                        ></i>
                        <i
                          class="overview__ranktrack-dot is-curr"
                          :style="{ left: rankPosition(entry.currentRank, entry.currentSize) }"
                        ></i>
                      </span>
                      <span class="overview__rank-nums">
                        {{ entry.previousRank }} → {{ entry.currentRank }}
                      </span>
                      <b
                        class="overview__rank-change"
                        :class="entry.change >= 0 ? 'is-up' : 'is-down'"
                      >{{ entry.change >= 0 ? '↑' : '↓' }}{{ Math.abs(entry.change) }}</b>
                    </button>
                  </li>
                </ol>
                <p v-else class="overview__empty">无明显变化</p>
              </div>
            </div>
            <button
              v-if="previousState === 'ready' && ranksExpandable"
              type="button"
              class="overview__link"
              @click="ranksExpanded = !ranksExpanded"
            >{{ ranksExpanded ? '收起' : '展开全部' }}</button>
          </section>
        </template>
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
              size="sm"
              class="overview__notes-action"
              @click="openReviewNote(note)"
            >去核对</AppButton>
          </li>
        </ul>
      </div>
    </section>

    <section class="overview__section" aria-labelledby="overview-questions-title">
      <div class="overview__section-head">
        <h2 id="overview-questions-title" class="overview__title">题目得分率</h2>
        <div class="overview__sort" role="group" aria-label="题目排序">
          <button
            type="button"
            :class="{ 'is-active': sortBy === 'rate' }"
            :aria-pressed="sortBy === 'rate'"
            @click="sortBy = 'rate'"
          >按得分率</button>
          <button
            type="button"
            :class="{ 'is-active': sortBy === 'number' }"
            :aria-pressed="sortBy === 'number'"
            @click="sortBy = 'number'"
          >按题号</button>
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
                v-if="row.classRates.length"
                class="overview__classrates"
              ><template
                v-for="(entry, index) in row.classRates"
                :key="entry.key"
              ><template v-if="index"> · </template><span
                :class="{ 'is-gap': entry.isLow }"
              >{{ entry.label }} {{ formatRate(entry.rate) }}</span></template></span>
            </span>
            <span class="overview__cause">
              <Skeleton
                v-if="analysisState === 'loading'"
                class="overview__skeleton-text"
                aria-hidden="true"
              />
              <template v-else-if="row.causeLegacy">
                <span class="overview__cause-legacy">旧版错因 · 去试题诊断更新</span>
              </template>
              <template v-else-if="row.causeLabel !== null">
                <span class="overview__ai-tag" title="来自 AI 错因整理">AI</span>
                {{ row.causeLabel }} <b>{{ row.causeCount }} 人</b>
              </template>
              <template v-else>—</template>
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
