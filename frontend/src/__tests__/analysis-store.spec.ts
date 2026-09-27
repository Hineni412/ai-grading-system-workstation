import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { QuestionAnalysisResponse } from '../api/analysis';

import { useAnalysisStore } from '../stores/analysis';

const questionItem = {
  class_name: '一班', question_id: 'Q1', max_score: 10, score_rate: 80,
  average_score: 8, deduction_count: 1, attempt_count: 2, metric_status: 'ready' as const,
}
const session7Questions: QuestionAnalysisResponse = {
  scope: { session_id: 7, class_name: null, question_id: null }, classes: ['一班'],
  items: [questionItem], total: 1, page: 1, page_size: 100, total_pages: 1,
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

})
