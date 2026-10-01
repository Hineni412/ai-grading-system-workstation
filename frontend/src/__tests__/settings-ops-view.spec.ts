import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'

import SettingsDataPanel from '../components/settings/SettingsDataPanel.vue'
import SettingsSystemPanel from '../components/settings/SettingsSystemPanel.vue'
import { aiDiagnosticsApi } from '../api/ai-diagnostics'

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

const storageApiMock = vi.hoisted(() => ({ getStorage: vi.fn(), getOriginals: vi.fn(), releaseScans: vi.fn(), clearOriginals: vi.fn(), clearLegacy: vi.fn() }))

const jobApiMock = vi.hoisted(() => ({
  getJob: vi.fn(),
  cancelJob: vi.fn(),
}))

vi.mock('../api/ops', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/ops')>(),
  opsApi: opsApiMock,
  storageApi: storageApiMock,
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

async function mountView(section: 'data' | 'system' = 'data') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(section === 'data' ? SettingsDataPanel : SettingsSystemPanel)
  app.use(pinia)
  app.mount(host)
  mounted.push(app)
  await vi.waitFor(() => expect(opsApiMock.getSelfCheck).toHaveBeenCalled())
  await settle()
  return document.body
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
  storageApiMock.getStorage.mockResolvedValue({ total_bytes: 100, categories: [{ key: 'originals', label: '学生原卷', bytes: 100 }], legacy_annotations: { files: 0, bytes: 0 }, sessions: [{ session_id: 1, name: '隔离测试考试', status_label: '已完成', created_at: null, originals_state: 'complete', scan_bytes: 50, page_bytes: 50, release_bytes: 50, clear_bytes: 100, can_clear: true, can_release_scans: true, blocked_reason: null }] })
  storageApiMock.getOriginals.mockResolvedValue({ originals_state: 'complete', revision: 'test:complete', release_bytes: 50, clear_bytes: 100, can_clear: true, can_release_scans: true, backup_covers_originals: false, latest_backup_at: null })
  storageApiMock.clearOriginals.mockResolvedValue({ freed_bytes: 100, deleted_files: 2, kept_unrendered: 0, originals_state: 'cleared' })
  storageApiMock.releaseScans.mockResolvedValue({ freed_bytes: 50, deleted_files: 1, kept_unrendered: 0, originals_state: 'scans_released' })
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

describe('settings data and system panels', () => {
  it('backs up in one click without a typed phrase', async () => {
    const host = await mountView()
    ;[...host.querySelectorAll<HTMLButtonElement>('.settings-panel__body button')].find(button => button.textContent === '立即备份')!.click()
    await vi.waitFor(() => expect(opsApiMock.submit).toHaveBeenCalledOnce())
    expect(opsApiMock.preflight).toHaveBeenCalledWith({ operation: 'backup', reason: 'manual', scopes: ['grading'] }, expect.any(AbortSignal))
    expect(host.querySelector('[data-testid=confirmation-phrase]')).toBeNull()
  })
  it('requires the exact restore phrase and keeps prepared separate from applied', async () => {
    opsApiMock.submit.mockResolvedValue(job({ status: 'succeeded', result: { operation_id: operation.operation_id, operation: 'restore', result_code: 'prepared_restart_required' } }))
    const host = await mountView()
    host.querySelector<HTMLInputElement>('input[name=restore-backup]')!.click()
    await settle()
    click(host, '[data-testid=preflight-restore]')
    await vi.waitFor(() => expect(host.querySelector('[data-testid=confirmation-phrase]')).not.toBeNull())
    const confirm = host.querySelector<HTMLButtonElement>('[data-testid=confirm-operation]')!
    expect(confirm.disabled).toBe(true)
    setInput(host, '[data-testid=confirmation-phrase]', '确认恢复')
    await settle()
    confirm.click()
    await vi.waitFor(() => expect(opsApiMock.getOperation).toHaveBeenCalled())
    expect(host.textContent).toContain('重启应用后生效')
    expect(host.textContent).not.toContain('恢复已生效。')
  })
  it.each(['release', 'clear'] as const)('confirms %s with the current revision', async mode => {
    const host = await mountView()
    const name = mode === 'release' ? '释放扫描文件' : '清除原卷'
    ;[...host.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === name)!.click()
    await vi.waitFor(() => expect(host.querySelector('[data-testid=confirm-operation]')).not.toBeNull())
    const confirm = host.querySelector<HTMLButtonElement>('[data-testid=confirm-operation]')!
    expect(confirm.disabled).toBe(mode === 'clear')
    expect(host.querySelector('[data-testid=confirmation-phrase]') === null).toBe(mode === 'release')
    if (mode === 'clear') {
      setInput(host, '[data-testid=confirmation-phrase]', '确认清除')
      await settle()
    }
    confirm.click()
    await vi.waitFor(() => expect(mode === 'release' ? storageApiMock.releaseScans : storageApiMock.clearOriginals).toHaveBeenCalledOnce())
    expect((mode === 'clear' ? storageApiMock.clearOriginals : storageApiMock.releaseScans).mock.calls[0]).toEqual(mode === 'clear' ? [1, 'test:complete', '确认清除'] : [1, 'test:complete'])
  })
  it('shows retry for interrupted cleanup and disables blocked exams', async () => {
    const overview = await storageApiMock.getStorage()
    overview.sessions = [{ ...overview.sessions[0], originals_state: 'clearing' }, { ...overview.sessions[0], session_id: 2, name: '未复核考试', can_clear: false, can_release_scans: false, blocked_reason: '复核完成后可清理' }]
    storageApiMock.getStorage.mockResolvedValue(overview)
    const host = await mountView()
    expect(host.textContent).toContain('继续清理')
    const blocked = [...host.querySelectorAll('tr')].find(row => row.textContent?.includes('未复核考试'))!
    expect(blocked.textContent).toContain('复核完成后可清理')
    expect([...blocked.querySelectorAll<HTMLButtonElement>('button')].every(button => button.disabled)).toBe(true)
  })
  it('copies only sanitized diagnostics from system status', async () => {
    vi.spyOn(aiDiagnosticsApi, 'list').mockResolvedValue({ items: [], returned: 0, matching: 0, scanned_event_count: 0, truncated: false })
    const host = await mountView('system')
    click(host, '[data-testid=copy-diagnostic]')
    await settle()
    expect(navigator.clipboard.writeText).toHaveBeenCalled()
    expect(host.textContent).toContain('已复制（不含密钥和学生信息）')
  })
})
