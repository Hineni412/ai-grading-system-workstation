import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  decodeOpsBackups,
  decodeOpsOperation,
  decodeOpsPreflight,
  decodeOpsSelfCheck,
  opsApi,
} from '../ops'

const selfCheck = {
  version: 'v1.5.0',
  status: 'warning',
  api_configured: false,
  directories: [
    { key: 'data', exists: true, writable: true, status: 'ok' },
  ],
  databases: [
    {
      key: 'grading',
      exists: true,
      size_bytes: 4096,
      integrity: 'ok',
      migration_version: '3',
      pending_migrations: 0,
      status: 'ok',
    },
  ],
  tools: [
    { key: 'microsoft_word', available: true, status: 'ok' },
    { key: 'libreoffice', available: false, status: 'warning' },
  ],
  warnings: ['tool_unavailable:libreoffice'],
} as const

const backups = {
  items: [
    {
      kind: 'zip',
      filename: 'backup_20260719_120000_manual.zip',
      created_at: '2026-07-19T12:00:00',
      reason: 'manual',
      size_bytes: 2048,
    },
    {
      kind: 'database',
      filename: 'grading_before_update_20260719.db',
      created_at: '2026-07-19T11:00:00',
      reason: 'database_automatic',
      size_bytes: 1024,
    },
  ],
  returned: 2,
  limit: 50,
} as const

const preflight = {
  operation: 'restore',
  confirmation_token: 'confirm-once',
  expires_at: '2026-07-19T12:05:00Z',
  requires_restart: true,
  summary: {
    file_count: 8,
    total_size_bytes: 4096,
    total_expanded_bytes: 8192,
    database_count: 2,
    skipped_count: 1,
    sensitive_skipped_count: 1,
    target: null,
    pending_migrations: null,
    applied_in_preview: null,
    integrity: 'ok',
    scope: null,
    warnings: ['current_data_will_be_backed_up'],
  },
} as const

const operation = {
  operation_id: '11111111-1111-4111-8111-111111111111',
  operation: 'restore',
  status: 'restart_required',
  result_code: 'prepared_restart_required',
  created_at: '2026-07-19T12:00:00',
  updated_at: '2026-07-19T12:01:00',
  recovery: {
    code: 'restart_required',
    backup_filename: 'backup_20260719_120000_before_restore.zip',
  },
} as const

const job = {
  id: 41,
  job_type: 'ops_restore_prepare',
  payload: {
    operation_id: operation.operation_id,
    operation: 'restore',
  },
  result: {},
  status: 'queued',
  progress: 0,
  stage: '',
  detail: '',
  error: null,
  cancel_requested: false,
  created_at: '2026-07-19T12:00:00Z',
  started_at: null,
  updated_at: '2026-07-19T12:00:00Z',
  finished_at: null,
} as const

afterEach(() => vi.restoreAllMocks())

describe('Ops API public contracts', () => {
  it('decodes the path-free self-check ledger', () => {
    expect(decodeOpsSelfCheck(selfCheck)).toEqual(selfCheck)
  })

  it.each([
    { ...selfCheck, status: 'healthy' },
    { ...selfCheck, api_configured: 'yes' },
    { ...selfCheck, directories: [{ ...selfCheck.directories[0], path: 'C:\\private' }] },
    { ...selfCheck, api_key: 'sk-secret' },
    { ...selfCheck, databases: [{ ...selfCheck.databases[0], size_bytes: -1 }] },
  ])('rejects unsafe or malformed self-check data', (payload) => {
    expect(() => decodeOpsSelfCheck(payload)).toThrow('Invalid Ops self-check')
  })

  it('decodes bounded safe backup metadata without treating filenames as paths', () => {
    expect(decodeOpsBackups(backups)).toEqual(backups)
  })

  it.each([
    { ...backups, limit: 101 },
    { ...backups, returned: 3 },
    {
      ...backups,
      items: [{ ...backups.items[0], filename: '..\\private\\backup.zip' }],
    },
    {
      ...backups,
      items: [{ ...backups.items[0], filename: 'C:\\private\\backup.zip' }],
    },
    {
      ...backups,
      items: [{ ...backups.items[0], secret: 'sk-secret' }],
    },
  ])('rejects malformed or sensitive backup metadata', (payload) => {
    expect(() => decodeOpsBackups(payload)).toThrow('Invalid Ops backups')
  })

  it('decodes a restart-required preflight without leaking its token into summaries', () => {
    expect(decodeOpsPreflight(preflight)).toEqual(preflight)
  })

  it.each([
    { ...preflight, operation: 'delete' },
    { ...preflight, confirmation_token: '' },
    { ...preflight, expires_at: 'not-a-time' },
    { ...preflight, summary: { ...preflight.summary, pending_migrations: -1 } },
    { ...preflight, summary: { ...preflight.summary, output_path: 'C:\\private' } },
  ])('rejects malformed or path-bearing preflight data', (payload) => {
    expect(() => decodeOpsPreflight(payload)).toThrow('Invalid Ops preflight')
  })

  it('decodes final offline operation state separately from Job success', () => {
    expect(decodeOpsOperation(operation)).toEqual(operation)
    expect(decodeOpsOperation({ ...operation, status: 'applied' }).status).toBe('applied')
    expect(decodeOpsOperation({ ...operation, status: 'rolled_back' }).status).toBe('rolled_back')
  })

  it.each([
    { ...operation, operation_id: 'not-a-uuid' },
    { ...operation, status: 'succeeded' },
    { ...operation, recovery: { ...operation.recovery, path: 'C:\\private' } },
  ])('rejects malformed or unsafe operation state', (payload) => {
    expect(() => decodeOpsOperation(payload)).toThrow('Invalid Ops operation')
  })
})

describe('Ops API exact endpoints', () => {
  it('uses only the dedicated read, preflight, submit and operation endpoints', async () => {
    const responses: unknown[] = [
      selfCheck,
      backups,
      preflight,
      job,
      operation,
      { ...operation, status: 'cancelled', result_code: 'cancelled_before_apply' },
    ]
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (path, init) => {
      const payload = responses.shift()
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: {
          'content-type': 'application/json',
          'x-request-id': String((init?.headers as Record<string, string>)['x-request-id']),
          'x-test-path': String(path),
        },
      })
    })

    await expect(opsApi.getSelfCheck()).resolves.toEqual(selfCheck)
    await expect(opsApi.getBackups(50)).resolves.toEqual(backups)
    await expect(opsApi.preflight({
      operation: 'restore',
      backup_filename: backups.items[0].filename,
    })).resolves.toEqual(preflight)
    await expect(opsApi.submit('confirm-once')).resolves.toEqual(job)
    await expect(opsApi.getOperation(operation.operation_id)).resolves.toEqual(operation)
    await expect(opsApi.cancelOperation(operation.operation_id)).resolves.toMatchObject({
      status: 'cancelled',
    })

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/ops/self-check',
      expect.objectContaining({ method: 'GET' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/ops/backups?limit=50',
      expect.objectContaining({ method: 'GET' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      '/api/ops/preflights',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          operation: 'restore',
          backup_filename: backups.items[0].filename,
        }),
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      '/api/ops/jobs',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ confirmation_token: 'confirm-once' }),
      }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      5,
      `/api/ops/operations/${operation.operation_id}`,
      expect.objectContaining({ method: 'GET' }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      6,
      `/api/ops/operations/${operation.operation_id}/cancel`,
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('streams a ZIP to the fixed upload endpoint and decodes safe metadata', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (path, init) =>
      new Response(JSON.stringify({
        upload_id: 'a'.repeat(32),
        filename: '课堂数据.zip',
        size_bytes: 9,
        sha256: 'b'.repeat(64),
      }), {
        status: 201,
        headers: {
          'content-type': 'application/json',
          'x-request-id': String((init?.headers as Record<string, string>)['x-request-id']),
          'x-test-path': String(path),
        },
      }),
    )
    const file = new File(['zip-bytes'], '课堂数据.zip', { type: 'application/zip' })

    await expect(opsApi.stageImport(file)).resolves.toMatchObject({
      upload_id: 'a'.repeat(32),
      filename: '课堂数据.zip',
    })
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/ops/transfer-import/uploads?filename=${encodeURIComponent('课堂数据.zip')}`,
      expect.objectContaining({
        method: 'POST',
        body: file,
        headers: expect.objectContaining({ 'content-type': 'application/zip' }),
      }),
    )
  })

  it.each([0, 101, Number.NaN])('rejects unsafe backup limit %s before fetch', async (limit) => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    await expect(opsApi.getBackups(limit)).rejects.toThrow('Invalid Ops backup limit')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('rejects unsafe identifiers and non-ZIP uploads before fetch', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    await expect(opsApi.submit('')).rejects.toThrow('Invalid Ops confirmation token')
    await expect(opsApi.getOperation('not-a-uuid')).rejects.toThrow('Invalid Ops operation id')
    await expect(opsApi.stageImport(
      new File(['x'], 'student.txt', { type: 'text/plain' }),
    )).rejects.toThrow('Ops import must be a ZIP file')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
