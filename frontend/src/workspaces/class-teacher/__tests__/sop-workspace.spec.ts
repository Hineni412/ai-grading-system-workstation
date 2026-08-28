import { createApp, nextTick, type App, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/components/ui/sheet', () => ({
  Sheet: { props: ['open'], template: '<div v-if="open" class="sheet-stub"><slot /></div>' },
  SheetContent: { props: ['side'], template: '<div class="sheet-content-stub"><slot /></div>' },
  SheetHeader: { template: '<div class="sheet-header-stub"><slot /></div>' },
  SheetTitle: { template: '<h2><slot /></h2>' },
  SheetDescription: { template: '<p><slot /></p>' },
}))

import { ApiError } from '@/api/errors'

import { affairR1Api, studentR1Api, type AffairDetail, type AffairStep } from '../api/r1'
import { decodeIntakeConversation } from '../api/intake'
import SopWorkspace from '../affairs/SopWorkspace.vue'

const apps: App[] = []
async function mount(component: Component, props: Record<string, unknown>) {
  const host = document.createElement('div'); document.body.append(host)
  const app = createApp(component, props); app.mount(host); apps.push(app)
  await nextTick(); await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()
  return host
}
function clickByText(host: ParentNode, text: string) {
  const button = [...host.querySelectorAll('button')].find((item) => item.textContent?.includes(text))
  if (!button) throw new Error(`button not found: ${text}`)
  button.dispatchEvent(new MouseEvent('click', { bubbles: true })); return button
}
function fillTextarea(host: ParentNode, placeholder: string, value: string) {
  const textarea = [...host.querySelectorAll('textarea')].find((item) => item.placeholder.includes(placeholder))
  if (!textarea) throw new Error(`textarea not found: ${placeholder}`)
  textarea.value = value
  textarea.dispatchEvent(new Event('input', { bubbles: true }))
}
function clickFlowNode(host: ParentNode, title: string) {
  const node = [...host.querySelectorAll('.flow-node')].find((item) => item.textContent?.includes(title))
  if (!node) throw new Error(`flow node not found: ${title}`)
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}
afterEach(() => { apps.splice(0).forEach((app) => app.unmount()); document.body.innerHTML = ''; vi.restoreAllMocks() })

function step(partial: Partial<AffairStep> & { step_instance_id: string; key: string; title: string }): AffairStep {
  return { state: 'blocked', revision: 1, depends_on: [], ...partial }
}

function makeAffair(overrides: Partial<AffairDetail> = {}): AffairDetail {
  return {
    affair_id: 'affair-1234567', title: '合成冲突处理', summary: '合成摘要', state: 'active', revision: 1,
    current_step_count: 1, completed_step_count: 0, projection_state: 'applied', updated_at: '2026-08-26T00:00:00Z',
    template_key: 'baseline.student_conflict', occurrence_sequence: 1,
    current_steps: [], completed_steps: [], preview_steps: [], drafts: [],
    participants: [
      { participant_id: 'p-1', reference: '甲同学', subject_id: 'subject-1' },
      { participant_id: 'p-2', reference: '乙同学', subject_id: 'subject-2' },
    ],
    decisions: [],
    to_verify: ['冲突具体起因', '目击者陈述内容'],
    profile_update_drafts: [],
    flow_revisions: [], sync_requests: [],
    ...overrides,
  }
}

function baseSteps(): AffairStep[] {
  return [
    step({ step_instance_id: 's1', key: 'secure', title: '确认现场安全', state: 'in_progress', safety_required: true }),
    step({ step_instance_id: 's2', key: 'record', title: '分别记录各方表述', depends_on: ['secure'] }),
    step({
      step_instance_id: 's3', key: 'decide', title: '分流判断', depends_on: ['record'],
      decision_key: 'route', decision_prompt: '请选择处理方向',
      decision_options: [
        { value: 'mediate', label: '普通矛盾调解' },
        { value: 'report', label: '疑似欺凌按流程上报' },
      ],
    }),
    step({ step_instance_id: 's4', key: 'mediate', title: '组织双方面对面调解沟通', depends_on: ['decide'], activation: { decision_key: 'route', allowed_values: ['mediate'] } }),
    step({ step_instance_id: 's5', key: 'follow', title: '一周后复查', depends_on: ['mediate'] }),
  ]
}

function arrange(detail: AffairDetail) {
  return vi.spyOn(affairR1Api, 'read').mockResolvedValue(detail)
}

describe('sop workspace', () => {
  it('loads an affair into the flow diagram and completes the current step', async () => {
    const steps = baseSteps()
    const initial = makeAffair({
      current_steps: [steps[0]!],
      preview_steps: steps.slice(1),
    })
    arrange(initial)
    const afterComplete = makeAffair({
      completed_steps: [{ ...steps[0]!, state: 'completed' }],
      current_steps: [{ ...steps[1]!, state: 'ready' }],
      preview_steps: steps.slice(2),
      revision: 2,
    })
    const command = vi.spyOn(affairR1Api, 'command').mockResolvedValue(afterComplete)
    const host = await mount(SopWorkspace, { affairId: 'affair-1234567' })
    await vi.waitFor(() => expect(host.querySelectorAll('.flow-node')).toHaveLength(5))

    expect(host.textContent).toContain('进行中 · 生成即生效')
    expect(host.textContent).toContain('甲同学')
    expect(host.textContent).toContain('冲突具体起因')
    expect(host.querySelector('.flow-node polygon')).toBeTruthy()

    clickFlowNode(host, '确认现场安全')
    await nextTick()
    expect(host.textContent).toContain('步骤检查器')
    clickByText(host, '标记完成')
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()

    expect(command).toHaveBeenCalledWith(initial, 'complete_step', expect.objectContaining({ step_instance_id: 's1', outcome: 'completed' }))
    expect(host.querySelector('.flow-node.is-completed')).toBeTruthy()
  })

  it('records a teacher decision with the selected branch option and lets the backend prune', async () => {
    const steps = baseSteps()
    const initial = makeAffair({
      completed_steps: [
        { ...steps[0]!, state: 'completed' },
        { ...steps[1]!, state: 'completed' },
      ],
      current_steps: [{ ...steps[2]!, state: 'ready' }],
      preview_steps: steps.slice(3),
    })
    arrange(initial)
    const afterDecision = makeAffair({
      completed_steps: [
        { ...steps[0]!, state: 'completed' },
        { ...steps[1]!, state: 'completed' },
        { ...steps[2]!, state: 'completed' },
      ],
      current_steps: [{ ...steps[3]!, state: 'ready' }],
      preview_steps: [{ ...steps[4]! }],
      completed_step_count: 3,
      revision: 2,
    })
    const command = vi.spyOn(affairR1Api, 'command').mockResolvedValue(afterDecision)
    const host = await mount(SopWorkspace, { affairId: 'affair-1234567' })
    await vi.waitFor(() => expect(host.querySelectorAll('.flow-node')).toHaveLength(5))

    clickFlowNode(host, '分流判断')
    await nextTick()
    clickByText(host, '普通矛盾调解')
    await nextTick()
    fillTextarea(host, '记录教师或学校已经作出的决定', '双方陈述一致，按普通调解处理')
    clickByText(host, '保存教师决定')
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()

    expect(command).toHaveBeenCalledWith(initial, 'teacher_decision', expect.objectContaining({
      decision_key: 'route', selected_option: 'mediate', step_instance_id: 's3',
    }))
  })

  it('sends a supplement to sync-updates and accepts the pending revision item by item', async () => {
    const steps = baseSteps()
    const initial = makeAffair({
      current_steps: [steps[0]!],
      preview_steps: steps.slice(1),
    })
    const withRevision = makeAffair({
      current_steps: [steps[0]!],
      preview_steps: steps.slice(1),
      sync_requests: [{ sync_id: 'sync-1', text: '双方已分开，无人受伤', state: 'answered', created_at: '2026-08-26T00:00:00Z' }],
      flow_revisions: [{
        revision_id: 'rev-1', sync_id: 'sync-1', source_text: '双方已分开，无人受伤',
        assistant_message: '建议补充复查安排',
        items: [{ item_id: 'item-1', kind: 'add_step', step_key: 'extra', title: '新增合成步骤', details: '', depends_on: [], reason: '', text: '', state: 'pending' }],
        dropped_items: [], state: 'pending_review', created_at: '2026-08-26T00:00:00Z',
      }],
      revision: 2,
    })
    const read = vi.spyOn(affairR1Api, 'read')
      .mockResolvedValueOnce(initial)
      .mockResolvedValue(withRevision)
    const syncUpdate = vi.spyOn(affairR1Api, 'syncUpdate').mockResolvedValue({ sync_id: 'sync-1', task_id: 'task-1', task_state: 'queued' })
    const decide = vi.spyOn(affairR1Api, 'decideFlowRevision').mockResolvedValue(makeAffair({ revision: 3 }))
    const host = await mount(SopWorkspace, { affairId: 'affair-1234567' })
    await vi.waitFor(() => expect(host.querySelectorAll('.flow-node')).toHaveLength(5))

    const input = host.querySelector<HTMLInputElement>('.ai-box input')!
    input.value = '双方已分开，无人受伤'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    clickByText(host, '发送')
    await vi.waitFor(() => expect(syncUpdate).toHaveBeenCalledWith(initial, '双方已分开，无人受伤'))

    await vi.waitFor(() => expect(host.textContent).toContain('AI 流程修订建议'), { timeout: 4000 })
    expect(read.mock.calls.length).toBeGreaterThan(1)
    clickByText(host, '接受所选调整')
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()
    expect(decide).toHaveBeenCalledWith(expect.objectContaining({ affair_id: 'affair-1234567' }), 'rev-1', ['item-1'])
  }, 10000)

  it('opens the student drawer from the aside chip and confirms the profile draft', async () => {
    const steps = baseSteps()
    const draft = {
      draft_id: 'draft-1', affair_id: 'affair-1234567', subject_id: 'subject-1', display_name: '甲同学',
      state: 'pending' as const, revision: 1, record_kind: 'reported_statement', source: '教师输入',
      record_summary: '甲同学与同学发生信息课冲突，情绪待观察', observed_at: '2026-08-26T08:00:00+08:00',
      profile_base_revision: 0,
    }
    const initial = makeAffair({
      current_steps: [steps[0]!],
      preview_steps: steps.slice(1),
      profile_update_drafts: [draft],
    })
    const read = arrange(initial)
    vi.spyOn(studentR1Api, 'studentCard').mockResolvedValue({
      subject: { subject_id: 'subject-1', student_ref: 'subject-1', display_name: '甲同学', source_student_id: 'student:S001', class_label: '一班', support_record_count: 0, support_plan_count: 0, confirmed_entry_count: 0, projection_state: 'none', attention_pending_count: 0, last_confirmed_at: null },
      entries: [], current_profile: { entry_id: 'e-1', revision: 1, summary: '当前档案合成摘要', dimensions: [], open_questions: [], support_focus: [], updated_at: null },
      existing_records: [], support_plans: [],
    })
    const confirm = vi.spyOn(affairR1Api, 'confirmProfileDraft').mockResolvedValue({ ...draft, state: 'confirmed' })
    const host = await mount(SopWorkspace, { affairId: 'affair-1234567' })
    await vi.waitFor(() => expect(host.textContent).toContain('草稿待确认'))

    clickFlowNode(host, '确认现场安全')
    await nextTick()
    expect(host.textContent).toContain('步骤检查器')
    clickByText(host, '甲同学')
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()

    expect(host.textContent).toContain('AI 拟写入的更新')
    expect(host.textContent).toContain('当前档案合成摘要')
    expect(host.textContent).not.toContain('步骤检查器')

    clickByText(host, '确认写入档案')
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()
    expect(confirm).toHaveBeenCalledWith('affair-1234567', 'draft-1')
    expect(read.mock.calls.length).toBeGreaterThan(1)
  })

  it('explains when the profile was changed elsewhere during confirm (409)', async () => {
    const steps = baseSteps()
    const draft = {
      draft_id: 'draft-1', affair_id: 'affair-1234567', subject_id: 'subject-1', display_name: '甲同学',
      state: 'pending' as const, revision: 1, record_kind: 'reported_statement', source: '教师输入',
      record_summary: '合成摘要', observed_at: '2026-08-26T08:00:00+08:00', profile_base_revision: 0,
    }
    arrange(makeAffair({
      current_steps: [steps[0]!],
      preview_steps: steps.slice(1),
      profile_update_drafts: [draft],
    }))
    vi.spyOn(studentR1Api, 'studentCard').mockRejectedValue(new Error('unavailable'))
    vi.spyOn(affairR1Api, 'confirmProfileDraft').mockRejectedValue(new ApiError({
      kind: 'conflict', status: 409, code: 'student_profile_conflict', message: 'conflict',
      details: {}, requestId: 'req-1', retryable: false,
    }))
    const host = await mount(SopWorkspace, { affairId: 'affair-1234567' })
    await vi.waitFor(() => expect(host.textContent).toContain('草稿待确认'))

    clickByText(host, '甲同学')
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()
    clickByText(host, '确认写入档案')
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()

    expect(host.textContent).toContain('档案已在别处更新，请刷新重核')
  })
})

describe('intake handoff affair_id decode', () => {
  it('keeps affair_id from the handoff summary so adopted sop cards reach the workspace', () => {
    const decoded = decodeIntakeConversation({
      conversation_id: 'conversation-1234', revision: 2, state: 'teacher_confirmed',
      homeroom_class: '一班', focused_subject_id: null, focused_subject_revision: null,
      created_at: '2026-08-26T00:00:00Z', updated_at: '2026-08-26T00:00:00Z',
      turns: [],
      handoffs: [{
        handoff_id: 'handoff-12345', draft_id: 'draft-123456', work_item_id: 'work-item-1234',
        turn_id: 'turn-12345678', domain: 'conflict_safety', handling_mode: 'sop', intent: 'create',
        destination_key: 'class_teacher.affair.sop', draft_revision: 1, adoption_state: 'adopted',
        missing_fields: [], subject_ref_count: 0, subject_id: null, auto_open_allowed: false,
        affair_id: 'affair-1234567',
      }],
    })

    expect(decoded.handoffs).toHaveLength(1)
    expect(decoded.handoffs[0]!.affair_id).toBe('affair-1234567')
  })

  it('defaults affair_id to null when the summary omits it', () => {
    const decoded = decodeIntakeConversation({
      conversation_id: 'conversation-1234', revision: 1, state: 'handoff_ready',
      homeroom_class: null, focused_subject_id: null, focused_subject_revision: null,
      created_at: '2026-08-26T00:00:00Z', updated_at: '2026-08-26T00:00:00Z',
      turns: [],
      handoffs: [{
        handoff_id: 'handoff-12345', draft_id: 'draft-123456', work_item_id: 'work-item-1234',
        turn_id: 'turn-12345678', domain: 'student_growth', handling_mode: 'record', intent: 'append',
        destination_key: 'class_teacher.student.record', draft_revision: 1, adoption_state: 'pending',
        missing_fields: [], subject_ref_count: 1, subject_id: 'subject-01', auto_open_allowed: true,
      }],
    })

    expect(decoded.handoffs[0]!.affair_id).toBeNull()
  })
})
