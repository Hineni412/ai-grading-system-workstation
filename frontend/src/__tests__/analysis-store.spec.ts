import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { QuestionAnalysisResponse, StudentAnalysisResponse } from '../api/analysis'
import type { GraphRowsResponse } from '../api/graph'
import { useAnalysisStore } from '../stores/analysis'

const questionItem = {
  class_name: '一班', question_id: 'Q1', max_score: 10, score_rate: 0.8,
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
  items: [], total: 0, page: 1, page_size: 100, total_pages: 1,
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
