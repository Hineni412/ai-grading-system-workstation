import { afterEach, describe, expect, it, vi } from 'vitest'

import { homeIntakeApi } from '../api/homeIntake'
import { sopApi } from '../api/sop'
import { supportApi } from '../api/support'

function response(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'content-type': 'application/json', 'x-request-id': 'test-request' },
  })
}

function previewPayload() {
  return {
    preview_id: 'preview-1', route: 'sensitive', recommended_route: 'student_support',
    date_interpretation: { status: 'pending', source: 'not_provided', resolved_date: null, selected_date: null, candidates: [], pending_reason: null },
    emergency_guidance: null, round_number: 1, prior_operations: [], round_physical_request_count: 0,
    cumulative_physical_request_count: 0, physical_request_count: 0, dispatch_ready: true, local_only: false,
    blocked_categories: [], removed_categories: ['student_identity'], student_aliases: ['学生A'],
    exact_payload: { task_text: '学生A需要支持' }, fingerprint: 'f'.repeat(64), expires_at: null,
    model_provider: 'fake', model_endpoint: null, model_name: 'fake', destination_fingerprint: 'd'.repeat(64),
    model_enabled: true, max_physical_requests: 1, estimated_cost: null, source_text: '原文', final_due_date: null, date_semantics: 'date-only',
  }
}

afterEach(() => vi.unstubAllGlobals())

describe('home intake API contract', () => {
  it('sends no selected date and decodes the exact sensitive preview', async () => {
    const fetch = vi.fn().mockResolvedValue(response(previewPayload()))
    vi.stubGlobal('fetch', fetch)

    const value = await homeIntakeApi.preview('原文', 'vault-token', '2026-08-03')

    expect(value.student_aliases).toEqual(['学生A'])
    const [, init] = fetch.mock.calls[0] as [string, RequestInit]
    expect(init.headers).toMatchObject({ 'x-class-teacher-session': 'vault-token', 'x-class-teacher-client': 'class-teacher-browser-v1' })
    expect(JSON.parse(String(init.body))).toEqual({ text: '原文', due_date: null, reference_date: '2026-08-03' })
  })

  it('rejects unknown union values instead of casting them into the UI', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ ...previewPayload(), route: 'invented-route' })))

    await expect(homeIntakeApi.preview('原文')).rejects.toMatchObject({ code: 'invalid_success_contract', kind: 'contract' })
  })

  it('forwards caller-owned operation ids for recoverable handoff writes', async () => {
    const fetch = vi.fn().mockImplementation(() => Promise.resolve(response({})))
    vi.stubGlobal('fetch', fetch)

    await expect(sopApi.createAffair('token', {
      template_version_id: 'template-1', title: '事务', summary: '教师原文', participant_refs: ['学生A'],
    }, 'affair-operation')).rejects.toMatchObject({ code: 'invalid_success_contract', kind: 'contract' })
    await expect(sopApi.recordAiSuggestion('token', { affair_id: 'affair-1' } as never, 'AI 参考', 'ai-operation'))
      .rejects.toMatchObject({ code: 'invalid_success_contract', kind: 'contract' })
    await supportApi.createRecord('token', 'subject-1', {
      record_kind: 'teacher_observation', content: '教师原文', scene: '教室', source: '教师本人观察',
      basis: null, counterexample: null, category: 'general', observed_at: '2026-08-03', review_at: null, expires_at: null,
    }, 'record-operation')

    expect(JSON.parse(String((fetch.mock.calls[0]?.[1] as RequestInit).body)).operation_id).toBe('affair-operation')
    expect(JSON.parse(String((fetch.mock.calls[1]?.[1] as RequestInit).body))).toMatchObject({
      operation_id: 'ai-operation', decision_kind: 'ai_suggestion',
    })
    expect(JSON.parse(String((fetch.mock.calls[2]?.[1] as RequestInit).body)).operation_id).toBe('record-operation')
  })
})
