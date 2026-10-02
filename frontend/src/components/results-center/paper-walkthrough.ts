import type { ClassAnalysisQuestion } from '../../api/class-analysis'
import type {
  ResultsCenterItem,
  ResultsCenterStudent,
} from '../../api/results-center'
import {
  classRanksOf,
  formatScore,
  isCompleteStudent,
  medianOf,
  rankChanges,
  resolvedItemScore,
  studentRate,
} from './results-overview'

/**
 * 「看卷 10 分钟」抽卡规则：纯函数，不发起请求。
 * 只统计成绩完整、且本题已给分（resolvedItemScore ≠ null）的学生；
 * 「本班」始终指学生自己所在的班级（按当前范围内的同班学生算）。
 */

export type WalkthroughCategory = 1 | 2 | 3 | 4 | 5

export const WALKTHROUGH_CATEGORY_LABELS: Record<WalkthroughCategory, string> = {
  1: '典型错法',
  2: '意外失分',
  3: '意外得分 / 特别解法',
  4: '名次变化大',
  5: '随机抽看',
}

export interface WalkthroughGroup {
  /** 错法名；兜底卡为「0 分」/「部分得分」 */
  label: string
  /** 代表生（中位得分）在最前，其余按 student_id 升序 */
  studentIds: number[]
}

export interface WalkthroughCard {
  id: string
  category: WalkthroughCategory
  /** 主要学生：只有该生计入「每人 ≤2 张」与「(学生,题) 不重复」限制 */
  studentId: number
  className: string | null
  /** null = 整卷抽看 */
  questionId: string | null
  /** 翻开分数后展示的入选理由 */
  reason: string
  /** 仅典型错法卡：错法分组 */
  groups?: WalkthroughGroup[]
  /** 初始展示的组下标 */
  groupIndex?: number
  /** 本题失分总人数（组内 + 另有） */
  lostCount?: number
  /** 不属于任何列出错法组的失分人数 */
  otherCount?: number
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
  /** 随机抽看种子（考试 id + 范围），重开结果一致 */
  seed: string
}

const CAP_TOTAL = 15
const CAP_PER_STUDENT = 2
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

function hashSeed(text: string): number {
  let hash = 2166136261
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

function mulberry32(seed: number): () => number {
  let a = seed | 0
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
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
  const classItemStats = new Map<string, Map<string, { fullFrac: number | null; average: number | null }>>()
  function itemStats(classKey: string, questionId: string) {
    let stats = classItemStats.get(classKey)
    if (!stats) { stats = new Map(); classItemStats.set(classKey, stats) }
    let entry = stats.get(questionId)
    if (entry) return entry
    let resolved = 0; let full = 0; let sum = 0
    for (const peer of classLists.get(classKey) ?? []) {
      const item = itemOf(peer, questionId)
      const score = resolvedItemScore(item)
      if (item === null || score === null) continue
      resolved += 1; sum += score
      if (item.max_score > 0 && score >= item.max_score) full += 1
    }
    entry = {
      fullFrac: resolved > 0 ? full / resolved : null,
      average: resolved > 0 ? sum / resolved : null,
    }
    stats.set(questionId, entry)
    return entry
  }

  /** 当前范围某题得分率（用于最弱题排序） */
  function scopeRate(questionId: string): number | null {
    let sum = 0; let max = 0
    for (const s of complete) {
      const item = itemOf(s, questionId)
      const score = resolvedItemScore(item)
      if (item === null || score === null) continue
      sum += score; max += item.max_score
    }
    return max > 0 ? sum / max : null
  }

  const scoreOf = (s: ResultsCenterStudent, qid: string) => resolvedItemScore(itemOf(s, qid))
  const maxOf = (s: ResultsCenterStudent, qid: string) => itemOf(s, qid)?.max_score ?? 0

  // ---- 名额 ----
  const cards: WalkthroughCard[] = []
  const primaryCount = new Map<number, number>()
  const usedPairs = new Set<string>()
  function push(card: Omit<WalkthroughCard, 'id'>): boolean {
    if (cards.length >= CAP_TOTAL) return false
    if ((primaryCount.get(card.studentId) ?? 0) >= CAP_PER_STUDENT) return false
    if (card.questionId !== null) {
      const pair = `${card.studentId}|${card.questionId}`
      if (usedPairs.has(pair)) return false
      usedPairs.add(pair)
    }
    primaryCount.set(card.studentId, (primaryCount.get(card.studentId) ?? 0) + 1)
    cards.push({ ...card, id: `w${cards.length + 1}` })
    return true
  }

  /** 组内排序：中位得分者在前（同分取小 id），其余按 student_id */
  function orderedGroup(members: ResultsCenterStudent[], qid: string): number[] {
    const sorted = [...members].sort(
      (a, b) => scoreOf(a, qid)! - scoreOf(b, qid)! || a.student_id - b.student_id,
    )
    const rep = sorted[Math.floor((sorted.length - 1) / 2)]!
    return [
      rep.student_id,
      ...sorted.map((s) => s.student_id).filter((id) => id !== rep.student_id).sort((a, b) => a - b),
    ]
  }

  /** 本题失分（已给分且未满分）的范围学生 */
  function lostStudents(qid: string): ResultsCenterStudent[] {
    return complete.filter((s) => {
      const item = itemOf(s, qid)
      const score = resolvedItemScore(item)
      return item !== null && score !== null && score < item.max_score
    })
  }

  function pushCauseCard(
    qid: string,
    groups: WalkthroughGroup[],
    extraReason: string,
  ): void {
    const lost = lostStudents(qid)
    const inGroups = new Set(groups.flatMap((g) => g.studentIds))
    const other = lost.filter((s) => !inGroups.has(s.student_id)).length
    for (let gi = 0; gi < groups.length; gi += 1) {
      for (const sid of groups[gi]!.studentIds) {
        const s = byId.get(sid)
        if (!s) continue
        if (push({
          category: 1,
          studentId: sid,
          className: s.class_name,
          questionId: qid,
          reason: `第 ${qid} 题 · 本题 ${groups.length} 种典型错法 · 共 ${lost.length} 人失分${extraReason}`,
          groups,
          groupIndex: gi,
          lostCount: lost.length,
          otherCount: other,
        })) return
      }
    }
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
            const members = [...ids]
              .map((id) => byId.get(id))
              .filter((s): s is ResultsCenterStudent => (
                s !== undefined && completeIds.has(s.student_id) && scoreOf(s, qid) !== null
              ))
            return { label: cause.reason, members }
          })
          .filter((g) => g.members.length >= MIN_GROUP_SIZE)
          .sort((a, b) => b.members.length - a.members.length || a.label.localeCompare(b.label))
        return { qid, groups }
      })
      .filter((entry) => entry.groups.length > 0)
      .sort((a, b) => (scopeRate(a.qid) ?? Infinity) - (scopeRate(b.qid) ?? Infinity))
      .slice(0, 3)
    if (candidates.length > 0) {
      for (const { qid, groups } of candidates) {
        pushCauseCard(qid, groups.map((g) => ({
          label: g.label,
          studentIds: orderedGroup(g.members, qid),
        })), '')
      }
      return
    }
    // 兜底：无已整理错因时，最弱 2 题按「0 分 / 部分得分」分组
    const weakest = [...input.questionIds]
      .filter((qid) => scopeRate(qid) !== null)
      .sort((a, b) => scopeRate(a)! - scopeRate(b)!)
      .slice(0, 2)
    for (const qid of weakest) {
      const lost = lostStudents(qid)
      const zeros = lost.filter((s) => scoreOf(s, qid) === 0)
      const partials = lost.filter((s) => scoreOf(s, qid)! > 0)
      const groups = ([['0 分', zeros], ['部分得分', partials]] as const)
        .filter(([, members]) => members.length > 0)
        .map(([label, members]) => ({ label, studentIds: orderedGroup(members, qid) }))
      if (groups.length === 0) continue
      pushCauseCard(qid, groups, '（错因尚未整理，按得分挑选代表）')
    }
  }

  // ---- 2. 意外失分（≤3）----
  function pickUnexpectedLoss(): void {
    const candidates: { s: ResultsCenterStudent; loss: UnexpectedLoss }[] = []
    for (const s of complete) {
      const losses = unexpectedLosses(s, classLists.get(classOf(s)) ?? [])
      for (const loss of losses) candidates.push({ s, loss })
    }
    candidates.sort((a, b) => b.loss.key - a.loss.key || a.s.student_id - b.s.student_id)
    let n = 0
    for (const { s, loss } of candidates) {
      if (n >= 3) break
      const rank = rankOf(s)
      if (push({
        category: 2,
        studentId: s.student_id,
        className: s.class_name,
        questionId: loss.questionId,
        reason: `本班 ${percent(loss.fullFraction)} 满分，本题丢了 ${formatScore(loss.maxScore - loss.score)}/${formatScore(loss.maxScore)} 分；本次总分班内第 ${rank?.rank ?? '—'} 名`,
      })) n += 1
    }
  }

  // ---- 3. 意外得分 / 特别解法（≤3，两个子来源各 ≤2）----
  function pickUnexpectedGain(): void {
    let n = 0
    const flagged = complete.flatMap((s) => s.items
      .filter((item) => {
        const score = resolvedItemScore(item)
        return score !== null
          && item.max_score > 0
          && score >= item.max_score / 2
          && (input.alternatives?.has(`${s.student_id}|${item.question_id}`) ?? false)
      })
      .map((item) => ({ s, item })))
      .sort((a, b) => {
        const belowA = (studentRate(a.s) ?? 0) < (medianOf2(a.s) ?? -1) ? 0 : 1
        const belowB = (studentRate(b.s) ?? 0) < (medianOf2(b.s) ?? -1) ? 0 : 1
        return belowA - belowB
          || (studentRate(a.s) ?? 0) - (studentRate(b.s) ?? 0)
          || a.s.student_id - b.s.student_id
      })
    let na = 0
    for (const { s, item } of flagged) {
      if (na >= 2 || n >= 3) break
      if (push({
        category: 3,
        studentId: s.student_id,
        className: s.class_name,
        questionId: item.question_id,
        reason: 'AI 标记：用了参考答案以外的解法',
      })) { na += 1; n += 1 }
    }
    let nb = 0
    for (const s of complete) {
      if (nb >= 2 || n >= 3) break
      const rank = rankOf(s)
      if (!rank || rank.rank <= rank.size / 2) continue
      for (const item of s.items) {
        const score = resolvedItemScore(item)
        if (score === null || item.max_score <= 0 || score < item.max_score) continue
        const fullFrac = itemStats(classOf(s), item.question_id).fullFrac
        if (fullFrac === null || fullFrac > FULL_SHARE_LOW) continue
        if (push({
          category: 3,
          studentId: s.student_id,
          className: s.class_name,
          questionId: item.question_id,
          reason: `本班只有 ${percent(fullFrac)} 满分，本题做对了；本次班内第 ${rank.rank} 名`,
        })) { nb += 1; n += 1 }
        break
      }
    }
  }

  // ---- 4. 名次变化大（≤4；无可对比考试时跳过）----
  function pickRankChange(): void {
    if (!input.previousStudents) return
    const entries = rankChanges(input.students, input.previousStudents)
      .filter((entry) => Math.abs(entry.change) >= RANK_CHANGE_MIN)
    const byAbs = (a: { change: number }, b: { change: number }) => Math.abs(b.change) - Math.abs(a.change)
    const picks = [
      ...entries.filter((e) => e.change > 0).sort(byAbs).slice(0, 2),
      ...entries.filter((e) => e.change < 0).sort(byAbs).slice(0, 2),
    ]
    for (const entry of picks) {
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
      if (!best) continue
      const scoreText = best.score >= best.max
        ? '本题满分'
        : `本题 ${formatScore(best.score)}/${formatScore(best.max)} 分`
      push({
        category: 4,
        studentId: s.student_id,
        className: s.class_name,
        questionId: best.qid,
        reason: `名次 ${entry.previousRank} → ${entry.currentRank}（${entry.change > 0 ? '进步' : '退步'} ${Math.abs(entry.change)} 名）；${scoreText}，本班平均 ${formatScore(best.average)} 分`,
      })
    }
  }

  // ---- 5. 随机抽看（整卷，3 张：高/中/低分段各 1）----
  function pickRandomPeek(): void {
    // 分段与名次都在范围全部完整学生上算（全部：跨班按总分率；单班：本班名次）；
    // 只是在每段内跳过已当过主要学生的人。
    const rankAll = new Map<number, { rank: number; size: number }>()
    if (input.scopeKey === null) {
      const ranked = [...complete].sort((a, b) => (
        (studentRate(b) ?? 0) - (studentRate(a) ?? 0) || a.student_id - b.student_id
      ))
      ranked.forEach((s, i) => rankAll.set(s.student_id, { rank: i + 1, size: ranked.length }))
    } else {
      for (const s of complete) {
        const r = rankOf(s)
        if (r) rankAll.set(s.student_id, r)
      }
    }
    const notPrimary = () => complete.filter((s) => !primaryCount.has(s.student_id))
    const segments: [string, (r: { rank: number; size: number }) => boolean][] = [
      ['高分段', (r) => r.rank / r.size <= 1 / 3],
      ['中段', (r) => r.rank / r.size > 1 / 3 && r.rank / r.size <= 2 / 3],
      ['低分段', (r) => r.rank / r.size > 2 / 3],
    ]
    for (const [label, inSeg] of segments) {
      const pool = notPrimary().filter((s) => {
        const r = rankAll.get(s.student_id)
        return r !== undefined && inSeg(r)
      })
      const from = pool.length > 0 ? pool : notPrimary()
      if (from.length === 0) continue
      const rand = mulberry32(hashSeed(`${input.seed}|${label}`))
      const s = from[Math.floor(rand() * from.length)]!
      const rank = rankAll.get(s.student_id)
      push({
        category: 5,
        studentId: s.student_id,
        className: s.class_name,
        questionId: null,
        reason: `随机抽看 · ${label}（${input.scopeKey === null ? '范围' : '本班'}第 ${rank?.rank ?? '—'} 名）`,
      })
    }
  }

  pickTypicalErrors()
  pickUnexpectedLoss()
  pickUnexpectedGain()
  pickRankChange()
  pickRandomPeek()
  return cards
}
