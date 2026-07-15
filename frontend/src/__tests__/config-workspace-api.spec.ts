import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fetchConfigEditor,
  fetchConfigSource,
  refineConfigEditor,
  saveConfigEditor,
  submitConfigGeneration,
  uploadConfigSource,
} from '../api/config-workspace'
import { createSessionDraft, renameSession } from '../api/sessions'

const session = {
  id: 7,
  name: '七年级数学',
  status: 'created',
  is_deleted: false,
  deleted_at: null,
  created_at: null,
  updated_at: null,
}

const source = {
  session_id: 7,
  source_id: 'a'.repeat(32),
  source_revision: 'b'.repeat(64),
  safe_filename: '数学卷.pdf',
  suffix: '.pdf',
  size_bytes: 9,
  sha256_prefix: 'c'.repeat(12),
  parse_state: 'ready',
  questions: [
    {
      question_id: 'Q1',
      question_type: 'calculation',
      question_preview: '计算题摘要',
      answer_preview: '答案摘要',
      answer_present: true,
      needs_review: false,
      local_answer_trusted: true,
      has_question_asset: false,
      has_answer_asset: false,
    },
  ],
}

const editor = {
  session_id: 7,
  configured: true,
  revision: 'd'.repeat(64),
  rows: [
    {
      row_id: 'row-1',
      question_id: 'Q1',
      part_id: 'Q1',
      step_id: 'step-1',
      part_label: '第 1 题',
      question_type: 'calculation',
      core_goal: '列式计算',
      score: 5,
      standard_answer: '42',
      accepted_answers: ['42'],
      match_rule: 'exact',
      knowledge: '整数运算',
      answer_only_max_score: null,
      require_final_answer: true,
      required_elements: ['列式'],
      deduction_rules: ['结果错误扣 1 分'],
      final_answer_rule: '答案完整',
    },
  ],
  total_score: 100,
  issues: [],
  source: {
    safe_filename: '数学卷.pdf',
    suffix: '.pdf',
    sha256_prefix: 'c'.repeat(12),
  },
}

const job = {
  id: 31,
  job_type: 'config_generation',
  payload: {},
  result: {},
  status: 'queued',
  progress: 0,
  stage: 'queued',
  detail: '',
  error: null,
  cancel_requested: false,
  created_at: '2026-07-15T00:00:00Z',
  started_at: null,
  updated_at: '2026-07-15T00:00:00Z',
  finished_at: null,
}

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

beforeEach(() => vi.restoreAllMocks())

describe('configuration workspace API', () => {
  it('sends a File once as octet-stream without JSON encoding', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(source, 201))
    const file = new File(['%PDF-test'], '数学卷.pdf', { type: 'application/pdf' })

    await expect(uploadConfigSource(7, file)).resolves.toEqual(source)
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      '/api/sessions/7/config/sources',
      expect.objectContaining({
        method: 'POST',
        body: file,
        headers: expect.objectContaining({
          accept: 'application/json',
          'content-type': 'application/octet-stream',
          'x-request-id': expect.any(String),
          'x-upload-filename': encodeURIComponent('数学卷.pdf'),
        }),
      }),
    )
  })

  it('never retries a raw upload write failure', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('offline'))
    await expect(uploadConfigSource(7, new File(['x'], 'a.pdf'))).rejects.toMatchObject({
      kind: 'network',
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('strictly decodes every public workspace response and sends bounded DTOs', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response(session, 201))
      .mockResolvedValueOnce(response({ ...session, name: '新名称' }))
      .mockResolvedValueOnce(response(source))
      .mockResolvedValueOnce(response(job, 202))
      .mockResolvedValueOnce(response(editor))
      .mockResolvedValueOnce(response({
        ...editor,
        save_result: {
          config_saved: true,
          mapping_status: 'not_present',
          mapping_message: '没有样卷映射。',
        },
      }))
      .mockResolvedValueOnce(response(job, 202))

    await expect(createSessionDraft(' 七年级数学 ')).resolves.toEqual(session)
    await expect(renameSession(7, ' 新名称 ')).resolves.toMatchObject({ name: '新名称' })
    await expect(fetchConfigSource(7, source.source_id)).resolves.toEqual(source)
    await expect(submitConfigGeneration(7, {
      source_id: source.source_id,
      source_revision: source.source_revision,
      generation_mode: 'per_question',
      decisions: [{ question_id: 'Q1', question_type: 'calculation', excluded: false }],
    })).resolves.toEqual(job)
    await expect(fetchConfigEditor(7)).resolves.toEqual(editor)
    await expect(saveConfigEditor(7, {
      revision: editor.revision,
      edits: [{ row_id: 'row-1', standard_answer: '43' }],
      commands: [],
    })).resolves.toMatchObject({ save_result: { config_saved: true } })
    await expect(refineConfigEditor(7, {
      revision: editor.revision,
      commands: [],
    })).resolves.toEqual(job)

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/sessions/drafts',
      '/api/sessions/7',
      `/api/sessions/7/config/sources/${source.source_id}`,
      '/api/sessions/7/config/generate-from-source',
      '/api/sessions/7/config/editor',
      '/api/sessions/7/config/editor',
      '/api/sessions/7/config/editor/refine',
    ])
  })

  it.each([
    ['path-like response keys', { ...source, source_path: 'D:/private/source.pdf' }],
    ['malformed nested rows', { ...editor, rows: [{ ...editor.rows[0], score: '5' }] }],
    ['unknown editor fields', { ...editor, internal_note: 'private' }],
  ])('rejects %s as an invalid success contract', async (_case, payload) => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(payload))
    const request = 'rows' in payload || 'internal_note' in payload
      ? fetchConfigEditor(7)
      : fetchConfigSource(7, source.source_id)
    await expect(request).rejects.toMatchObject({
      kind: 'contract',
      code: 'invalid_success_contract',
    })
  })
})
