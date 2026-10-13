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

export interface LineCrossing {
  student: ResultsCenterStudent
  previousScore: number
  previousMax: number
  previousRate: number
  currentRate: number
}

export interface LineCrossings {
  /** 参与比较的学生数：两场都完整、同班且两场满分都 >0 */
  comparedCount: number
  /** 参与比较的每一名学生，按 student_id 排序 */
  points: LineCrossing[]
  /** 上次 <60%、本次 ≥60% */
  passGained: LineCrossing[]
  /** 上次 ≥60%、本次 <60% */
  passLost: LineCrossing[]
  /** 上次 <85%、本次 ≥85% */
  excellentGained: LineCrossing[]
  /** 上次 ≥85%、本次 <85% */
  excellentLost: LineCrossing[]
}

/** 与班级及格率、优秀率相同的 60%/85% 口径，列出跨线学生；组间不互斥。 */
export function lineCrossings(
  entries: readonly RankChange[],
): LineCrossings {
  const result: LineCrossings = {
    comparedCount: 0,
    points: [],
    passGained: [],
    passLost: [],
    excellentGained: [],
    excellentLost: [],
  }
  for (const entry of entries) {
    if (entry.previousMax <= 0 || entry.student.max_score <= 0) continue
    result.comparedCount += 1
    const item: LineCrossing = {
      student: entry.student,
      previousScore: entry.previousScore,
      previousMax: entry.previousMax,
      previousRate: entry.previousScore / entry.previousMax,
      currentRate: entry.student.current_score / entry.student.max_score,
    }
    result.points.push(item)
    const wasPass = item.previousRate >= 0.6
    const isPass = item.currentRate >= 0.6
    const wasExcellent = item.previousRate >= 0.85
    const isExcellent = item.currentRate >= 0.85
    if (!wasPass && isPass) result.passGained.push(item)
    if (wasPass && !isPass) result.passLost.push(item)
    if (!wasExcellent && isExcellent) result.excellentGained.push(item)
    if (wasExcellent && !isExcellent) result.excellentLost.push(item)
  }
  const byMagnitude = (left: LineCrossing, right: LineCrossing) => (
    Math.abs(right.currentRate - right.previousRate)
      - Math.abs(left.currentRate - left.previousRate)
    || left.student.student_id - right.student.student_id
  )
  result.points.sort(
    (left, right) => left.student.student_id - right.student.student_id,
  )
  result.passGained.sort(byMagnitude)
  result.passLost.sort(byMagnitude)
  result.excellentGained.sort(byMagnitude)
  result.excellentLost.sort(byMagnitude)
  return result
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

/**
 * 合并给分：识别每个组条目覆盖的成员题目。
 * 成员的明细只出现在满分/零分等极端学生身上，中间分学生只有组条目——
 * 因此成员题的学生集合与本组条目的学生集合互斥，可据此配对，无需组标签可解析。
 */
export function combinedGroupMembers(
  questions: ReadonlyArray<{ question_id: string; combined_group?: boolean }>,
  students: readonly ResultsCenterStudent[],
): Map<string, Set<string>> {
  const groupIds = questions
    .filter((question) => question.combined_group === true)
    .map((question) => question.question_id)
  const groups = new Map<string, Set<string>>(
    groupIds.map((groupId) => [groupId, new Set<string>()] as const),
  )
  if (groupIds.length === 0) return groups
  const ordinaryIds = new Set(
    questions
      .filter((question) => question.combined_group !== true)
      .map((question) => question.question_id),
  )
  const holders = new Map<string, Set<number>>()
  for (const student of students) {
    for (const item of student.items) {
      const set = holders.get(item.question_id) ?? new Set<number>()
      set.add(student.student_id)
      holders.set(item.question_id, set)
    }
  }
  const disjointGroups = new Map<string, string[]>()
  for (const questionId of ordinaryIds) {
    const memberStudents = holders.get(questionId)
    if (!memberStudents) continue
    const candidates: string[] = []
    for (const groupId of groupIds) {
      const groupStudents = holders.get(groupId) ?? new Set<number>()
      let disjoint = true
      for (const studentId of memberStudents) {
        if (groupStudents.has(studentId)) { disjoint = false; break }
      }
      if (disjoint) candidates.push(groupId)
    }
    if (candidates.length > 0) disjointGroups.set(questionId, candidates)
  }
  for (const [questionId, candidates] of disjointGroups) {
    const memberStudents = holders.get(questionId) ?? new Set<number>()
    // 组条目与成员题理想上恰好分完所有学生，取覆盖最广的一组。
    const best = candidates.reduce((winner, groupId) => {
      const coverage = (holders.get(groupId)?.size ?? 0) + memberStudents.size
      const bestCoverage = (holders.get(winner)?.size ?? 0) + memberStudents.size
      return coverage > bestCoverage ? groupId : winner
    })
    groups.get(best)!.add(questionId)
  }
  return groups
}

/** 成员题号 → 所属组条目题号。 */
export function combinedMemberMap(
  groups: ReadonlyMap<string, Set<string>>,
): Map<string, string> {
  const map = new Map<string, string>()
  for (const [groupId, members] of groups) {
    for (const member of members) if (!map.has(member)) map.set(member, groupId)
  }
  return map
}

/**
 * 学生的合并组得分：有组条目取组条目；否则取成员明细之和。
 * 两端都取不到时为 null。
 */
export function groupScoreOf(
  student: ResultsCenterStudent,
  groupId: string,
  members: ReadonlySet<string>,
): { score: number; max: number } | null {
  const entry = student.items.find((item) => item.question_id === groupId)
  if (entry !== undefined) {
    const score = resolvedItemScore(entry)
    return score === null ? null : { score, max: entry.max_score }
  }
  const memberItems = student.items.filter((item) => members.has(item.question_id))
  if (memberItems.length === 0) return null
  let score = 0
  let max = 0
  for (const item of memberItems) {
    const resolved = resolvedItemScore(item)
    if (resolved === null) return null
    score += resolved
    max += item.max_score
  }
  return { score, max }
}

/** 合并组按全体的得分率；无人有组内任何得分时为 null。 */
export function groupRateFor(
  students: readonly ResultsCenterStudent[],
  groupId: string,
  members: ReadonlySet<string>,
): number | null {
  let score = 0
  let max = 0
  for (const student of students) {
    const entry = groupScoreOf(student, groupId, members)
    if (entry === null || entry.max <= 0) continue
    score += entry.score
    max += entry.max
  }
  return max > 0 ? score / max : null
}

/** 合并组按全体的满分/部分/0分人数结构。 */
export function groupStructureFor(
  students: readonly ResultsCenterStudent[],
  groupId: string,
  members: ReadonlySet<string>,
): ScoreStructure {
  const structure: ScoreStructure = { full: 0, partial: 0, zero: 0, resolved: 0 }
  for (const student of students) {
    const entry = groupScoreOf(student, groupId, members)
    if (entry === null) continue
    structure.resolved += 1
    if (entry.score === 0) structure.zero += 1
    else if (entry.max > 0 && entry.score >= entry.max) structure.full += 1
    else structure.partial += 1
  }
  return structure
}
