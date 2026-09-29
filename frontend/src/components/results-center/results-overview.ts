import type { ClassAnalysisQuestion } from '../../api/class-analysis'
import type {
  ResultsCenterItem,
  ResultsCenterStudent,
} from '../../api/results-center'
import type { SessionSummary } from '../../api/sessions'

export type OverviewBandId = 'excellent' | 'good' | 'pass' | 'watch' | 'low'

export interface OverviewBand {
  id: OverviewBandId
  label: string
  range: string
}

export const OVERVIEW_BANDS: readonly OverviewBand[] = [
  { id: 'excellent', label: '优秀', range: '≥85%' },
  { id: 'good', label: '良好', range: '70–85%' },
  { id: 'pass', label: '及格', range: '60–70%' },
  { id: 'watch', label: '待提高', range: '40–60%' },
  { id: 'low', label: '低分', range: '<40%' },
]

export interface ClassSummary {
  /** 原始班级键；null 表示全部班级合并行。 */
  key: string | null
  label: string
  studentCount: number
  completeCount: number
  absentCount: number | null
  average: number | null
  median: number | null
  highest: number | null
  lowest: number | null
  passRate: number | null
  excellentRate: number | null
  bandCounts: Record<OverviewBandId, number>
}

export interface ScoreStructure {
  /** 已给分人数中满分 / 部分得分 / 0 分的人数 */
  full: number
  partial: number
  zero: number
  resolved: number
}

export interface LowTailTile {
  id: 'low-tail'
  count: number
  perClass: { label: string; count: number }[]
  zeroAverage: number | null
  /** 中位数高出平均 ≥5 分时给出 */
  medianGap: { average: number; median: number } | null
}

export interface WeakQuestionsTile {
  id: 'weak-questions'
  rows: { questionId: string; rate: number }[]
}

export interface ZeroShareTile {
  id: 'zero-share'
  top: { questionId: string; share: number }
  topIds: string[]
  bars: { questionId: string; share: number }[]
}

export interface ClassGapTile {
  id: 'class-gap'
  gap: number
  lowLabel: string
  lowAverage: number
  highLabel: string
  highAverage: number
  widest: { questionId: string; lowRate: number; highRate: number } | null
}

export interface CauseCategoriesTile {
  id: 'cause-categories'
  segments: { category: string; count: number }[]
  total: number
}

export type OverviewTile =
  | LowTailTile
  | WeakQuestionsTile
  | ZeroShareTile
  | ClassGapTile
  | CauseCategoriesTile

export interface OverviewTilesInput {
  /** null = 全部班级合并 */
  scopeKey: string | null
  scopeStudents: readonly ResultsCenterStudent[]
  /** 全部班级的逐班汇总（scope=全部 时用于低分分布与班级差） */
  classes: readonly ClassSummary[]
  /** 当前范围各题得分率，按卷面题号顺序 */
  questionRates: ReadonlyMap<string, number | null>
  /** classKey → questionId → rate，仅 class-gap 需要 */
  classQuestionRates?: ReadonlyMap<string, ReadonlyMap<string, number | null>>
  /** 当前范围的 class-analysis 题目（含错因）；null 表示尚未加载 */
  analysisQuestions: readonly ClassAnalysisQuestion[] | null
  /** 当前范围各题分数结构，按卷面题号顺序 */
  scoreStructure: ReadonlyMap<string, ScoreStructure>
}

export function isCompleteStudent(student: ResultsCenterStudent): boolean {
  return student.ungraded_count === 0 && student.failed_count === 0
}

export function studentRate(student: ResultsCenterStudent): number | null {
  return student.max_score > 0 ? student.current_score / student.max_score : null
}

export function bandOfRate(rate: number): OverviewBandId {
  if (rate >= 0.85) return 'excellent'
  if (rate >= 0.7) return 'good'
  if (rate >= 0.6) return 'pass'
  if (rate >= 0.4) return 'watch'
  return 'low'
}

export function classDisplayLabel(className: string | null | undefined): string {
  const raw = (className ?? '').trim()
  if (!raw) return '未填写班级'
  return /^\d+$/.test(raw) ? `${raw}班` : raw
}

export function classKeysOf(students: readonly ResultsCenterStudent[]): string[] {
  return [...new Set(students.map((student) => student.class_name ?? ''))]
    .sort((left, right) => left.localeCompare(right, 'zh-CN', { numeric: true }))
}

export function medianOf(values: readonly number[]): number | null {
  if (!values.length) return null
  const sorted = [...values].sort((left, right) => left - right)
  const middle = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 1
    ? sorted[middle]!
    : (sorted[middle - 1]! + sorted[middle]!) / 2
}

export function resolvedItemScore(
  item: ResultsCenterItem | null | undefined,
): number | null {
  if (!item || item.score_awarded === null) return null
  if (item.score_status === 'ungraded' || item.score_status === 'failed') return null
  return item.score_awarded
}

export function zeroScoreCount(student: ResultsCenterStudent): number {
  return student.items.filter((item) => resolvedItemScore(item) === 0).length
}

export function summarizeStudents(
  students: readonly ResultsCenterStudent[],
  key: string | null,
  absentCount: number | null = null,
): ClassSummary {
  const complete = students.filter(isCompleteStudent)
  const scores = complete.map((student) => student.current_score)
  const rates = complete
    .map(studentRate)
    .filter((rate): rate is number => rate !== null)
  const bandCounts: Record<OverviewBandId, number> = {
    excellent: 0,
    good: 0,
    pass: 0,
    watch: 0,
    low: 0,
  }
  for (const rate of rates) bandCounts[bandOfRate(rate)] += 1
  return {
    key,
    label: key === null ? '全部' : classDisplayLabel(key),
    studentCount: students.length,
    completeCount: complete.length,
    absentCount,
    average: scores.length
      ? scores.reduce((total, score) => total + score, 0) / scores.length
      : null,
    median: medianOf(scores),
    highest: scores.length ? Math.max(...scores) : null,
    lowest: scores.length ? Math.min(...scores) : null,
    passRate: rates.length
      ? rates.filter((rate) => rate >= 0.6).length / rates.length
      : null,
    excellentRate: rates.length
      ? rates.filter((rate) => rate >= 0.85).length / rates.length
      : null,
    bandCounts,
  }
}

export function questionRatesFor(
  students: readonly ResultsCenterStudent[],
  questionIds: readonly string[],
): Map<string, number | null> {
  const sums = new Map<string, { score: number; max: number }>()
  for (const id of questionIds) sums.set(id, { score: 0, max: 0 })
  for (const student of students) {
    for (const item of student.items) {
      const sum = sums.get(item.question_id)
      if (!sum) continue
      const score = resolvedItemScore(item)
      if (score === null || item.max_score <= 0) continue
      sum.score += score
      sum.max += item.max_score
    }
  }
  return new Map(
    [...sums].map(([id, sum]) => [id, sum.max > 0 ? sum.score / sum.max : null]),
  )
}

export function blankCountsByStudent(
  questions: readonly ClassAnalysisQuestion[],
): Map<number, number> {
  const counts = new Map<number, number>()
  for (const question of questions) {
    const ids = new Set<number>()
    for (const cause of question.causes ?? []) {
      if (cause.kind !== 'response_state') continue
      for (const item of cause.evidence ?? []) {
        for (const id of item.student_ids) ids.add(id)
      }
    }
    for (const id of ids) counts.set(id, (counts.get(id) ?? 0) + 1)
  }
  return counts
}

/** 总览只使用新版结构化错因（带 kind）；旧版归并结果按 'legacy' 区分展示。 */
export type TopCause = { label: string; count: number } | 'legacy' | null

export function topCauseOf(question: ClassAnalysisQuestion): TopCause {
  const causes = question.causes ?? []
  const top = causes
    .filter((cause) => cause.kind !== undefined)
    .sort((a, b) => b.count - a.count)[0]
  if (!top) return causes.length > 0 ? 'legacy' : null
  return {
    label: top.kind === 'response_state' ? '未作答' : top.reason,
    count: top.count,
  }
}

export function formatScore(value: number | null): string {
  if (value === null) return '—'
  return Number.isInteger(value)
    ? String(value)
    : value.toFixed(1).replace(/\.0$/, '')
}

export function formatRate(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`
}

export function formatCount(value: number): string {
  return formatScore(value)
}

export function scoreStructureFor(
  students: readonly ResultsCenterStudent[],
  questionIds: readonly string[],
): Map<string, ScoreStructure> {
  const sums = new Map<string, ScoreStructure>(
    questionIds.map((id) => [id, { full: 0, partial: 0, zero: 0, resolved: 0 }]),
  )
  for (const student of students) {
    for (const item of student.items) {
      const bucket = sums.get(item.question_id)
      if (!bucket) continue
      const score = resolvedItemScore(item)
      if (score === null) continue
      bucket.resolved += 1
      if (score === 0) bucket.zero += 1
      else if (item.max_score > 0 && score >= item.max_score) bucket.full += 1
      else bucket.partial += 1
    }
  }
  return sums
}

export function buildOverviewTiles(input: OverviewTilesInput): OverviewTile[] {
  const tiles: OverviewTile[] = []
  const complete = input.scopeStudents.filter(isCompleteStudent)
  const rated = complete
    .map((student) => ({ student, rate: studentRate(student) }))
    .filter((entry): entry is { student: ResultsCenterStudent; rate: number } => (
      entry.rate !== null
    ))
  const low = rated.filter((entry) => entry.rate < 0.4)

  if (low.length > 0) {
    const scores = complete.map((student) => student.current_score)
    const median = medianOf(scores)
    const average = scores.length
      ? scores.reduce((total, score) => total + score, 0) / scores.length
      : null
    tiles.push({
      id: 'low-tail',
      count: low.length,
      perClass: input.scopeKey === null
        ? input.classes
          .filter((entry) => entry.bandCounts.low > 0)
          .map((entry) => ({ label: entry.label, count: entry.bandCounts.low }))
        : [],
      zeroAverage: low.reduce(
        (total, entry) => total + zeroScoreCount(entry.student), 0,
      ) / low.length,
      medianGap: median !== null && average !== null && median - average >= 5
        ? { average, median }
        : null,
    })
  }

  const weakest = [...input.questionRates.entries()]
    .filter((entry): entry is [string, number] => entry[1] !== null)
    .sort((left, right) => left[1] - right[1])
    .slice(0, 5)
  if (weakest.length > 0) {
    tiles.push({
      id: 'weak-questions',
      rows: weakest.map(([questionId, rate]) => ({ questionId, rate })),
    })
  }

  const zeroBars = [...input.scoreStructure.entries()]
    .filter((entry) => entry[1].resolved > 0 && entry[1].zero > 0)
    .map(([questionId, structure]) => ({
      questionId,
      share: structure.zero / structure.resolved,
    }))
  if (zeroBars.length > 0) {
    const topIds = [...zeroBars]
      .sort((left, right) => right.share - left.share)
      .slice(0, 3)
      .map((bar) => bar.questionId)
    tiles.push({
      id: 'zero-share',
      top: zeroBars.find((bar) => bar.questionId === topIds[0])!,
      topIds,
      bars: zeroBars,
    })
  }

  if (input.scopeKey === null && input.classes.length >= 2) {
    const ranked = input.classes
      .filter((entry) => entry.average !== null)
      .sort((left, right) => left.average! - right.average!)
    if (ranked.length >= 2) {
      const lowClass = ranked[0]!
      const highClass = ranked[ranked.length - 1]!
      const gap = highClass.average! - lowClass.average!
      if (gap >= 5) {
        const lowRates = lowClass.key === null
          ? undefined
          : input.classQuestionRates?.get(lowClass.key)
        const highRates = highClass.key === null
          ? undefined
          : input.classQuestionRates?.get(highClass.key)
        let widest: ClassGapTile['widest'] = null
        if (lowRates && highRates) {
          let widestGap = 0
          for (const [questionId, lowRate] of lowRates) {
            const highRate = highRates.get(questionId)
            if (lowRate === null || highRate === null || highRate === undefined) continue
            const rateGap = Math.abs(highRate - lowRate)
            if (rateGap > widestGap) {
              widestGap = rateGap
              widest = { questionId, lowRate, highRate }
            }
          }
        }
        tiles.push({
          id: 'class-gap',
          gap,
          lowLabel: lowClass.label,
          lowAverage: lowClass.average!,
          highLabel: highClass.label,
          highAverage: highClass.average!,
          widest,
        })
      }
    }
  }

  if (input.analysisQuestions !== null) {
    const categories = new Map<string, number>()
    for (const question of input.analysisQuestions) {
      for (const cause of question.causes ?? []) {
        if (cause.kind === undefined || cause.kind === 'response_state') continue
        const category = cause.category ?? ''
        if (category === '' || category === '未作答') continue
        categories.set(category, (categories.get(category) ?? 0) + cause.count)
      }
    }
    const segments = [...categories.entries()]
      .sort((left, right) => right[1] - left[1])
      .slice(0, 4)
      .map(([category, count]) => ({ category, count }))
    if (segments.length > 0) {
      tiles.push({
        id: 'cause-categories',
        segments,
        total: segments.reduce((total, segment) => total + segment.count, 0),
      })
    }
  }

  return tiles
}

/** 子题参考答案展示：首个「 或 」后若仍是大段内容，只保留首选写法，全文放 title。 */
export function displayAnswer(answer: string | null): string | null {
  if (answer === null) return null
  const index = answer.indexOf(' 或 ')
  if (index === -1) return answer
  const rest = answer.slice(index + 3).trim()
  return rest.length > 20 ? answer.slice(0, index).trim() : answer
}

/** 班内名次：完整成绩按分降序，同分同名次，下一名次跳号。 */
export function classRanksOf(
  students: readonly ResultsCenterStudent[],
): Map<number, { rank: number; size: number }> {
  const groups = new Map<string, ResultsCenterStudent[]>()
  for (const student of students) {
    const key = student.class_name ?? ''
    const group = groups.get(key) ?? []
    group.push(student)
    groups.set(key, group)
  }
  const ranks = new Map<number, { rank: number; size: number }>()
  for (const group of groups.values()) {
    const ranked = group
      .filter(isCompleteStudent)
      .sort((left, right) => (
        right.current_score - left.current_score
        || left.student_id - right.student_id
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
}

export interface RankChange {
  student: ResultsCenterStudent
  previousScore: number
  previousMax: number
  previousRank: number
  currentRank: number
  /** 本次班内参与排名的完整学生数，用于名次轨道比例。 */
  currentSize: number
  /** 上次名次 − 本次名次；正数为进步 */
  change: number
}

/** 只比较两场考试中都完整、且班级未变的学生。 */
export function rankChanges(
  current: readonly ResultsCenterStudent[],
  previous: readonly ResultsCenterStudent[],
): RankChange[] {
  const currentRanks = classRanksOf(current)
  const previousRanks = classRanksOf(previous)
  const previousById = new Map(
    previous.map((student) => [student.student_id, student] as const),
  )
  const entries: RankChange[] = []
  for (const student of current) {
    const before = previousById.get(student.student_id)
    if (!before) continue
    if (!isCompleteStudent(student) || !isCompleteStudent(before)) continue
    if ((student.class_name ?? '') !== (before.class_name ?? '')) continue
    const currentEntry = currentRanks.get(student.student_id)
    const previousEntry = previousRanks.get(student.student_id)
    if (!currentEntry || !previousEntry) continue
    entries.push({
      student,
      previousScore: before.current_score,
      previousMax: before.max_score,
      previousRank: previousEntry.rank,
      currentRank: currentEntry.rank,
      currentSize: currentEntry.size,
      change: previousEntry.rank - currentEntry.rank,
    })
  }
  return entries
}

export function rankChangeGroups(
  entries: readonly RankChange[],
  minChange = 5,
  limit = 8,
): { improved: RankChange[]; declined: RankChange[] } {
  const byChange = (left: RankChange, right: RankChange) => (
    Math.abs(right.change) - Math.abs(left.change)
    || left.student.student_id - right.student.student_id
  )
  return {
    improved: entries
      .filter((entry) => entry.change >= minChange)
      .sort(byChange)
      .slice(0, limit),
    declined: entries
      .filter((entry) => entry.change <= -minChange)
      .sort(byChange)
      .slice(0, limit),
  }
}

/** 同一学期卷别下可作对比的其他已完成考试，按创建时间从近到远。 */
export function comparisonCandidates(
  current: SessionSummary | null,
  sessions: readonly SessionSummary[],
): SessionSummary[] {
  if (!current) return []
  return sessions
    .filter((session) => (
      session.id !== current.id
      && !session.is_deleted
      && session.status === 'completed'
      && (session.curriculum_volume_id ?? null) === (current.curriculum_volume_id ?? null)
    ))
    .sort((left, right) => (right.created_at ?? '').localeCompare(left.created_at ?? ''))
}

/** 默认对比 = 同卷别中创建时间早于本场、最近的一场。 */
export function defaultComparison(
  current: SessionSummary | null,
  sessions: readonly SessionSummary[],
): SessionSummary | null {
  const candidates = comparisonCandidates(current, sessions)
  const earlier = current?.created_at != null
    ? candidates.filter((session) => (
      session.created_at !== null && session.created_at < current.created_at!
    ))
    : []
  return earlier[0] ?? candidates[0] ?? null
}

/** 形如 Q14(P8) 的小问；返回父题号与小问序号。 */
export function subQuestionOf(
  questionId: string,
): { parentId: string; index: number } | null {
  const match = /^(.*)\(P(\d+)\)$/.exec(questionId)
  if (!match) return null
  return { parentId: match[1]!, index: Number(match[2]) }
}

/**
 * 判断一行是不是小问行：题号形如 Q14(P8)，且同父题号下另有题目
 * 与之共用同一题干（即题干是小问组标题），或题干为空/等于父题号。
 * parentStem 是用于 title 的小问组标题；无法确定时为 null。
 */
export function subQuestionLabelFor(
  questionId: string,
  stem: string | null,
  questions: ReadonlyArray<{ question_id: string; stem_summary?: string | null }>,
): { index: number; parentStem: string | null } | null {
  const sub = subQuestionOf(questionId)
  if (sub === null) return null
  const text = stem?.trim() ?? ''
  if (text === '' || text === sub.parentId) {
    return { index: sub.index, parentStem: null }
  }
  const siblingSharesStem = questions.some((other) => (
    other.question_id !== questionId
    && subQuestionOf(other.question_id)?.parentId === sub.parentId
    && (other.stem_summary ?? null) === stem
  ))
  return siblingSharesStem ? { index: sub.index, parentStem: stem } : null
}