import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { TrainingDiagnosis } from '../api/training'
import { useTrainingStore } from '../stores/training'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

const diagnosis = {
  scope: { mode: 'selected', student_ids: ['12', '13'] },
  exam_scope: {
    mode: 'manual',
    session_ids: [14],
    sessions: [{ session_id: 14, session_name: '七年级阶段测验' }],
  },
  students: [{
    student_id: '12',
    student_code: '20260012',
    student_name: '张同学',
    class_id: '七年级一班',
    score_rate: 55,
    weak_points: [{
      knowledge_key: 'knowledge_point:三角形全等',
      knowledge_point: '三角形全等',
      mastery: 0.55,
      score_sum: 11,
      full_score_sum: 20,
      deduction_count: 2,
      evidence_count: 2,
      exam_count: 1,
      source_question_refs: [],
      actionable_reasons: ['辅助线思路缺失'],
      tag_context: { knowledge_point: ['三角形全等'] },
      error_counts: { primary: { 逻辑断裂: 1 } },
    }],
  }],
  coverage: {
    covered_items: 1,
    total_items: 2,
    missing_items: { Q2: '题目尚未关联题库来源' },
  },
  confirmed_concept_ids: [],
  suggested_terms: ['三角形全等'],
  unmapped_terms: [],
  warnings: [],
  diagnosis_identity: 'question_tag',
} satisfies TrainingDiagnosis

function makeTrainingApi(overrides: Record<string, unknown> = {}) {
  return {
    diagnose: vi.fn(async () => diagnosis),
    ...overrides,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('training store', () => {
  it('reuses chapter previews by evidence content until the source, settings or workspace changes', () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000)
    const store = useTrainingStore()
    store.rememberGroupDiagnosis('chapter-a', diagnosis, diagnosis)
    expect(store.cachedGroupDiagnosis('chapter-a', diagnosis)).toEqual(diagnosis)
    expect(store.cachedGroupDiagnosis('chapter-b', diagnosis)).toBeNull()
    expect(store.cachedGroupDiagnosis('chapter-a', { ...diagnosis })).toEqual(diagnosis)
    const changed = structuredClone(diagnosis)
    changed.students[0]!.weak_points[0]!.score_sum = 12
    expect(store.cachedGroupDiagnosis('chapter-a', changed)).toBeNull()
    now.mockReturnValue(121_000)
    expect(store.cachedGroupDiagnosis('chapter-a', diagnosis)).toEqual(diagnosis)
    store.rememberGroupDiagnosis('chapter-a', diagnosis, diagnosis)
    store.reset()
    expect(store.cachedGroupDiagnosis('chapter-a', diagnosis)).toBeNull()
  })

  it('normalizes explicit scope and ignores a diagnosis from the old selection', async () => {
    const first = deferred<TrainingDiagnosis>()
    const second = deferred<TrainingDiagnosis>()
    const api = makeTrainingApi({
      diagnose: vi.fn()
        .mockReturnValueOnce(first.promise)
        .mockReturnValueOnce(second.promise),
    })
    const store = useTrainingStore()

    store.setStudentScope({
      mode: 'selected',
      studentIds: ['13', '12', '12'],
      classId: '',
    })
    store.setExamScope({
      mode: 'manual',
      sessionIds: [14, 14],
    })
    const oldRequest = store.analyze(api)

    store.setStudentScope({
      mode: 'student',
      studentIds: ['12'],
      classId: '',
    })
    const currentRequest = store.analyze(api)
    first.resolve(diagnosis)
    await oldRequest

    expect(store.diagnosis).toBeNull()
    expect(store.hasCurrentDiagnosis).toBe(false)
    second.resolve({
      ...diagnosis,
      scope: { mode: 'student', student_ids: ['12'] },
    })
    await currentRequest

    expect(api.diagnose).toHaveBeenNthCalledWith(1, {
      scope: { mode: 'selected', student_ids: ['12', '13'] },
      exam_scope: { mode: 'manual', session_ids: [14] },
    }, expect.any(AbortSignal))
    expect(store.diagnosis?.scope).toEqual({
      mode: 'student',
      student_ids: ['12'],
    })
  })
})
