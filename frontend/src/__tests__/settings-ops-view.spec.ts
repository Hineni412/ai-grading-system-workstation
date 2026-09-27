import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'

import SettingsOpsView from '../views/SettingsOpsView.vue'

const opsApiMock = vi.hoisted(() => ({
  getSelfCheck: vi.fn(),
  getBackups: vi.fn(),
  stageImport: vi.fn(),
  preflight: vi.fn(),
  submit: vi.fn(),
  getOperation: vi.fn(),
  cancelOperation: vi.fn(),
  downloadJob: vi.fn(),
}))

const jobApiMock = vi.hoisted(() => ({
  getJob: vi.fn(),
  cancelJob: vi.fn(),
}))

vi.mock('../api/ops', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/ops')>(),
  opsApi: opsApiMock,
}))

vi.mock('../api/jobs', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/jobs')>(),
  jobApi: jobApiMock,
}))

const selfCheck = {
  version: 'v1.5.0',
  status: 'warning',
  api_configured: false,
  directories: [
    { key: 'data', exists: true, writable: true, status: 'ok' },
    { key: 'backups', exists: true, writable: true, status: 'ok' },
  ],
  databases: [
    {
      key: 'grading',
      exists: true,
      size_bytes: 4096,
      integrity: 'ok',
      migration_version: '3',
      pending_migrations: 1,
      status: 'warning',
    },
  ],
  tools: [
    { key: 'microsoft_word', available: true, status: 'ok' },
    { key: 'libreoffice', available: false, status: 'warning' },
  ],
  warnings: ['tool_unavailable:libreoffice'],
}

const backups = {
  items: [{
    kind: 'zip',
    filename: 'backup_20260719_120000_manual.zip',
    created_at: '2026-07-19T12:00:00',
    reason: 'manual',
    size_bytes: 2048,
  }, {
    kind: 'database',
    filename: 'grading_before_update_20260719.db',
    created_at: '2026-07-19T11:00:00',
    reason: 'database_automatic',
    size_bytes: 1024,
  }],
  returned: 2,
  limit: 50,
}

function preflight(operation: string, requiresRestart: boolean) {
  return {
    operation,
    confirmation_token: 'confirm-once',
    expires_at: '2026-07-19T12:05:00Z',
    requires_restart: requiresRestart,
    summary: {
      file_count: 8,
      total_size_bytes: 4096,
      total_expanded_bytes: 8192,
      database_count: 2,
      skipped_count: 1,
      sensitive_skipped_count: 1,
      target: operation === 'migration' ? 'all' : null,
      pending_migrations: operation === 'migration' ? 1 : null,
      applied_in_preview: operation === 'migration' ? 1 : null,
      integrity: 'ok',
      scope: operation === 'transfer_export' ? 'lean' : null,
      warnings: [],
    },
  }
}

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: 41,
    job_type: 'ops_restore_prepare',
    payload: {
      operation_id: '11111111-1111-4111-8111-111111111111',
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
}

const mounted: App[] = []

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountView(section: 'backup' | 'maintenance' = 'backup') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(SettingsOpsView, { section })
  app.use(pinia)
  app.mount(host)
  mounted.push(app)
  await vi.waitFor(() => expect(opsApiMock.getSelfCheck).toHaveBeenCalled())
  await settle()
  return host
}

function click(host: HTMLElement, selector: string): void {
  const button = host.querySelector<HTMLButtonElement>(selector)
  if (!button) throw new Error(`Missing button ${selector}`)
  button.click()
}

function setInput(host: HTMLElement, selector: string, value: string): void {
  const input = host.querySelector<HTMLInputElement>(selector)
  if (!input) throw new Error(`Missing input ${selector}`)
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  opsApiMock.getSelfCheck.mockResolvedValue(selfCheck)
  opsApiMock.getBackups.mockResolvedValue(backups)
  opsApiMock.stageImport.mockResolvedValue({
    upload_id: 'a'.repeat(32),
    filename: '课堂数据.zip',
    size_bytes: 3,
    sha256: 'b'.repeat(64),
  })
  opsApiMock.preflight.mockImplementation(async (request: { operation: string }) =>
    preflight(
      request.operation,
      ['restore', 'migration', 'transfer_import'].includes(request.operation),
    ),
  )
  opsApiMock.submit.mockResolvedValue(job())
  opsApiMock.getOperation.mockResolvedValue(operation)
  opsApiMock.cancelOperation.mockResolvedValue({
    ...operation,
    status: 'cancelled',
    result_code: 'cancelled_before_apply',
  })
  jobApiMock.getJob.mockResolvedValue(job())
  jobApiMock.cancelJob.mockResolvedValue(job({
    status: 'cancelled',
    finished_at: '2026-07-19T12:02:00Z',
  }))
  vi.stubGlobal('navigator', {
    ...navigator,
    clipboard: { writeText: vi.fn(async () => undefined) },
  })
})

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('settings and Ops view', () => {

  it('keeps restore behind preflight, an isolated gate and an exact confirmation phrase', async () => {
    const host = await mountView()
    const backupRadio = host.querySelector<HTMLInputElement>(
      'input[name="restore-backup"]',
    )!
    backupRadio.click()
    await settle()

    click(host, '[data-testid="preflight-restore"]')
    await vi.waitFor(() => expect(opsApiMock.preflight).toHaveBeenCalled())
    await settle()

    const gate = host.querySelector<HTMLElement>('[data-testid="ops-safety-gate"]')!
    const confirm = host.querySelector<HTMLButtonElement>('[data-testid="confirm-operation"]')!
    expect(gate.textContent).toContain('核对后再执行')
    expect(gate.textContent).toContain('预检完成')
    expect(gate.textContent).toContain('本次操作需要重启应用后才会生效')
    expect(gate.textContent).toContain('覆盖备份包内的同名数据')
    expect(gate.textContent).toContain('不会删除包外文件')
    expect(gate.textContent).toContain('展开后大小')
    expect(gate.textContent).toContain('8.0 KB')
    expect(gate.textContent).toContain('敏感文件跳过')
    expect(confirm.disabled).toBe(true)

    setInput(host, '[data-testid="confirmation-phrase"]', '确认恢复')
    await settle()
    expect(confirm.disabled).toBe(false)
    confirm.click()
    await vi.waitFor(() => expect(opsApiMock.submit).toHaveBeenCalledTimes(1))

    expect(opsApiMock.preflight).toHaveBeenCalledWith({
      operation: 'restore',
      backup_filename: 'backup_20260719_120000_manual.zip',
    }, expect.any(AbortSignal))
    expect(opsApiMock.submit).toHaveBeenCalledWith(
      'confirm-once',
      expect.any(AbortSignal),
    )
  })

  it('never labels an offline prepare Job as already applied', async () => {
    opsApiMock.submit.mockResolvedValue(job({
      status: 'succeeded',
      result: {
        operation_id: operation.operation_id,
        operation: 'restore',
        result_code: 'prepared_restart_required',
      },
      finished_at: '2026-07-19T12:01:00Z',
    }))
    const host = await mountView()
    host.querySelector<HTMLInputElement>('input[name="restore-backup"]')!.click()
    click(host, '[data-testid="preflight-restore"]')
    await vi.waitFor(() => expect(opsApiMock.preflight).toHaveBeenCalled())
    await vi.waitFor(() =>
      expect(host.querySelector('[data-testid="confirmation-phrase"]')).not.toBeNull(),
    )
    setInput(host, '[data-testid="confirmation-phrase"]', '确认恢复')
    await settle()
    click(host, '[data-testid="confirm-operation"]')
    await vi.waitFor(() => expect(opsApiMock.getOperation).toHaveBeenCalled())
    await settle()

    expect(host.textContent).toContain('准备完成，尚未应用')
    expect(host.textContent).toContain('下次启动应用前执行')
    expect(host.textContent).not.toContain('恢复已经生效')
    expect(host.querySelector('[data-testid="cancel-prepared-operation"]')).not.toBeNull()
  })

})
