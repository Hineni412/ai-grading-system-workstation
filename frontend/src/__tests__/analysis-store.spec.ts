import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { QuestionAnalysisResponse, StudentAnalysisResponse } from '../api/analysis'
import type { GraphRowsResponse } from '../api/graph'
import { useAnalysisStore } from '../stores/analysis'

const questionItem = {
  class_name: '一班', question_id: 'Q1', max_score: 10, score_rate: 80,
  average_score: 8, deduction_count: 1, attempt_count: 2, metric_status: 'ready' as const,
}
const session7Questions: QuestionAnalysisResponse = {
  scope: { session_id: 7, class_name: null, question_id: null }, classes: ['一班'],
  items: [questionItem], total: 1, page: 1, page_size: 100, total_pages: 1,
}
const session8Questions: QuestionAnalysisResponse = {
  ...session7Questions,
  scope: { ...session7Questions.scope, session_id: 8 },
  items: [{ ...questionItem, question_id: 'Q8' }],
}
const students: StudentAnalysisResponse = {
  scope: { session_id: 7, class_name: '一班', question_id: 'Q1' },
  items: [], total: 0, page: 1, page_size: 100, total_pages: 0,
}
const graph: GraphRowsResponse = {
  scope: { mode: 'class', student_ids: [], class_id: '一班' },
  exam_scope: { mode: 'current', session_ids: [7], sessions: [{ session_id: 7, session_name: '期中考试' }] },
  rows: [], nodes: [], edges: [],
  coverage: { covered_items: 0, total_items: 0, missing_items: {} },
  warnings: [], diagnosis_identity: 'question_tag',
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise })
  return { promise, resolve }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.restoreAllMocks()
})

describe('analysis store', () => {
  it('retains pagination metadata and appends later question pages without duplicates', async () => {
    const store = useAnalysisStore()
    const pages: QuestionAnalysisResponse[] = [{
      ...session7Questions,
      items: Array.from({ length: 100 }, (_, index) => ({
        ...questionItem,
        question_id: `Q${index + 1}`,
      })),
      total: 102,
      total_pages: 2,
    }, {
      ...session7Questions,
      items: [
        { ...questionItem, question_id: 'Q100' },
        { ...questionItem, question_id: 'Q101' },
      ],
      total: 102,
      page: 2,
      total_pages: 2,
    }]
    const loader = vi.fn(async (_sessionId, _className, _signal, page = 1) => pages[page - 1]!)

    await store.loadQuestions(7, null, loader)
    expect(store.questions).toHaveLength(100)
    expect(store.questionsTotal).toBe(102)
    expect(store.questionsPage).toBe(1)
    expect(store.questionsTotalPages).toBe(2)

    await store.loadMoreQuestions(loader)
    expect(loader).toHaveBeenLastCalledWith(7, null, expect.any(AbortSignal), 2)
    expect(store.questions).toHaveLength(101)
    expect(store.questions[0]?.question_id).toBe('Q1')
    expect(store.questions[99]?.question_id).toBe('Q100')
    expect(store.questions[100]?.question_id).toBe('Q101')
    expect(store.questionsTotal).toBe(102)
    expect(store.questionsPage).toBe(2)
    expect(store.questionsPageSize).toBe(100)
    expect(store.questionsTotalPages).toBe(2)
  })

  it('appends more than 100 students and clears pagination when the question scope changes', async () => {
    const store = useAnalysisStore()
    const item = {
      result_id: 1, detail_id: 1, student_id: 1, student_code: 'S1', student_name: 'student',
      class_name: 'class-a', question_id: 'Q1', score_awarded: 8, max_score: 10,
      deduction_amount: 2, deduction_reason: null, needs_review: false,
      evidence_url: '/api/sessions/7/results/1/details/1/crop',
    }
    const loader = vi.fn(async (_sessionId, questionId, _className, _signal, page = 1) => ({
      scope: { session_id: 7, class_name: 'class-a', question_id: questionId },
      items: page === 1
        ? Array.from({ length: 100 }, (_, index) => ({
            ...item,
            result_id: index + 1,
            detail_id: index + 1,
            student_id: index + 1,
            evidence_url: `/api/sessions/7/results/${index + 1}/details/${index + 1}/crop`,
          }))
        : [{
            ...item, result_id: 100, detail_id: 100, student_id: 100,
            evidence_url: '/api/sessions/7/results/100/details/100/crop',
          }, {
            ...item, result_id: 101, detail_id: 101, student_id: 101,
            evidence_url: '/api/sessions/7/results/101/details/101/crop',
          }],
      total: 102, page, page_size: 100, total_pages: 2,
    } satisfies StudentAnalysisResponse))

    await store.loadStudents(7, 'Q1', 'class-a', loader)
    await store.loadMoreStudents(loader)
    expect(store.students).toHaveLength(101)
    expect(store.students[0]?.detail_id).toBe(1)
    expect(store.students[99]?.detail_id).toBe(100)
    expect(store.students[100]?.detail_id).toBe(101)
    expect(store.studentsTotal).toBe(102)
    expect(store.studentsPage).toBe(2)
    expect(store.studentsPageSize).toBe(100)
    expect(store.studentsTotalPages).toBe(2)

    await store.loadStudents(7, 'Q2', 'class-a', async () => ({
      ...students,
      scope: { session_id: 7, class_name: 'class-a', question_id: 'Q2' },
    }))
    expect(store.students).toEqual([])
    expect(store.studentsTotal).toBe(0)
    expect(store.studentsPage).toBe(1)
  })

  it('ignores the old session response after switching exams', async () => {
    const store = useAnalysisStore()
    const old = deferred<QuestionAnalysisResponse>()
    const oldLoad = store.loadQuestions(7, null, () => old.promise)
    await store.loadQuestions(8, null, async () => session8Questions)
    old.resolve(session7Questions)
    await oldLoad
    expect(store.sessionId).toBe(8)
    expect(store.questions).toEqual(session8Questions.items)
  })

  it('keeps each resource state independent and retries failures', async () => {
    const store = useAnalysisStore()
    await store.loadQuestions(7, null, async () => session7Questions)
    await store.loadStudents(7, 'Q1', '一班', async () => { throw new Error('private failure') })
    await store.loadGraph(7, '一班', async () => graph)

    expect(store.questionsState).toBe('ready')
    expect(store.studentsState).toBe('error')
    expect(store.studentsError).toBe('学生明细暂时无法更新')
    expect(store.graphState).toBe('empty')

    await store.loadStudents(7, 'Q1', '一班', async () => students)
    expect(store.students).toEqual([])
    expect(store.studentsState).toBe('empty')
    expect(store.studentsError).toBe('')
  })

  it('keeps last successful questions and timestamp after same-session failure', async () => {
    const store = useAnalysisStore()
    await store.loadQuestions(7, null, async () => session7Questions)
    const updatedAt = store.questionsUpdatedAt
    await store.loadQuestions(7, null, async () => { throw new Error('private failure') })
    expect(store.questions).toEqual(session7Questions.items)
    expect(store.questionsState).toBe('stale-error')
    expect(store.questionsError).toBe('题目分析暂时无法更新')
    expect(store.questionsUpdatedAt).toBe(updatedAt)
    expect(store.questionsScope).toEqual(session7Questions.scope)
  })

  it('clears class A content and ignores its late response after selecting class B', async () => {
    const store = useAnalysisStore()
    const classA = {
      ...session7Questions,
      scope: { ...session7Questions.scope, class_name: '一班' },
    }
    await store.loadQuestions(7, '一班', async () => classA)
    const old = deferred<QuestionAnalysisResponse>()
    let oldSignal: AbortSignal | undefined
    const oldLoad = store.loadQuestions(7, '一班', (_sessionId, _className, signal) => {
      oldSignal = signal
      return old.promise
    })
    await store.loadQuestions(7, '二班', async () => { throw new Error('private failure') })

    expect(oldSignal?.aborted).toBe(true)
    expect(store.questions).toEqual([])
    expect(store.questionsScope).toBeNull()
    expect(store.questionsState).toBe('error')
    old.resolve(classA)
    await oldLoad
    expect(store.questions).toEqual([])
    expect(store.questionsScope).toBeNull()
  })

  it('clears Q1 students before a failed Q2 request', async () => {
    const store = useAnalysisStore()
    const q1 = {
      ...students,
      items: [{
        result_id: 1,
        detail_id: 2,
        student_id: 3,
        student_code: 'S3',
        student_name: '学生甲',
        class_name: '一班',
        question_id: 'Q1',
        score_awarded: 8,
        max_score: 10,
        deduction_amount: 2,
        deduction_reason: null,
        needs_review: false,
        evidence_url: '/api/evidence/2',
      }],
      total: 1,
      total_pages: 1,
    }
    await store.loadStudents(7, 'Q1', '一班', async () => q1)
    expect(store.studentsScope).toEqual(q1.scope)
    await store.loadStudents(7, 'Q2', '一班', async () => { throw new Error('private failure') })
    expect(store.students).toEqual([])
    expect(store.studentsScope).toBeNull()
    expect(store.studentsState).toBe('error')
  })

  it('cancels and clears an in-flight student request when the view changes class scope', async () => {
    const store = useAnalysisStore()
    const old = deferred<StudentAnalysisResponse>()
    let oldSignal: AbortSignal | undefined
    const oldLoad = store.loadStudents(7, 'Q1', '一班', (_sessionId, _questionId, _className, signal) => {
      oldSignal = signal
      return old.promise
    })

    store.resetStudents()

    expect(oldSignal?.aborted).toBe(true)
    expect(store.students).toEqual([])
    expect(store.studentsScope).toBeNull()
    expect(store.studentsState).toBe('idle')

    old.resolve({
      ...students,
      items: [{
        result_id: 1,
        detail_id: 2,
        student_id: 3,
        student_code: 'S3',
        student_name: '旧班级学生',
        class_name: '一班',
        question_id: 'Q1',
        score_awarded: 8,
        max_score: 10,
        deduction_amount: 2,
        deduction_reason: null,
        needs_review: false,
        evidence_url: '/api/evidence/2',
      }],
      total: 1,
      total_pages: 1,
    })
    await oldLoad
    expect(store.students).toEqual([])
    expect(store.studentsState).toBe('idle')
  })

  it('clears graph content when the class changes even if the new request aborts', async () => {
    const store = useAnalysisStore()
    await store.loadGraph(7, '一班', async () => graph)
    await store.loadGraph(7, '二班', async () => { throw new DOMException('aborted', 'AbortError') })
    expect(store.graph).toBeNull()
    expect(store.graphState).toBe('idle')
  })

  it('rejects a successful response for a different backend scope', async () => {
    const store = useAnalysisStore()
    await store.loadQuestions(7, '二班', async () => ({
      ...session7Questions,
      scope: { ...session7Questions.scope, class_name: '一班' },
    }))
    expect(store.questions).toEqual([])
    expect(store.questionsScope).toBeNull()
    expect(store.questionsState).toBe('error')
  })

  it('does not request graph data until a class is selected', async () => {
    const store = useAnalysisStore()
    const loader = vi.fn(async () => graph)
    await store.loadGraph(7, '   ', loader)
    expect(loader).not.toHaveBeenCalled()
    expect(store.graphState).toBe('idle')
    expect(store.graph).toBeNull()
  })

  it('aborts all in-flight resources and clears content on session switch', async () => {
    const store = useAnalysisStore()
    const pending = deferred<QuestionAnalysisResponse>()
    let signal: AbortSignal | undefined
    const load = store.loadQuestions(7, null, (_sessionId, _className, nextSignal) => {
      signal = nextSignal
      return pending.promise
    })
    store.resetForSession(8)
    expect(signal?.aborted).toBe(true)
    expect(store.questions).toEqual([])
    expect(store.students).toEqual([])
    expect(store.graph).toBeNull()
    pending.resolve(session7Questions)
    await load
    expect(store.sessionId).toBe(8)
    expect(store.questions).toEqual([])
  })

  it('never uses localStorage', async () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem')
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    await useAnalysisStore().loadQuestions(7, null, async () => session7Questions)
    expect(getItem).not.toHaveBeenCalled()
    expect(setItem).not.toHaveBeenCalled()
  })
})
