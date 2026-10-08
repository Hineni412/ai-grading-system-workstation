import { createApp, h, nextTick, ref } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { uploadConfigSource, type ConfigSource } from '../api/config-workspace';
import type { JobResponse } from '../api/jobs';
import { ApiError } from '../api/errors';
import type { RegionReadiness } from '../api/template-regions';
import { useConfigWorkspaceStore } from '../stores/config-workspace';
import { useJobStore } from '../stores/jobs';
import { useSessionStore } from '../stores/session';
import SessionConfigView from '../views/SessionConfigView.vue'
import ConfirmDialogHost from '../components/design-system/ConfirmDialogHost.vue'

vi.mock('../api/config-workspace', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/config-workspace')>(),
  uploadConfigSource: vi.fn(),
}))

function source(id = 'a', revision = 'b'): ConfigSource {
  return {
    session_id: 7, source_id: id.repeat(32), source_revision: revision.repeat(64),
    safe_filename: `${id}.pdf`, suffix: '.pdf', size_bytes: 10,
    sha256_prefix: 'c'.repeat(12), parse_state: 'ready', questions: [],
  }
}

function activeJob(): JobResponse {
  return {
    id: 31, job_type: 'config_generation',
    payload: { session_id: 7, mode: 'generate' }, result: {}, status: 'running',
    progress: 0.4, stage: 'config_generation', detail: '', error: null,
    cancel_requested: false, created_at: '2026-07-15T00:00:00Z',
    started_at: '2026-07-15T00:00:01Z', updated_at: '2026-07-15T00:00:02Z',
    finished_at: null,
  }
}

async function settle(): Promise<void> {
  await Promise.resolve(); await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 240))
  await Promise.resolve(); await nextTick()
}

async function mountDirtyView(hydrating = false) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const sessions = useSessionStore(pinia)
  sessions.$patch({
    sessions: [{ id: 7, name: '七年级数学', status: 'created', is_deleted: false,
      deleted_at: null, created_at: null, updated_at: null }],
    selectedSessionId: 7, loadState: 'ready',
  })
  const workspace = useConfigWorkspaceStore(pinia)
  workspace.selectSession(7)
  workspace.setSource(source())
  workspace.setEditor({
    session_id: 7, configured: true, revision: 'd'.repeat(64), rows: [], total_score: 0,
    issues: [], source: null,
  })
  workspace.updateEditor({ row_id: 'row-1', standard_answer: '教师草稿' })
  const host = document.createElement('div')
  document.body.append(host)
  const workspaceHydrating = ref(hydrating)
  const viewProps = {
    templateReadinessLoader: vi.fn(async (): Promise<RegionReadiness> => ({
      session_id: 7, scoring_configured: true, template_present: false,
      template_ready: false,
    })),
  }
  const app = createApp({ render: () => h(SessionConfigView, {
    ...viewProps, workspaceHydrating: workspaceHydrating.value,
  }) })
  app.use(pinia)
  app.mount(host)
  await nextTick()
  return { host, workspace, workspaceHydrating, sessions, unmount: () => app.unmount() }
}

async function chooseAndSubmit(host: HTMLElement): Promise<void> {
  const sourceStage = [...host.querySelectorAll<HTMLButtonElement>('.config-stage-rail button')]
    .find((button) => button.textContent?.includes('上传与拆题'))
  sourceStage?.click()
  await settle()
  const input = host.querySelector<HTMLInputElement>('input[type="file"]')!
  Object.defineProperty(input, 'files', {
    configurable: true, value: [new File(['%PDF'], '替换.pdf')],
  })
  input.dispatchEvent(new Event('change', { bubbles: true }))
  await nextTick()
  host.querySelector<HTMLButtonElement>('.config-source button[type="submit"]')!.click()
  await settle()
}

function mountConfirmHost(): { unmount: () => void } {
  const el = document.createElement('div')
  document.body.append(el)
  const app = createApp(ConfirmDialogHost)
  app.mount(el)
  return app
}

async function clickConfirmDialog(label: string): Promise<void> {
  await vi.waitFor(() => {
    expect(document.body.querySelector('[data-testid="app-confirm-dialog"]')).not.toBeNull()
  })
  const dialog = document.body.querySelector<HTMLElement>('[data-testid="app-confirm-dialog"]')!
  ;[...dialog.querySelectorAll<HTMLButtonElement>('button')]
    .find((button) => button.textContent?.trim() === label)!.click()
  await settle()
}

beforeEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
  window.history.replaceState({}, '', '/')
  vi.clearAllMocks()
})

describe('SessionConfigView source replacement guard', () => {

  it('waits for configuration hydration before showing stages and uses the restored phase after an exam change', async () => {
    const mounted = await mountDirtyView(true)
    expect(mounted.host.textContent).toContain('正在读取考试配置')
    expect(mounted.host.querySelector('.config-stage-rail')).toBeNull()
    expect(mounted.host.querySelector('.session-draft-panel')).toBeNull()
    expect(mounted.host.querySelector('.rubric-ledger')).toBeNull()

    mounted.workspaceHydrating.value = false
    await settle()
    expect(mounted.host.querySelector('.config-stage-rail')).not.toBeNull()
    expect(mounted.host.querySelector('.rubric-ledger')).not.toBeNull()
    expect(mounted.host.querySelector('.session-draft-panel')).toBeNull()
    expect(mounted.workspace.editorEdits[0]?.standard_answer).toBe('教师草稿')

    mounted.workspaceHydrating.value = true
    mounted.sessions.sessions.push({
      id: 8, name: '合成新考试', status: 'created', is_deleted: false,
      deleted_at: null, created_at: null, updated_at: null,
    })
    mounted.workspace.discardEditorDraft()
    mounted.sessions.selectSession(8)
    mounted.workspace.selectSession(8)
    await nextTick()
    expect(mounted.host.querySelector('.config-stage-rail')).toBeNull()
    expect(mounted.host.querySelector('.session-draft-panel')).toBeNull()

    mounted.workspaceHydrating.value = false
    await settle()
    expect(mounted.host.querySelector('.rubric-ledger')).toBeNull()
    expect(mounted.host.querySelector<HTMLInputElement>('#session-draft-name')?.value)
      .toBe('合成新考试')
    mounted.unmount()
  })

  it('unlocks an ambiguous single-question retry when its exact lookup confirms 404', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const sessions = useSessionStore(pinia)
    sessions.$patch({
      sessions: [{ id: 7, name: '旧考试', status: 'created', is_deleted: false,
        deleted_at: null, created_at: null, updated_at: null }],
      selectedSessionId: 7,
      loadState: 'ready',
    })
    useJobStore(pinia).track({
      ...activeJob(), status: 'succeeded',
      payload: { session_id: 7, mode: 'generate', curriculum_volume_id: 'junior-math' },
    })
    const workspace = useConfigWorkspaceStore(pinia)
    workspace.selectSession(7)
    workspace.setSource({
      session_id: 7, source_id: 'e'.repeat(32), source_revision: 'f'.repeat(64),
      safe_filename: '单题重试.pdf', suffix: '.pdf', size_bytes: 12,
      sha256_prefix: 'c'.repeat(12), parse_state: 'ready',
      questions: [{ question_id: 'Q1', question_type: 'calculation',
        question_preview: '计算题', answer_preview: '1', answer_present: true,
        needs_review: false, local_answer_trusted: true,
        has_question_asset: false, has_answer_asset: false }],
    })
    workspace.setEditor({
      session_id: 7, configured: true, revision: 'd'.repeat(64), total_score: 100,
      issues: [], source: null,
      rows: [{ row_id: 'row-q1-p1-s1', question_id: 'Q1', part_id: 'P1', step_id: 'S1',
        part_label: '第 1 问', question_type: 'calculation', core_goal: '计算', score: 100,
        standard_answer: '1', accepted_answers: ['1'], match_rule: 'exact',
        answer_only_max_score: null, require_final_answer: true, required_elements: [],
        deduction_rules: [], part_deduction_rules: [], final_answer_rule: '' }],
    })
    const timeout = new ApiError({ kind: 'network', status: null, code: 'network_error',
      message: 'offline', details: {}, requestId: 'safe', retryable: true })
    const notFound = new ApiError({ kind: 'not_found', status: 404,
      code: 'config_generation_job_not_found', message: 'missing', details: {},
      requestId: 'safe-404', retryable: false })
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(SessionConfigView, {
      generationSubmitter: vi.fn(async () => { throw timeout }),
      generationLoader: vi.fn(async () => { throw notFound }),
      requestAbandoner: vi.fn(async () => undefined),
    })
    app.use(pinia)
    app.mount(host)
    await nextTick()

    expect(host.querySelectorAll('.rubric-unit-card')).toHaveLength(1)
    expect(host.querySelector('.rubric-ledger table')).toBeNull()
    host.querySelector<HTMLButtonElement>('button[name="单题AI重试"]')!.click()
    await settle()

    expect(workspace.pendingJobRequestToken).toBeNull()
    expect(host.textContent).toContain('服务器确认未收到这次请求')
    expect(localStorage.getItem('config-score-review-required:7')).toBeNull()
    app.unmount()
  })

  it('retains the dirty editor when a confirmed replacement fails', async () => {
    const confirmApp = mountConfirmHost()
    vi.mocked(uploadConfigSource).mockRejectedValue(new Error('private path'))
    const mounted = await mountDirtyView()
    await chooseAndSubmit(mounted.host)
    await clickConfirmDialog('替换')

    expect(uploadConfigSource).toHaveBeenCalledOnce()
    expect(mounted.workspace.sourceId).toBe('a'.repeat(32))
    expect(mounted.workspace.editor).not.toBeNull()
    expect(mounted.workspace.hasDirtyEditor).toBe(true)
    expect(mounted.host.textContent).not.toContain('private path')
    confirmApp.unmount()
  })

  it('disables upload and editor save while a configuration job is active', async () => {
    const mounted = await mountDirtyView()
    mounted.workspace.jobId = 31
    useJobStore().track(activeJob())
    await nextTick()

    expect(mounted.host.querySelector<HTMLButtonElement>('button[name="保存评分依据"]')?.disabled).toBe(true)
    const sourceStage = [...mounted.host.querySelectorAll<HTMLButtonElement>('.config-stage-rail button')]
      .find((button) => button.textContent?.includes('上传与拆题'))
    sourceStage?.click()
    await settle()
    expect(mounted.host.querySelector<HTMLInputElement>('.config-source input[type="file"]')?.disabled).toBe(true)
    expect(mounted.host.textContent).toContain('样卷题框')
  })

})
