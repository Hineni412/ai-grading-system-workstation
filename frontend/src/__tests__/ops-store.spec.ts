import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/errors'
import type { JobResponse } from '../api/jobs'
import type {
  OpsApi,
  OpsBackupList,
  OpsOperationState,
  OpsPreflight,
  OpsSelfCheck,
} from '../api/ops'
import { useJobStore, type JobStoreDependencies } from '../stores/jobs'
import { useOpsStore } from '../stores/ops'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

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

describe('Ops store read-only ledger', () => {
  it('loads self-check and backup ledgers independently', async () => {
    const api = makeOpsApi()
    const store = useOpsStore()

    await store.initialize(api)

    expect(store.selfCheck).toEqual(selfCheck)
    expect(store.backups).toEqual(backups.items)
    expect(api.getSelfCheck).toHaveBeenCalledTimes(1)
    expect(api.getBackups).toHaveBeenCalledWith(50, expect.any(AbortSignal))
  })

  it('keeps the last successful ledger when a refresh fails', async () => {
    const api = makeOpsApi()
    const store = useOpsStore()
    await store.initialize(api)
    vi.mocked(api.getSelfCheck).mockRejectedValueOnce(new ApiError({
      kind: 'server',
      status: 503,
      code: 'ops_self_check_unavailable',
      message: 'unavailable',
      details: {},
      requestId: 'req-safe',
      retryable: true,
    }))

    await store.refreshSelfCheck(api)

    expect(store.selfCheck).toEqual(selfCheck)
    expect(store.selfCheckError).toMatchObject({
      message: '系统状态暂时无法更新',
      retryable: true,
      requestId: 'req-safe',
    })
  })

  it('ignores a late self-check response from an older refresh', async () => {
    const oldRequest = deferred<OpsSelfCheck>()
    const currentRequest = deferred<OpsSelfCheck>()
    const api = makeOpsApi({
      getSelfCheck: vi.fn()
        .mockReturnValueOnce(oldRequest.promise)
        .mockReturnValueOnce(currentRequest.promise),
    })
    const store = useOpsStore()

    const oldRefresh = store.refreshSelfCheck(api)
    const currentRefresh = store.refreshSelfCheck(api)
    currentRequest.resolve({ ...selfCheck, version: 'v-current' })
    await currentRefresh
    oldRequest.resolve({ ...selfCheck, version: 'v-old' })
    await oldRefresh

    expect(store.selfCheck?.version).toBe('v-current')
  })

  it('builds a copyable diagnostic only from safe public fields', async () => {
    const store = useOpsStore()
    await store.initialize(makeOpsApi())

    expect(store.diagnosticText).toContain('系统版本：v1.5.0')
    expect(store.diagnosticText).toContain('API 配置：未配置')
    expect(store.diagnosticText).toContain('阅卷数据库：完整性 ok')
    expect(store.diagnosticText).not.toMatch(/C:\\|\/user_data|sk-[a-z0-9]|sk-secret/i)
  })
})

describe('Ops store protected write flow', () => {
  it('locks every protected entry while a dangerous submit is unresolved', async () => {
    const pending = deferred<JobResponse>()
    const api = makeOpsApi({
      submit: vi.fn(() => pending.promise),
    })
    const store = useOpsStore()
    await store.startPreflight({
      operation: 'backup',
      reason: 'manual',
    }, api)

    const submission = store.submitConfirmed(api)
    await Promise.resolve()
    await store.startPreflight({
      operation: 'migration',
      target: 'all',
    }, api)
    await store.stageImport(
      new File(['zip'], '课堂数据.zip', { type: 'application/zip' }),
      api,
    )

    expect(api.preflight).toHaveBeenCalledTimes(1)
    expect(api.stageImport).not.toHaveBeenCalled()
    expect(store.hasBlockingOperation).toBe(true)
    expect(store.actionError).toMatchObject({
      message: '已有运维操作正在进行',
    })

    pending.resolve(makeJob())
    await submission
    expect(store.activeJob?.id).toBe(41)
    expect(store.actionError).toBeNull()
  })

  it('clears a finished result when a new protected flow begins', async () => {
    const api = makeOpsApi()
    const jobs = useJobStore()
    jobs.track(makeJob({
      job_type: 'ops_backup',
      status: 'succeeded',
      finished_at: '2026-07-19T12:01:00Z',
    }))
    const store = useOpsStore()
    await store.recoverTrackedOperation(api)
    expect(store.activeJob?.status).toBe('succeeded')

    await store.startPreflight({
      operation: 'restore',
      backup_filename: backups.items[0]!.filename,
    }, api)

    expect(store.activeJob).toBeNull()
    expect(store.preflight?.operation).toBe('restore')
  })

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

  it('uploads a ZIP before transfer-import preflight and never persists upload metadata', async () => {
    const api = makeOpsApi()
    const store = useOpsStore()
    const file = new File(['zip'], '课堂数据.zip', { type: 'application/zip' })

    await store.stageImport(file, api)
    await store.startPreflight({
      operation: 'transfer_import',
      upload_id: store.importUpload!.upload_id,
    }, api)

    expect(api.stageImport).toHaveBeenCalledWith(file, expect.any(AbortSignal))
    expect(api.preflight).toHaveBeenCalledWith({
      operation: 'transfer_import',
      upload_id: 'a'.repeat(32),
    }, expect.any(AbortSignal))
    expect(localStorage.length).toBe(0)
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

  it('uses Job cancel before preparation and operation cancel after restart-required', async () => {
    const api = makeOpsApi()
    const store = useOpsStore()
    const jobs = useJobStore()
    jobs.track(makeJob())
    await store.recoverTrackedOperation(api)

    await store.cancelCurrent(api)
    expect(jobs.jobs[41]?.status).toBe('cancelled')
    expect(api.cancelOperation).not.toHaveBeenCalled()

    jobs.track(makeJob({ status: 'succeeded' }))
    await store.recoverTrackedOperation(api)
    expect(store.operationState?.status).toBe('restart_required')
    await store.cancelCurrent(api)

    expect(api.cancelOperation).toHaveBeenCalledWith(
      operation.operation_id,
      expect.any(AbortSignal),
    )
    expect(store.operationState?.status).toBe('cancelled')
  })

  it('clears an expired preflight and explains that no operation was started', async () => {
    const api = makeOpsApi({
      submit: vi.fn(async () => {
        throw new ApiError({
          kind: 'conflict',
          status: 409,
          code: 'ops_confirmation_expired',
          message: 'expired',
          details: {},
          requestId: 'req-expired',
          retryable: false,
        })
      }),
    })
    const store = useOpsStore()
    await store.startPreflight({
      operation: 'migration',
      target: 'all',
    }, api)

    await store.submitConfirmed(api)

    expect(store.preflight).toBeNull()
    expect(store.resultUnknown).toBe(false)
    expect(store.actionError).toMatchObject({
      message: '预检已过期，请重新预检',
      impact: '没有启动新的运维任务',
    })
  })
})
