import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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

async function mountView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(SettingsOpsView)
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
  it('restores a completed online Ops Job after a page refresh', async () => {
    localStorage.setItem('ai-grading:tracked-jobs:v1', JSON.stringify([{
      id: 41,
      jobType: 'ops_backup',
      trackedAt: '2026-07-19T12:00:00Z',
    }]))
    jobApiMock.getJob.mockResolvedValue(job({
      job_type: 'ops_backup',
      status: 'succeeded',
      result: {
        filename: 'backup_20260719_120000_manual.zip',
        download_url: '/api/jobs/41/download',
      },
      progress: 1,
      finished_at: '2026-07-19T12:01:00Z',
    }))

    const host = await mountView()

    await vi.waitFor(() => expect(jobApiMock.getJob).toHaveBeenCalledWith(
      41,
      expect.any(AbortSignal),
    ))
    await vi.waitFor(() => expect(host.textContent).toContain('任务处理完成'))
    expect(host.textContent).toContain('下载结果')
    const progress = host.querySelector<HTMLProgressElement>('progress')!
    expect(progress.max).toBe(1)
    expect(progress.value).toBe(1)
  })

  it('shows a terminal failed Job as failed instead of still running', async () => {
    localStorage.setItem('ai-grading:tracked-jobs:v1', JSON.stringify([{
      id: 41,
      jobType: 'ops_transfer_export',
      trackedAt: '2026-07-19T12:00:00Z',
    }]))
    jobApiMock.getJob.mockResolvedValue(job({
      job_type: 'ops_transfer_export',
      status: 'failed',
      error: 'Job failed; see local logs for details.',
      finished_at: '2026-07-19T12:01:00Z',
    }))

    const host = await mountView()

    await vi.waitFor(() => expect(host.textContent).toContain('任务执行失败'))
    expect(host.textContent).toContain('本次任务没有生成可下载结果')
    expect(host.textContent).not.toContain('任务正在处理')
    expect(
      [...host.querySelectorAll('button')].some((button) => button.textContent === '下载结果'),
    ).toBe(false)
  })

  it('explains offline preparation failure without claiming business data was applied', async () => {
    localStorage.setItem('ai-grading:tracked-jobs:v1', JSON.stringify([{
      id: 41,
      jobType: 'ops_restore_prepare',
      trackedAt: '2026-07-19T12:00:00Z',
    }]))
    jobApiMock.getJob.mockResolvedValue(job({
      job_type: 'ops_restore_prepare',
      status: 'failed',
      error: 'Job failed; see local logs for details.',
      finished_at: '2026-07-19T12:01:00Z',
    }))

    const host = await mountView()

    await vi.waitFor(() => expect(host.textContent).toContain('离线准备失败'))
    expect(host.textContent).toContain('业务数据尚未应用')
    expect(host.textContent).toContain('可能已经创建安全备份')
    expect(host.textContent).toContain('“恢复备份”中确认可用备份')
    expect(host.textContent).not.toContain('操作已经应用')
  })

  it('shows a readable system ledger and all five protected operation entries', async () => {
    const host = await mountView()

    expect(host.textContent).toContain('设置与运维')
    expect(host.textContent).toContain('系统状态账本')
    expect(host.textContent).toContain('阅卷数据库')
    expect(host.textContent).toContain('待迁移 1')
    expect(host.textContent).toContain('API 配置未完成')
    expect(host.textContent).toContain('Microsoft Word')
    expect(host.textContent).toContain('创建备份')
    expect(host.textContent).toContain('恢复备份')
    expect(host.textContent).toContain('数据库迁移')
    expect(host.textContent).toContain('导出数据包')
    expect(host.textContent).toContain('导入数据包')
    expect(host.textContent).toContain('手动备份')
    expect(host.textContent).not.toMatch(/C:\\|\/user_data|sk-secret/i)
  })

  it('copies only the public diagnostic ledger', async () => {
    const host = await mountView()

    click(host, '[data-testid="copy-diagnostic"]')
    await settle()

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      expect.stringContaining('AI 阅卷系统脱敏诊断'),
    )
    const copied = vi.mocked(navigator.clipboard.writeText).mock.calls[0]?.[0] ?? ''
    expect(copied).not.toMatch(/C:\\|\/user_data|sk-[a-z0-9]/i)
    expect(host.textContent).toContain('已复制脱敏诊断')
  })

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
    expect(gate.textContent).toContain('安全闸门')
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

  it('shows applied and rolled-back results as different outcomes', async () => {
    opsApiMock.submit.mockResolvedValue(job({
      status: 'succeeded',
      result: {
        operation_id: operation.operation_id,
        operation: 'restore',
      },
      finished_at: '2026-07-19T12:01:00Z',
    }))
    opsApiMock.getOperation.mockResolvedValueOnce({
      ...operation,
      status: 'applied',
      result_code: 'applied',
    })
    const appliedHost = await mountView()
    appliedHost.querySelector<HTMLInputElement>('input[name="restore-backup"]')!.click()
    click(appliedHost, '[data-testid="preflight-restore"]')
    await vi.waitFor(() => expect(opsApiMock.preflight).toHaveBeenCalled())
    await vi.waitFor(() =>
      expect(appliedHost.querySelector('[data-testid="confirmation-phrase"]')).not.toBeNull(),
    )
    setInput(appliedHost, '[data-testid="confirmation-phrase"]', '确认恢复')
    await settle()
    click(appliedHost, '[data-testid="confirm-operation"]')
    await vi.waitFor(() => expect(appliedHost.textContent).toContain('操作已经应用'))

    for (const app of mounted.splice(0)) app.unmount()
    document.body.innerHTML = ''
    localStorage.clear()
    vi.clearAllMocks()
    opsApiMock.getSelfCheck.mockResolvedValue(selfCheck)
    opsApiMock.getBackups.mockResolvedValue(backups)
    opsApiMock.preflight.mockResolvedValue(preflight('restore', true))
    opsApiMock.submit.mockResolvedValue(job({
      status: 'succeeded',
      result: {
        operation_id: operation.operation_id,
        operation: 'restore',
      },
      finished_at: '2026-07-19T12:01:00Z',
    }))
    opsApiMock.getOperation.mockResolvedValue({
      ...operation,
      status: 'rolled_back',
      result_code: 'rolled_back_after_failure',
    })
    const rolledBackHost = await mountView()
    rolledBackHost.querySelector<HTMLInputElement>('input[name="restore-backup"]')!.click()
    click(rolledBackHost, '[data-testid="preflight-restore"]')
    await vi.waitFor(() => expect(opsApiMock.preflight).toHaveBeenCalled())
    await vi.waitFor(() =>
      expect(rolledBackHost.querySelector('[data-testid="confirmation-phrase"]')).not.toBeNull(),
    )
    setInput(rolledBackHost, '[data-testid="confirmation-phrase"]', '确认恢复')
    await settle()
    click(rolledBackHost, '[data-testid="confirm-operation"]')
    await vi.waitFor(() => expect(rolledBackHost.textContent).toContain('操作未生效，系统已回退'))
  })

  it('stages an import before allowing its preflight', async () => {
    const host = await mountView()
    const input = host.querySelector<HTMLInputElement>('input[type="file"]')!
    const file = new File(['zip'], '课堂数据.zip', { type: 'application/zip' })
    Object.defineProperty(input, 'files', { value: [file] })
    input.dispatchEvent(new Event('change', { bubbles: true }))
    await vi.waitFor(() => expect(opsApiMock.stageImport).toHaveBeenCalled())
    await settle()

    expect(host.textContent).toContain('课堂数据.zip')
    click(host, '[data-testid="preflight-import"]')
    await vi.waitFor(() => expect(opsApiMock.preflight).toHaveBeenCalledWith({
      operation: 'transfer_import',
      upload_id: 'a'.repeat(32),
    }, expect.any(AbortSignal)))
  })
})
