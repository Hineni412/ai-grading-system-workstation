import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/errors';
import type { JobResponse } from '../api/jobs';
import type { OpsApi, OpsBackupList, OpsOperationState, OpsPreflight, OpsSelfCheck } from '../api/ops';
import { useJobStore, type JobStoreDependencies } from '../stores/jobs';
import { useOpsStore } from '../stores/ops';

const selfCheck: OpsSelfCheck = {
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
}

const backups: OpsBackupList = {
  items: [{
    kind: 'zip',
    filename: 'backup_20260719_120000_manual.zip',
    created_at: '2026-07-19T12:00:00',
    reason: 'manual',
    size_bytes: 2048,
  }],
  returned: 1,
  limit: 50,
}

const preflight: OpsPreflight = {
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
    warnings: [],
  },
}

const operation: OpsOperationState = {
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
}

function makeJob(overrides: Partial<JobResponse> = {}): JobResponse {
  return {
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
    ...overrides,
  }
}

function makeOpsApi(overrides: Partial<OpsApi> = {}): OpsApi {
  return {
    getSelfCheck: vi.fn(async () => selfCheck),
    getBackups: vi.fn(async () => backups),
    stageImport: vi.fn(async (file) => ({
      upload_id: 'a'.repeat(32),
      filename: file.name,
      size_bytes: file.size,
      sha256: 'b'.repeat(64),
    })),
    preflight: vi.fn(async () => preflight),
    submit: vi.fn(async () => makeJob()),
    getOperation: vi.fn(async () => operation),
    cancelOperation: vi.fn(async (): Promise<OpsOperationState> => ({
      ...operation,
      status: 'cancelled',
      result_code: 'cancelled_before_apply',
    })),
    downloadJob: vi.fn(),
    ...overrides,
  }
}

function jobDependencies(overrides: Partial<JobStoreDependencies> = {}): JobStoreDependencies {
  return {
    api: {
      getJob: vi.fn(async () => makeJob()),
      getJobStatusBatch: vi.fn(async (ids: number[]) =>
        ids.map((id) => ({ id, found: true, job: makeJob() })),
      ),
      cancelJob: vi.fn(async () => makeJob({
        status: 'cancelled',
        finished_at: '2026-07-19T12:02:00Z',
      })),
    },
    now: () => new Date('2026-07-19T12:00:00Z'),
    schedule: vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>),
    cancelScheduled: vi.fn(),
    pollIntervalMs: 2_000,
    maxBackoffMs: 30_000,
    ...overrides,
  }
}

beforeEach(async () => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.restoreAllMocks()
  await useJobStore().initialize(jobDependencies())
})

describe('Ops store protected write flow', () => {

  it('preflights once, consumes the token once and tracks the submitted Job', async () => {
    const api = makeOpsApi()
    const store = useOpsStore()

    await store.startPreflight({
      operation: 'restore',
      backup_filename: backups.items[0]!.filename,
    }, api)
    const firstSubmit = store.submitConfirmed(api)
    const duplicateSubmit = store.submitConfirmed(api)
    await Promise.all([firstSubmit, duplicateSubmit])

    expect(api.preflight).toHaveBeenCalledTimes(1)
    expect(api.submit).toHaveBeenCalledTimes(1)
    expect(api.submit).toHaveBeenCalledWith('confirm-once', expect.any(AbortSignal))
    expect(store.activeJob?.id).toBe(41)
    expect(store.preflight).toBeNull()
    expect(localStorage.getItem('ai-grading:tracked-jobs:v1')).not.toContain('confirm-once')
  })

  it('does not retry an ambiguous submit and requires a new preflight', async () => {
    const api = makeOpsApi({
      submit: vi.fn(async () => {
        throw new ApiError({
          kind: 'network',
          status: null,
          code: 'network_error',
          message: '暂时无法连接服务器',
          details: {},
          requestId: 'req-unknown',
          retryable: true,
        })
      }),
    })
    const store = useOpsStore()
    await store.startPreflight({
      operation: 'backup',
      reason: 'manual',
    }, api)

    await store.submitConfirmed(api)
    await store.submitConfirmed(api)

    expect(api.submit).toHaveBeenCalledTimes(1)
    expect(store.preflight).toBeNull()
    expect(store.resultUnknown).toBe(true)
    expect(store.actionError).toMatchObject({
      message: '提交结果未知，请先刷新状态，不要重复操作',
      requestId: 'req-unknown',
    })
  })

  it('restores a tracked offline Job and distinguishes prepared from applied', async () => {
    const jobs = useJobStore()
    jobs.track(makeJob({
      status: 'succeeded',
      result: {
        operation_id: operation.operation_id,
        operation: 'restore',
        result_code: 'prepared_restart_required',
      },
      finished_at: '2026-07-19T12:01:00Z',
    }))
    const api = makeOpsApi()
    const store = useOpsStore()

    await store.recoverTrackedOperation(api)

    expect(store.activeJob?.status).toBe('succeeded')
    expect(api.getOperation).toHaveBeenCalledWith(
      operation.operation_id,
      expect.any(AbortSignal),
    )
    expect(store.operationState?.status).toBe('restart_required')
    expect(store.operationApplied).toBe(false)

    vi.mocked(api.getOperation).mockResolvedValueOnce({
      ...operation,
      status: 'applied',
      result_code: 'applied',
    })
    await store.refreshOperation(api)
    expect(store.operationApplied).toBe(true)
  })

})
