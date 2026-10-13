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
import type {
  ResultsCenterResponse,
} from '../../api/results-center'
import { exportsApi } from '../../api/exports'
import { useJobStore } from '../../stores/jobs'
import { useSessionStore } from '../../stores/session'
import AppButton from '../design-system/AppButton.vue'
import StatePanel from '../design-system/StatePanel.vue'
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
  combinedGroupMembers,
  compareByClassGap,
  compareByRate,
  compareByZeroShare,
  comparisonCandidates,
  defaultComparison,
  displayAnswer,
  formatRate,
  formatScore,
  groupRateFor,
  groupStructureFor,
  isCompleteStudent,
  lineCrossings,
  questionRatesFor,
  rankChangeGroups,
  rankChanges,
  scoreStructureFor,
  subQuestionLabelFor,
  summarizeStudents,
  zeroShareOf,
  type ClassDelta,
  type ClassSummary,
  type LineCrossing,
  type OverviewBand,
  type QuestionSort,
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

// 合并给分：组条目替代成员题展示；成员明细只出现在满分/零分学生身上，
// 单独给成员题算得分率会产生偏差，因此成员行折叠进组行。
const combinedGroups = computed(() => combinedGroupMembers(
  props.results.questions,
  props.results.students,
))
const combinedMemberIds = computed(() => new Set(
  [...combinedGroups.value.values()].flatMap((members) => [...members]),
))
const displayQuestions = computed(() => props.results.questions.filter(
  (question) => !combinedMemberIds.value.has(question.question_id),
))
const questionIds = computed(() => displayQuestions.value.map(
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

interface QuestionRow {
  questionId: string
  maxScore: number
  combinedGroup: boolean
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
  const stemList = displayQuestions.value.map((question) => ({
    question_id: question.question_id,
    stem_summary: analysisById.get(question.question_id)?.stem_summary ?? null,
  }))
  const rows = displayQuestions.value.map((question) => {
    const analysisQuestion = analysisById.get(question.question_id)
    const members = combinedGroups.value.get(question.question_id)
    const classRates = perClass.map((entry) => ({
      key: entry.key,
      label: entry.label,
      rate: members !== undefined
        ? groupRateFor(
            props.results.students.filter(
              (student) => (student.class_name ?? '') === entry.key,
            ),
            question.question_id,
            members,
          )
        : entry.rates.get(question.question_id) ?? null,
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
    const structure = members !== undefined
      ? groupStructureFor(studentsInScope.value, question.question_id, members)
      : scopeScoreStructure.value.get(question.question_id)
        ?? { full: 0, partial: 0, zero: 0, resolved: 0 }
    return {
      questionId: question.question_id,
      maxScore: question.max_score,
      combinedGroup: members !== undefined,
      rate: members !== undefined
        ? groupRateFor(studentsInScope.value, question.question_id, members)
        : scopeQuestionRates.value.get(question.question_id) ?? null,
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
const crossings = computed(() => lineCrossings(rankEntries.value))

function crossText(entry: LineCrossing): string {
  return sameFullScore.value
    ? `${formatScore(entry.previousScore)} → ${formatScore(entry.student.current_score)} 分`
    : `${formatRate(entry.previousRate)} → ${formatRate(entry.currentRate)}`
}

const SCATTER_W = 340
const SCATTER_H = 320
const SCATTER_PAD = { left: 46, right: 12, top: 24, bottom: 38 }
const SCATTER_LINES = [0.6, 0.85]
const SCATTER_BAND_LABELS = ['<60%', '60–85%', '≥85%']

function scatterBand(rate: number): 0 | 1 | 2 {
  return rate >= 0.85 ? 2 : rate >= 0.6 ? 1 : 0
}

const scatterPlot = computed(() => {
  const points = crossings.value.points
  const rates = points.flatMap((point) => [point.previousRate, point.currentRate])
  const lo = rates.length
    ? Math.min(0.5, Math.floor(Math.min(...rates) * 10) / 10)
    : 0.5
  const width = SCATTER_W - SCATTER_PAD.left - SCATTER_PAD.right
  const height = SCATTER_H - SCATTER_PAD.top - SCATTER_PAD.bottom
  const x = (rate: number) => (
    SCATTER_PAD.left + ((rate - lo) / (1 - lo)) * width
  )
  const y = (rate: number) => (
    SCATTER_PAD.top + ((1 - rate) / (1 - lo)) * height
  )
  const edges = [lo, ...SCATTER_LINES, 1]
  const counts = new Map<string, number>()
  for (const point of points) {
    const key = `${scatterBand(point.previousRate)}|${scatterBand(point.currentRate)}`
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  const cells = []
  for (let prev = 0; prev <= 2; prev += 1) {
    for (let curr = 0; curr <= 2; curr += 1) {
      const count = counts.get(`${prev}|${curr}`) ?? 0
      cells.push({
        key: `${prev}|${curr}`,
        x: x(edges[prev]!),
        y: y(edges[curr + 1]!),
        width: x(edges[prev + 1]!) - x(edges[prev]!),
        height: y(edges[curr]!) - y(edges[curr + 1]!),
        tint: curr > prev ? 'is-up' : curr < prev ? 'is-down' : '',
        count,
        ariaLabel: `上次 ${SCATTER_BAND_LABELS[prev]}、本次 ${SCATTER_BAND_LABELS[curr]}：${count} 人`,
      })
    }
  }
  return { lo, x, y, cells }
})

const scatterPoints = computed(() => {
  const gained = new Set([
    ...crossings.value.passGained,
    ...crossings.value.excellentGained,
  ].map((entry) => entry.student.student_id))
  const lost = new Set([
    ...crossings.value.passLost,
    ...crossings.value.excellentLost,
  ].map((entry) => entry.student.student_id))
  const plot = scatterPlot.value
  return crossings.value.points.map((entry) => {
    const id = entry.student.student_id
    const isGained = gained.has(id)
    const isLost = lost.has(id)
    return {
      entry,
      cx: plot.x(entry.previousRate),
      cy: plot.y(entry.currentRate),
      crossing: isGained || isLost,
      className: isGained ? 'is-up' : isLost ? 'is-down' : '',
      label: `${entry.student.student_name}${scope.value === null
        ? ` ${classDisplayLabel(entry.student.class_name)}` : ''} · 上次 ${crossText(entry)}`,
    }
  })
})

const scatterTicks = computed(() => {
  const plot = scatterPlot.value
  return [plot.lo, ...SCATTER_LINES, 1].map((rate) => ({
    rate,
    x: plot.x(rate),
    y: plot.y(rate),
    label: `${Math.round(rate * 100)}%`,
  }))
})

const scatterQuery = ref('')
const scatterCellKey = ref<string | null>(null)
const scatterHotId = ref<number | null>(null)

function toggleScatterCell(key: string): void {
  scatterCellKey.value = scatterCellKey.value === key ? null : key
}

const scatterSearch = computed(() => {
  const query = scatterQuery.value.trim().toLowerCase()
  if (!query) return null
  return crossings.value.points.filter((entry) => [
    entry.student.student_name,
    entry.student.pinyin_initials,
    entry.student.pinyin_full,
  ].some((value) => value.toLowerCase().includes(query)))
})

const scatterMatchIds = computed(() => (
  scatterSearch.value === null
    ? null
    : new Set(scatterSearch.value.map((entry) => entry.student.student_id))
))

function byDeltaDesc(left: LineCrossing, right: LineCrossing): number {
  return Math.abs(right.currentRate - right.previousRate)
      - Math.abs(left.currentRate - left.previousRate)
    || left.student.student_id - right.student.student_id
}

const crossingEntries = computed(() => {
  const seen = new Map<number, LineCrossing>()
  for (const group of [
    crossings.value.passGained,
    crossings.value.passLost,
    crossings.value.excellentGained,
    crossings.value.excellentLost,
  ]) {
    for (const entry of group) seen.set(entry.student.student_id, entry)
  }
  return [...seen.values()].sort(byDeltaDesc)
})

const selectedScatterCell = computed(() => {
  if (scatterSearch.value !== null || scatterCellKey.value === null) return null
  const [prev, curr] = scatterCellKey.value.split('|').map(Number)
  return { prev: prev!, curr: curr! }
})

const scatterRows = computed(() => {
  if (scatterSearch.value !== null) {
    return [...scatterSearch.value].sort(byDeltaDesc)
  }
  if (selectedScatterCell.value !== null) {
    const { prev, curr } = selectedScatterCell.value
    return crossings.value.points
      .filter((entry) => (
        scatterBand(entry.previousRate) === prev
        && scatterBand(entry.currentRate) === curr
      ))
      .sort(byDeltaDesc)
  }
  return crossingEntries.value
})

const scatterListHeader = computed(() => {
  const count = scatterRows.value.length
  if (scatterSearch.value !== null) {
    return `搜索：${scatterQuery.value.trim()} · ${count} 人`
  }
  if (selectedScatterCell.value !== null) {
    const { prev, curr } = selectedScatterCell.value
    return `上次 ${SCATTER_BAND_LABELS[prev]} → 本次 ${SCATTER_BAND_LABELS[curr]} · ${count} 人`
  }
  return `跨线学生 · ${count} 人`
})

const scatterLists = computed(() => (
  scatterSearch.value !== null || selectedScatterCell.value !== null
    ? [{ key: 'filtered', title: scatterListHeader.value, rows: scatterRows.value }]
    : [
      {
        key: 'excellent',
        title: `跨优秀线 · ${crossings.value.excellentGained.length + crossings.value.excellentLost.length} 人`,
        rows: [...crossings.value.excellentGained, ...crossings.value.excellentLost].sort(byDeltaDesc),
      },
      {
        key: 'pass',
        title: `跨及格线 · ${crossings.value.passGained.length + crossings.value.passLost.length} 人`,
        rows: [...crossings.value.passGained, ...crossings.value.passLost].sort(byDeltaDesc),
      },
    ]
))

function scatterDelta(entry: LineCrossing): { text: string; cls: string } {
  const delta = sameFullScore.value
    ? entry.student.current_score - entry.previousScore
    : entry.currentRate - entry.previousRate
  if (delta === 0) return { text: '±0', cls: '' }
  const amount = sameFullScore.value
    ? formatScore(Math.abs(delta))
    : `${Math.abs(Math.round(delta * 100))}%`
  return {
    text: `${delta > 0 ? '+' : '−'}${amount}`,
    cls: delta > 0 ? 'is-up' : 'is-down',
  }
}

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

function visibleBands(summary: ClassSummary): (OverviewBand & { count: number; rate: number })[] {
  const total = Object.values(summary.bandCounts).reduce((sum, count) => sum + count, 0)
  return OVERVIEW_BANDS
    .map((band) => ({
      ...band,
      count: summary.bandCounts[band.id],
      rate: total > 0 ? summary.bandCounts[band.id] / total : 0,
    }))
    .filter((band) => band.count > 0)
}

function bandAriaLabel(summary: ClassSummary): string {
  return visibleBands(summary).map(
    (band) => `${band.label} ${formatRate(band.rate)}，${band.count} 人`,
  ).join('，') || '暂无完整成绩'
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
const isImportSession = computed(
  () => sessionStore.currentSession?.kind === 'zhixue_import',
)
const walkthroughData = computed(() => walkthroughDataFor(props.results))
const walkthroughResumeIndex = ref<number | undefined>(undefined)
const walkthroughAvailable = computed(() => (
  !isImportSession.value && studentsInScope.value.some(isCompleteStudent)
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
          <span v-if="isImportSession" class="overview__todo-text">
            成绩来自智学网导入（教师最终分）
          </span>
          <template v-else-if="reviewPending > 0">
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
    <section class="overview__section overview__classes-card" aria-labelledby="overview-classes-title">
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
              <th scope="col">分数分布（%）</th>
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
                  v-if="row.passRate !== null"
                  class="overview__bandbar"
                  role="img"
                  :aria-label="bandAriaLabel(row)"
                >
                  <i
                    v-for="band in visibleBands(row)"
                    :key="band.id"
                    :data-band="band.id"
                    :style="{ width: `${band.rate * 100}%` }"
                    :title="`${band.label} ${formatRate(band.rate)} · ${band.count} 人`"
                  >{{ band.rate >= 0.08 ? formatRate(band.rate) : '' }}</i>
                </span>
                <span v-else>—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="overview__legend">
        <span v-for="band in OVERVIEW_BANDS" :key="band.id">
          <i :data-band="band.id" aria-hidden="true"></i>{{ band.label }} {{ band.range }}
        </span>
        <span class="overview__legend-note">分数分布按评分完整的参考学生计算占比；窄色段悬停查看。</span>
        <span
          v-if="previousState === 'ready' && comparisonName !== null"
          class="overview__legend-note"
        >↑↓ 为较「{{ comparisonName }}」的变化；平均分仅在两次满分相同时比较，及格率、优秀率按百分点。</span>
      </p>
    </section>
    <section
      class="overview__section overview__cross-card"
      aria-labelledby="overview-cross-title"
      data-testid="line-crossings"
    >
      <div class="overview__section-head">
        <h2 id="overview-cross-title" class="overview__title">及格线、优秀线进出</h2>
        <input
          v-if="previousState === 'ready'"
          v-model="scatterQuery"
          type="search"
          class="overview__scatter-search"
          placeholder="搜姓名"
          aria-label="在散点图中搜索学生"
        >
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
        <p class="overview__tile-sub">
          较「{{ comparisonName }}」，两场都有完整成绩且同班的 {{ crossings.comparedCount }} 人
        </p>
        <figure class="overview__scatter">
          <svg
            :viewBox="`0 0 ${SCATTER_W} ${SCATTER_H}`"
            role="img"
            :aria-label="`与「${comparisonName}」的得分率分布，横轴上次、纵轴本次`"
          >
            <rect
              v-for="cell in scatterPlot.cells"
              :key="cell.key"
              class="overview__scatter-cell"
              :class="[cell.tint, { 'is-selected': selectedScatterCell !== null
                && scatterCellKey === cell.key }]"
              :x="cell.x"
              :y="cell.y"
              :width="cell.width"
              :height="cell.height"
              tabindex="0"
              role="button"
              :aria-label="cell.ariaLabel"
              @click="toggleScatterCell(cell.key)"
              @keydown.enter.prevent="toggleScatterCell(cell.key)"
              @keydown.space.prevent="toggleScatterCell(cell.key)"
            />
            <line
              class="overview__scatter-diag"
              :x1="scatterPlot.x(scatterPlot.lo)"
              :y1="scatterPlot.y(scatterPlot.lo)"
              :x2="scatterPlot.x(1)"
              :y2="scatterPlot.y(1)"
            />
            <line
              v-for="tick in scatterTicks"
              :key="`x${tick.rate}`"
              class="overview__scatter-axis"
              :x1="tick.x"
              :y1="SCATTER_PAD.top"
              :x2="tick.x"
              :y2="SCATTER_H - SCATTER_PAD.bottom"
            />
            <line
              v-for="tick in scatterTicks"
              :key="`y${tick.rate}`"
              class="overview__scatter-axis"
              :x1="SCATTER_PAD.left"
              :y1="tick.y"
              :x2="SCATTER_W - SCATTER_PAD.right"
              :y2="tick.y"
            />
            <text
              v-for="tick in scatterTicks"
              :key="`xl${tick.rate}`"
              class="overview__scatter-tick"
              :x="tick.x"
              :y="SCATTER_H - SCATTER_PAD.bottom + 14"
              :text-anchor="tick.rate === 1 ? 'end' : 'middle'"
            >{{ tick.label }}</text>
            <text
              v-for="tick in scatterTicks"
              :key="`yl${tick.rate}`"
              class="overview__scatter-tick"
              :x="SCATTER_PAD.left - 5"
              :y="tick.y + 3"
              text-anchor="end"
            >{{ tick.label }}</text>
            <text
              class="overview__scatter-caption"
              :x="SCATTER_PAD.left + (SCATTER_W - SCATTER_PAD.left - SCATTER_PAD.right) / 2"
              :y="SCATTER_H - 4"
              text-anchor="middle"
            >上次得分率</text>
            <text
              class="overview__scatter-caption"
              :x="SCATTER_PAD.left"
              :y="10"
              text-anchor="start"
            >本次得分率</text>
            <circle
              v-for="point in scatterPoints"
              :key="point.entry.student.student_id"
              class="overview__scatter-dot"
              :class="[point.className, {
                'is-hot': scatterHotId === point.entry.student.student_id,
                'is-match': scatterMatchIds?.has(point.entry.student.student_id),
              }]"
              :cx="point.cx"
              :cy="point.cy"
              r="4"
              :tabindex="point.crossing ? 0 : undefined"
              :role="point.crossing ? 'button' : undefined"
              :aria-label="point.crossing ? point.label : undefined"
              @click="emit('open-student', point.entry.student.student_id)"
              @mouseenter="scatterHotId = point.entry.student.student_id"
              @mouseleave="scatterHotId = null"
              @keydown.enter.prevent="emit('open-student', point.entry.student.student_id)"
              @keydown.space.prevent="emit('open-student', point.entry.student.student_id)"
            ><title>{{ point.label }}</title></circle>
            <template v-for="point in scatterPoints" :key="`n${point.entry.student.student_id}`">
              <text
                v-if="scatterMatchIds !== null
                  && scatterMatchIds.has(point.entry.student.student_id)"
                class="overview__scatter-name"
                :x="point.cx + 6"
                :y="point.cy + 3"
              >{{ point.entry.student.student_name }}</text>
            </template>
            <text
              v-for="cell in scatterPlot.cells"
              v-show="cell.count > 0"
              :key="`c${cell.key}`"
              class="overview__scatter-count"
              :x="cell.x + 4"
              :y="cell.y + 12"
            >{{ cell.count }}人</text>
          </svg>
          <div class="overview__scatter-lists" :class="{ 'is-filtered': scatterLists.length === 1 }">
            <section v-for="list in scatterLists" :key="list.key" class="overview__scatter-list-group">
              <h3 class="overview__scatter-list-head">
                {{ list.title }}
                <button
                  v-if="selectedScatterCell !== null"
                  type="button"
                  class="overview__link"
                  @click="scatterCellKey = null"
                >显示全部跨线学生</button>
              </h3>
              <ol v-if="list.rows.length" class="overview__scatter-list">
                <li
                  v-for="row in list.rows"
                  :key="row.student.student_id"
                >
                  <button
                    type="button"
                    class="overview__scatter-row"
                    :class="{ 'is-hot': scatterHotId === row.student.student_id }"
                    @mouseenter="scatterHotId = row.student.student_id"
                    @mouseleave="scatterHotId = null"
                    @focus="scatterHotId = row.student.student_id"
                    @blur="scatterHotId = null"
                    @click="emit('open-student', row.student.student_id)"
                  >
                    <span class="overview__scatter-rowname">
                      {{ row.student.student_name }}<small v-if="scope === null">
                        {{ classDisplayLabel(row.student.class_name) }}
                      </small>
                    </span>
                    <span class="overview__scatter-rowscore">{{ crossText(row) }}</span>
                    <b
                      class="overview__scatter-rowdelta"
                      :class="scatterDelta(row).cls"
                    >{{ scatterDelta(row).text }}</b>
                  </button>
                </li>
              </ol>
              <p v-else class="overview__scatter-empty">
                {{ scatterSearch !== null ? '没有匹配的学生' : '没有学生' }}
              </p>
            </section>
          </div>
          <figcaption class="overview__scatter-legend">
            新达及格 <b class="is-up">{{ crossings.passGained.length }}</b>
            · 跌破及格 <b class="is-down">{{ crossings.passLost.length }}</b>
            · 新进优秀 <b class="is-up">{{ crossings.excellentGained.length }}</b>
            · 跌出优秀 <b class="is-down">{{ crossings.excellentLost.length }}</b>
            <small>虚线为两次持平；同一人跨过两条线时，两份名单都会列出</small>
          </figcaption>
        </figure>
      </template>
    </section>
    <section class="overview__section overview__ranks-card" aria-labelledby="overview-ranks-title">
      <h2 id="overview-ranks-title" class="overview__title">排名变化</h2>
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
              { key: 'improved', title: '进步明显 · 前 10 位', rows: rankGroups.improved },
              { key: 'declined', title: '退步明显 · 前 10 位', rows: rankGroups.declined },
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
            <StatePanel v-else kind="empty" compact title="无明显变化" />
          </div>
      </div>
    </section>
    </div>

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
            <span class="overview__qid">
              {{ row.questionId }}
              <i v-if="row.combinedGroup" class="overview__combined" title="这几题合起来给一个总分">合并</i>
            </span>
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
