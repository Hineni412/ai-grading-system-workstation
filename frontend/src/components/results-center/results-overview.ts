import type {
  ResultsCenterItem,
  ResultsCenterStudent,
} from '../../api/results-center'
import type { SessionSummary } from '../../api/sessions'
import { formatScore } from '../../lib/format'

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

/** 临界名单的最大分差：距及格线/优秀线 5 分内入组。 */
export const CRITICAL_GAP = 5

export interface CriticalStudentEntry {
  student: ResultsCenterStudent
  /** 距对应分数线的分差（正数，≤ CRITICAL_GAP） */
  gap: number
}

export interface CriticalStudents {
  /** 距及格线（满分×60%）5 分内的未及格学生 */
  passing: CriticalStudentEntry[]
  /** 距优秀线（满分×85%）5 分内的未优秀学生；与及格组互斥 */
  excellent: CriticalStudentEntry[]
  total: number
}

export function criticalStudents(
  students: readonly ResultsCenterStudent[],
): CriticalStudents {
  const byGap = (left: CriticalStudentEntry, right: CriticalStudentEntry) => (
    left.gap - right.gap
    || left.student.student_id - right.student.student_id
  )
  const passing: CriticalStudentEntry[] = []
  const excellent: CriticalStudentEntry[] = []
  for (const student of students) {
    if (!isCompleteStudent(student) || student.max_score <= 0) continue
    const rate = studentRate(student)
    if (rate === null) continue
    if (rate < 0.6) {
      const gap = student.max_score * 0.6 - student.current_score
      if (gap <= CRITICAL_GAP + 1e-9) passing.push({ student, gap })
      continue
    }
    if (rate < 0.85) {
      const gap = student.max_score * 0.85 - student.current_score
      if (gap <= CRITICAL_GAP + 1e-9) excellent.push({ student, gap })
    }
  }
  passing.sort(byGap)
  excellent.sort(byGap)
  return { passing, excellent, total: passing.length + excellent.length }
}

export interface ClassDelta {
  /** 平均分变化（分）；满分不同或任一侧无完整学生时为 null */
  average: number | null
  /** 及格率 / 优秀率变化（比率差；展示时换算百分点） */
  passRate: number | null
  excellentRate: number | null
}

export function classDelta(
  current: ClassSummary,
  previous: ClassSummary | null,
  sameMaxScore: boolean,
): ClassDelta | null {
  if (previous === null || previous.completeCount === 0) return null
  return {
    average: sameMaxScore
      && current.average !== null && previous.average !== null
      ? current.average - previous.average
      : null,
    passRate: current.passRate !== null && previous.passRate !== null
      ? current.passRate - previous.passRate
      : null,
    excellentRate: current.excellentRate !== null
      && previous.excellentRate !== null
      ? current.excellentRate - previous.excellentRate
      : null,
  }
}

export type QuestionSort = 'rate' | 'number' | 'zero' | 'gap'

export interface SortableQuestionEntry {
  rate: number | null
  /** 已给分学生中的 0 分占比；无已给分学生为 null */
  zeroShare: number | null
  /** 各班得分率 max−min；不足两个班级得分率为 null */
  classRateGap: number | null
}

export function zeroShareOf(structure: ScoreStructure): number | null {
  return structure.resolved > 0 ? structure.zero / structure.resolved : null
}

export function classRateGapOf(
  rates: readonly (number | null)[],
): number | null {
  const valid = rates.filter((rate): rate is number => rate !== null)
  if (valid.length < 2) return null
  return Math.max(...valid) - Math.min(...valid)
}

/* 排序保持传入顺序作平局次序：调用方按卷面题序传入。 */
export function compareByRate(
  left: SortableQuestionEntry, right: SortableQuestionEntry,
): number {
  if (left.rate === null) return right.rate === null ? 0 : 1
  if (right.rate === null) return -1
  return left.rate - right.rate
}

export function compareByZeroShare(
  left: SortableQuestionEntry, right: SortableQuestionEntry,
): number {
  if (left.zeroShare === null) return right.zeroShare === null ? 0 : 1
  if (right.zeroShare === null) return -1
  return right.zeroShare - left.zeroShare
}

export function compareByClassGap(
  left: SortableQuestionEntry, right: SortableQuestionEntry,
): number {
  if (left.classRateGap === null) return right.classRateGap === null ? 0 : 1
  if (right.classRateGap === null) return -1
  return right.classRateGap - left.classRateGap
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

export { formatScore }

export function formatRate(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`
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
  /** 本次班内参与排名的完整学生数。 */
  currentSize: number
  /** 上次班内参与排名的完整学生数。 */
  previousSize: number
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
      previousSize: previousEntry.size,
      change: previousEntry.rank - currentEntry.rank,
    })
  }
  return entries
}

export interface RankTierEntry {
  student: ResultsCenterStudent
  previousRank: number
  currentRank: number
  /** 上次名次 − 本次名次；正数为进步 */
  change: number
}

export interface RankTierChanges {
  /** 上次不在前 1/4、本次在前 1/4 */
  enteringTop: RankTierEntry[]
  /** 上次在前 1/4、本次不在 */
  leavingTop: RankTierEntry[]
  /** 上次在后 1/4、本次不在 */
  leavingBottom: RankTierEntry[]
  /** 上次不在后 1/4、本次在后 1/4 */
  enteringBottom: RankTierEntry[]
}

/** 班内名次按各场各班 ceil(size/4) 分前后两层，比较两场间的进层/出层；组间不互斥。 */
export function rankTierChanges(
  current: readonly ResultsCenterStudent[],
  previous: readonly ResultsCenterStudent[],
): RankTierChanges {
  const groups: RankTierChanges = {
    enteringTop: [],
    leavingTop: [],
    leavingBottom: [],
    enteringBottom: [],
  }
  for (const entry of rankChanges(current, previous)) {
    const item: RankTierEntry = {
      student: entry.student,
      previousRank: entry.previousRank,
      currentRank: entry.currentRank,
      change: entry.change,
    }
    const topBefore = entry.previousRank <= Math.ceil(entry.previousSize / 4)
    const topNow = entry.currentRank <= Math.ceil(entry.currentSize / 4)
    const bottomBefore = entry.previousRank
      > entry.previousSize - Math.ceil(entry.previousSize / 4)
    const bottomNow = entry.currentRank
      > entry.currentSize - Math.ceil(entry.currentSize / 4)
    if (!topBefore && topNow && entry.change > 0) groups.enteringTop.push(item)
    if (topBefore && !topNow && entry.change < 0) groups.leavingTop.push(item)
    if (bottomBefore && !bottomNow && entry.change > 0) groups.leavingBottom.push(item)
    if (!bottomBefore && bottomNow && entry.change < 0) groups.enteringBottom.push(item)
  }
  const byMagnitude = (left: RankTierEntry, right: RankTierEntry) => (
    Math.abs(right.change) - Math.abs(left.change)
    || left.student.student_id - right.student.student_id
  )
  groups.enteringTop.sort(byMagnitude)
  groups.leavingTop.sort(byMagnitude)
  groups.leavingBottom.sort(byMagnitude)
  groups.enteringBottom.sort(byMagnitude)
  return groups
}

export interface ConsecutiveRankEntry {
  student: ResultsCenterStudent
  earlierRank: number
  previousRank: number
  currentRank: number
  /** 第三场名次 − 本场名次；正数为进步 */
  totalChange: number
}

/** 三场考试同班且都完整的学生中，两段名次变化同向且各 ≥ minStep。 */
export function consecutiveRankChanges(
  current: readonly ResultsCenterStudent[],
  previous: readonly ResultsCenterStudent[],
  earlier: readonly ResultsCenterStudent[],
  minStep = 3,
): { improving: ConsecutiveRankEntry[]; declining: ConsecutiveRankEntry[] } {
  const currentRanks = classRanksOf(current)
  const previousRanks = classRanksOf(previous)
  const earlierRanks = classRanksOf(earlier)
  const previousById = new Map(
    previous.map((student) => [student.student_id, student] as const),
  )
  const earlierById = new Map(
    earlier.map((student) => [student.student_id, student] as const),
  )
  const improving: ConsecutiveRankEntry[] = []
  const declining: ConsecutiveRankEntry[] = []
  for (const student of current) {
    const before = previousById.get(student.student_id)
    const first = earlierById.get(student.student_id)
    if (!before || !first) continue
    if (!isCompleteStudent(student)
      || !isCompleteStudent(before)
      || !isCompleteStudent(first)) continue
    const klass = student.class_name ?? ''
    if ((before.class_name ?? '') !== klass
      || (first.class_name ?? '') !== klass) continue
    const earlierEntry = earlierRanks.get(student.student_id)
    const previousEntry = previousRanks.get(student.student_id)
    const currentEntry = currentRanks.get(student.student_id)
    if (!earlierEntry || !previousEntry || !currentEntry) continue
    const entry: ConsecutiveRankEntry = {
      student,
      earlierRank: earlierEntry.rank,
      previousRank: previousEntry.rank,
      currentRank: currentEntry.rank,
      totalChange: earlierEntry.rank - currentEntry.rank,
    }
    if (earlierEntry.rank - previousEntry.rank >= minStep
      && previousEntry.rank - currentEntry.rank >= minStep) {
      improving.push(entry)
    } else if (previousEntry.rank - earlierEntry.rank >= minStep
      && currentEntry.rank - previousEntry.rank >= minStep) {
      declining.push(entry)
    }
  }
  const byMagnitude = (left: ConsecutiveRankEntry, right: ConsecutiveRankEntry) => (
    Math.abs(right.totalChange) - Math.abs(left.totalChange)
    || left.student.student_id - right.student.student_id
  )
  improving.sort(byMagnitude)
  declining.sort(byMagnitude)
  return { improving, declining }
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