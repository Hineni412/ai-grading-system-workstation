import type { ClassAnalysisQuestion } from '../../api/class-analysis'
import type {
  ResultsCenterItem,
  ResultsCenterStudent,
} from '../../api/results-center'

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

export type FindingSegment =
  | { type: 'text'; text: string }
  | { type: 'strong'; text: string }
  | { type: 'question'; questionId: string }
  | { type: 'action'; action: 'attention'; label: string }

export interface OverviewFinding {
  id: 'low-tail' | 'weak-questions' | 'main-errors' | 'blanks' | 'class-gap'
  segments: FindingSegment[]
}

export interface FindingsInput {
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

export interface AttentionStudent {
  student: ResultsCenterStudent
  rate: number
  zeroCount: number
}

export function attentionStudents(
  students: readonly ResultsCenterStudent[],
): { low: AttentionStudent[]; nearPass: AttentionStudent[] } {
  const low: AttentionStudent[] = []
  const nearPass: AttentionStudent[] = []
  for (const student of students) {
    if (!isCompleteStudent(student)) continue
    const rate = studentRate(student)
    if (rate === null) continue
    const entry = { student, rate, zeroCount: zeroScoreCount(student) }
    if (rate < 0.4) low.push(entry)
    else if (rate >= 0.5 && rate < 0.6) nearPass.push(entry)
  }
  const byScore = (left: AttentionStudent, right: AttentionStudent) => (
    left.student.current_score - right.student.current_score
    || left.student.student_name.localeCompare(right.student.student_name, 'zh-CN')
    || left.student.student_id - right.student.student_id
  )
  return { low: low.sort(byScore), nearPass: nearPass.sort(byScore) }
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

function responseStateCount(question: ClassAnalysisQuestion): number {
  return (question.causes ?? [])
    .filter((cause) => cause.kind === 'response_state')
    .reduce((total, cause) => total + cause.count, 0)
}

export function buildFindings(input: FindingsInput): OverviewFinding[] {
  const findings: OverviewFinding[] = []
  const complete = input.scopeStudents.filter(isCompleteStudent)
  const scores = complete.map((student) => student.current_score)
  const rated = complete
    .map((student) => ({ student, rate: studentRate(student) }))
    .filter((entry): entry is { student: ResultsCenterStudent; rate: number } => (
      entry.rate !== null
    ))
  const low = rated.filter((entry) => entry.rate < 0.4)

  if (low.length > 0) {
    const perClass = input.scopeKey === null
      ? input.classes
        .filter((entry) => entry.bandCounts.low > 0)
        .map((entry) => `${entry.label} ${entry.bandCounts.low}`)
        .join(' · ')
      : ''
    const zeroAverage = low.reduce(
      (total, entry) => total + zeroScoreCount(entry.student), 0,
    ) / low.length
    const segments: FindingSegment[] = [
      { type: 'text', text: '低于 40% 的有 ' },
      { type: 'strong', text: `${low.length} 人` },
    ]
    if (perClass) segments.push({ type: 'text', text: `（${perClass}）` })
    segments.push(
      { type: 'text', text: '，人均 0 分题 ' },
      { type: 'strong', text: `${formatCount(zeroAverage)} 道` },
    )
    const median = medianOf(scores)
    const average = scores.length
      ? scores.reduce((total, score) => total + score, 0) / scores.length
      : null
    if (median !== null && average !== null && median - average >= 5) {
      segments.push(
        { type: 'text', text: '；中位数 ' },
        { type: 'strong', text: formatScore(median) },
        { type: 'text', text: ' 高于平均 ' },
        { type: 'strong', text: formatScore(average) },
        { type: 'text', text: '，低分学生明显拉低了平均' },
      )
    }
    segments.push({ type: 'action', action: 'attention', label: '查看名单' })
    findings.push({ id: 'low-tail', segments })
  }

  const weakest = [...input.questionRates.entries()]
    .filter((entry): entry is [string, number] => entry[1] !== null)
    .sort((left, right) => left[1] - right[1])
    .slice(0, 3)
  if (weakest.length > 0) {
    const segments: FindingSegment[] = [{ type: 'text', text: '得分率最低：' }]
    weakest.forEach(([questionId, rate], index) => {
      if (index > 0) segments.push({ type: 'text', text: ' · ' })
      segments.push(
        { type: 'question', questionId },
        { type: 'strong', text: ` ${formatRate(rate)}` },
      )
    })
    const lowestId = weakest[0]![0]
    const lowestQuestion = input.analysisQuestions?.find(
      (question) => question.question_id === lowestId,
    )
    const topCause = (lowestQuestion?.causes ?? [])
      .filter((cause) => cause.kind !== undefined && cause.kind !== 'response_state')
      .sort((left, right) => right.count - left.count)[0]
    if (topCause) {
      segments.push(
        { type: 'text', text: '；' },
        { type: 'question', questionId: lowestId },
        { type: 'text', text: ` 主要是「${topCause.reason}」` },
        { type: 'strong', text: `${topCause.count} 人` },
      )
    }
    findings.push({ id: 'weak-questions', segments })
  }

  if (input.analysisQuestions !== null) {
    const categories = new Map<string, { total: number; perQuestion: Map<string, number> }>()
    for (const question of input.analysisQuestions) {
      if (!(question.causes ?? []).some((cause) => cause.kind !== undefined)) continue
      for (const entry of question.cause_category_counts ?? []) {
        if (entry.category === '未作答') continue
        const bucket = categories.get(entry.category) ?? { total: 0, perQuestion: new Map() }
        bucket.total += entry.count
        bucket.perQuestion.set(
          question.question_id,
          (bucket.perQuestion.get(question.question_id) ?? 0) + entry.count,
        )
        categories.set(entry.category, bucket)
      }
    }
    const top = [...categories.entries()]
      .sort((left, right) => right[1].total - left[1].total)
      .slice(0, 2)
    if (top.length > 0) {
      const segments: FindingSegment[] = [{ type: 'text', text: '失分人次最多：' }]
      top.forEach(([category, bucket], index) => {
        if (index > 0) segments.push({ type: 'text', text: ' · ' })
        segments.push({ type: 'strong', text: `${category} ${bucket.total} 人次` })
        const topQuestions = [...bucket.perQuestion.entries()]
          .sort((left, right) => right[1] - left[1])
          .slice(0, 3)
        if (topQuestions.length > 0) {
          segments.push({ type: 'text', text: '（主要在 ' })
          topQuestions.forEach(([questionId], questionIndex) => {
            if (questionIndex > 0) segments.push({ type: 'text', text: '、' })
            segments.push({ type: 'question', questionId })
          })
          segments.push({ type: 'text', text: '）' })
        }
      })
      findings.push({ id: 'main-errors', segments })
    }

    const blankPerQuestion = input.analysisQuestions
      .map((question) => ({
        questionId: question.question_id,
        count: responseStateCount(question),
      }))
      .filter((entry) => entry.count > 0)
      .sort((left, right) => right.count - left.count)
    const blankTotal = blankPerQuestion.reduce((total, entry) => total + entry.count, 0)
    if (blankTotal > 0) {
      const segments: FindingSegment[] = [
        { type: 'text', text: '未作答共 ' },
        { type: 'strong', text: `${blankTotal} 人次` },
        { type: 'text', text: '，集中在 ' },
      ]
      blankPerQuestion.slice(0, 3).forEach((entry, index) => {
        if (index > 0) segments.push({ type: 'text', text: '、' })
        segments.push({ type: 'question', questionId: entry.questionId })
      })
      findings.push({ id: 'blanks', segments })
    }
  }

  if (input.scopeKey === null && input.classes.length >= 2) {
    const ranked = input.classes
      .filter((entry) => entry.average !== null)
      .sort((left, right) => left.average! - right.average!)
    if (ranked.length >= 2) {
      const lowClass = ranked[0]!
      const highClass = ranked[ranked.length - 1]!
      const diff = highClass.average! - lowClass.average!
      if (diff >= 5) {
        const segments: FindingSegment[] = [{
          type: 'strong',
          text: `${lowClass.label}平均比${highClass.label}低 ${formatScore(diff)} 分`,
        }]
        const lowRates = lowClass.key === null
          ? undefined
          : input.classQuestionRates?.get(lowClass.key)
        const highRates = highClass.key === null
          ? undefined
          : input.classQuestionRates?.get(highClass.key)
        let widest: { questionId: string; low: number; high: number; gap: number } | null = null
        if (lowRates && highRates) {
          for (const [questionId, lowRate] of lowRates) {
            const highRate = highRates.get(questionId)
            if (lowRate === null || highRate === null || highRate === undefined) continue
            const gap = Math.abs(highRate - lowRate)
            if (!widest || gap > widest.gap) {
              widest = { questionId, low: lowRate, high: highRate, gap }
            }
          }
        }
        if (widest) {
          segments.push(
            { type: 'text', text: '；差距最大在 ' },
            { type: 'question', questionId: widest.questionId },
            { type: 'text', text: '（' },
            { type: 'strong', text: `${highClass.label} ${formatRate(widest.high)}` },
            { type: 'text', text: ' / ' },
            { type: 'strong', text: `${lowClass.label} ${formatRate(widest.low)}` },
            { type: 'text', text: '）' },
          )
        }
        findings.push({ id: 'class-gap', segments })
      }
    }
  }

  return findings
}

export function findingText(finding: OverviewFinding): string {
  return finding.segments
    .map((segment) => {
      if (segment.type === 'text' || segment.type === 'strong') return segment.text
      if (segment.type === 'question') return segment.questionId
      return ` ${segment.label}`
    })
    .join('')
}