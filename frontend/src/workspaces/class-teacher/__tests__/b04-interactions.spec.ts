import { createApp, h, nextTick, ref, type App } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../components/PlanningInboxPanel.vue', () => ({
  default: { render: () => null },
}))
vi.mock('../components/SopWorkspacePanel.vue', () => ({
  default: { render: () => null },
}))
vi.mock('../components/SupportWorkspacePanel.vue', () => ({
  default: { render: () => null },
}))

import {
  actionApi,
  type ActionDashboard,
  type ActionItem,
  type SchoolCalendar,
  type WorkPlan,
} from '../api/actions'
import { collectionApi, type MeetingDraft, type MeetingInbox } from '../api/collections'
import { vaultApi, type VaultStatus } from '../api/vault'
import CollectionInboxPanel from '../components/CollectionInboxPanel.vue'
import ActionLedgerPanel from '../components/ActionLedgerPanel.vue'
import ClassTeacherWorkbenchView from '../views/ClassTeacherWorkbenchView.vue'

const mounted: App[] = []

function draft(id: string, title: string, deadline: string): MeetingDraft {
  return {
    meeting_draft_id: id,
    title,
    objective: `${deadline.slice(0, 10)} ${title}`,
    deadline_date: deadline.slice(0, 10),
    final_deadline: deadline,
    deadline_source: 'teacher_edited',
    actions: [{
      draft_action_id: `${id}-action`,
      title: `${title}行动`,
      details: null,
      due_at: deadline,
      depends_on_draft_action_ids: [],
    }],
    unknowns: [],
    sensitive_findings: [],
    is_late: false,
    template_kind: 'general',
  }
}

function inbox(): MeetingInbox {
  return {
    inbox_id: 'synthetic-inbox',
    revision: 1,
    status: 'draft',
    raw_text: '合成会议任务',
    source_deleted: false,
    delete_source_after_confirm: true,
    model_enabled: false,
    physical_request_count: 0,
    drafts: [
      draft('draft-a', '收齐合成回执', '2026-08-14T09:30:00.000Z'),
      draft('draft-b', '完成合成材料', '2026-08-18T09:30:00.000Z'),
    ],
    confirmed_plan_ids: [],
    confirmed_action_ids: [],
    created_at: '2026-07-30T00:00:00.000Z',
    updated_at: '2026-07-30T00:00:00.000Z',
  }
}

function dashboard(): ActionDashboard {
  return {
    as_of: '2026-07-30T00:00:00.000Z',
    today: [],
    overdue: [],
    upcoming: [],
    waiting: [],
    unscheduled: [],
  }
}

const plan: WorkPlan = {
  plan_id: 'synthetic-plan',
  revision: 1,
  title: '合成回执目标',
  description: null,
  final_deadline: '2026-08-14T09:30:00.000Z',
  created_at: '2026-07-30T00:00:00.000Z',
  updated_at: '2026-07-30T00:00:00.000Z',
}

function action(status: ActionItem['status'] = 'pending'): ActionItem {
  return {
    action_id: 'synthetic-action',
    plan_id: plan.plan_id,
    revision: status === 'pending' ? 1 : 2,
    title: '完成最终收齐与核对',
    details: '等待一位合成家长回复',
    status,
    due_at: '2026-08-13T09:30:00.000Z',
    waiting_for_kind: status === 'waiting' ? '合成家长回复' : null,
    review_at: status === 'waiting' ? '2026-08-01T01:00:00.000Z' : null,
    completion_result: null,
    completed_at: null,
    reopened_count: 0,
    transition_history: [],
    depends_on_action_ids: [],
    created_at: '2026-07-30T00:00:00.000Z',
    updated_at: '2026-07-30T00:00:00.000Z',
  }
}

const calendar: SchoolCalendar = {
  configured: true,
  revision: 1,
  school_day_end: '17:30',
  locked_dates: [],
  working_weekdays: [1, 2, 3, 4, 5],
}

const lockedStatus: VaultStatus = {
  initialized: true,
  locked: true,
  idle_timeout_seconds: 300,
  retry_after_seconds: 0,
  format_version: 1,
}

async function flush(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function flushAll(): Promise<void> {
  for (let index = 0; index < 6; index += 1) await flush()
}

async function mountCollection(): Promise<{
  host: HTMLElement
  errors: unknown[]
}> {
  vi.spyOn(collectionApi, 'listInboxes').mockResolvedValue([inbox()])
  vi.spyOn(collectionApi, 'listBoards').mockResolvedValue([])
  vi.spyOn(actionApi, 'listActions').mockResolvedValue([])
  vi.spyOn(actionApi, 'dashboard').mockResolvedValue(dashboard())

  const host = document.createElement('div')
  document.body.append(host)
  const errors: unknown[] = []
  const app = createApp(CollectionInboxPanel, { sessionToken: 'synthetic-token' })
  app.config.errorHandler = (error) => errors.push(error)
  app.mount(host)
  mounted.push(app)
  await flush()
  return { host, errors }
}

function buttons(host: HTMLElement, label: string): HTMLButtonElement[] {
  return [...host.querySelectorAll<HTMLButtonElement>('button')]
    .filter((button) => button.textContent?.trim() === label)
}

function setValue(
  control: HTMLInputElement | HTMLSelectElement,
  value: string,
): void {
  control.value = value
  control.dispatchEvent(new Event(
    control instanceof HTMLSelectElement ? 'change' : 'input',
    { bubbles: true },
  ))
}

function labelledControl<T extends HTMLInputElement | HTMLSelectElement>(
  host: HTMLElement,
  label: string,
): T {
  const wrapper = [...host.querySelectorAll<HTMLLabelElement>('label')]
    .find((item) => item.querySelector('span')?.textContent?.trim() === label)
  const control = wrapper?.querySelector<T>('input, select')
  if (!control) throw new Error(`Missing labelled control: ${label}`)
  return control
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('B04 meeting draft interactions', () => {
  it('splits a visible meeting draft without interrupting the page', async () => {
    const { host, errors } = await mountCollection()
    const splitButtons = buttons(host, '拆成两项')
    expect(splitButtons).toHaveLength(2)

    splitButtons[0]!.click()
    await nextTick()

    expect(errors).toEqual([])
    expect(host.querySelectorAll('.meeting-card')).toHaveLength(3)
    const values = [...host.querySelectorAll<HTMLInputElement>('.meeting-card input')]
      .map((input) => input.value)
    expect(values).toContain('收齐合成回执（拆分项）')
    expect(values).toContain('收齐合成回执行动')
  })

  it('merges drafts with different deadlines and keeps confirmation blocked', async () => {
    const { host, errors } = await mountCollection()
    const mergeButtons = buttons(host, '与下一项合并')
    expect(mergeButtons).toHaveLength(2)
    expect(mergeButtons[0]!.disabled).toBe(false)

    mergeButtons[0]!.click()
    await nextTick()

    expect(errors).toEqual([])
    expect(host.querySelectorAll('.meeting-card')).toHaveLength(1)
    const values = [...host.querySelectorAll<HTMLInputElement>('.meeting-card input')]
      .map((input) => input.value)
    expect(values).toContain('收齐合成回执 / 完成合成材料')
    const deadlineValues = [
      ...host.querySelectorAll<HTMLInputElement>('.meeting-card input[type="datetime-local"]'),
    ].map((input) => input.value)
    expect(deadlineValues).toEqual(['', '', ''])
    expect(host.textContent).toContain('请补齐最终截止时间和每个行动时间。')
    expect(buttons(host, '一次写入全部正式行动')[0]!.disabled).toBe(true)
  })
})

describe('B04 waiting review consistency', () => {
  it('updates the B04 waiting pocket after an action is saved without a page reload', async () => {
    let currentAction = action()
    vi.spyOn(vaultApi, 'status').mockImplementation(async (token) => ({
      ...lockedStatus,
      locked: !token,
    }))
    vi.spyOn(vaultApi, 'unlock').mockResolvedValue({
      session_token: 'synthetic-session',
      idle_timeout_seconds: 300,
      recovery_key: null,
    })
    vi.spyOn(vaultApi, 'listBackups').mockResolvedValue([])
    vi.spyOn(vaultApi, 'lock').mockResolvedValue({})
    vi.spyOn(vaultApi, 'touch').mockResolvedValue({})

    vi.spyOn(collectionApi, 'listInboxes').mockResolvedValue([])
    vi.spyOn(collectionApi, 'listBoards').mockResolvedValue([])
    vi.spyOn(actionApi, 'listPlans').mockResolvedValue([plan])
    vi.spyOn(actionApi, 'listActions').mockImplementation(
      async () => [structuredClone(currentAction)],
    )
    vi.spyOn(actionApi, 'dashboard').mockImplementation(async () => ({
      ...dashboard(),
      today: currentAction.status === 'pending'
        ? [structuredClone(currentAction)]
        : [],
      waiting: currentAction.status === 'waiting'
        ? [structuredClone(currentAction)]
        : [],
    }))
    vi.spyOn(actionApi, 'getCalendar').mockResolvedValue(calendar)
    const updateAction = vi.spyOn(actionApi, 'updateAction')
      .mockImplementation(async () => {
        currentAction = action('waiting')
        return structuredClone(currentAction)
      })

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(ClassTeacherWorkbenchView)
    app.mount(host)
    mounted.push(app)
    await flushAll()

    const password = host.querySelector<HTMLInputElement>('input[type="password"]')
    if (!password) throw new Error('Missing vault password input')
    setValue(password, '合成B04验收密码-足够长-001')
    await nextTick()
    buttons(host, '解锁工作台')[0]!.click()
    await flushAll()

    const actionButton = [...host.querySelectorAll<HTMLButtonElement>('.action-list button')]
      .find((button) => button.textContent?.includes(currentAction.title))
    if (!actionButton) throw new Error('Missing synthetic action')
    actionButton.click()
    await nextTick()

    setValue(
      labelledControl<HTMLSelectElement>(host, '当前状态'),
      'waiting',
    )
    await nextTick()
    setValue(
      labelledControl<HTMLInputElement>(host, '正在等待谁或什么'),
      '合成家长回复',
    )
    setValue(
      labelledControl<HTMLInputElement>(host, '复查时间'),
      '2026-08-01T09:00',
    )
    await nextTick()
    buttons(host, '保存行动变化')[0]!.click()
    await flushAll()

    expect(updateAction).toHaveBeenCalledOnce()
    const waitingPocket = host.querySelector('.waiting-pocket')
    expect(waitingPocket?.textContent).toContain('完成最终收齐与核对')
    expect(waitingPocket?.textContent).toContain('合成家长回复')
    expect(waitingPocket?.textContent).toContain('2026-08-01 09:00')
  })

  it('does not notify B04 when the action ledger cannot refresh after saving', async () => {
    const changed = vi.fn()
    const errors: string[] = []
    vi.spyOn(actionApi, 'listPlans')
      .mockResolvedValueOnce([plan])
      .mockRejectedValueOnce(new Error('synthetic refresh failure'))
    vi.spyOn(actionApi, 'listActions').mockResolvedValue([action()])
    vi.spyOn(actionApi, 'dashboard').mockResolvedValue({
      ...dashboard(),
      today: [action()],
    })
    vi.spyOn(actionApi, 'getCalendar').mockResolvedValue(calendar)
    vi.spyOn(actionApi, 'updateAction').mockResolvedValue(action('waiting'))

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(ActionLedgerPanel, {
      sessionToken: 'synthetic-token',
      onChanged: changed,
      onError: (message: string) => errors.push(message),
    })
    app.mount(host)
    mounted.push(app)
    await flushAll()

    const actionButton = host.querySelector<HTMLButtonElement>('.action-list button')
    if (!actionButton) throw new Error('Missing synthetic action')
    actionButton.click()
    await nextTick()
    setValue(labelledControl<HTMLSelectElement>(host, '当前状态'), 'waiting')
    await nextTick()
    setValue(
      labelledControl<HTMLInputElement>(host, '正在等待谁或什么'),
      '合成家长回复',
    )
    setValue(
      labelledControl<HTMLInputElement>(host, '复查时间'),
      '2026-08-01T09:00',
    )
    await nextTick()
    buttons(host, '保存行动变化')[0]!.click()
    await flushAll()

    expect(actionApi.updateAction).toHaveBeenCalledOnce()
    expect(changed).not.toHaveBeenCalled()
    expect(errors).toEqual([
      '行动已经保存，但行动账本暂时无法重新读取最新内容，请稍后重试。',
    ])
    expect(errors[0]).not.toContain('已保存内容没有改变')
  })

  it('reports a read failure without overwriting an unsaved meeting draft', async () => {
    const refreshKey = ref(0)
    const errors: string[] = []
    vi.spyOn(collectionApi, 'listInboxes').mockResolvedValue([inbox()])
    vi.spyOn(collectionApi, 'listBoards').mockResolvedValue([])
    vi.spyOn(actionApi, 'listActions')
      .mockResolvedValueOnce([])
      .mockRejectedValueOnce(new Error('synthetic B04 refresh failure'))
    vi.spyOn(actionApi, 'dashboard')
      .mockResolvedValueOnce(dashboard())
      .mockResolvedValueOnce(dashboard())

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp({
      setup() {
        return () => h(CollectionInboxPanel, {
          sessionToken: 'synthetic-token',
          actionRefreshKey: refreshKey.value,
          onError: (message: string) => errors.push(message),
        })
      },
    })
    app.mount(host)
    mounted.push(app)
    await flushAll()

    const taskName = labelledControl<HTMLInputElement>(host, '任务名称')
    setValue(taskName, '教师尚未保存的会议草稿')
    await nextTick()
    refreshKey.value += 1
    await flushAll()

    expect(taskName.value).toBe('教师尚未保存的会议草稿')
    expect(errors).toEqual([
      '行动已经保存，但 B04 等待复查信息暂时无法重新读取，请稍后重试。',
    ])
    expect(errors[0]).not.toContain('正式记录没有改变')
  })
})
