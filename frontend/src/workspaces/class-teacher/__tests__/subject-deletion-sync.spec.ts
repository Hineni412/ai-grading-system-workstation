import { createApp, h, nextTick, ref, type App } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/errors'

vi.mock('../components/PlanningInboxPanel.vue', () => ({
  default: { render: () => null },
}))
vi.mock('../components/SopWorkspacePanel.vue', () => ({
  default: { render: () => null },
}))

import {
  actionApi,
  type ActionDashboard,
  type ActionItem,
  type WorkPlan,
} from '../api/actions'
import { collectionApi } from '../api/collections'
import {
  supportApi,
  type AttentionCard,
  type SupportSubject,
} from '../api/support'
import { sopApi } from '../api/sop'
import CollectionInboxPanel from '../components/CollectionInboxPanel.vue'
import SupportWorkspacePanel from '../components/SupportWorkspacePanel.vue'

const mounted: App[] = []

const subjectA: SupportSubject = {
  subject_id: 'synthetic-subject-a',
  revision: 1,
  source_student_id: 'SYN-STU-A',
  display_name: '匿名学生 A',
  class_label: '合成七年级',
}

const subjectB: SupportSubject = {
  subject_id: 'synthetic-subject-b',
  revision: 1,
  source_student_id: 'SYN-STU-B',
  display_name: '匿名学生 B',
  class_label: '合成七年级',
}

const plan: WorkPlan = {
  plan_id: 'synthetic-plan',
  revision: 1,
  title: '合成学生支持复查',
  description: null,
  final_deadline: null,
  created_at: '2026-07-30T00:00:00.000Z',
  updated_at: '2026-07-30T00:00:00.000Z',
}

const derivedAction: ActionItem = {
  action_id: 'synthetic-derived-action',
  plan_id: plan.plan_id,
  revision: 1,
  title: '了解近期情况',
  details: '合成关注卡形成的唯一跟进行动',
  status: 'pending',
  due_at: null,
  waiting_for_kind: null,
  review_at: null,
  completion_result: null,
  completed_at: null,
  reopened_count: 0,
  transition_history: [],
  depends_on_action_ids: [],
  created_at: '2026-07-30T00:00:00.000Z',
  updated_at: '2026-07-30T00:00:00.000Z',
}

const attentionCard: AttentionCard = {
  attention_card_id: 'synthetic-attention',
  revision: 2,
  subject_id: subjectA.subject_id,
  evidence_version_id: 'synthetic-evidence',
  state: 'resolved',
  decision: 'follow_up',
  action_id: derivedAction.action_id,
  observed_fact: '合成数学单元检查记录为缺考。',
  evidence_source: {
    assessment_title: '合成数学单元检查',
    occurred_on: '2026-07-29',
  },
  comparability: '信息不足',
  limitations: ['当前只有单条证据，不能形成稳定结论'],
  verification_question: '近期学习安排或考试条件是否发生变化？',
  low_risk_next_step: '建议教师先了解近期情况',
  evidence_sufficiency: '单条证据，仅能提示进一步了解',
  review_suggestion: '结合后续同口径证据复查',
  risk_score: null,
}

function dashboard(actions: ActionItem[]): ActionDashboard {
  return {
    as_of: '2026-07-30T00:00:00.000Z',
    today: structuredClone(actions),
    overdue: [],
    upcoming: [],
    waiting: [],
    unscheduled: [],
  }
}

async function flush(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function flushAll(): Promise<void> {
  for (let index = 0; index < 8; index += 1) await flush()
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

function deferred<T>(): {
  promise: Promise<T>
  resolve: (value: T) => void
} {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((complete) => {
    resolve = complete
  })
  return { promise, resolve }
}

async function mountSupportDeletionWithRefreshFailure(error: unknown): Promise<{
  host: HTMLElement
  confirmed: string[]
  errors: string[]
  lockedMessages: Array<string | undefined>
}> {
  vi.spyOn(actionApi, 'listPlans').mockResolvedValue([plan])
  vi.spyOn(sopApi, 'listTemplates').mockResolvedValue([])
  vi.spyOn(supportApi, 'listSubjects')
    .mockResolvedValueOnce([subjectA, subjectB])
    .mockRejectedValueOnce(error)
  vi.spyOn(supportApi, 'listRecords').mockResolvedValue([])
  vi.spyOn(supportApi, 'listEvidence').mockResolvedValue([])
  vi.spyOn(supportApi, 'listAttention').mockResolvedValue([attentionCard])
  vi.spyOn(supportApi, 'getSummary').mockResolvedValue({
    subject_id: subjectA.subject_id,
    as_of: '2026-07-30T00:00:00.000Z',
    items: [],
    source_record_count: 0,
  })
  vi.spyOn(supportApi, 'listSupportPlans').mockResolvedValue([])
  vi.spyOn(supportApi, 'listQuickInbox').mockResolvedValue([])
  vi.spyOn(supportApi, 'previewSubjectDeletion').mockResolvedValue({
    subject_id: subjectA.subject_id,
    affected_backup_count: 0,
    affected_backups: [],
    shared_object_count: 0,
    shared_objects: [],
    delete_confirmation_phrase: '确认完整删除学生支持数据',
    backup_confirmation_phrase: null,
  })
  vi.spyOn(supportApi, 'deleteSubject').mockResolvedValue({})

  const confirmed: string[] = []
  const errors: string[] = []
  const lockedMessages: Array<string | undefined> = []
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(SupportWorkspacePanel, {
    sessionToken: 'synthetic-session',
    actionPlans: [plan],
    onConfirmed: (result: string) => confirmed.push(result),
    onError: (message: string) => errors.push(message),
    onLocked: (message?: string) => lockedMessages.push(message),
  })
  app.mount(host)
  mounted.push(app)
  await flushAll()

  const deleteSummary = [...host.querySelectorAll<HTMLElement>('summary')]
    .find((item) => item.textContent?.includes(`完整删除 ${subjectA.display_name}`))
  if (!deleteSummary) throw new Error('Missing subject deletion controls')
  deleteSummary.click()
  await nextTick()
  buttons(host, '查看删除影响')[0]!.click()
  await flushAll()

  const confirmation = host.querySelector<HTMLInputElement>(
    'input[placeholder="输入：确认完整删除学生支持数据"]',
  )
  if (!confirmation) throw new Error('Missing subject deletion confirmation')
  setValue(confirmation, '确认完整删除学生支持数据')
  await nextTick()
  buttons(host, '完整删除')[0]!.click()
  await flushAll()

  return { host, confirmed, errors, lockedMessages }
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('student deletion current-page consistency', () => {
  it('reports that deletion already completed when the B04 reread fails', async () => {
    const refreshKey = ref(0)
    const errors: string[] = []
    vi.spyOn(collectionApi, 'listInboxes').mockResolvedValue([])
    vi.spyOn(collectionApi, 'listBoards').mockResolvedValue([])
    vi.spyOn(actionApi, 'listActions')
      .mockResolvedValueOnce([derivedAction])
      .mockRejectedValueOnce(new Error('synthetic post-delete refresh failure'))
    vi.spyOn(actionApi, 'dashboard').mockResolvedValue(dashboard([derivedAction]))

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp({
      setup() {
        return () => h(CollectionInboxPanel, {
          sessionToken: 'synthetic-session',
          actionRefreshKey: refreshKey.value,
          actionRefreshReason: 'subject_deleted',
          onError: (message: string) => errors.push(message),
        })
      },
    })
    app.mount(host)
    mounted.push(app)
    await flushAll()

    refreshKey.value += 1
    await flushAll()

    expect(errors).toEqual([
      '学生支持数据已经删除，但 B04 行动选项暂时无法重新读取，请稍后重试。',
    ])
  })

  it('keeps the completed-deletion fact when the B04 reread finds a locked vault', async () => {
    const refreshKey = ref(0)
    const lockedMessages: Array<string | undefined> = []
    vi.spyOn(collectionApi, 'listInboxes').mockResolvedValue([])
    vi.spyOn(collectionApi, 'listBoards').mockResolvedValue([])
    vi.spyOn(actionApi, 'listActions')
      .mockResolvedValueOnce([derivedAction])
      .mockRejectedValueOnce(new ApiError({
        kind: 'forbidden',
        status: 403,
        code: 'vault_locked',
        message: '保险箱已锁定',
        details: {},
        requestId: 'synthetic-request',
        retryable: false,
      }))
    vi.spyOn(actionApi, 'dashboard').mockResolvedValue(dashboard([derivedAction]))

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp({
      setup() {
        return () => h(CollectionInboxPanel, {
          sessionToken: 'synthetic-session',
          actionRefreshKey: refreshKey.value,
          actionRefreshReason: 'subject_deleted',
          onLocked: (message?: string) => lockedMessages.push(message),
        })
      },
    })
    app.mount(host)
    mounted.push(app)
    await flushAll()

    refreshKey.value += 1
    await flushAll()

    expect(lockedMessages).toEqual([
      '学生支持数据已经删除，但 B04 行动选项暂时无法重新读取；保险箱已锁定，请重新解锁后核对。',
    ])
  })

  it('does not let an older action response restore a deleted action', async () => {
    const refreshKey = ref(0)
    const oldActions = deferred<ActionItem[]>()
    const oldDashboard = deferred<ActionDashboard>()
    vi.spyOn(collectionApi, 'listInboxes').mockResolvedValue([])
    vi.spyOn(collectionApi, 'listBoards').mockResolvedValue([])
    vi.spyOn(actionApi, 'listActions')
      .mockImplementationOnce(async () => oldActions.promise)
      .mockResolvedValueOnce([])
    vi.spyOn(actionApi, 'dashboard')
      .mockImplementationOnce(async () => oldDashboard.promise)
      .mockResolvedValueOnce(dashboard([]))

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp({
      setup() {
        return () => h(CollectionInboxPanel, {
          sessionToken: 'synthetic-session',
          actionRefreshKey: refreshKey.value,
          actionRefreshReason: 'subject_deleted',
        })
      },
    })
    app.mount(host)
    mounted.push(app)
    await flush()

    refreshKey.value += 1
    await flushAll()
    oldActions.resolve([structuredClone(derivedAction)])
    oldDashboard.resolve(dashboard([derivedAction]))
    await flushAll()

    const b04Action = labelledControl<HTMLSelectElement>(host, '关联正式行动')
    expect([...b04Action.options].map((option) => option.text))
      .not.toContain(derivedAction.title)
  })

  it('notifies the page before reporting a post-delete support reread failure', async () => {
    const { host, confirmed, errors, lockedMessages } =
      await mountSupportDeletionWithRefreshFailure(
        new Error('synthetic support reread failure'),
      )

    expect(confirmed).toEqual(['subject_deleted'])
    expect(host.textContent).toContain(subjectB.display_name)
    expect(host.textContent).not.toContain(subjectA.display_name)
    expect(host.textContent).not.toContain(attentionCard.observed_fact)
    expect(errors).toEqual([
      '学生支持数据及受影响旧备份已经删除，但支持区最新状态暂时无法重新读取，请稍后重试。',
    ])
    expect(lockedMessages).toEqual([])
  })

  it('keeps the completed-deletion fact when the support reread finds a locked vault', async () => {
    const { confirmed, errors, lockedMessages } =
      await mountSupportDeletionWithRefreshFailure(new ApiError({
        kind: 'forbidden',
        status: 403,
        code: 'vault_locked',
        message: '保险箱已锁定',
        details: {},
        requestId: 'synthetic-support-request',
        retryable: false,
      }))

    expect(confirmed).toEqual(['subject_deleted'])
    expect(errors).toEqual([])
    expect(lockedMessages).toEqual([
      '学生支持数据及受影响旧备份已经删除，但支持区最新状态暂时无法重新读取；保险箱已锁定，请重新解锁后核对。',
    ])
  })
})
