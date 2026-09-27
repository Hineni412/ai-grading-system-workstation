import { describe, expect, it } from 'vitest';

import type { ClassAnalysisQuestion } from '../../../api/class-analysis';
import type { ResultsCenterItem, ResultsCenterStudent, ResultsScoreStatus } from '../../../api/results-center';
import { bandOfRate, blankCountsByStudent, medianOf, questionRatesFor, summarizeStudents } from '../results-overview';

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

})
