import { afterEach, describe, expect, it, vi } from 'vitest'

import { trainingApi, decodePersonalizedRecommendationDraft, decodeTrainingDiagnosis, decodeTrainingDisplayDiagnosis } from '../api/training'
import { createPinia, setActivePinia } from 'pinia'
import { useTrainingStore } from '../stores/training'
import { assertNoPathLikeKeys } from '../api/validation'
import { previewWrongQuestionBooks } from '../api/students'

it('uses the batch wrong-book preview contract and rejects incomplete counts', async () => {
  const data = { students: [], sessions: [], session_ids: [], semester_label: 'TEST', question_count: 0,
    missing_items: [], session_wrong_counts: {}, out_of_scope_count: 0, empty_students: [] }
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(data), { status: 200, headers: { 'content-type': 'application/json' } }))
  const body = { curriculum_volume_id: 'TEST', student_ids: [12, 22], scope_keys: ['kp_c1'], session_ids: [7] }
  expect(await previewWrongQuestionBooks(body)).toEqual(data)
  const [url, options] = fetch.mock.calls[0]!
  expect(String(url)).toContain('/api/students/wrong-question-books/preview')
  expect(JSON.parse(String(options?.body))).toEqual(body)
  fetch.mockResolvedValue(new Response(JSON.stringify({ ...data, out_of_scope_count: -1 }), { status: 200, headers: { 'content-type': 'application/json' } }))
  await expect(previewWrongQuestionBooks(body)).rejects.toThrow()
})

const submissionId = 'c'.repeat(64)
it('decodes fixed class assembly provenance and evidence-free members', () => {
  const fixed = {draft_id:'a'.repeat(64),status:'draft',revision:1,result_version:'b'.repeat(64),
    engine_version:'synthetic',source_version:'c'.repeat(64),config:{paper_mode:'shared',assembly_source:{source:'班级组卷 · 全班',revision:'d'.repeat(64)}},
    students:[{student_id:'TEST-NO-EVIDENCE',student_code:'',student_name:'',class_id:'合成班',selection_mode:'teacher_fixed_class',
      targets:[],items:[],shortages:[],warnings:[],estimated_minutes:0}],warnings:[],history:[]}
  expect(decodePersonalizedRecommendationDraft(fixed)).toEqual(fixed)
  const withoutVersions = Object.fromEntries(Object.entries(fixed)
    .filter(([name]) => !name.endsWith('_version')))
  expect(decodePersonalizedRecommendationDraft({ ...withoutVersions,
    generated_at: '2026-10-09T10:00:00+08:00', needs_update: true })).toEqual({ ...withoutVersions,
    generated_at: '2026-10-09T10:00:00+08:00', needs_update: true })

  expect(()=>decodePersonalizedRecommendationDraft({...fixed,students:[{...fixed.students[0],selection_mode:'unknown'}]})).toThrow()
})
const payload = {
  run_id: 'a'.repeat(64),
  submission_id: submissionId,
  submission_revision: 1,
  status: 'succeeded',
  request_count: 1,
  expected_question_count: 1,
  expected_point_count: 1,
  model_name: 'fake-training-assessment-v1',
  usage: {
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
  },
  latency_ms: 0,
  issue_codes: [],
  error_code: null,
  questions: [{
    task_item_code: 'P4-SYN-Q01',
    item_order: 1,
    status: 'candidate',
    review_status: 'review_required',
    met_count: 0,
    not_met_count: 0,
    uncertain_count: 1,
    unreadable_count: 0,
    total_count: 1,
    issue_codes: [],
    points: [{
      point_id: 'p-process',
      state: 'uncertain',
      evidence: '合成答卷字迹需教师确认',
    }],
    review_points: [{
      point_id: 'p-process',
      expected_point: {
        point_id: 'p-process',
        target: '写出关键步骤',
        observable_evidence: '答卷中存在可连续核对的关键步骤',
        equivalent_rules: [],
        counterexamples: [],
      },
      candidate_state: 'uncertain',
      state: 'uncertain',
      evidence: '合成答卷字迹需教师确认',
      teacher_locked: false,
      teacher_reason: null,
      actor_ref: null,
      lock_revision: null,
    }],
  }],
  review_revision: 1,
  control_state: 'active',
  workflow_status: 'partial_review',
  action_message: '请只复核仍标记待复核的判定点。',
  attempts: [{
    attempt_number: 1,
    status: 'succeeded',
    request_count: 1,
    error_code: null,
    model_name: 'fake-training-assessment-v1',
  }],
}

function stubAssessmentResponse(value: unknown): void {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(
    JSON.stringify(value),
    {
      status: 200,
      headers: { 'content-type': 'application/json' },
    },
  )))
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('training group diagnosis api', () => {
  it('decodes explicit display data into the store, retains unobserved items, and rejects mixed contracts', async () => {
    const weak = { knowledge_key: 'TEST-type', knowledge_point: 'TEST题型', mastery: .4,
      tier: 'weak', observation_count: 1, evidence_count: 0, source_reference_count: 2,
      parent_knowledge_key: 'TEST-section' }
    const display = { response_mode: 'display', diagnosis_identity: 'question_tag', target_kind: 'type',
      scope: { mode: 'class', class_ids: ['TEST班'], student_ids: ['TEST-student'] },
      exam_scope: { mode: 'semester', session_ids: [1], sessions: [{ session_id: 1, session_name: 'TEST考试' }] },
      students: [{ student_id: 'TEST-student', student_name: 'TEST学生', student_code: 'TEST-1', class_id: 'TEST班',
        weak_points: [weak, { ...weak, knowledge_key: 'TEST-unobserved', mastery: null,
          tier: 'insufficient', observation_count: 0, source_reference_count: 0 }] }],
      group_weak_points: [], knowledge_catalog: [{ knowledge_key: 'TEST-type', knowledge_point: 'TEST题型',
        node_kind: 'type', parent_knowledge_key: 'TEST-section' }],
      coverage: { covered_items: 1, total_items: 1, missing_items: {} },
      confirmed_concept_ids: [], suggested_terms: [], unmapped_terms: [], warnings: [],
    }
    const fetch = vi.fn<(url: RequestInfo | URL, options?: RequestInit) => Promise<Response>>(async () => new Response(JSON.stringify(display), {
      status: 200, headers: { 'content-type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetch)
    setActivePinia(createPinia())
    const store = useTrainingStore()
    store.setStudentScope({ mode: 'class', classId: 'TEST班', studentIds: [] })
    store.setExamScope({ mode: 'semester', sessionIds: [], curriculumVolumeId: 'TEST-volume' })
    await store.analyze()
    expect(store.diagnosis).toEqual(display)
    expect(store.analysisState).toBe('ready')
    expect(store.diagnosis?.students[0]?.weak_points).toHaveLength(2)
    expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))).toMatchObject({ response_mode: 'display_shared' })
    expect(() => decodeTrainingDiagnosis(display)).toThrow('Invalid training diagnosis')
    expect(() => decodeTrainingDisplayDiagnosis({ ...display, response_mode: 'full' })).toThrow()
    expect(() => decodeTrainingDisplayDiagnosis({ ...display, include_student_detail: false })).toThrow()
    for (const point of [{ ...weak, source_reference_count: -1 }, { ...weak, source_reference_count: 1.5 },
      { ...weak, source_question_refs: [] }, { ...weak, observation_count: -1 }]) {
      expect(() => decodeTrainingDisplayDiagnosis({ ...display,
        students: [{ ...display.students[0], weak_points: [point] }] })).toThrow()
    }
    expect(() => decodeTrainingDisplayDiagnosis({ ...display, grouping: { TEST: { local_path: 'secret' } } })).toThrow()
    expect(() => decodeTrainingDisplayDiagnosis({ ...display, knowledge_catalog: [{ knowledge_key: 'TEST-type',
      knowledge_point: 'TEST题型', node_kind: 'unknown' }] })).toThrow()
  })

  it('expands shared static metadata with exact dynamic values and independent point objects', () => {
    const weak = { knowledge_key: 'TEST-type', knowledge_point: 'TEST题型', mastery: .4,
      tier: 'weak', observation_count: 2, evidence_count: 1, parent_knowledge_key: 'TEST-section',
      source_reference_count: 2 }
    const unobserved = { knowledge_key: 'TEST-unobserved', knowledge_point: 'TEST未测项',
      tier: 'insufficient', observation_count: 0, evidence_count: 0, source_reference_count: 0 }
    const staticPoint = { knowledge_key: weak.knowledge_key, knowledge_point: weak.knowledge_point,
      parent_knowledge_key: weak.parent_knowledge_key }
    const catalog = [staticPoint, { knowledge_key: unobserved.knowledge_key, knowledge_point: unobserved.knowledge_point },
      { ...staticPoint, parent_knowledge_key: 'TEST-other-parent' }, { ...staticPoint, knowledge_point: 'TEST-other-label' }]
    const first = { student_id: 'TEST-first', student_name: 'TEST甲', student_code: 'TEST-1', class_id: 'TEST班' }
    const second = { ...first, student_id: 'TEST-second', student_name: 'TEST乙' }
    const metadata = {
      diagnosis_identity: 'question_tag', target_kind: 'type',
      scope: { mode: 'class', class_ids: ['TEST班'], student_ids: ['TEST-first', 'TEST-second'] },
      exam_scope: { mode: 'semester', session_ids: [1], sessions: [{ session_id: 1, session_name: 'TEST考试' }] },
      knowledge_catalog: [{ knowledge_key: 'TEST-type', knowledge_point: 'TEST题型', node_kind: 'type' }],
      coverage: { covered_items: 1, total_items: 1, missing_items: {} },
      confirmed_concept_ids: [], suggested_terms: [], unmapped_terms: [], warnings: [],
    }
    const ordinaryValues = [0, .4, 'weak', 2, 1, 2]
    const unobservedValues = [1, null, 'insufficient', 0, 0, 0]
    const wire = { ...metadata, response_mode: 'display_shared', weak_point_catalog: catalog,
      students: [{ ...first, weak_points: [ordinaryValues, unobservedValues, ordinaryValues,
        [0, .7, 'weak', 2, 1, 2], [0, .4, 'weak', 3, 1, 2], [2, .4, 'weak', 2, 1, 2], [3, .4, 'weak', 2, 1, 2]] },
      { ...second, weak_points: [unobservedValues, [0, .8, 'stable', 8, 7, 6]] }],
      group_weak_points: [[0, .4, 'weak', 3, 1, 2], ordinaryValues, unobservedValues] }
    const display = { ...metadata, response_mode: 'display',
      students: [{ ...first, weak_points: [weak, unobserved, weak, { ...weak, mastery: .7 },
        { ...weak, observation_count: 3 }, { ...weak, parent_knowledge_key: 'TEST-other-parent' },
        { ...weak, knowledge_point: 'TEST-other-label' }] },
      { ...second, weak_points: [unobserved, { ...weak, mastery: .8, tier: 'stable', observation_count: 8,
        evidence_count: 7, source_reference_count: 6 }] }],
      group_weak_points: [{ ...weak, observation_count: 3 }, weak, unobserved] }
    const decoded = decodeTrainingDisplayDiagnosis(wire)
    expect(decoded).toEqual(display)
    expect(JSON.stringify(decoded)).toBe(JSON.stringify(display))
    expect(decodeTrainingDisplayDiagnosis(display)).toEqual(decoded)
    expect(decoded.students[0]?.weak_points[1]).not.toHaveProperty('mastery')
    expect(() => decodeTrainingDiagnosis(wire)).toThrow('Invalid training diagnosis')
    expect(decoded.students[0]?.weak_points[0]).not.toBe(decoded.students[0]?.weak_points[2])
    expect(decoded.students[0]?.weak_points[0]).not.toBe(decoded.group_weak_points[1])
    const firstPoint = decoded.students[0]?.weak_points[0]
    if (firstPoint === undefined) throw new Error('TEST expected the first displayed point')
    firstPoint.mastery = .9
    firstPoint.knowledge_point = 'TEST-mutation'
    expect(decoded.students[0]?.weak_points[2]?.mastery).toBe(.4)
    expect(decoded.students[0]?.weak_points[2]?.knowledge_point).toBe('TEST题型')
    expect(decoded.students[1]?.weak_points[1]?.mastery).toBe(.8)
    expect(decoded.group_weak_points[1]?.mastery).toBe(.4)
    expect(weak.mastery).toBe(.4)
    expect(staticPoint.knowledge_point).toBe('TEST题型')
    expect(() => decodeTrainingDisplayDiagnosis({ ...display, weak_point_catalog: catalog })).toThrow()
    expect(() => decodeTrainingDisplayDiagnosis({ ...wire, include_student_detail: false })).toThrow()
    const invalidPoints: unknown[] = [-1, 1.5, true, '0', 4, Number.NaN, Number.POSITIVE_INFINITY]
      .map(index => [index, .4, 'weak', 2, 1, 2])
    invalidPoints.push(...[Number.NaN, Number.POSITIVE_INFINITY, 'NaN', 'Infinity', '0.4', -.1, 1.1, true, undefined]
      .map(mastery => [0, mastery, 'weak', 2, 1, 2]))
    invalidPoints.push([0, .4, 'unknown', 2, 1, 2], [0, .4, 'weak', 2, 1], [0, .4, 'weak', 2, 1, 2, 0], 0)
    for (const position of [3, 4, 5]) {
      for (const value of [-1, 1.5, true, '1', Number.NaN, Number.POSITIVE_INFINITY]) {
        const point: unknown[] = [0, .4, 'weak', 2, 1, 2]
        point[position] = value
        invalidPoints.push(point)
      }
    }
    for (const point of invalidPoints) {
      expect(() => decodeTrainingDisplayDiagnosis({ ...wire,
        students: [{ ...first, weak_points: [point] }] })).toThrow()
      expect(() => decodeTrainingDisplayDiagnosis({ ...wire, group_weak_points: [point] })).toThrow()
    }
    for (const point of [{ ...staticPoint, knowledge_key: '' }, { ...staticPoint, knowledge_point: 123 },
      { ...staticPoint, parent_knowledge_key: 123 }, { ...staticPoint, mastery: .4 },
      { ...staticPoint, source_question_refs: [] }, { ...staticPoint, source_reference_count: 2 }]) {
      expect(() => decodeTrainingDisplayDiagnosis({ ...wire, weak_point_catalog: [point] })).toThrow()
    }
    expect(() => decodeTrainingDisplayDiagnosis({ ...wire,
      grouping: { TEST: { nested: [{ local_path: 'C:/TEST-private' }] } } })).toThrow('Path-like response key')
    expect(() => decodeTrainingDisplayDiagnosis({ ...wire,
      weak_point_catalog: [{ ...staticPoint, TEST: { local_path: 'C:/TEST-private' } }] })).toThrow('Path-like response key')
    const empty = { ...wire, weak_point_catalog: [], students: [{ ...first, weak_points: [] }], group_weak_points: [] }
    expect(decodeTrainingDisplayDiagnosis(empty)).toEqual({ ...metadata, response_mode: 'display',
      students: [{ ...first, weak_points: [] }], group_weak_points: [] })
  })

  it('preserves summary statistics and evidence while distinguishing detailed responses', async () => {
    const point = { knowledge_key: 'TEST-type', knowledge_point: 'TEST知识项', mastery: .4,
      tier: 'weak', observation_count: 2, full_correct_count: 0, evidence_count: 2,
      score_sum: 4, full_score_sum: 10, deduction_count: 2, effective_weight: 2, exam_count: 1,
      source_question_refs: [{ session_id: 1, session_name: 'TEST考试', question_id: 'Q1',
        bank_question_id: 1, score_awarded: 4, full_score: 10 }], parent_knowledge_key: 'TEST-section' }
    const diagnosis = { include_student_detail: false, diagnosis_identity: 'question_tag',
      scope: { mode: 'all', student_ids: [] },
      exam_scope: { mode: 'semester', session_ids: [], sessions: [] },
      students: [{ student_id: 'TEST-student', student_name: 'TEST学生', student_code: 'TEST-1',
        class_id: 'TEST班', score_rate: .4, score_rate_source: 'current_exam', weak_points: [point] }],
      group_weak_points: [point], knowledge_catalog: [{ knowledge_key: 'TEST-type', node_kind: 'type' }],
      coverage: { covered_items: 1, total_items: 1, missing_items: {} },
      confirmed_concept_ids: [], suggested_terms: [], unmapped_terms: [], warnings: [],
      grouping: { groups: [], selection: null, unassigned: [], warnings: [] } }
    const fetch = vi.fn<(url: RequestInfo | URL, options?: RequestInit) => Promise<Response>>(async () => new Response(JSON.stringify(diagnosis), {
      status: 200, headers: { 'content-type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetch)
    const body = { scope: { mode: 'all' as const, student_ids: [] },
      exam_scope: { mode: 'semester' as const, session_ids: [] }, include_student_detail: false }
    expect(await trainingApi.diagnose(body)).toEqual(diagnosis)
    expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))).toEqual(body)
    const { include_student_detail: _marker, ...unmarked } = diagnosis
    expect(_marker).toBe(false)
    expect(() => decodeTrainingDiagnosis(unmarked)).toThrow('Invalid training diagnosis')
    const detailed = { ...point, actionable_reasons: ['TEST说明'], tag_context: {}, error_counts: {} }
    expect(decodeTrainingDiagnosis({ ...unmarked, students: [{ ...diagnosis.students[0], weak_points: [detailed] }],
      group_weak_points: [detailed] }).students[0]?.weak_points[0]?.source_question_refs).toEqual(point.source_question_refs)
    expect(() => decodeTrainingDiagnosis({ ...diagnosis, students: [{ ...diagnosis.students[0],
      weak_points: [{ ...point, observation_count: -1 }] }] })).toThrow()
    expect(() => decodeTrainingDiagnosis({ ...diagnosis, include_student_detail: 'false' })).toThrow()
  })

  it('accepts a group result after the former 30 second cutoff', async () => {
    vi.useFakeTimers()
    const diagnosis = {
      diagnosis_identity: 'question_tag', scope: { mode: 'all', student_ids: [] },
      exam_scope: { mode: 'semester', session_ids: [], sessions: [] }, students: [],
      coverage: { covered_items: 0, total_items: 0, missing_items: {} },
      confirmed_concept_ids: [], suggested_terms: [], unmapped_terms: [], warnings: [],
      grouping: { groups: [], selection: null, unassigned: [], warnings: [] },
    }
    vi.stubGlobal('fetch', vi.fn((_url, options: RequestInit) => new Promise<Response>((resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      setTimeout(() => resolve(new Response(JSON.stringify(diagnosis), {
        status: 200, headers: { 'content-type': 'application/json' },
      })), 40_000)
    })))
    const outcome = trainingApi.diagnose({ scope: { mode: 'all', student_ids: [] },
      exam_scope: { mode: 'semester', session_ids: [] }, grouping: { scope_keys: ['chapter'],
        question_count: 10, difficulty_max: 8, exclude_current_exam_originals: true, curriculum_volume_id: '' } })
    await Promise.all([
      expect(outcome).resolves.toMatchObject({ grouping: diagnosis.grouping }),
      vi.advanceTimersByTimeAsync(40_000),
    ])
  })
})

describe('training assessment api', () => {

  it('rejects inconsistent point identities and teacher-lock metadata', async () => {
    const repeated = Array.from({ length: 1000 }, () => ({
      assessment: { point_id: 'TEST', evidence: '合成证据', nested: [{ safe_filename: 'TEST.txt',
        file_status: 'ready', file_count: 1, directory_evidence: [] }] },
    }))
    expect(() => assertNoPathLikeKeys(repeated)).not.toThrow()
    for (const key of ['sourcePath', 'HTTPRoot', 'nested_file', 'x-dir', 'ROOT']) {
      expect(() => assertNoPathLikeKeys([...repeated, { assessment: { [key]: 'TEST' } }])).toThrow('Path-like response key')
    }
    // Rechecking a response must still inspect new nested data under a key
    // accepted in a previous call.
    expect(() => assertNoPathLikeKeys({ assessment: { point_id: 'TEST' } })).not.toThrow()
    expect(() => assertNoPathLikeKeys({ assessment: { point_id: { sourcePath: 'TEST' } } })).toThrow()
    const question = payload.questions[0]!
    const point = question.review_points[0]!
    stubAssessmentResponse({
      ...payload,
      questions: [{
        ...question,
        review_points: [{
          ...point,
          expected_point: {
            ...point.expected_point,
            point_id: 'p-other',
          },
        }],
      }],
    })
    await expect(
      trainingApi.startTrainingAssessment(submissionId, 1),
    ).rejects.toMatchObject({ code: 'invalid_success_contract' })

    stubAssessmentResponse({
      ...payload,
      questions: [{
        ...question,
        review_points: [{
          ...point,
          teacher_locked: true,
          lock_revision: null,
        }],
      }],
    })
    await expect(
      trainingApi.startTrainingAssessment(submissionId, 1),
    ).rejects.toMatchObject({ code: 'invalid_success_contract' })
  })
})


it('reads saved feedback by its shape with or without an older schema label', async () => {
  const feedback = { feedback_id: 'a'.repeat(64), submission_id: submissionId,
    submission_revision: 1, source_review_revision: 1, status: 'complete', student: {},
    summary: { published_question_count: 1, ready_question_count: 1, total_question_count: 1,
      pending_outbox_count: 0, message: 'TEST-saved feedback' },
    questions: [], mastery_changes: [], next_round: { status: 'ready', message: 'TEST', changes: [] },
    timeline: [], safety: { is_exam_score: false, changes_exam_score: false,
      auto_paper_created: false, auto_printed: false }, evidence_version: 'b'.repeat(64) }
  const fetch = vi.spyOn(globalThis, 'fetch')
  for (const value of [feedback, { ...feedback, schema_version: 'TEST-older-format' }]) {
    fetch.mockResolvedValue(new Response(JSON.stringify(value), { status: 200,
      headers: { 'content-type': 'application/json' } }))
    expect(await trainingApi.getTrainingFeedback(submissionId, 1)).toEqual(value)
  }
})
