import { describe, expect, it } from 'vitest';

import type { ClassAnalysisQuestion } from '../../../api/class-analysis';
import type { ResultsCenterItem, ResultsCenterStudent, ResultsScoreStatus } from '../../../api/results-center';
import {
  bandOfRate,
  blankCountsByStudent,
  buildOverviewTiles,
  classRanksOf,
  displayAnswer,
  medianOf,
  questionRatesFor,
  rankChangeGroups,
  rankChanges,
  scoreStructureFor,
  subQuestionLabelFor,
  subQuestionOf,
  summarizeStudents,
  type RankChange,
} from '../results-overview';
import { buildWalkthroughDeck } from '../paper-walkthrough';

function item(
  questionId: string,
  score: number | null,
  status: ResultsScoreStatus = 'ai_ready',
  maxScore = 10,
): ResultsCenterItem {
  return {
    review_item_id: `r:${questionId}`,
    question_id: questionId,
    score_awarded: score,
    max_score: maxScore,
    score_status: status,
    score_source: score === null ? 'none' : 'ai',
    confidence_score: null,
    needs_review: false,
    review_reason: null,
    result_id: 1,
    detail_id: 1,
  }
}

function student(
  id: number,
  score: number,
  maxScore: number,
  className: string | null = '9',
  overrides: Partial<ResultsCenterStudent> = {},
): ResultsCenterStudent {
  return {
    student_id: id,
    student_code: `S${id}`,
    student_name: `学生${id}`,
    class_name: className,
    pinyin_initials: '',
    pinyin_full: '',
    current_score: score,
    max_score: maxScore,
    ungraded_count: 0,
    failed_count: 0,
    needs_review_count: 0,
    status: 'complete',
    items: [],
    ...overrides,
  }
}

function question(
  questionId: string,
  overrides: Partial<ClassAnalysisQuestion> = {},
): ClassAnalysisQuestion {
  return {
    question_id: questionId,
    max_score: 10,
    class_rate: 0.5,
    stem_summary: null,
    canonical_answer: null,
    records: [],
    ...overrides,
  }
}

describe('results overview pure helpers', () => {
  it('assigns score bands on the fixed percentage boundaries', () => {
    expect(bandOfRate(1)).toBe('excellent')
    expect(bandOfRate(0.85)).toBe('excellent')
    expect(bandOfRate(0.849)).toBe('good')
    expect(bandOfRate(0.7)).toBe('good')
    expect(bandOfRate(0.6)).toBe('pass')
    expect(bandOfRate(0.5)).toBe('watch')
    expect(bandOfRate(0.4)).toBe('watch')
    expect(bandOfRate(0.399)).toBe('low')
  })

  it('computes medians for odd and even cohorts', () => {
    expect(medianOf([10, 20, 30])).toBe(20)
    expect(medianOf([10, 20, 30, 40])).toBe(25)
    expect(medianOf([])).toBeNull()
  })

  it('aggregates only complete students', () => {
    const summary = summarizeStudents([
      student(1, 100, 100),
      student(2, 0, 100),
      student(3, 90, 100, '9', { ungraded_count: 1, status: 'incomplete' }),
      student(4, 90, 100, '9', { failed_count: 1, status: 'failed' }),
    ], '9')
    expect(summary.studentCount).toBe(4)
    expect(summary.completeCount).toBe(2)
    expect(summary.average).toBe(50)
    expect(summary.median).toBe(50)
    expect(summary.highest).toBe(100)
    expect(summary.lowest).toBe(0)
    expect(summary.passRate).toBe(0.5)
    expect(summary.excellentRate).toBe(0.5)
    expect(summary.bandCounts.excellent).toBe(1)
    expect(summary.bandCounts.low).toBe(1)
  })

  it('computes per-class question rates from resolved items only', () => {
    const students = [
      student(1, 0, 0, '9', { items: [item('Q1', 5), item('Q2', 10)] }),
      student(2, 0, 0, '9', { items: [item('Q1', 3), item('Q2', null, 'ungraded')] }),
      student(3, 0, 0, '10', { items: [item('Q1', 0), item('Q2', 8)] }),
    ]
    const nine = questionRatesFor(
      students.filter((entry) => entry.class_name === '9'), ['Q1', 'Q2'],
    )
    expect(nine.get('Q1')).toBeCloseTo(0.4)
    expect(nine.get('Q2')).toBe(1)
    const ten = questionRatesFor(
      students.filter((entry) => entry.class_name === '10'), ['Q1', 'Q2'],
    )
    expect(ten.get('Q1')).toBe(0)
    expect(ten.get('Q2')).toBeCloseTo(0.8)
  })

  it('counts blanks per student from response_state evidence only', () => {
    const counts = blankCountsByStudent([
      question('Q1', {
        causes: [
          {
            kind: 'response_state', reason: '未作答', count: 2,
            evidence: [{ text: '空白', student_ids: [1, 2] }],
          },
          {
            kind: 'error', reason: '化简错误', count: 1,
            evidence: [{ text: '错', student_ids: [3] }],
          },
        ],
      }),
      question('Q2', {
        causes: [
          {
            kind: 'response_state', reason: '未作答', count: 1,
            evidence: [{ text: '空白', student_ids: [1] }],
          },
        ],
      }),
    ])
    expect(counts.get(1)).toBe(2)
    expect(counts.get(2)).toBe(1)
    expect(counts.get(3)).toBeUndefined()
  })

  it('parses sub-question ids', () => {
    expect(subQuestionOf('Q14(P8)')).toEqual({ parentId: 'Q14', index: 8 })
    expect(subQuestionOf('Q12')).toBeNull()
    expect(subQuestionOf('Q14(Px)')).toBeNull()
  })

  it('labels sub-question rows without requiring a parent row', () => {
    const heading = '计算（每题6分，共48分）'
    const questions = [
      { question_id: 'Q14(P1)', stem_summary: heading },
      { question_id: 'Q14(P2)', stem_summary: heading },
      { question_id: 'Q14(P3)', stem_summary: heading },
      { question_id: 'Q9', stem_summary: '解方程' },
      { question_id: 'Q12(P1)', stem_summary: '' },
      { question_id: 'Q12(P2)', stem_summary: 'Q12' },
      { question_id: 'Q13(P1)', stem_summary: '化简：' },
    ]
    // 同父题号下有共享题干的姊妹行 → 小问行，父标题即共享题干
    expect(subQuestionLabelFor('Q14(P2)', heading, questions))
      .toEqual({ index: 2, parentStem: heading })
    // 题干为空或等于父题号 → 小问行但无父标题
    expect(subQuestionLabelFor('Q12(P1)', '', questions))
      .toEqual({ index: 1, parentStem: null })
    expect(subQuestionLabelFor('Q12(P2)', 'Q12', questions))
      .toEqual({ index: 2, parentStem: null })
    // 普通题号或题干独一无二的子题号 → 非小问行
    expect(subQuestionLabelFor('Q9', '解方程', questions)).toBeNull()
    expect(subQuestionLabelFor('Q13(P1)', '化简：', questions)).toBeNull()
  })

  it('splits resolved students into full / partial / zero shares', () => {
    const structure = scoreStructureFor([
      student(1, 0, 0, '9', { items: [item('Q1', 10), item('Q2', 0)] }),
      student(2, 0, 0, '9', { items: [item('Q1', 5), item('Q2', 0)] }),
      student(3, 0, 0, '9', { items: [item('Q1', 0), item('Q2', null, 'ungraded')] }),
    ], ['Q1', 'Q2'])
    expect(structure.get('Q1')).toEqual({ full: 1, partial: 1, zero: 1, resolved: 3 })
    expect(structure.get('Q2')).toEqual({ full: 0, partial: 0, zero: 2, resolved: 2 })
  })

  it('ranks students per class with competition ranking', () => {
    const ranks = classRanksOf([
      student(1, 90, 100),
      student(2, 80, 100),
      student(3, 80, 100),
      student(4, 60, 100),
      student(5, 95, 100, '10'),
      student(6, 50, 100, '9', { ungraded_count: 1, status: 'incomplete' }),
    ])
    expect(ranks.get(1)).toEqual({ rank: 1, size: 4 })
    expect(ranks.get(2)).toEqual({ rank: 2, size: 4 })
    expect(ranks.get(3)).toEqual({ rank: 2, size: 4 })
    expect(ranks.get(4)).toEqual({ rank: 4, size: 4 })
    expect(ranks.get(5)).toEqual({ rank: 1, size: 1 })
    expect(ranks.get(6)).toBeUndefined()
  })

  it('computes rank changes and excludes class changes and incomplete students', () => {
    const current = [
      student(1, 90, 100),
      student(2, 80, 100),
      student(3, 70, 100, '10'),
      student(4, 60, 100, '9', { ungraded_count: 1, status: 'incomplete' }),
    ]
    const previous = [
      student(1, 70, 100),
      student(2, 80, 100),
      student(3, 70, 100, '9'),
      student(4, 60, 100),
    ]
    const changes = rankChanges(current, previous)
    expect(changes.map((entry) => entry.student.student_id)).toEqual([1, 2])
    expect(changes[0]!.currentRank).toBe(1)
    expect(changes[0]!.previousRank).toBe(2)
    expect(changes[0]!.change).toBe(1)
  })

  it('groups rank changes by direction with threshold 5 and the requested limit', () => {
    const entry = (id: number, change: number): RankChange => ({
      student: student(id, 80, 100),
      previousScore: 70,
      previousMax: 100,
      currentRank: 1,
      previousRank: 1 + change,
      currentSize: 10,
      change,
    })
    const improved = Array.from({ length: 12 }, (_, index) => entry(100 + index, 5 + index))
    const groups = rankChangeGroups([
      ...improved,
      entry(1, 4),
      entry(2, -6),
      entry(3, -5),
    ], 5, 10)
    expect(groups.improved).toHaveLength(10)
    expect(groups.improved[0]!.change).toBe(16)
    expect(groups.improved.every((row) => row.change >= 5)).toBe(true)
    expect(groups.declined.map((row) => row.student.student_id)).toEqual([2, 3])
    expect(groups.declined.every((row) => row.change <= -5)).toBe(true)
  })

  it('builds low-tail, weak-question and zero-share tiles only when their data exists', () => {
    const scopeStudents = [
      student(1, 90, 100),
      student(2, 90, 100),
      student(3, 30, 100),
    ]
    const classes = [summarizeStudents(scopeStudents, '9')]
    const tiles = buildOverviewTiles({
      scopeKey: null,
      scopeStudents,
      classes,
      questionRates: new Map([['Q1', 0.3], ['Q2', 0.9], ['Q3', 0.5], ['Q4', 0.8]]),
      analysisQuestions: null,
      scoreStructure: new Map([
        ['Q1', { full: 1, partial: 0, zero: 3, resolved: 4 }],
        ['Q2', { full: 4, partial: 0, zero: 0, resolved: 4 }],
      ]),
    })
    const ids = tiles.map((tile) => tile.id)
    expect(ids).toEqual(['low-tail', 'weak-questions', 'zero-share'])
    const lowTail = tiles[0]!
    expect(lowTail.id === 'low-tail' && lowTail.count).toBe(1)
    expect(lowTail.id === 'low-tail' && lowTail.medianGap).toEqual({ average: 70, median: 90 })
    const weak = tiles[1]!
    expect(weak.id === 'weak-questions' && weak.rows.map((row) => row.questionId))
      .toEqual(['Q1', 'Q3', 'Q4', 'Q2'])
    const zero = tiles[2]!
    expect(zero.id === 'zero-share' && zero.top.questionId).toBe('Q1')
    expect(zero.id === 'zero-share' && zero.top.share).toBeCloseTo(0.75)
    expect(zero.id === 'zero-share' && zero.topIds).toEqual(['Q1'])

    const quiet = buildOverviewTiles({
      scopeKey: null,
      scopeStudents: [student(1, 90, 100)],
      classes: [summarizeStudents([student(1, 90, 100)], '9')],
      questionRates: new Map(),
      analysisQuestions: null,
      scoreStructure: new Map(),
    })
    expect(quiet).toEqual([])
  })

  it('builds the class-gap tile only when averages differ by at least 5', () => {
    const build = (lowAverage: number) => buildOverviewTiles({
      scopeKey: null,
      scopeStudents: [],
      classes: [
        summarizeStudents([student(1, 70, 100)], '9'),
        summarizeStudents([student(2, lowAverage, 100, '10')], '10'),
      ],
      questionRates: new Map(),
      classQuestionRates: new Map([
        ['9', new Map([['Q1', 0.7]])],
        ['10', new Map([['Q1', 0.4]])],
      ]),
      analysisQuestions: null,
      scoreStructure: new Map(),
    })
    const gap = build(50).find((tile) => tile.id === 'class-gap')
    expect(gap).toBeTruthy()
    expect(gap?.id === 'class-gap' && gap.gap).toBe(20)
    expect(gap?.id === 'class-gap' && gap.widest?.questionId).toBe('Q1')
    expect(gap?.id === 'class-gap' && gap.lowLabel).toBe('10班')
    expect(build(66).some((tile) => tile.id === 'class-gap')).toBe(false)
    const scoped = buildOverviewTiles({
      scopeKey: '9',
      scopeStudents: [],
      classes: [
        summarizeStudents([student(1, 70, 100)], '9'),
        summarizeStudents([student(2, 50, 100, '10')], '10'),
      ],
      questionRates: new Map(),
      analysisQuestions: null,
      scoreStructure: new Map(),
    })
    expect(scoped.some((tile) => tile.id === 'class-gap')).toBe(false)
  })

  it('builds the cause-category tile only from structured causes', () => {
    const build = (causes: ClassAnalysisQuestion['causes']) => buildOverviewTiles({
      scopeKey: null,
      scopeStudents: [],
      classes: [],
      questionRates: new Map(),
      analysisQuestions: [question('Q1', { causes })],
      scoreStructure: new Map(),
    })
    const structured = build([
      { kind: 'error', reason: '化简错', count: 4, category: '计算与化简' },
      { kind: 'response_state', reason: '未作答', count: 9, category: '未作答' },
    ])
    const tile = structured.find((entry) => entry.id === 'cause-categories')
    expect(tile?.id === 'cause-categories' && tile.total).toBe(4)
    expect(
      tile?.id === 'cause-categories'
      && tile.segments.map((seg) => seg.category),
    ).toEqual(['计算与化简'])
    expect(build([{ reason: '旧错因', count: 3 }])
      .some((entry) => entry.id === 'cause-categories')).toBe(false)
  })

})

describe('displayAnswer', () => {
  it('keeps the first alternative when a long solution follows 或', () => {
    expect(displayAnswer('0 或 将每个二次根式化为最简二次根式后再合并同类项，过程略'))
      .toBe('0')
    expect(displayAnswer('2√3 或 3√2')).toBe('2√3 或 3√2')
    expect(displayAnswer(null)).toBeNull()
    expect(displayAnswer('1/9')).toBe('1/9')
  })
})

// ---- 看卷 10 分钟抽卡规则 ----
describe('buildWalkthroughDeck', () => {
  const walkStudent = (
    id: number,
    className: string | null,
    items: Array<[string, number | null, number?]>,
    overrides: Partial<ResultsCenterStudent> = {},
  ): ResultsCenterStudent => student(
    id,
    items.reduce((total, [, score]) => total + (score ?? 0), 0),
    items.reduce((total, [, , max]) => total + (max ?? 10), 0),
    className,
    {
      items: items.map(([qid, score, max = 10]) => item(qid, score, 'ai_ready', max)),
      ...overrides,
    },
  )

  const cause = (
    reason: string,
    ids: number[],
    kind: 'error' | 'process' | 'response_state' = 'error',
  ) => ({
    reason,
    count: ids.length,
    kind,
    evidence: [{ text: '摘录', student_ids: ids }],
  })

  const deck = (
    students: ResultsCenterStudent[],
    overrides: Partial<Parameters<typeof buildWalkthroughDeck>[0]> = {},
  ) => buildWalkthroughDeck({
    scopeKey: '9',
    students,
    questionIds: ['Q1', 'Q2'],
    analysisQuestions: null,
    previousStudents: null,
    seed: 'exam1|9',
    ...overrides,
  })

  it('groups organized causes by question and orders representative first', () => {
    const students = [
      walkStudent(1, '9', [['Q1', 2], ['Q2', 10]]),
      walkStudent(2, '9', [['Q1', 5], ['Q2', 10]]),
      walkStudent(3, '9', [['Q1', 9], ['Q2', 10]]),
      walkStudent(4, '9', [['Q1', 3], ['Q2', 10]]),
      walkStudent(5, '9', [['Q1', 10], ['Q2', 10]]),
    ]
    const cards = deck(students, {
      analysisQuestions: [
        question('Q1', { causes: [cause('漏写负根', [1, 2, 3])] }),
      ],
    })
    const card = cards.find((entry) => entry.category === 1)
    expect(card?.questionId).toBe('Q1')
    // 中位得分者（2 号，5 分）在前，其余按学号
    expect(card?.groups).toEqual([{ label: '漏写负根', studentIds: [2, 1, 3] }])
    expect(card?.lostCount).toBe(4)
    expect(card?.otherCount).toBe(1) // 4 号失分但不在任何组
    expect(card?.studentId).toBe(2)
  })

  it('drops causes below two students and non-error kinds', () => {
    const students = [
      walkStudent(1, '9', [['Q1', 2], ['Q2', 10]]),
      walkStudent(2, '9', [['Q1', 5], ['Q2', 10]]),
      walkStudent(3, '9', [['Q1', 0], ['Q2', 10]]),
      walkStudent(4, '9', [['Q1', 10], ['Q2', 10]]),
    ]
    const cards = deck(students, {
      analysisQuestions: [
        question('Q1', {
          causes: [
            cause('只扣一人的错法', [1]),
            cause('未作答', [3], 'response_state'),
          ],
        }),
      ],
    })
    const card = cards.find((entry) => entry.category === 1)
    // 没有 ≥2 人的整理错法 → 兜底分组（0 分 / 部分得分）
    expect(card?.questionId).toBe('Q1')
    expect(card?.groups?.map((g) => g.label)).toEqual(['0 分', '部分得分'])
    expect(card?.groups?.[0]?.studentIds).toEqual([3])
    expect(card?.groups?.[1]?.studentIds).toEqual([1, 2])
    expect(card?.lostCount).toBe(3)
  })

  it('keeps at most three typical-error cards, weakest questions first', () => {
    const students = [
      walkStudent(1, '9', [['Q1', 5], ['Q2', 5], ['Q3', 5], ['Q4', 5]]),
      walkStudent(2, '9', [['Q1', 5], ['Q2', 5], ['Q3', 5], ['Q4', 5]]),
      walkStudent(3, '9', [['Q1', 9], ['Q2', 9], ['Q3', 9], ['Q4', 9]]),
    ]
    const cards = deck(students, {
      questionIds: ['Q1', 'Q2', 'Q3', 'Q4'],
      analysisQuestions: [
        question('Q1', { causes: [cause('a', [1, 2])] }),
        question('Q2', { causes: [cause('b', [1, 2])] }),
        question('Q3', { causes: [cause('c', [1, 2])] }),
        question('Q4', { causes: [cause('d', [1, 2])] }),
      ],
    })
    const cat1 = cards.filter((entry) => entry.category === 1)
    // 每题得分率相同，按题号顺序前三题
    expect(cat1.map((entry) => entry.questionId)).toEqual(['Q1', 'Q2', 'Q3'])
  })

  it('picks unexpected losses only at ≥75% class full share and above-median totals', () => {
    // Q1：s1–s3 满分、s4 得 6 → 满分比例 0.75；s4 总分率 0.8 高于本班中位 0.7
    const students = [
      walkStudent(1, '9', [['Q1', 10], ['Q2', 4]]),
      walkStudent(2, '9', [['Q1', 10], ['Q2', 4]]),
      walkStudent(3, '9', [['Q1', 10], ['Q2', 4]]),
      walkStudent(4, '9', [['Q1', 6], ['Q2', 10]]),
    ]
    // 给 Q2 一条已整理错因，避免典型错法走兜底分组抢占 (学生,题)
    const cards = deck(students, {
      analysisQuestions: [question('Q2', { causes: [cause('化简错误', [1, 2])] })],
    })
    const losses = cards.filter((entry) => entry.category === 2)
    expect(losses).toHaveLength(1)
    expect(losses[0]?.studentId).toBe(4)
    expect(losses[0]?.questionId).toBe('Q1')
    expect(losses[0]?.reason).toContain('本班 75% 满分')
    // Q2 本班满分比例只有 0.25 → s1–s3 丢分不算意外失分
    expect(losses.some((entry) => entry.questionId === 'Q2')).toBe(false)
  })

  it('excludes below-median students from unexpected losses', () => {
    const students = [
      walkStudent(1, '9', [['Q1', 10], ['Q2', 10]]),
      walkStudent(2, '9', [['Q1', 10], ['Q2', 10]]),
      walkStudent(3, '9', [['Q1', 10], ['Q2', 10]]),
      walkStudent(4, '9', [['Q1', 2], ['Q2', 2]]), // 垫底学生丢分
    ]
    const cards = deck(students)
    expect(cards.filter((entry) => entry.category === 2)).toHaveLength(0)
  })

  it('caps alternative-solution picks at two and prefers below-median students', () => {
    const students = [
      walkStudent(1, '9', [['Q1', 10], ['Q2', 10]]), // 中位以上
      walkStudent(2, '9', [['Q1', 10], ['Q2', 10]]),
      walkStudent(3, '9', [['Q1', 10], ['Q2', 6]]), // 低于中位
      walkStudent(4, '9', [['Q1', 10], ['Q2', 6]]), // 低于中位
      walkStudent(5, '9', [['Q1', 10], ['Q2', 8]]),
    ]
    const alternatives = new Set(['1|Q2', '3|Q2', '4|Q2'])
    // Q1 上有已整理错因 → 典型错法走正常分组，不占 Q2 的 (学生,题)
    const cards = deck(students, {
      alternatives,
      analysisQuestions: [question('Q1', { causes: [cause('化简错误', [1, 2])] })],
    })
    const gains = cards.filter((entry) => entry.category === 3)
    // 只取 2 张；低于中位的 3、4 优先于中位以上的 1
    expect(gains.map((entry) => entry.studentId).sort()).toEqual([3, 4])
    expect(gains.every((entry) => entry.reason.includes('参考答案以外'))).toBe(true)
  })

  it('picks full scores on hard questions only for lower-half ranks', () => {
    // Q2 全班只有 s5 满分（0.2 ≤ 0.3）；s5 名次垫底（下半区）
    const students = [
      walkStudent(1, '9', [['Q1', 10], ['Q2', 8]]),
      walkStudent(2, '9', [['Q1', 10], ['Q2', 7]]),
      walkStudent(3, '9', [['Q1', 9], ['Q2', 6]]),
      walkStudent(4, '9', [['Q1', 8], ['Q2', 5]]),
      walkStudent(5, '9', [['Q1', 5], ['Q2', 10]]),
    ]
    const cards = deck(students)
    const gains = cards.filter((entry) => entry.category === 3)
    expect(gains.map((entry) => entry.studentId)).toEqual([5])
    expect(gains[0]?.reason).toContain('本班只有 20% 满分')
  })

  it('picks |rank change| ≥ 10 and lands the card on the biggest point gap', () => {
    // 12 名学生：s12 从第 12 名跳到第 1 名；其余名次不变
    const current = Array.from({ length: 12 }, (_, i) => {
      const id = i + 1
      return walkStudent(id, '9', [
        ['Q1', id === 12 ? 10 : 5],
        ['Q2', id === 12 ? 10 : 4],
      ], { current_score: id === 12 ? 100 : 60 - i })
    })
    const previous = current.map((s, i) => ({
      ...s,
      current_score: s.student_id === 12 ? 10 : 100 - i,
    }))
    const cards = deck(current, { previousStudents: previous })
    const rankCards = cards.filter((entry) => entry.category === 4)
    expect(rankCards).toHaveLength(1)
    expect(rankCards[0]?.studentId).toBe(12)
    // Q2 的 该生得分−本班均分 正差大于 Q1（10−4.5 > 10−5.4）
    expect(rankCards[0]?.questionId).toBe('Q2')
    expect(rankCards[0]?.reason).toContain('进步 11 名')
  })

  it('skips rank-change cards entirely without a comparison exam', () => {
    const students = [
      walkStudent(1, '9', [['Q1', 5]]),
      walkStudent(2, '9', [['Q1', 6]]),
    ]
    expect(deck(students, { previousStudents: null })
      .some((entry) => entry.category === 4)).toBe(false)
  })

  it('picks three whole-paper cards deterministically from rank thirds', () => {
    const students = Array.from({ length: 9 }, (_, i) => (
      walkStudent(i + 1, '9', [['Q1', 10 - i]])
    ))
    const first = deck(students)
    const second = deck(students)
    const peeks = first.filter((entry) => entry.category === 5)
    expect(peeks).toHaveLength(3)
    expect(peeks.every((entry) => entry.questionId === null)).toBe(true)
    expect(peeks.map((entry) => entry.studentId))
      .toEqual(second.filter((entry) => entry.category === 5).map((entry) => entry.studentId))
    // 三段各一人：名次 1–3 / 4–6 / 7–9 各命中一张
    const ranks = peeks.map((entry) => Number(entry.reason.match(/第 (\d+) 名/)?.[1]))
    expect(ranks.filter((r) => r <= 3)).toHaveLength(1)
    expect(ranks.filter((r) => r > 3 && r <= 6)).toHaveLength(1)
    expect(ranks.filter((r) => r > 6)).toHaveLength(1)
  })

  it('ranks random-peek picks against the whole scope, not just unused students', () => {
    // s1、s2 已分别是两题典型错法卡的主要学生；高分段只剩 s3，
    // 显示的名次应为全体第 3 名，而不是"未用学生里的第 1 名"
    const students = Array.from({ length: 9 }, (_, i) => {
      const id = i + 1
      const q1 = id === 1 ? 0 : id === 4 ? 3 : 8
      const q2 = id === 2 ? 0 : id === 5 ? 3 : 8
      const q3 = id <= 2 ? 20 : 10 - i
      return walkStudent(id, '9', [['Q1', q1], ['Q2', q2], ['Q3', q3, 20]])
    })
    const cards = deck(students, {
      questionIds: ['Q1', 'Q2', 'Q3'],
      analysisQuestions: [
        question('Q1', { causes: [cause('a', [1, 4])] }),
        question('Q2', { causes: [cause('b', [2, 5])] }),
      ],
    })
    const cat1 = cards.filter((entry) => entry.category === 1)
    expect(cat1.map((entry) => entry.studentId).sort()).toEqual([1, 2])
    const peek = cards.find(
      (entry) => entry.category === 5 && entry.reason.includes('高分段'),
    )
    expect(peek?.studentId).toBe(3)
    expect(peek?.reason).toContain('本班第 3 名')
  })

  it('enforces caps: ≤15 cards, ≤2 primary per student, unique (student, question)', () => {
    // 同一学生触发多个类别也只能当 2 次主要学生
    const students = Array.from({ length: 8 }, (_, i) => walkStudent(
      i + 1, '9',
      [['Q1', i + 1 === 8 ? 2 : 10], ['Q2', i + 1 === 8 ? 2 : 10]],
      { current_score: 40 + i * 5 },
    ))
    const alternatives = new Set(['8|Q1', '8|Q2'])
    const cards = deck(students, { alternatives })
    expect(cards.length).toBeLessThanOrEqual(15)
    const primaryCounts = new Map<number, number>()
    for (const card of cards) {
      primaryCounts.set(card.studentId, (primaryCounts.get(card.studentId) ?? 0) + 1)
    }
    const pairs = cards
      .filter((card) => card.questionId !== null)
      .map((card) => `${card.studentId}|${card.questionId}`)
    expect(new Set(pairs).size).toBe(pairs.length)
    for (const count of primaryCounts.values()) expect(count).toBeLessThanOrEqual(2)
    // 组内浏览不占名额：8 号作为组成员出现，同时仍可做主要学生
    const cat1 = cards.find((entry) => entry.category === 1)
    expect(cat1?.groups?.flatMap((g) => g.studentIds)).toContain(8)
  })
})
