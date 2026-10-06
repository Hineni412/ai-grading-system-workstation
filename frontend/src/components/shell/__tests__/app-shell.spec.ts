
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, h, nextTick } from 'vue';
import { createMemoryHistory, createRouter } from 'vue-router';

import type { SessionSummary } from '../../../api/sessions';
import AppShell from '../../../layouts/AppShell.vue'
import ConfirmDialogHost from '../../design-system/ConfirmDialogHost.vue'
import { createAppRouter } from '../../../router';
import { useConfigWorkspaceStore } from '../../../stores/config-workspace';

import { useReviewDraftStore } from '../../../stores/review-drafts';
import { useSessionStore } from '../../../stores/session';
import { useJobStore } from '../../../stores/jobs';
import type { JobResponse } from '../../../api/jobs'
import { questionBankApi } from '../../../api/question-bank'
import { exportsApi } from '../../../api/exports'
import { taskDetail, taskName, taskOutcome } from '../task-center-format'

function task(overrides: Partial<JobResponse> = {}): JobResponse {
  return { id: 42, job_type: 'ops_backup', status: 'running', progress: 0.5,
    stage: 'writing', detail: 'preparing', payload: {}, result: {}, error: null,
    cancel_requested: false, created_at: '2026-10-02T00:00:00', updated_at: '2026-10-02T00:00:00',
    started_at: null, finished_at: null, ...overrides }
}

async function settleUi(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

/* 侧栏考试切换卡 → reka Popover 内容 Teleport 到 body */
async function openExamSwitcher(host: HTMLElement): Promise<HTMLElement> {
  const trigger = host.querySelector<HTMLButtonElement>(
    '.exam-switcher__card, .exam-switcher__icon',
  )
  expect(trigger).not.toBeNull()
  trigger!.click()
  await settleUi()
  const popover = document.body.querySelector<HTMLElement>('.exam-switcher-popover')
  expect(popover).not.toBeNull()
  return popover!
}

function switcherRows(popover: ParentNode): HTMLElement[] {
  return [...popover.querySelectorAll<HTMLElement>('.exam-switcher-popover__row')]
}

function mountConfirmHost(): ReturnType<typeof createApp> {
  const el = document.createElement('div')
  document.body.append(el)
  const confirmApp = createApp(ConfirmDialogHost)
  confirmApp.mount(el)
  return confirmApp
}

function confirmDialog(): HTMLElement {
  const found = document.body.querySelector<HTMLElement>('[data-testid="app-confirm-dialog"]')
  if (!found) throw new Error('confirm dialog not open')
  return found
}

function confirmDialogButton(label: string): HTMLButtonElement {
  return [...confirmDialog().querySelectorAll<HTMLButtonElement>('button')]
    .find(item => item.textContent?.trim() === label)!
}

/* wide=true → ≥1440px 展开模式（显示切换卡）；false → 图标轨 */

async function mountShell({
  path = '/grading',
  prepareStore = true,
  stubPages = false,
}: { path?: string; prepareStore?: boolean; stubPages?: boolean } = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useSessionStore()
  if (prepareStore) await store.initialize(async () => [])
  const initialize = vi.spyOn(store, 'initialize')
  if (prepareStore) initialize.mockResolvedValue()
  const router = stubPages ? createRouter({ history: createMemoryHistory(), routes: ['/question-bank', '/results', '/sessions'].map(path => ({ path, component: { render: () => h('section', [h('h1', { tabindex: -1 }, path === '/results' ? '合成结果页' : '合成题库页'), h('button', '合成题卡')]) } })) }) : createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()

  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(AppShell)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  await settleUi()

  return { app, host, initialize, router }
}

beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('AppShell', () => {
  it('loads exam configuration only when entering setup and preserves an existing editor on return', async () => {
    const { app, router } = await mountShell({ path: '/question-bank', stubPages: true })
    const sessions = useSessionStore()
    sessions.sessions = [{ id: 7, name: '合成考试', status: 'created', is_deleted: false, deleted_at: null, created_at: null, updated_at: null }]
    const config = useConfigWorkspaceStore()
    const hydrate = vi.spyOn(config, 'hydrateSafeIndex').mockImplementation(async () => {
      config.setEditor({ session_id: 7, configured: true, revision: 'a'.repeat(64), rows: [], total_score: 0, issues: [], source: null })
    })
    sessions.selectSession(7)
    await settleUi()
    expect(hydrate).not.toHaveBeenCalled()
    await router.push('/sessions')
    await settleUi()
    expect(hydrate).toHaveBeenCalledOnce()
    expect(hydrate).toHaveBeenCalledWith([7], 7)
    config.updateEditor({ row_id: 'row-1', standard_answer: '未保存的合成答案' })
    await router.push('/results'); await settleUi()
    await router.push('/sessions'); await settleUi()
    expect(hydrate).toHaveBeenCalledOnce()
    expect(config.editorEdits[0]?.standard_answer).toBe('未保存的合成答案')
    app.unmount()
  })
  it('renders the full exam card inside the rail trigger so hover expansion can show it', async () => {
    /* jsdom 的 matchMedia 全部不匹配 → 普通页面为图标轨模式；
       悬停展开由 CSS 完成，这里只断言卡片内容仍在同一触发按钮中 */
    const { app, host } = await mountShell()
    const sessions = useSessionStore()
    sessions.sessions = [{ id: 7, name: '合成考试', status: 'created', is_deleted: false, deleted_at: null, created_at: null, updated_at: null }]
    sessions.selectSession(7)
    await settleUi()
    expect(host.querySelector('.app-shell--rail')).not.toBeNull()
    const trigger = host.querySelector<HTMLButtonElement>('.exam-switcher__card')!
    expect(trigger.classList.contains('exam-switcher__card--rail')).toBe(true)
    expect(trigger.querySelector('.exam-switcher__icon-box')).not.toBeNull()
    expect(trigger.querySelector('.exam-switcher__name')!.textContent).toContain('合成考试')
    app.unmount()
  })
  it('opens task center from the sidebar and cancels through the existing job store', async () => {
    const { app, host } = await mountShell({ path: '/question-bank', stubPages: true })
    const jobs = useJobStore()
    jobs.jobs[42] = { id: 42, job_type: 'ops_backup', status: 'running', progress: 0.5, stage: 'writing', detail: '测试备份', payload: {}, result: {}, error: null, cancel_requested: false, created_at: '2026-10-02T00:00:00', updated_at: '2026-10-02T00:00:00', started_at: null, finished_at: null }
    vi.spyOn(jobs, 'initialize').mockResolvedValue()
    vi.spyOn(jobs, 'refresh').mockResolvedValue()
    const cancel = vi.spyOn(jobs, 'cancel').mockResolvedValue()
    host.querySelector<HTMLButtonElement>('[aria-label="任务中心"]')!.click()
    await settleUi()
    const popover = document.body.querySelector('.task-center-popover')!
    expect(popover.textContent).toContain('测试备份')
    expect(popover.textContent).toContain('50%')
    const button = [...popover.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent === '取消任务')!
    button.click()
    await settleUi()
    expect(cancel).toHaveBeenCalledWith(42)
    app.unmount()
  })
  it('downloads personal bundles and keeps tasks visible after a download failure', async () => {
    const { app, host } = await mountShell({ path: '/question-bank', stubPages: true })
    const jobs = useJobStore()
    jobs.jobs[42] = task({ job_type: 'personal_report_bundle', status: 'succeeded',
      payload: { session_ids: [7], student_ids: [1], scope_label: '指定1人' },
      result: { generated: 1, failed: 0, skipped: 0, download_url: '/api/jobs/42/file' } })
    vi.spyOn(jobs, 'initialize').mockResolvedValue()
    vi.spyOn(jobs, 'refresh').mockResolvedValue()
    const download = vi.spyOn(exportsApi, 'downloadJobFile').mockRejectedValueOnce(new Error('expired'))
      .mockResolvedValueOnce({ blob: new Blob(['合成报告']), filename: '合成报告.html' })
    vi.stubGlobal('URL', class extends URL {
      static createObjectURL = vi.fn(() => 'blob:synthetic-report')
      static revokeObjectURL = vi.fn()
    })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    host.querySelector<HTMLButtonElement>('[aria-label="任务中心"]')!.click()
    await settleUi()
    const popover = document.body.querySelector('.task-center-popover')!
    const button = [...popover.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent === '下载个人报告')!
    button.click()
    await vi.waitFor(() => expect(popover.textContent).toContain('下载未完成'))
    expect(popover.querySelector('[data-job-id="42"]')).not.toBeNull()
    button.click()
    await vi.waitFor(() => expect(click).toHaveBeenCalledOnce())
    expect(download).toHaveBeenNthCalledWith(2, 42)
    expect(popover.textContent).not.toContain('下载个人报告')
    app.unmount()
  })
  it('shows the actual exam and paper sources even after selecting another exam', async () => {
    const { app, host } = await mountShell({ path: '/question-bank', stubPages: true })
    const sessions = useSessionStore()
    sessions.sessions = [7, 9].map(id => ({ id, name: `合成考试${id}`, status: 'created', is_deleted: false, deleted_at: null, created_at: null, updated_at: null }))
    sessions.selectSession(9)
    const jobs = useJobStore()
    jobs.jobs = {
      41: task({ id: 41, job_type: 'grading_run', payload: { session_id: 7 }, detail: 'graded=36 failed=2' }),
      42: task(),
      43: task({ id: 43, job_type: 'tagging_sync', payload: { question_ids: [11] } }),
      44: task({ id: 44, job_type: 'question_import', payload: { request_id: 'a'.repeat(32) } }),
    }
    vi.spyOn(jobs, 'initialize').mockResolvedValue()
    vi.spyOn(jobs, 'refresh').mockResolvedValue()
    const context = vi.spyOn(questionBankApi, 'taskContext').mockImplementation(async id => id === 43
      ? { papers: [{ id: 3, title: '合成几何卷' }], source_filename: null }
      : { papers: [], source_filename: '合成待导入卷.docx' })
    host.querySelector<HTMLButtonElement>('[aria-label="任务中心"]')!.click()
    await settleUi(); await settleUi()
    const popover = document.body.querySelector('.task-center-popover')!
    expect(popover.querySelector('[data-job-id="41"]')!.textContent).toContain('考试：合成考试7')
    expect(popover.querySelector('[data-job-id="41"]')!.textContent).not.toContain('合成考试9')
    expect(popover.querySelector('[data-job-id="41"]')!.textContent).toContain('已批改 36 份答卷，失败 2 份')
    expect(popover.querySelector('[data-job-id="42"]')!.textContent).toContain('正在准备')
    expect(popover.querySelector('[data-job-id="43"]')!.textContent).toContain('试卷：合成几何卷')
    expect(popover.querySelector('[data-job-id="44"]')!.textContent).toContain('导入文件：合成待导入卷.docx')
    jobs.jobs[43] = task({ id: 43, job_type: 'tagging_sync', status: 'succeeded', result: { outcome: 'partial', failed_count: 1 } })
    await settleUi(); await settleUi()
    const row = popover.querySelector('[data-job-id="43"]')!
    expect(row.textContent).toContain('部分完成')
    expect(row.textContent).toContain('仍有 1 道题未完成')
    expect(context).toHaveBeenCalledTimes(3)
    expect(row.querySelector('button')).toBeNull()
    app.unmount()
  })
  it.each([
    ['grading_run', '答卷批改'], ['config_generation', '试卷分析与本场赋分'],
    ['class_analysis_generate', 'AI 整理（错因、班级与个人报告）'], ['question_bank_repair', '补齐题库资料'],
    ['report_export', '生成学生个人分析报告'], ['personalized_handout_export', '导出训练讲义'],
      ['personal_report_bundle', '导出学生个人报告'],
    ['unknown_future_job', '后台处理'],
  ])('uses a Chinese title for %s', (jobType, expected) => {
    expect(taskName(task({ job_type: jobType, payload: { report_type: 'personal_analysis_html' } }))).toBe(expected)
  })
  it.each([
    ['all successful', 'tagging_sync', { outcome: 'complete' }, '全部完成'],
    ['analysis failures', 'tagging_sync', { outcome: 'partial', failed_question_ids: [11] }, '部分完成'],
    ['rubric review', 'config_generation', { outcome: 'complete', needs_teacher_resolution: true }, '等待教师核对'],
    ['intake incomplete', 'config_generation', { exam_intake_complete: false }, '部分完成'],
    ['allocation pending', 'config_generation', { score_allocation_pending: true }, '部分完成'],
    ['business failed', 'question_import', { outcome: 'failed' }, '未完成'],
    ['analysis not configured', 'class_analysis_generate', { status: 'not_configured' }, '未完成'],
    ['remaining gaps', 'question_bank_repair', { remaining: [{ id: 11 }] }, '部分完成'],
    ['report review', 'report_export', { review_note_count: 3 }, '等待教师核对'],
    ['scan issues', 'scan_analysis', { summary: { issues: 2 } }, '等待教师核对'],
    ['grading failures', 'grading_run', { summary: { graded: 3, failed: 1 } }, '部分完成'],
    ['student export failure', 'wrong_question_export', { failed_students: [{}] }, '部分完成'],
    ['restart pending', 'ops_restore_prepare', { outcome: 'prepared_restart_required' }, '等待重启完成'],
    ['unknown finished task', 'unknown_future_job', {}, '处理已结束'],
    ['legacy result missing', 'tagging_sync', {}, '处理已结束'],
  ])('distinguishes %s from full completion', (_case, jobType, result, expected) => {
    const value = task({ job_type: jobType, status: 'succeeded', result })
    expect(taskOutcome(value).label).toBe(expected)
    expect(value.status).toBe('succeeded')
  })
  it('uses Chinese explanations for raw English, mixed machine errors and cancellation', () => {
    expect(taskDetail(task({ detail: 'Generating grading configuration.' }))).toContain('正在分析试卷')
    expect(taskDetail(task({ detail: 'published' }))).toBe('结果已保存')
    expect(taskDetail(task({ detail: '处理失败：TimeoutError', status: 'failed' }))).toContain('本次处理未完成')
    expect(taskDetail(task({ detail: 'secret-file.zip' }))).not.toContain('secret-file')
    expect(taskOutcome(task({ cancel_requested: true })).label).toBe('正在取消')
  })
  it('preserves focus for question-bank bookmarks and focuses the heading on page navigation', async () => {
    const { app, host, router } = await mountShell({ path: '/question-bank', stubPages: true })
    const workspace = host.querySelector('#main-workspace')!
    const button = workspace.querySelector<HTMLButtonElement>('button')!
    button.focus()
    await router.replace({ query: { tab: 'skill', question: '17' } }); await settleUi()
    expect(document.activeElement).toBe(button)
    await router.push('/results'); await settleUi()
    expect(document.activeElement?.textContent).toBe('合成结果页')
    app.unmount(); host.remove()
  })


  it.each(['review', 'config'] as const)('warns before leaving with dirty %s work', async (kind) => {
    const { app } = await mountShell()
    if (kind === 'review') {
      const store = useReviewDraftStore()
      store.drafts['7:Q1:1'] = {
        key: '7:Q1:1', sessionId: 7, questionId: 'Q1', detailId: 1,
        scoreText: '4', note: '', baseScoreText: '3', baseNote: '',
        dirty: true, updatedAt: Date.now(),
      }
    } else {
      const store = useConfigWorkspaceStore()
      store.updateEditor({ row_id: 'row-1', standard_answer: '草稿答案' })
    }
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    app.unmount()
  })

  it('does not let the exam switcher silently discard config edits', async () => {
    const { app, host } = await mountShell()
    const sessionStore = useSessionStore()
    const configStore = useConfigWorkspaceStore()
    const available: SessionSummary[] = [
      { id: 7, name: '考试一', status: 'created', is_deleted: false, deleted_at: null, created_at: null, updated_at: null },
      { id: 9, name: '考试二', status: 'created', is_deleted: false, deleted_at: null, created_at: null, updated_at: null },
    ]
    sessionStore.sessions = available
    sessionStore.selectSession(7)
    configStore.selectSession(7)
    configStore.setEditor({
      session_id: 7, configured: true, revision: 'a'.repeat(64), rows: [],
      total_score: 0, issues: [], source: null,
    })
    configStore.updateEditor({ row_id: 'row-1', standard_answer: '未保存答案' })
    const confirmApp = mountConfirmHost()

    const popover = await openExamSwitcher(host)
    const row = switcherRows(popover).find(el => el.textContent?.includes('考试二'))!
    row.click()
    await settleUi()

    expect(confirmDialog().textContent).toContain('切换考试？')
    confirmDialogButton('取消').click()
    await settleUi()

    /* 守卫拒绝：浮层保持打开、选择不变 */
    expect(document.body.querySelector('.exam-switcher-popover')).not.toBeNull()
    expect(sessionStore.selectedSessionId).toBe(7)
    expect(configStore.sessionId).toBe(7)
    expect(configStore.editorEdits[0]?.standard_answer).toBe('未保存答案')
    app.unmount()
    confirmApp.unmount()
  })

  it.each(['generation', 'upload'] as const)(
    'blocks exam switching while an unknown %s result still needs reconciliation',
    async (kind) => {
      const { app, host } = await mountShell()
      const sessionStore = useSessionStore()
      const configStore = useConfigWorkspaceStore()
      sessionStore.sessions = [
        { id: 7, name: '考试一', status: 'created', is_deleted: false, deleted_at: null, created_at: null, updated_at: null },
        { id: 9, name: '考试二', status: 'created', is_deleted: false, deleted_at: null, created_at: null, updated_at: null },
      ]
      sessionStore.selectSession(7)
      configStore.selectSession(7)
      if (kind === 'generation') configStore.markJobSubmissionPending('1'.repeat(32), 'retry')
      else configStore.markUploadSubmissionPending('2'.repeat(32))
      const confirmApp = mountConfirmHost()

      const popover = await openExamSwitcher(host)
      const row = switcherRows(popover).find(el => el.textContent?.includes('考试二'))!
      row.click()
      await settleUi()

      expect(confirmDialog().textContent).toContain('核对')
      confirmDialogButton('知道了').click()
      await settleUi()
      expect(document.body.querySelector('[data-testid="app-confirm-dialog"]')).toBeNull()
      expect(document.body.querySelector('.exam-switcher-popover')).not.toBeNull()
      expect(sessionStore.selectedSessionId).toBe(7)
      expect(configStore.sessionId).toBe(7)
      app.unmount()
      confirmApp.unmount()
    },
  )

  describe('command palette', () => {
    async function openPalette(): Promise<HTMLInputElement> {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true }))
      await settleUi()
      const input = document.body.querySelector<HTMLInputElement>('.command-palette__input')
      expect(input).not.toBeNull()
      return input!
    }

    function paletteItems(): HTMLElement[] {
      return [...document.body.querySelectorAll<HTMLElement>('.command-palette__item')]
    }

    async function typeQuery(input: HTMLInputElement, value: string): Promise<void> {
      input.value = value
      input.dispatchEvent(new Event('input', { bubbles: true }))
      await settleUi()
    }

    it('opens with Ctrl+K and navigates to the highlighted page item', async () => {
      const { app, router } = await mountShell({ path: '/workbench' })
      const input = await openPalette()

      await typeQuery(input, '批改')
      expect(paletteItems().map(item => item.textContent)).toEqual(
        expect.arrayContaining([expect.stringContaining('考试批改')]),
      )
      expect(paletteItems().map(item => item.textContent)).not.toEqual(
        expect.arrayContaining([expect.stringContaining('成绩中心')]),
      )

      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/grading?scope=all'))
      expect(document.body.querySelector('.command-palette')).toBeNull()
      app.unmount()
    })

  })
})
