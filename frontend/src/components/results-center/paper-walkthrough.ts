import {
  CAUSE_CATEGORIES,
  type CauseCategory,
  type ClassAnalysisQuestion,
} from '../../api/class-analysis'
import type {
  ResultsCenterItem,
  ResultsCenterResponse,
  ResultsCenterStudent,
} from '../../api/results-center'
import {
  fetchReviewItems,
  fetchReviewRubric,
  resolveReviewItem,
  type ResolvedReviewItem,
  type ReviewRubricSection,
} from '../../api/review'
import {
  classRanksOf,
  formatScore,
  isCompleteStudent,
  medianOf,
  rankChanges,
  resolvedItemScore,
  studentRate,
  type RankChange,
} from './results-overview'

/**
 * 「看卷 10 分钟」抽卡规则：纯函数，不发起请求。
 * 只统计成绩完整、且本题已给分（resolvedItemScore ≠ null）的学生；
 * 「本班」始终指学生自己所在的班级（按当前范围内的同班学生算）。
 */

export type WalkthroughCategory = 1 | 2 | 3 | 4

export const WALKTHROUGH_CATEGORY_LABELS: Record<WalkthroughCategory, string> = {
  1: '典型错法',
  2: '意外失分',
  3: '意外得分 / 特别解法',
  4: '名次变化大',
}

export interface WalkthroughMember {
  studentId: number
  /** 该成员对应的题号；名次变化大卡各成员可能不同 */
  questionId: string
  /** 翻开分数后展示的入选理由 */
  reason: string
}

export interface WalkthroughGroup {
  /** 分组名：错法名 / 错因大类 / 得分档 / 特别解法·下半区做对 / 进步·退步 */
  label: string
  members: WalkthroughMember[]
}

export interface WalkthroughCard {
  id: string
  category: WalkthroughCategory
  /** 本卡共享的题号；仅名次变化大卡为 null（成员各自带题号） */
  questionId: string | null
  /** 翻开分数前就展示的卡题 */
  title: string
  /** ≥1 组；每组 ≥1 名成员 */
  groups: WalkthroughGroup[]
  /** 不属于任何列出分组的人数说明（仅典型错法卡） */
  otherLabel?: string
}

export interface WalkthroughDeckInput {
  /** null = 全部班级 */
  scopeKey: string | null
  /** 当前范围学生（含不完整学生；统计只看完整且有分的） */
  students: readonly ResultsCenterStudent[]
  questionIds: readonly string[]
  /** 当前范围的班级分析题目；null/无错因时走兜底分组 */
  analysisQuestions: readonly ClassAnalysisQuestion[] | null
  /** 对比考试同范围学生；null 表示没有可对比考试 */
  previousStudents: readonly ResultsCenterStudent[] | null
  /** `${student_id}|${question_id}`：AI 标记了参考答案以外解法的条目 */
  alternatives?: ReadonlySet<string>
}

export interface WalkthroughDataCache {
  items: (questionId: string) => Promise<ResolvedReviewItem[]>
  rubric: (questionId: string) => Promise<ReviewRubricSection | null>
}

// 一份成绩快照共享加载中的请求与结果；新快照自然使用新缓存。
const dataCaches = new WeakMap<ResultsCenterResponse, WalkthroughDataCache>()
const CACHE_TTL_MS = 60_000
const CACHE_MAX_QUESTIONS = 16

export function walkthroughDataFor(results: ResultsCenterResponse): WalkthroughDataCache {
  const cached = dataCaches.get(results)
  if (cached) return cached
  function loader<T>(load: (qid: string) => Promise<T>): (qid: string) => Promise<T> {
    const entries = new Map<string, { promise: Promise<T>; expires: number }>()
    return (qid) => {
      const entry = entries.get(qid)
      if (entry && entry.expires > Date.now()) return entry.promise
      entries.delete(qid)
      const promise = load(qid)
      entries.set(qid, { promise, expires: Date.now() + CACHE_TTL_MS })
      if (entries.size > CACHE_MAX_QUESTIONS) entries.delete(entries.keys().next().value!)
      void promise.catch(() => {
        if (entries.get(qid)?.promise === promise) entries.delete(qid)
      })
      return promise
    }
  }
  const cache: WalkthroughDataCache = {
    items: loader((qid) => fetchReviewItems(results.session_id, qid, { scope: 'all' })
      .then((items) => items.map(resolveReviewItem))),
    rubric: loader((qid) => fetchReviewRubric(results.session_id, qid)),
  }
  dataCaches.set(results, cache)
  return cache
}

const MIN_GROUP_SIZE = 2
const FULL_SHARE_HIGH = 0.75
const FULL_SHARE_LOW = 0.3
const RANK_CHANGE_MIN = 10

export function itemOf(
  student: ResultsCenterStudent,
  questionId: string,
): ResultsCenterItem | null {
  return student.items.find((item) => item.question_id === questionId) ?? null
}

export interface UnexpectedLoss {
  questionId: string
  score: number
  maxScore: number
  /** 本班已给分学生中的满分比例 */
  fullFraction: number
  key: number
}

/**
 * 规则 2 的逐题条件：本班满分比例 ≥75%，该生本题未拿满，
 * 且总分得分率不低于本班中位。按 满分比例×失分比例 降序。
 * 抽屉行与抽卡共用；传入该生本班的完整学生列表。
 */
export function unexpectedLosses(
  student: ResultsCenterStudent,
  classCompleteStudents: readonly ResultsCenterStudent[],
): UnexpectedLoss[] {
  const rate = studentRate(student)
  const median = medianOf(
    classCompleteStudents
      .map(studentRate)
      .filter((value): value is number => value !== null),
  )
  if (rate === null || median === null || rate < median) return []
  const out: UnexpectedLoss[] = []
  for (const item of student.items) {
    const score = resolvedItemScore(item)
    if (score === null || item.max_score <= 0 || score >= item.max_score) continue
    let resolved = 0
    let full = 0
    for (const peer of classCompleteStudents) {
      if (peer.student_id === student.student_id) continue
      const peerItem = itemOf(peer, item.question_id)
      const peerScore = resolvedItemScore(peerItem)
      if (peerScore === null || peerItem === null) continue
      resolved += 1
      if (peerItem.max_score > 0 && peerScore >= peerItem.max_score) full += 1
    }
    // 学生自己也算在分母里（满分比例 = 本班满分人数 / 已给分人数）
    resolved += 1
    const fullFraction = resolved > 0 ? full / resolved : 0
    if (fullFraction < FULL_SHARE_HIGH) continue
    out.push({
      questionId: item.question_id,
      score,
      maxScore: item.max_score,
      fullFraction,
      key: fullFraction * ((item.max_score - score) / item.max_score),
    })
  }
  return out.sort((left, right) => right.key - left.key)
}

const percent = (value: number) => `${Math.round(value * 100)}%`

export function buildWalkthroughDeck(
  input: WalkthroughDeckInput,
): WalkthroughCard[] {
  const complete = input.students.filter(isCompleteStudent)
  const byId = new Map(input.students.map((s) => [s.student_id, s] as const))
  const completeIds = new Set(complete.map((s) => s.student_id))
  const analysisById = new Map(
    (input.analysisQuestions ?? []).map((q) => [q.question_id, q] as const),
  )
  const itemsByStudent = new Map(input.students.map((s) => [
    s.student_id, new Map([...s.items].reverse().map((item) => [item.question_id, item])),
  ]))
  const indexedItem = (s: ResultsCenterStudent, qid: string) => (
    itemsByStudent.get(s.student_id)?.get(qid) ?? null
  )

  // ---- 本班统计 ----
  const classOf = (s: ResultsCenterStudent) => s.class_name ?? ''
  const classLists = new Map<string, ResultsCenterStudent[]>()
  for (const s of complete) {
    const list = classLists.get(classOf(s)) ?? []
    list.push(s)
    classLists.set(classOf(s), list)
  }
  const classMedian = new Map<string, number | null>()
  const classRanks = new Map<string, Map<number, { rank: number; size: number }>>()
  for (const [key, list] of classLists) {
    classMedian.set(key, medianOf(
      list.map(studentRate).filter((v): v is number => v !== null),
    ))
    classRanks.set(key, classRanksOf(list))
  }
  const rankOf = (s: ResultsCenterStudent) => classRanks.get(classOf(s))?.get(s.student_id) ?? null
  const medianOf2 = (s: ResultsCenterStudent) => classMedian.get(classOf(s)) ?? null

  /** 本班某题：已给分人数里的满分比例 / 平均分 */
  const classItemStats = new Map<string, Map<string, {
    fullFrac: number | null; average: number | null; resolved: number; full: number
  }>>()
  function itemStats(classKey: string, questionId: string) {
    let stats = classItemStats.get(classKey)
    if (!stats) { stats = new Map(); classItemStats.set(classKey, stats) }
    let entry = stats.get(questionId)
    if (entry) return entry
    let resolved = 0; let full = 0; let sum = 0
    for (const peer of classLists.get(classKey) ?? []) {
      const item = indexedItem(peer, questionId)
      const score = resolvedItemScore(item)
      if (item === null || score === null) continue
      resolved += 1; sum += score
      if (item.max_score > 0 && score >= item.max_score) full += 1
    }
    entry = {
      fullFrac: resolved > 0 ? full / resolved : null,
      average: resolved > 0 ? sum / resolved : null,
      resolved, full,
    }
    stats.set(questionId, entry)
    return entry
  }

  /** 当前范围某题得分率（用于最弱题排序） */
  const scopeRates = new Map<string, number | null>()
  function scopeRate(questionId: string): number | null {
    if (scopeRates.has(questionId)) return scopeRates.get(questionId)!
    let sum = 0; let max = 0
    for (const s of complete) {
      const item = indexedItem(s, questionId)
      const score = resolvedItemScore(item)
      if (item === null || score === null) continue
      sum += score; max += item.max_score
    }
    const rate = max > 0 ? sum / max : null
    scopeRates.set(questionId, rate)
    return rate
  }

  const scoreOf = (s: ResultsCenterStudent, qid: string) => resolvedItemScore(indexedItem(s, qid))
  const maxOf = (s: ResultsCenterStudent, qid: string) => indexedItem(s, qid)?.max_score ?? 0

  // ---- 卡片 ----
  const cards: WalkthroughCard[] = []
  function push(card: Omit<WalkthroughCard, 'id'>): void {
    cards.push({ ...card, id: `w${cards.length + 1}` })
  }

  /** 组内排序：中位得分者在前（同分取小 id），其余按 student_id */
  function orderedGroup(members: ResultsCenterStudent[], qid: string): ResultsCenterStudent[] {
    const sorted = [...members].sort(
      (a, b) => scoreOf(a, qid)! - scoreOf(b, qid)! || a.student_id - b.student_id,
    )
    const rep = sorted[Math.floor((sorted.length - 1) / 2)]!
    return [
      rep,
      ...sorted.filter((s) => s !== rep).sort((a, b) => a.student_id - b.student_id),
    ]
  }

  /** 本题失分（已给分且未满分）的范围学生 */
  function lostStudents(qid: string): ResultsCenterStudent[] {
    return complete.filter((s) => {
      const item = indexedItem(s, qid)
      const score = resolvedItemScore(item)
      return item !== null && score !== null && score < item.max_score
    })
  }

  function pushTypicalCard(
    qid: string,
    groups: { label: string; students: ResultsCenterStudent[] }[],
    reasonSuffix: string,
  ): void {
    const lost = lostStudents(qid)
    const inGroups = new Set(groups.flatMap((g) => g.students.map((s) => s.student_id)))
    const others = lost.filter((s) => !inGroups.has(s.student_id)).length
    const title = `第 ${qid} 题 · 本题 ${groups.length} 种典型错法 · 共 ${lost.length} 人失分`
    push({
      category: 1,
      questionId: qid,
      title,
      groups: groups.map((group) => ({
        label: group.label,
        members: orderedGroup(group.students, qid).map((s) => ({
          studentId: s.student_id,
          questionId: qid,
          reason: `${title}${reasonSuffix}`,
        })),
      })),
      ...(others > 0 ? { otherLabel: `另有 ${others} 人错法各不相同或未整理` } : {}),
    })
  }

  // ---- 1. 典型错法（≤3，每题一卡）----
  function pickTypicalErrors(): void {
    const candidates = input.questionIds
      .map((qid) => {
        const causes = (analysisById.get(qid)?.causes ?? [])
          .filter((cause) => cause.kind === 'error' || cause.kind === 'process')
        const groups = causes
          .map((cause) => {
            const ids = new Set<number>()
            for (const ev of cause.evidence ?? []) {
              for (const id of ev.student_ids) ids.add(id)
            }
            const students = [...ids]
              .map((id) => byId.get(id))
              .filter((s): s is ResultsCenterStudent => (
                s !== undefined && completeIds.has(s.student_id) && scoreOf(s, qid) !== null
              ))
            return { label: cause.reason, students }
          })
          .filter((g) => g.students.length >= MIN_GROUP_SIZE)
          .sort((a, b) => b.students.length - a.students.length || a.label.localeCompare(b.label))
        return { qid, groups }
      })
      .filter((entry) => entry.groups.length > 0)
      .sort((a, b) => (scopeRate(a.qid) ?? Infinity) - (scopeRate(b.qid) ?? Infinity))
      .slice(0, 3)
    if (candidates.length > 0) {
      for (const { qid, groups } of candidates) pushTypicalCard(qid, groups, '')
      return
    }
    // 兜底：无已整理错因时，最弱 2 题按「0 分 / 部分得分」分组
    const weakest = [...input.questionIds]
      .filter((qid) => scopeRate(qid) !== null)
      .sort((a, b) => scopeRate(a)! - scopeRate(b)!)
      .slice(0, 2)
    for (const qid of weakest) {
      const lost = lostStudents(qid)
      const groups = [
        { label: '0 分', students: lost.filter((s) => scoreOf(s, qid) === 0) },
        { label: '部分得分', students: lost.filter((s) => scoreOf(s, qid)! > 0) },
      ].filter((group) => group.students.length > 0)
      if (groups.length === 0) continue
      pushTypicalCard(qid, groups, '（错因尚未整理，按得分挑选代表）')
    }
  }

  // ---- 2. 意外失分（≤3 题，每题一卡）----
  function pickUnexpectedLoss(): void {
    const candidates: { s: ResultsCenterStudent; loss: UnexpectedLoss; reason: string }[] = []
    for (const s of complete) {
      const rate = studentRate(s)
      const median = medianOf2(s)
      if (rate === null || median === null || rate < median) continue
      for (const item of s.items) {
        const score = resolvedItemScore(item)
        if (score === null || item.max_score <= 0 || score >= item.max_score) continue
        const stats = itemStats(classOf(s), item.question_id)
        const own = indexedItem(s, item.question_id)
        const ownScore = resolvedItemScore(own)
        const ownResolved = own !== null && ownScore !== null
        const ownFull = ownResolved && own.max_score > 0 && ownScore >= own.max_score
        const fullFraction = (stats.full - Number(ownFull))
          / (stats.resolved - Number(ownResolved) + 1)
        if (fullFraction < FULL_SHARE_HIGH) continue
        const rank = rankOf(s)
        candidates.push({
          s,
          loss: {
            questionId: item.question_id, score, maxScore: item.max_score, fullFraction,
            key: fullFraction * ((item.max_score - score) / item.max_score),
          },
          reason: `本班 ${percent(fullFraction)} 满分，本题丢了 ${formatScore(item.max_score - score)}/${formatScore(item.max_score)} 分；本次总分班内第 ${rank?.rank ?? '—'} 名`,
        })
      }
    }
    const byQuestion = new Map<string, typeof candidates>()
    for (const entry of candidates) {
      const list = byQuestion.get(entry.loss.questionId) ?? []
      list.push(entry)
      byQuestion.set(entry.loss.questionId, list)
    }
    const picked = [...byQuestion.entries()]
      .map(([qid, members]) => ({
        qid,
        members,
        maxKey: Math.max(...members.map((entry) => entry.loss.key)),
      }))
      .sort((a, b) => b.maxKey - a.maxKey
        || b.members.length - a.members.length
        || a.qid.localeCompare(b.qid))
      .slice(0, 3)
    for (const { qid, members } of picked) {
      // 学生在多条带大类错因里出现时，取 count 最大的大类（平手按大类顺序）
      const bestByStudent = new Map<number, { category: CauseCategory; count: number }>()
      for (const cause of analysisById.get(qid)?.causes ?? []) {
        if (!cause.category) continue
        const ids = new Set(cause.evidence?.flatMap((ev) => ev.student_ids) ?? [])
        for (const id of ids) {
          const best = bestByStudent.get(id)
          const order = CAUSE_CATEGORIES.indexOf(cause.category)
          if (!best
            || cause.count > best.count
            || (cause.count === best.count
              && order < CAUSE_CATEGORIES.indexOf(best.category))) {
            bestByStudent.set(id, { category: cause.category, count: cause.count })
          }
        }
      }
      const anyCategory = members.some((entry) => bestByStudent.has(entry.s.student_id))
      const buckets = new Map<string, typeof members>()
      for (const entry of members) {
        const label = anyCategory
          ? bestByStudent.get(entry.s.student_id)?.category ?? '错因未整理'
          : entry.loss.score === 0 ? '0 分' : '部分得分'
        const list = buckets.get(label) ?? []
        list.push(entry)
        buckets.set(label, list)
      }
      const groups = [...buckets.entries()]
        .sort(([labelA, listA], [labelB, listB]) => {
          if (!anyCategory) return labelA === '0 分' ? -1 : labelB === '0 分' ? 1 : 0
          if (labelA === '错因未整理') return 1
          if (labelB === '错因未整理') return -1
          return listB.length - listA.length || labelA.localeCompare(labelB)
        })
        .map(([label, list]) => ({
          label,
          members: [...list]
            .sort((a, b) => b.loss.key - a.loss.key || a.s.student_id - b.s.student_id)
            .map((entry) => ({
              studentId: entry.s.student_id,
              questionId: qid,
              reason: entry.reason,
            })),
        }))
      push({
        category: 2,
        questionId: qid,
        title: `第 ${qid} 题 · 本班满分率高 · ${members.length} 人意外失分`,
        groups,
      })
    }
  }

  // ---- 3. 意外得分 / 特别解法（≤3 题，每题一卡，「特别解法」/「下半区做对」两组）----
  function pickUnexpectedGain(): void {
    interface Gain { s: ResultsCenterStudent; reason: string }
    const flagged = new Map<string, Gain[]>()
    const lowerHalf = new Map<string, Gain[]>()
    for (const s of complete) {
      for (const item of s.items) {
        const score = resolvedItemScore(item)
        if (score === null || item.max_score <= 0) continue
        const qid = item.question_id
        if (score >= item.max_score / 2
          && (input.alternatives
            ? input.alternatives.has(`${s.student_id}|${qid}`)
            : item.alternative_solution_detected === true)) {
          const list = flagged.get(qid) ?? []
          list.push({ s, reason: 'AI 标记：用了参考答案以外的解法' })
          flagged.set(qid, list)
        }
        const rank = rankOf(s)
        if (!rank || rank.rank <= rank.size / 2) continue
        if (score < item.max_score) continue
        const fullFrac = itemStats(classOf(s), qid).fullFrac
        if (fullFrac === null || fullFrac > FULL_SHARE_LOW) continue
        const list = lowerHalf.get(qid) ?? []
        list.push({
          s,
          reason: `本班只有 ${percent(fullFrac)} 满分，本题做对了；本次班内第 ${rank.rank} 名`,
        })
        lowerHalf.set(qid, list)
      }
    }
    const qids = new Set([...flagged.keys(), ...lowerHalf.keys()])
    const picked = [...qids]
      .map((qid) => {
        const alt = flagged.get(qid) ?? []
        const altIds = new Set(alt.map((entry) => entry.s.student_id))
        const low = (lowerHalf.get(qid) ?? [])
          .filter((entry) => !altIds.has(entry.s.student_id))
        return { qid, alt, low, total: alt.length + low.length }
      })
      .filter((entry) => entry.total > 0)
      .sort((a, b) => b.total - a.total
        || (scopeRate(a.qid) ?? Infinity) - (scopeRate(b.qid) ?? Infinity)
        || a.qid.localeCompare(b.qid))
      .slice(0, 3)
    for (const { qid, alt, low, total } of picked) {
      const groups = [
        { label: '特别解法', list: alt },
        { label: '下半区做对', list: low },
      ]
        .filter((group) => group.list.length > 0)
        .map((group) => ({
          label: group.label,
          members: group.list.map((entry) => ({
            studentId: entry.s.student_id,
            questionId: qid,
            reason: entry.reason,
          })),
        }))
      push({
        category: 3,
        questionId: qid,
        title: `第 ${qid} 题 · ${total} 人意外得分或用了特别解法`,
        groups,
      })
    }
  }

  // ---- 4. 名次变化大（一张卡，「进步」/「退步」两组；无可对比考试时跳过）----
  function pickRankChange(): void {
    if (!input.previousStudents) return
    const toMember = (entry: RankChange): WalkthroughMember | null => {
      const s = entry.student
      // 卡题：该生得分 − 本班均分，进步取最大正差、退步取最大负差
      let best: { qid: string; score: number; max: number; diff: number; average: number } | null = null
      for (const qid of input.questionIds) {
        const score = scoreOf(s, qid)
        if (score === null) continue
        const average = itemStats(classOf(s), qid).average
        if (average === null) continue
        const diff = score - average
        const better = best === null || (entry.change > 0 ? diff > best.diff : diff < best.diff)
        if (better) best = { qid, score, max: maxOf(s, qid), diff, average }
      }
      if (!best) return null
      const scoreText = best.score >= best.max
        ? '本题满分'
        : `本题 ${formatScore(best.score)}/${formatScore(best.max)} 分`
      return {
        studentId: s.student_id,
        questionId: best.qid,
        reason: `名次 ${entry.previousRank} → ${entry.currentRank}（${entry.change > 0 ? '进步' : '退步'} ${Math.abs(entry.change)} 名）；${scoreText}，本班平均 ${formatScore(best.average)} 分`,
      }
    }
    const entries = rankChanges(input.students, input.previousStudents)
      .filter((entry) => Math.abs(entry.change) >= RANK_CHANGE_MIN)
    const groups = ([['进步', 1], ['退步', -1]] as const)
      .map(([label, sign]) => ({
        label,
        members: entries
          .filter((entry) => Math.sign(entry.change) === sign)
          .sort((a, b) => Math.abs(b.change) - Math.abs(a.change)
            || a.student.student_id - b.student.student_id)
          .map(toMember)
          .filter((member): member is WalkthroughMember => member !== null),
      }))
      .filter((group) => group.members.length > 0)
    if (groups.length === 0) return
    push({
      category: 4,
      questionId: null,
      title: `名次变化 ≥10 名 · ${groups.reduce((sum, group) => sum + group.members.length, 0)} 人`,
      groups,
    })
  }

  pickTypicalErrors()
  pickUnexpectedLoss()
  pickUnexpectedGain()
  pickRankChange()
  return cards
}
