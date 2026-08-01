import { createApp, h, nextTick, ref, type App } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/errors'

vi.mock('../components/PlanningInboxPanel.vue', () => ({
  default: { render: () => null },
}))
vi.mock('../components/CollectionInboxPanel.vue', () => ({
  default: { render: () => null },
}))
vi.mock('../components/SopWorkspacePanel.vue', () => ({
  default: { render: () => null },
}))

import {
  actionApi,
  type ActionDashboard,
  type SchoolCalendar,
  type WorkPlan,
} from '../api/actions'
import { sopApi } from '../api/sop'
import {
  supportApi,
  type AttentionCard,
  type SupportSubject,
} from '../api/support'
import ActionLedgerPanel from '../components/ActionLedgerPanel.vue'
import SupportWorkspacePanel from '../components/SupportWorkspacePanel.vue'

const mounted: App[] = []

const subject: SupportSubject = {
  subject_id: 'synthetic-subject',
  revision: 1,
  source_student_id: 'SYN-STU-B',
  display_name: '匿名学生 A',
  class_label: '合成七年级',
}

const attentionCard: AttentionCard = {
  attention_card_id: 'synthetic-attention',
  revision: 1,
  subject_id: subject.subject_id,
  evidence_version_id: 'synthetic-evidence',
  state: 'draft',
  decision: null,
  action_id: null,
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

const calendar: SchoolCalendar = {
  configured: false,
  revision: 0,
  school_day_end: null,
  locked_dates: [],
  working_weekdays: [1, 2, 3, 4, 5],
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

async function flush(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function flushAll(): Promise<void> {
  for (let index = 0; index < 6; index += 1) await flush()
}

function buttons(host: HTMLElement, label: string): HTMLButtonElement[] {
  return [...host.querySelectorAll<HTMLButtonElement>('button')]
    .filter((button) => button.textContent?.trim() === label)
}

function setValue(
  control: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement,
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

function mockSupport(
  attentionItems: () => AttentionCard[] = () => [attentionCard],
): void {
  vi.spyOn(supportApi, 'listSubjects').mockResolvedValue([subject])
  vi.spyOn(supportApi, 'listRecords').mockResolvedValue([])
  vi.spyOn(supportApi, 'listEvidence').mockResolvedValue([])
  vi.spyOn(supportApi, 'listAttention').mockImplementation(
    async () => structuredClone(attentionItems()),
  )
  vi.spyOn(supportApi, 'getSummary').mockResolvedValue({
    subject_id: subject.subject_id,
    as_of: '2026-07-30T00:00:00.000Z',
    items: [],
    source_record_count: 0,
  })
  vi.spyOn(supportApi, 'listSupportPlans').mockResolvedValue([])
  vi.spyOn(supportApi, 'listQuickInbox').mockResolvedValue([])
  vi.spyOn(sopApi, 'listTemplates').mockResolvedValue([])
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('B10 and B02 current-page consistency', () => {
  it('reports that a plan was saved when the post-save ledger refresh fails', async () => {
    const createdPlan: WorkPlan = {
      plan_id: 'synthetic-plan',
      revision: 1,
      title: '已经保存的合成复查目标',
      description: null,
      final_deadline: null,
      created_at: '2026-07-30T00:00:00.000Z',
      updated_at: '2026-07-30T00:00:00.000Z',
    }
    const errors: string[] = []
    vi.spyOn(actionApi, 'listPlans')
      .mockResolvedValueOnce([])
      .mockRejectedValueOnce(new Error('synthetic plan refresh failure'))
    vi.spyOn(actionApi, 'listActions').mockResolvedValue([])
    vi.spyOn(actionApi, 'dashboard').mockResolvedValue(dashboard())
    vi.spyOn(actionApi, 'getCalendar').mockResolvedValue(calendar)
    vi.spyOn(actionApi, 'createPlan').mockResolvedValue(createdPlan)

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(ActionLedgerPanel, {
      sessionToken: 'synthetic-token',
      onError: (message: string) => errors.push(message),
    })
    app.mount(host)
    mounted.push(app)
    await flushAll()

    const settings = host.querySelector<HTMLElement>('summary')
    if (!settings) throw new Error('Missing work plan settings')
    settings.click()
    await nextTick()
    setValue(
      labelledControl<HTMLInputElement>(host, '目标名称'),
      createdPlan.title,
    )
    await nextTick()
    buttons(host, '保存工作目标')[0]!.click()
    await flushAll()

    expect(actionApi.createPlan).toHaveBeenCalledOnce()
    expect(errors).toEqual([
      '工作目标已经保存，但行动账本暂时无法重新读取最新内容，请稍后重试。',
    ])
    expect(errors[0]).not.toContain('已保存内容没有改变')
  })

  it('keeps a newer B02 plan snapshot when the initial B10 plan read finishes later', async () => {
    const createdPlan: WorkPlan = {
      plan_id: 'synthetic-plan',
      revision: 1,
      title: '合成学生支持复查',
      description: null,
      final_deadline: null,
      created_at: '2026-07-30T00:00:00.000Z',
      updated_at: '2026-07-30T00:00:00.000Z',
    }
    const latestPlans = ref<WorkPlan[] | null>(null)
    let releaseInitialPlans!: (value: WorkPlan[]) => void

    mockSupport()
    vi.spyOn(actionApi, 'listPlans').mockImplementation(
      () => new Promise((resolve) => {
        releaseInitialPlans = resolve
      }),
    )

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp({
      setup() {
        return () => h(SupportWorkspacePanel, {
          sessionToken: 'synthetic-token',
          actionPlans: latestPlans.value,
        })
      },
    })
    app.mount(host)
    mounted.push(app)
    await flush()

    latestPlans.value = [createdPlan]
    await flush()
    releaseInitialPlans([])
    await flushAll()

    const followUpPlan = labelledControl<HTMLSelectElement>(host, '跟进行动放入')
    expect([...followUpPlan.options].map((option) => option.textContent?.trim()))
      .toContain(createdPlan.title)
    expect(followUpPlan.value).toBe(createdPlan.plan_id)
  })

  it('keeps the saved decision fact when the B10 read-back locks the vault', async () => {
    const plan: WorkPlan = {
      plan_id: 'synthetic-plan',
      revision: 1,
      title: '合成学生支持复查',
      description: null,
      final_deadline: null,
      created_at: '2026-07-30T00:00:00.000Z',
      updated_at: '2026-07-30T00:00:00.000Z',
    }
    const lockedReasons: string[] = []
    let confirmedCount = 0

    mockSupport()
    vi.spyOn(actionApi, 'listPlans').mockResolvedValue([plan])
    vi.spyOn(supportApi, 'resolveAttention').mockResolvedValue({
      attention_card_id: attentionCard.attention_card_id,
      decision: 'follow_up',
      action_id: 'synthetic-action',
      action_created: true,
    })

    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(SupportWorkspacePanel, {
      sessionToken: 'synthetic-token',
      actionPlans: [plan],
      onConfirmed: () => { confirmedCount += 1 },
      onLocked: (reason?: string) => lockedReasons.push(reason ?? ''),
    })
    app.mount(host)
    mounted.push(app)
    await flushAll()

    vi.mocked(supportApi.listAttention).mockRejectedValueOnce(new ApiError({
      kind: 'forbidden',
      status: 403,
      code: 'vault_locked',
      message: '保险箱已锁定',
      details: {},
      requestId: 'synthetic-request',
      retryable: false,
    }))
    setValue(
      labelledControl<HTMLSelectElement>(host, '跟进行动放入'),
      plan.plan_id,
    )
    setValue(
      labelledControl<HTMLInputElement>(host, '暂时观察复查时间'),
      '2026-08-06T16:00',
    )
    await nextTick()
    buttons(host, '需要跟进')[0]!.click()
    await flushAll()

    expect(confirmedCount).toBe(1)
    expect(lockedReasons).toEqual([
      '关注卡处置和对应行动已经保存，但支持区最新状态暂时无法重新读取；保险箱已锁定，请重新解锁后核对。',
    ])
  })
})
