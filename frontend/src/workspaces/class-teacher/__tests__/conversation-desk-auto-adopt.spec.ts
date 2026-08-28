import { createApp, nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/errors'
import { intakeApi, type HandoffDraft, type IntakeConversation, type IntakeHandoffSummary } from '../api/intake'
import { workApi } from '../api/work'
import ConversationDesk from '../intake/ConversationDesk.vue'

const mounted: Array<ReturnType<typeof createApp>> = []

async function settle(): Promise<void> {
  await Promise.resolve(); await Promise.resolve(); await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()
}

function conversation(state = 'collecting'): IntakeConversation {
  return {
    conversation_id: 'conversation-1234', revision: 1, state, homeroom_class: '一班',
    created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
    turns: [{
      turn_id: 'turn-01', conversation_id: 'conversation-1234', sequence: 1,
      operation_id: 'operation-01', teacher_message: '合成学生近况', assistant_message: '已整理到当前档案。',
      clarification_questions: [], task_id: 'task-01', task_state: 'response_persisted',
      created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
    }],
    handoffs: [],
  }
}

function profileHandoff(id: string, state: IntakeHandoffSummary['adoption_state']): IntakeHandoffSummary {
  return {
    handoff_id: id, draft_id: `draft-${id}`, work_item_id: `work-${id}`,
    turn_id: 'turn-01', domain: 'student_growth', handling_mode: 'record', intent: 'append',
    destination_key: 'class_teacher.student.record', draft_revision: 1, adoption_state: state,
    missing_fields: [], subject_ref_count: 1, subject_id: 'subject-01', auto_open_allowed: true,
  }
}

function profileDraft(id: string): HandoffDraft {
  return {
    contract_version: 'teacher_workspace_handoff.v1', handoff_id: id, work_item_id: `work-${id}`,
    conversation_id: 'conversation-1234', turn_id: 'turn-01', draft_id: `draft-${id}`, draft_revision: 1,
    domain: 'student_growth', handling_mode: 'record', intent: 'append',
    destination_key: 'class_teacher.student.record', adoption_id: `adoption-${id}`, adoption_state: 'opened',
    content: { summary: '合成学生近况', profile_update: { summary: '合成档案', dimensions: [], open_questions: [], support_focus: [] } },
    subject_refs: [{ kind: 'student', id: 'subject-01', revision: '3' }],
    missing_fields: [], return_context: { destination_key: 'class_teacher.home', focus_ref: `work-${id}` },
  }
}

async function mountDesk(startValue: IntakeConversation) {
  vi.spyOn(intakeApi, 'homeroom').mockResolvedValue({
    homeroom_class: '一班', revision: 1, classes: ['一班'], source_revision: 'a'.repeat(64),
  })
  vi.spyOn(intakeApi, 'listConversations').mockResolvedValue([])
  vi.spyOn(intakeApi, 'startConversation').mockResolvedValue(startValue)
  vi.spyOn(intakeApi, 'speechCapabilities').mockResolvedValue({
    available: false, status: 'dependency_missing', engine: 'synthetic-local-speech', offline: true,
    sample_rate: 16000, max_duration_seconds: 60, max_audio_bytes: 2_100_000,
    accepted_content_type: 'audio/wav',
    cloud_audio: {
      available: false, status: 'profile_missing', provider: 'configured_model',
      model: null, destination_fingerprint: 'unconfigured-fingerprint',
    },
  })
  vi.spyOn(workApi, 'read').mockResolvedValue({
    as_of: '2026-08-05T00:00:00Z', start_date: '2026-08-04', end_date: '2026-08-10',
    nodes: [], edges: [], today: [], overdue: [], waiting: [], review_due: [],
    summary: { today: 0, overdue: 0, waiting: 0, review_due: 0 },
    view: 'week', cursor: null, source_version: 'synthetic',
  })
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(ConversationDesk)
  app.mount(host); mounted.push(app)
  await settle()
  return host
}

async function sendMessage(host: HTMLElement, text: string): Promise<void> {
  const textarea = host.querySelector('textarea')!
  textarea.value = text
  textarea.dispatchEvent(new Event('input', { bubbles: true }))
  host.querySelector('form.composer')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  for (let index = 0; index < 6; index++) await settle()
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('ConversationDesk 学生档案自动并入与撤回', () => {
  it('auto-adopts a settled student record handoff and shows the revert action', async () => {
    const ready: IntakeConversation = { ...conversation('handoff_ready'), revision: 2, handoffs: [profileHandoff('handoff-auto-a', 'pending')] }
    const host = await mountDesk(conversation())
    vi.spyOn(intakeApi, 'appendTurn').mockResolvedValue(ready)
    vi.spyOn(intakeApi, 'handoff').mockResolvedValue(profileDraft('handoff-auto-a'))
    const adopt = vi.spyOn(intakeApi, 'adopt').mockResolvedValue({ saved: true })
    vi.spyOn(intakeApi, 'conversation').mockResolvedValue({
      ...ready, revision: 3, state: 'teacher_confirmed', handoffs: [profileHandoff('handoff-auto-a', 'adopted')],
    })

    await sendMessage(host, '合成学生近况')

    expect(adopt).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ handoff_id: 'handoff-auto-a' }), '3')
    expect(host.textContent).toContain('已自动并入档案，可一键撤回')
    expect(host.textContent).toContain('学生档案更新已自动并入当前档案')
    expect([...host.querySelectorAll('button')].some((item) => item.textContent?.includes('撤回本轮更新'))).toBe(true)
  })

  it('falls back to the manual card with an explicit notice on a 409 conflict', async () => {
    const ready: IntakeConversation = { ...conversation('handoff_ready'), revision: 2, handoffs: [profileHandoff('handoff-auto-b', 'pending')] }
    const host = await mountDesk(conversation())
    vi.spyOn(intakeApi, 'appendTurn').mockResolvedValue(ready)
    vi.spyOn(intakeApi, 'handoff').mockResolvedValue(profileDraft('handoff-auto-b'))
    const adopt = vi.spyOn(intakeApi, 'adopt').mockRejectedValue(new ApiError({
      kind: 'conflict', status: 409, code: 'class_teacher_target_conflict',
      message: '学生资料已变化', details: {}, requestId: 'synthetic-request', retryable: false,
    }))
    vi.spyOn(intakeApi, 'conversation').mockResolvedValue(ready)

    await sendMessage(host, '合成学生近况')

    expect(adopt).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ handoff_id: 'handoff-auto-b' }), '3')
    expect(host.textContent).toContain('自动并入未完成')
    expect(host.textContent).toContain('打开学生档案核对后手动应用')
    expect([...host.querySelectorAll('button')].some((item) => item.textContent?.includes('撤回本轮更新'))).toBe(false)
  })

  it('reverts an adopted profile merge from the desk card', async () => {
    const adopted: IntakeConversation = {
      ...conversation('teacher_confirmed'), revision: 3, handoffs: [profileHandoff('handoff-auto-c', 'adopted')],
    }
    const host = await mountDesk(adopted)
    const revert = vi.spyOn(intakeApi, 'revertProfile').mockResolvedValue({
      adoption_state: 'reverted', profile_state: 'restored',
    })
    vi.spyOn(intakeApi, 'conversation').mockResolvedValue({
      ...adopted, revision: 4, handoffs: [profileHandoff('handoff-auto-c', 'reverted')],
    })

    const button = [...host.querySelectorAll('button')].find((item) => item.textContent?.includes('撤回本轮更新'))
    expect(button).toBeTruthy()
    button!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(revert).toHaveBeenCalledExactlyOnceWith('handoff-auto-c')
    expect(host.textContent).toContain('已撤回，档案回到本轮更新前')
  })

  it('shows only the latest profile card per student', async () => {
    const host = await mountDesk({
      ...conversation('teacher_confirmed'), revision: 3,
      handoffs: [
        { ...profileHandoff('handoff-auto-old', 'reverted') },
        profileHandoff('handoff-auto-new', 'adopted'),
        { ...profileHandoff('handoff-auto-other', 'pending'), subject_id: 'subject-02' },
      ],
    })

    expect(host.querySelector('[data-work-item="work-handoff-auto-old"]')).toBeNull()
    expect(host.querySelector('[data-work-item="work-handoff-auto-new"]')).not.toBeNull()
    expect(host.querySelector('[data-work-item="work-handoff-auto-other"]')).not.toBeNull()
    const revertButtons = [...host.querySelectorAll('button')]
      .filter((item) => item.textContent?.includes('撤回本轮更新'))
    expect(revertButtons).toHaveLength(1)
  })

  it('keeps the unanswered-clarification hint visible until the teacher replies', async () => {
    const asking: IntakeConversation = {
      ...conversation('needs_input'), revision: 2,
      turns: [{
        turn_id: 'turn-ask', conversation_id: 'conversation-1234', sequence: 1,
        operation_id: 'operation-ask', teacher_message: '合成学生近况', assistant_message: '还需要补充一点信息。',
        clarification_questions: ['具体表现发生在哪些情境？', '之前试过哪些办法？'],
        task_id: 'task-ask', task_state: 'response_persisted',
        created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
      }],
    }
    const host = await mountDesk(asking)

    expect(host.textContent).toContain('AI 还有 2 个追问待你回答，直接在下方回复即可')

    const answered: IntakeConversation = {
      ...asking, revision: 3, state: 'handoff_ready',
      turns: [...asking.turns, {
        turn_id: 'turn-answer', conversation_id: 'conversation-1234', sequence: 2,
        operation_id: 'operation-answer', teacher_message: '都在小组任务里，试过同桌提醒。',
        assistant_message: '已整理到当前档案。', clarification_questions: [],
        task_id: 'task-answer', task_state: 'response_persisted',
        created_at: '2026-08-05T00:01:00Z', updated_at: '2026-08-05T00:01:00Z',
      }],
    }
    vi.spyOn(intakeApi, 'appendTurn').mockResolvedValue(answered)
    await sendMessage(host, '都在小组任务里，试过同桌提醒。')

    expect(host.textContent).not.toContain('个追问待你回答')
  })

  it('labels handoff cards with their adoption state', async () => {
    const host = await mountDesk({
      ...conversation('teacher_confirmed'), revision: 3,
      handoffs: [
        profileHandoff('handoff-state-pending', 'pending'),
        { ...profileHandoff('handoff-state-adopted', 'adopted'), subject_id: 'subject-02' },
      ],
    })

    const pending = host.querySelector('[data-work-item="work-handoff-state-pending"] .handoff-state')
    const adopted = host.querySelector('[data-work-item="work-handoff-state-adopted"] .handoff-state')
    expect(pending?.textContent).toBe('待核对')
    expect(adopted?.textContent).toBe('已并入')
  })
})
