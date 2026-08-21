import { createApp, nextTick, type App, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { studentR1Api } from '../api/r1'
import { supportApi, type SupportPlan, type SupportRecord } from '../api/support'
import StudentOverviewPanel from '../students/StudentOverviewPanel.vue'
import StudentSurface from '../students/StudentSurface.vue'
import SupportActionPanel from '../students/SupportActionPanel.vue'

const apps: App[] = []
async function mount(component: Component, props: Record<string, unknown>) {
  const host = document.createElement('div'); document.body.append(host)
  const app = createApp(component, props); app.mount(host); apps.push(app)
  await nextTick(); await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()
  return host
}
function clickByText(host: HTMLElement, text: string) {
  const button = [...host.querySelectorAll('button')].find((item) => item.textContent?.includes(text))
  if (!button) throw new Error(`button not found: ${text}`)
  button.dispatchEvent(new MouseEvent('click', { bubbles: true })); return button
}
function setInput(host: HTMLElement, selector: string, value: string) {
  const field = host.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector)
  if (!field) throw new Error(`field not found: ${selector}`)
  field.value = value
  field.dispatchEvent(new Event('input', { bubbles: true }))
}
function submitForm(host: HTMLElement, selector: string) {
  const form = host.querySelector(selector)
  if (!form) throw new Error(`form not found: ${selector}`)
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
}
async function flush() {
  await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()
}
afterEach(() => { apps.splice(0).forEach((app) => app.unmount()); document.body.innerHTML=''; window.history.replaceState({}, '', '/'); vi.restoreAllMocks() })

const subject = { subject_id:'s1', display_name:'合成学生', source_student_id:'S1', class_label:'一班', support_record_count:0, support_plan_count:1, confirmed_entry_count:0, projection_state:'none', attention_pending_count:0, last_confirmed_at:null }

function planFixture(overrides: Partial<SupportPlan> = {}): SupportPlan {
  return {
    support_plan_id:'plan-1', subject_id:'s1', revision:1, action_id:null, state:'active',
    review_at:'2099-08-20', goal:'两周内形成稳定的任务核对习惯',
    support_actions:['课前提供步骤卡','课后由教师复查一次'],
    result:null, outcome:null, completed_at:null,
    created_at:'2026-08-01', updated_at:'2026-08-01',
    ...overrides,
  }
}

function recordFixture(overrides: Partial<SupportRecord> = {}): SupportRecord {
  return {
    record_id:'r1', subject_id:'s1', record_kind:'fact', state:'active', current_revision:1,
    plan_id:null, content:'合成：课前发了步骤卡', scene:'支持行动', source:'教师本人记录',
    counterexample:null, observed_at:'2026-08-12', review_at:null, expires_at:null,
    ...overrides,
  }
}

const emptyProfile = { entry_id:null, revision:0, summary:'', dimensions:[], open_questions:[], support_focus:[], updated_at:null }
function cardFixture(overrides: Record<string, unknown> = {}) {
  return {
    subject, entries:[],
    current_profile:{ ...emptyProfile, summary:'能按计划完成任务。', entry_id:'entry-1', revision:1, updated_at:'2026-08-01' },
    existing_records:[], support_plans:[],
    ...overrides,
  }
}

describe('支持行动页（单学生干预闭环）', () => {
  it('runs the full loop: create plan, log an action, complete as effective', async () => {
    const plans: SupportPlan[] = []
    const records: SupportRecord[] = []
    vi.spyOn(supportApi, 'listSupportPlans').mockImplementation(async () => [...plans])
    vi.spyOn(supportApi, 'listRecords').mockImplementation(async () => [...records])
    const createPlan = vi.spyOn(supportApi, 'createSupportPlan').mockImplementation(async (_id, input) => {
      const plan = planFixture({ goal:input.goal, support_actions:input.support_actions, review_at:input.review_at })
      plans.push(plan); return plan
    })
    const createRecord = vi.spyOn(supportApi, 'createRecord').mockImplementation(async (_id, input) => {
      const record = recordFixture({ content:input.content, observed_at:input.observed_at, plan_id:input.plan_id ?? null })
      records.push(record); return record
    })
    const complete = vi.spyOn(supportApi, 'completeSupportPlan').mockImplementation(async (planId, revision, result, outcome) => {
      const index = plans.findIndex((item) => item.support_plan_id === planId)
      plans[index] = { ...plans[index]!, state:'completed', revision:revision + 1, result, outcome: outcome ?? null, completed_at:'2026-08-19' }
      return plans[index]!
    })

    const host = await mount(SupportActionPanel, { subject })
    expect(host.textContent).toContain('还没有进行中的方案')
    expect(host.textContent).toContain('方案由 AI 起草、你核对保存')

    clickByText(host, '＋ 新建方案'); await nextTick()
    setInput(host, 'form.plan-form input', '两周内形成稳定的任务核对习惯')
    setInput(host, 'form.plan-form textarea', '课前提供步骤卡\n课后由教师复查一次')
    setInput(host, 'form.plan-form input[type="date"]', '2099-08-20')
    submitForm(host, 'form.plan-form'); await flush()
    expect(createPlan).toHaveBeenCalledExactlyOnceWith('s1', {
      goal:'两周内形成稳定的任务核对习惯',
      support_actions:['课前提供步骤卡','课后由教师复查一次'],
      review_at:'2099-08-20',
    })
    expect(host.textContent).toContain('方案已保存')
    expect(host.textContent).toContain('课前提供步骤卡')
    expect(host.textContent).toContain('还没有行动记录')

    clickByText(host, '记一次行动'); await nextTick()
    setInput(host, 'form.inline-form textarea', '课前发了步骤卡，学生当堂完成核对')
    submitForm(host, 'form.inline-form'); await flush()
    expect(createRecord).toHaveBeenCalledExactlyOnceWith('s1', expect.objectContaining({
      record_kind:'fact', scene:'支持行动', plan_id:'plan-1',
    }))
    expect(host.textContent).toContain('已记到这个方案下')
    expect(host.textContent).toContain('课前发了步骤卡，学生当堂完成核对')

    clickByText(host, '完成并评效果'); await nextTick()
    setInput(host, 'form.inline-form textarea', '复查时能独立核对步骤')
    submitForm(host, 'form.inline-form'); await flush()
    expect(complete).toHaveBeenCalledExactlyOnceWith('plan-1', 1, '复查时能独立核对步骤', 'effective')
    expect(host.textContent).toContain('已验证有效')
    expect(host.textContent).toContain('进行中的方案')

    clickByText(host, '已完成方案（1）'); await nextTick()
    expect(host.textContent).toContain('有效')
    expect(host.textContent).toContain('复查时能独立核对步骤')
  })

  it('lets AI draft the plan from the profile, then saves the teacher-edited version', async () => {
    const plans: SupportPlan[] = []
    vi.spyOn(supportApi, 'listSupportPlans').mockImplementation(async () => [...plans])
    vi.spyOn(supportApi, 'listRecords').mockResolvedValue([])
    const draft = vi.spyOn(supportApi, 'draftSupportPlan').mockResolvedValue({
      goal:'AI 起草目标', support_actions:['行动一','行动二'], review_at:'2099-09-01',
    })
    const createPlan = vi.spyOn(supportApi, 'createSupportPlan').mockImplementation(async (_id, input) => {
      const plan = planFixture({ goal:input.goal, support_actions:input.support_actions, review_at:input.review_at })
      plans.push(plan); return plan
    })

    const host = await mount(SupportActionPanel, { subject })
    clickByText(host, '＋ 新建方案'); await nextTick()
    clickByText(host, '让 AI 起草方案'); await flush()

    expect(draft).toHaveBeenCalledExactlyOnceWith('s1', expect.any(String))
    expect(host.textContent).toContain('已按当前档案起草，请核对修改；保存仍是你的决定')
    expect(host.querySelector<HTMLInputElement>('form.plan-form input')?.value).toBe('AI 起草目标')
    expect(host.querySelector<HTMLTextAreaElement>('form.plan-form textarea')?.value).toBe('行动一\n行动二')
    expect(host.querySelector<HTMLInputElement>('form.plan-form input[type="date"]')?.value).toBe('2099-09-01')

    setInput(host, 'form.plan-form input', '教师修改后的目标')
    submitForm(host, 'form.plan-form'); await flush()
    expect(createPlan).toHaveBeenCalledExactlyOnceWith('s1', {
      goal:'教师修改后的目标',
      support_actions:['行动一','行动二'],
      review_at:'2099-09-01',
    })
    expect(host.textContent).toContain('方案已保存')
  })

  it('keeps the form usable when AI drafting fails', async () => {
    vi.spyOn(supportApi, 'listSupportPlans').mockResolvedValue([])
    vi.spyOn(supportApi, 'listRecords').mockResolvedValue([])
    vi.spyOn(supportApi, 'draftSupportPlan').mockRejectedValue(new Error('当前班主任模型不可用，无法起草方案；可以手动填写'))

    const host = await mount(SupportActionPanel, { subject })
    clickByText(host, '＋ 新建方案'); await nextTick()
    clickByText(host, '让 AI 起草方案'); await flush()

    expect(host.textContent).toContain('起草失败，可重试或手动填写')
    expect(host.textContent).toContain('当前班主任模型不可用')
    expect(host.querySelector('form.plan-form')).toBeTruthy()
  })

  it('flags plans whose review date is due and completes one as continue', async () => {
    const plans = [planFixture({ review_at:'2001-01-01' })]
    vi.spyOn(supportApi, 'listSupportPlans').mockImplementation(async () => [...plans])
    vi.spyOn(supportApi, 'listRecords').mockResolvedValue([])
    const complete = vi.spyOn(supportApi, 'completeSupportPlan').mockImplementation(async (planId, revision, result, outcome) => {
      plans[0] = { ...plans[0]!, state:'completed', revision:revision + 1, result, outcome: outcome ?? null, completed_at:'2026-08-19' }
      return plans[0]!
    })

    const host = await mount(SupportActionPanel, { subject })
    expect(host.textContent).toContain('1 个方案到了复查时间')
    expect(host.textContent).toContain('已到复查时间')

    clickByText(host, '完成并评效果'); await nextTick()
    clickByText(host, '继续观察'); await flush()
    expect(complete).toHaveBeenCalledExactlyOnceWith(
      'plan-1', 1, '继续观察，暂不下结论；之后可另建新一轮方案。', 'continue',
    )
    expect(host.textContent).toContain('之后可另建新一轮方案')
  })

  it('opens the dossier drawer on the support tab from the panel entry', async () => {
    vi.spyOn(studentR1Api, 'header').mockResolvedValue(subject)
    vi.spyOn(supportApi, 'listSupportPlans').mockResolvedValue([])
    vi.spyOn(supportApi, 'listRecords').mockResolvedValue([])
    vi.spyOn(studentR1Api, 'studentCard').mockResolvedValue(cardFixture())

    const host = await mount(StudentSurface, { panel:'support', subjectId:'s1' })
    await vi.waitFor(() => expect(host.textContent).toContain('当前学生：'))

    clickByText(host, '查看/补录观察记录 →'); await flush()
    expect(host.querySelector('[role="dialog"]')).toBeTruthy()
    const tab = host.querySelector('.dossier-tabs button[aria-selected="true"]')
    expect(tab?.textContent).toContain('成长与支持')
  })
})

describe('档案抽屉参考材料区', () => {
  async function mountDrawer(records: SupportRecord[]) {
    vi.spyOn(studentR1Api, 'studentCard').mockResolvedValue(cardFixture())
    vi.spyOn(supportApi, 'listRecords').mockResolvedValue(records)
    const host = await mount(StudentOverviewPanel, { subject, initialTab:'support' })
    clickByText(host, '查看 0 条原始记录'); await flush()
    return host
  }

  it('adds an observation record locally with zero AI requests', async () => {
    const host = await mountDrawer([])
    const create = vi.spyOn(supportApi, 'createRecord').mockResolvedValue(recordFixture({
      record_kind:'teacher_observation', scene:'日常观察', source:'教师本人观察',
    }))

    clickByText(host, '＋ 补录一条观察记录'); await nextTick()
    setInput(host, 'form.add-form textarea', '合成：小组讨论时主动分工一次')
    const dates = [...host.querySelectorAll<HTMLInputElement>('form.add-form input[type="date"]')]
    dates[0]!.value = '2026-08-12'; dates[0]!.dispatchEvent(new Event('input', { bubbles:true }))
    dates[1]!.value = '2026-08-26'; dates[1]!.dispatchEvent(new Event('input', { bubbles:true }))
    dates[2]!.value = '2026-09-12'; dates[2]!.dispatchEvent(new Event('input', { bubbles:true }))
    submitForm(host, 'form.add-form'); await flush()

    expect(create).toHaveBeenCalledExactlyOnceWith('s1', expect.objectContaining({
      record_kind:'teacher_observation', scene:'日常观察', source:'教师本人观察',
      observed_at:'2026-08-12', review_at:'2026-08-26', expires_at:'2026-09-12',
    }))
    expect(host.textContent).toContain('没有调用 AI')
  })

  it('withdraws a source record and keeps it recoverable', async () => {
    const record = recordFixture({ record_kind:'teacher_observation', scene:'日常观察', source:'教师本人观察', current_revision:2 })
    const host = await mountDrawer([record])
    const setState = vi.spyOn(supportApi, 'setRecordState').mockResolvedValue({ ...record, state:'withdrawn', current_revision:3 })

    expect(host.textContent).toContain('合成：课前发了步骤卡')
    clickByText(host, '撤回'); await nextTick()
    clickByText(host, '确认撤回'); await flush()
    expect(setState).toHaveBeenCalledWith(record, 'withdrawn', '记录有误，撤回')
    expect(host.textContent).toContain('已撤回')
  })
})
