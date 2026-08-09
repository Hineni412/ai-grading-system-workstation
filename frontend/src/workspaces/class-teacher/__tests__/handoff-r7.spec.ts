import { createApp, nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { intakeApi, type HandoffDraft } from '../api/intake'
import { studentR1Api } from '../api/r1'
import HandoffWorkspace from '../intake/HandoffWorkspace.vue'

const mounted: Array<ReturnType<typeof createApp>> = []

async function settle(): Promise<void> {
  await Promise.resolve()
  await Promise.resolve()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
}

function draft(overrides: Partial<HandoffDraft> = {}): HandoffDraft {
  return {
    contract_version: 'teacher_workspace_handoff.v1',
    handoff_id: 'handoff-12345',
    work_item_id: 'work-item-1234',
    conversation_id: 'conversation-1234',
    turn_id: 'turn-12345678',
    draft_id: 'draft-12345678',
    draft_revision: 1,
    domain: 'student_growth',
    handling_mode: 'record',
    intent: 'create',
    destination_key: 'class_teacher.student.record',
    adoption_id: 'adoption-1234',
    adoption_state: 'pending',
    content: { summary: '合成学生课堂状态', observed_at: '2026-08-05T08:00:00+08:00' },
    subject_refs: [],
    missing_fields: ['请选择学生'],
    return_context: { destination_key: 'class_teacher.home', focus_ref: 'work-item-1234' },
    ...overrides,
  }
}

async function mountHandoff(value: HandoffDraft): Promise<HTMLElement> {
  vi.spyOn(intakeApi, 'handoff').mockResolvedValue(value)
  vi.spyOn(intakeApi, 'homeroom').mockResolvedValue({
    homeroom_class: '一班', revision: 1, classes: ['一班'], source_revision: 'a'.repeat(64),
  })
  vi.spyOn(studentR1Api, 'rosterSource').mockResolvedValue({
    items: [
      { source_key: 'A01', student_code: 'A01', display_name: '同名学生', class_label: '一班', subject_id: null, roster_state: 'available', opaque_ref: 'subject-a', student_revision: '3' },
      { source_key: 'B01', student_code: 'B01', display_name: '同名学生', class_label: '一班', subject_id: null, roster_state: 'available', opaque_ref: 'subject-b', student_revision: '3' },
    ],
    classes: ['一班'], source_revision: 'a'.repeat(64), cursor: null, total: 2,
  })
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(HandoffWorkspace, { handoffId: value.handoff_id, token: '' })
  app.mount(host)
  mounted.push(app)
  await settle()
  return host
}

function button(host: HTMLElement, label: string): HTMLButtonElement {
  const result = [...host.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.includes(label))
  if (!result) throw new Error(`missing button ${label}`)
  return result
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('B-UI-R7 handoff workspaces', () => {
  it('shows a readable error instead of an endless loading state when the draft cannot open', async () => {
    vi.spyOn(intakeApi, 'handoff').mockRejectedValue(new Error('synthetic unavailable'))
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(HandoffWorkspace, { handoffId: 'handoff-error-01', token: '' })
    app.mount(host)
    mounted.push(app)
    await settle()

    expect(host.textContent).toContain('草稿暂时无法打开')
    expect(host.textContent).not.toContain('正在打开草稿')
    expect(host.querySelector('[role="alert"]')).not.toBeNull()
  })

  it('keeps a student record as a draft until the teacher selects one opaque subject and confirms', async () => {
    const initial = draft()
    const update = vi.spyOn(intakeApi, 'updateDraft').mockImplementation(async (_current, content, refs) => ({
      ...initial, draft_revision: 2, content, subject_refs: refs ?? initial.subject_refs,
    }))
    const adopt = vi.spyOn(intakeApi, 'adopt').mockResolvedValue({ formal_object_id: 'record-1' })
    const host = await mountHandoff(initial)

    expect(host.textContent).toContain('直接观察或可核事实')
    expect(host.textContent).toContain('他人转述')
    expect(host.textContent).toContain('教师当前判断')
    expect(host.querySelectorAll('select option')).toHaveLength(10)
    expect(adopt).not.toHaveBeenCalled()

    const student = [...host.querySelectorAll<HTMLSelectElement>('select')].find((item) => item.textContent?.includes('同名学生'))!
    student.value = 'subject-b'
    student.dispatchEvent(new Event('change', { bubbles: true }))
    const kind = [...host.querySelectorAll<HTMLSelectElement>('select')]
      .find((item) => item.textContent?.includes('可核对事实'))!
    kind.value = 'fact'
    kind.dispatchEvent(new Event('change', { bubbles: true }))
    const sourceLabel = [...host.querySelectorAll<HTMLLabelElement>('label')]
      .find((item) => item.querySelector('span')?.textContent === '来源')!
    const source = sourceLabel.querySelector<HTMLInputElement>('input')!
    source.value = '合成教师观察'
    source.dispatchEvent(new Event('input', { bubbles: true }))
    await settle()
    button(host, '确认保存记录').click()
    await settle()
    await settle()

    expect(update).toHaveBeenCalledWith(expect.any(Object), expect.objectContaining({
      record_kind: 'fact',
      source: '合成教师观察',
      summary: expect.stringContaining('直接事实：合成学生课堂状态'),
    }), [{ kind: 'student', id: 'subject-b', revision: '3' }])
    expect(adopt).toHaveBeenCalledWith('', expect.objectContaining({ draft_revision: 2 }), '3')
  })

  it('requires explicit source attribution before a supplied professional fact becomes formal', async () => {
    const initial = draft({
      content: {
        summary: '合成医院已经提供书面诊断，内容只作为待审事实显示',
        observed_at: '2026-08-05T08:00:00+08:00',
      },
      subject_refs: [{ kind: 'student', id: 'subject-b', revision: '3' }],
      missing_fields: [],
    })
    const update = vi.spyOn(intakeApi, 'updateDraft').mockImplementation(async (_current, content, refs) => ({
      ...initial, draft_revision: 2, content, subject_refs: refs ?? initial.subject_refs,
    }))
    const adopt = vi.spyOn(intakeApi, 'adopt').mockResolvedValue({ formal_object_id: 'record-professional-1' })
    const host = await mountHandoff(initial)

    expect([...host.querySelectorAll<HTMLTextAreaElement>('textarea')].map((item) => item.value))
      .toContain('合成医院已经提供书面诊断，内容只作为待审事实显示')
    button(host, '确认保存记录').click()
    await settle()

    expect(host.textContent).toContain('请明确选择记录性质并填写信息来源')
    expect(update).not.toHaveBeenCalled()
    expect(adopt).not.toHaveBeenCalled()

    const kind = [...host.querySelectorAll<HTMLSelectElement>('select')]
      .find((item) => item.textContent?.includes('有依据的专业结论'))!
    kind.value = 'professional_conclusion'
    kind.dispatchEvent(new Event('change', { bubbles: true }))
    const sourceLabel = [...host.querySelectorAll<HTMLLabelElement>('label')]
      .find((item) => item.querySelector('span')?.textContent === '来源')!
    const source = sourceLabel.querySelector<HTMLInputElement>('input')!
    source.value = '合成医院已提供的书面材料'
    source.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    button(host, '确认保存记录').click()
    await settle()
    await settle()

    expect(update).toHaveBeenCalledWith(expect.any(Object), expect.objectContaining({
      record_kind: 'professional_conclusion',
      source: '合成医院已提供的书面材料',
      summary: expect.stringContaining('直接事实：合成医院已经提供书面诊断'),
    }), [{ kind: 'student', id: 'subject-b', revision: '3' }])
    expect(adopt).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['plan_calendar', '计划／日历草稿', '确认加入计划／日历', '日期可修改'],
    ['sop', 'SOP 处理草稿', '确认建立 SOP', '先确认即时安全'],
  ] as const)('renders the %s decision style without adopting on open', async (mode, heading, action, guidance) => {
    const value = draft({
      handling_mode: mode,
      destination_key: mode === 'sop' ? 'class_teacher.affair.sop' : 'class_teacher.plan.calendar',
      domain: mode === 'sop' ? 'conflict_safety' : 'activities_culture',
      content: mode === 'sop'
        ? { summary: '两名合成参与人发生冲突', participant_refs: ['synthetic-a'] }
        : { summary: '合成活动计划', final_deadline: '2026-08-20T16:00:00+08:00', actions: [] },
    })
    const adopt = vi.spyOn(intakeApi, 'adopt').mockResolvedValue({})
    const host = await mountHandoff(value)

    expect(host.textContent).toContain(heading)
    expect(host.textContent).toContain(action)
    expect(host.textContent).toContain(guidance)
    expect(adopt).not.toHaveBeenCalled()
  })

  it('preserves plan details and non-linear dependencies when the teacher confirms without editing', async () => {
    const initial = draft({
      handling_mode: 'plan_calendar',
      destination_key: 'class_teacher.plan.calendar',
      domain: 'activities_culture',
      content: {
        summary: '合成活动计划',
        plan_title: '合成活动计划',
        final_deadline: '2026-08-20T16:00:00+08:00',
        actions: [
          {
            draft_action_id: 'prepare-materials', title: '准备素材', details: '保留原始说明',
            due_at: '2026-08-10T16:00:00+08:00', depends_on_draft_action_ids: [],
          },
          {
            draft_action_id: 'invite-reviewer', title: '邀请检查人', details: '这一步与素材准备并行',
            due_at: '2026-08-10T17:00:00+08:00', depends_on_draft_action_ids: [],
          },
          {
            draft_action_id: 'final-review', title: '检查初稿', details: '等待两项并行准备完成',
            due_at: '2026-08-15T16:00:00+08:00',
            depends_on_draft_action_ids: ['prepare-materials', 'invite-reviewer'],
          },
        ],
      },
    })
    const update = vi.spyOn(intakeApi, 'updateDraft').mockImplementation(async (_current, content, refs) => ({
      ...initial, draft_revision: 2, content, subject_refs: refs ?? initial.subject_refs,
    }))
    const adopt = vi.spyOn(intakeApi, 'adopt').mockResolvedValue({})
    const host = await mountHandoff(initial)

    expect([...host.querySelectorAll<HTMLTextAreaElement>('.plan-action textarea')].map((item) => item.value))
      .toContain('保留原始说明')
    expect(host.textContent).toContain('邀请检查人')
    expect(host.querySelectorAll('.plan-action')).toHaveLength(3)
    button(host, '确认加入计划／日历').click()
    await settle()
    await settle()

    const saved = update.mock.calls[0]?.[1]
    expect(saved?.actions).toEqual([
      expect.objectContaining({
        draft_action_id: 'prepare-materials', details: '保留原始说明',
        depends_on_draft_action_ids: [],
      }),
      expect.objectContaining({
        draft_action_id: 'invite-reviewer', details: '这一步与素材准备并行',
        depends_on_draft_action_ids: [],
      }),
      expect.objectContaining({
        draft_action_id: 'final-review', details: '等待两项并行准备完成',
        depends_on_draft_action_ids: ['prepare-materials', 'invite-reviewer'],
      }),
    ])
    expect(adopt).toHaveBeenCalledWith('', expect.objectContaining({ draft_revision: 2 }), 'new')
  })

  it('requires the teacher to choose the manual SOP template instead of assuming a student conflict', async () => {
    const initial = draft({
      handling_mode: 'sop',
      destination_key: 'class_teacher.affair.sop',
      domain: 'conflict_safety',
      content: {
        summary: '合成学生受伤，需要人工选择流程',
        participant_refs: ['synthetic-student'],
        manual_routing: true,
        template_key: '',
      },
    })
    const update = vi.spyOn(intakeApi, 'updateDraft').mockImplementation(async (_current, content, refs) => ({
      ...initial, draft_revision: 2, content, subject_refs: refs ?? initial.subject_refs,
    }))
    const adopt = vi.spyOn(intakeApi, 'adopt').mockResolvedValue({})
    const host = await mountHandoff(initial)

    button(host, '确认建立 SOP').click()
    await settle()
    expect(host.textContent).toContain('请先选择与实际情况相符')
    expect(update).not.toHaveBeenCalled()
    expect(adopt).not.toHaveBeenCalled()

    const template = [...host.querySelectorAll<HTMLSelectElement>('select')]
      .find((item) => item.textContent?.includes('学生伤害与紧急安全'))!
    template.value = 'baseline.student_injury'
    template.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    button(host, '确认建立 SOP').click()
    await settle()
    await settle()

    expect(update).toHaveBeenCalledWith(expect.any(Object), expect.objectContaining({
      template_key: 'baseline.student_injury',
    }), expect.any(Array))
    expect(adopt).toHaveBeenCalledWith('', expect.objectContaining({ draft_revision: 2 }), 'new')
  })

  it('keeps safety, recognition, punishment and closure decisions with the teacher', async () => {
    const host = await mountHandoff(draft({
      handling_mode: 'sop', destination_key: 'class_teacher.affair.sop', domain: 'conflict_safety',
      content: { summary: '两名合成参与人发生冲突', participant_refs: ['synthetic-a'] },
    }))
    expect(host.textContent).toContain('先确认即时安全')
    expect(host.textContent).toContain('不先作欺凌认定')
    expect(host.textContent).toContain('惩戒、认定和结案仍需教师')
  })

  it('creates a separate AI revision task without adopting the draft', async () => {
    const initial = draft({
      destination_key: 'class_teacher.affair.record',
      subject_refs: [],
      missing_fields: [],
    })
    vi.spyOn(intakeApi, 'updateDraft').mockImplementation(async (_current, content, refs) => ({
      ...initial, draft_revision: 2, content, subject_refs: refs ?? initial.subject_refs,
    }))
    const requestRevision = vi.spyOn(intakeApi, 'requestDraftRevision').mockResolvedValue({
      request_id: 'revision-request-01', handoff_id: initial.handoff_id, source_draft_revision: 2,
      task_id: null, task_state: 'failed_before_dispatch',
      created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
    })
    const adopt = vi.spyOn(intakeApi, 'adopt').mockResolvedValue({})
    const host = await mountHandoff(initial)
    const instruction = host.querySelector<HTMLTextAreaElement>('.ai-revision textarea')!
    instruction.value = '把合成内容按时间顺序重排'
    instruction.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    button(host, '提交调整').click()
    await settle()
    await settle()

    expect(requestRevision).toHaveBeenCalledWith(expect.objectContaining({ draft_revision: 2 }), '把合成内容按时间顺序重排')
    expect(adopt).not.toHaveBeenCalled()
    expect(host.textContent).toContain('原草稿没有变化')
  })
})
