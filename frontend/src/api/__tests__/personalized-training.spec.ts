import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  decodePersonalizedRecommendationDraft,
  trainingApi,
} from '../training'

const draftPayload = {
  draft_id: 'd'.repeat(64),
  status: 'draft',
  revision: 1,
  result_version: 'a'.repeat(64),
  engine_version: 'personalized-recommendation-v1',
  source_version: 'b'.repeat(64),
  config: {
    question_count: 8,
    expected_minutes: 45,
  },
  students: [{
    student_id: 'SYN-S01',
    student_code: 'S01',
    student_name: '合成学生',
    class_id: 'SYN-C01',
    selection_mode: 'mastery_targeted',
    targets: [{
      stable_key: 'kp_alg_linear_equation',
      display_name: '一元一次方程',
    }],
    items: [{
      item_id: 'item-1',
      item_order: 1,
      slot: 1,
      question_id: 31,
      question_number: '3',
      stage: 'direct',
      target: {
        stable_key: 'kp_alg_linear_equation',
      },
      matched_key: 'kp_alg_linear_equation',
      matched_name: '一元一次方程',
      relation: null,
      criterion_version_id: 'c'.repeat(64),
      criterion_point_count: 3,
      difficulty: 5,
      estimated_minutes: 6,
      source_paper: '合成题源',
      reason: '直接巩固一元一次方程。',
      locked: false,
      replacement_history: [],
    }],
    shortages: [],
    warnings: [],
    estimated_minutes: 6,
  }],
  warnings: [],
  history: [{
    action: 'created',
    revision: 1,
  }],
} as const

afterEach(() => {
  vi.restoreAllMocks()
})

describe('personalized training API', () => {
  it('decodes auditable recommendation items and rejects storage fields', () => {
    const decoded = decodePersonalizedRecommendationDraft(draftPayload)

    expect(decoded.students[0]?.items[0]).toMatchObject({
      question_id: 31,
      criterion_point_count: 3,
      matched_key: 'kp_alg_linear_equation',
    })
    expect(() => decodePersonalizedRecommendationDraft({
      ...draftPayload,
      output_path: 'C:\\private\\draft.json',
    })).toThrow('Path-like response key')
  })

  it('posts create and teacher edit commands to isolated draft endpoints', async () => {
    const updated = {
      ...draftPayload,
      revision: 2,
      students: [{
        ...draftPayload.students[0],
        items: [{
          ...draftPayload.students[0].items[0],
          locked: true,
        }],
      }],
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify(draftPayload), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(updated), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }))

    await trainingApi.createPersonalizedDraft({
      request_token: '1'.repeat(32),
      scope: { mode: 'student', student_ids: ['SYN-S01'] },
      exam_scope: { mode: 'current', session_ids: [7] },
      question_count: 8,
      expected_minutes: 45,
      difficulty_min: 2,
      difficulty_max: 8,
      stage_ratios: {
        direct: 0.6,
        prerequisite: 0.3,
        transfer: 0.1,
      },
      target_names: ['一元一次方程'],
      exclude_current_exam_originals: true,
    })
    await trainingApi.editPersonalizedDraft(draftPayload.draft_id, {
      request_token: '2'.repeat(32),
      expected_revision: 1,
      action: 'lock',
      student_id: 'SYN-S01',
      item_id: 'item-1',
      reason: '保留课堂讲过的题型',
    })

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/training/personalized-drafts',
      `/api/training/personalized-drafts/${draftPayload.draft_id}/edits`,
    ])
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toMatchObject({
      target_names: ['一元一次方程'],
      expected_minutes: 45,
    })
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toMatchObject({
      expected_revision: 1,
      action: 'lock',
      reason: '保留课堂讲过的题型',
    })
  })
})
