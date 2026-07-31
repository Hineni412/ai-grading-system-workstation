import { afterEach, describe, expect, it, vi } from 'vitest'

import { trainingApi } from '../api/training'

const submissionId = 'c'.repeat(64)
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
  vi.unstubAllGlobals()
})

describe('training assessment api', () => {
  it('accepts the review-point shape returned by a real assessment run', async () => {
    stubAssessmentResponse(payload)

    await expect(
      trainingApi.startTrainingAssessment(submissionId, 1),
    ).resolves.toMatchObject({
      status: 'succeeded',
      questions: [{
        review_points: [{
          content: '写出关键步骤',
          expected_point: { target: '写出关键步骤' },
          lock_revision: 0,
        }],
      }],
    })
  })

  it('rejects inconsistent point identities and teacher-lock metadata', async () => {
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
