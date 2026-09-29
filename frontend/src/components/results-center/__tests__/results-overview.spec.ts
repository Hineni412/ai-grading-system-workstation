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
