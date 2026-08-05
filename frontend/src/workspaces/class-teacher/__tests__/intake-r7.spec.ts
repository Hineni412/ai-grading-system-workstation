import { createApp, nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { decodeHandoffDraft, intakeApi, type IntakeConversation } from '../api/intake'
import { workApi, type WorkNode } from '../api/work'
import ConversationDesk from '../intake/ConversationDesk.vue'

const mounted: Array<ReturnType<typeof createApp>> = []

function conversation(state = 'collecting'): IntakeConversation {
  return {
    conversation_id: 'conversation-1234', revision: 1, state, homeroom_class: '一班',
    created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
    turns: [], handoffs: [],
  }
}

async function settle(): Promise<void> {
  await Promise.resolve(); await Promise.resolve(); await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()
}

async function mountDesk(startValue = conversation(), workNodes: WorkNode[] = []) {
  vi.spyOn(intakeApi, 'homeroom').mockResolvedValue({
    homeroom_class: '一班', revision: 1, classes: ['一班', '二班'], source_revision: 'a'.repeat(64),
  })
  vi.spyOn(intakeApi, 'listConversations').mockResolvedValue([])
  vi.spyOn(intakeApi, 'startConversation').mockResolvedValue(startValue)
  vi.spyOn(workApi, 'read').mockResolvedValue({
    as_of: '2026-08-05T00:00:00Z', start_date: '2026-08-04', end_date: '2026-08-10',
    nodes: workNodes, edges: [], today: workNodes, overdue: [], waiting: [], review_due: [],
    summary: { today: workNodes.length, overdue: 0, waiting: 0, review_due: 0 },
    view: 'week', cursor: null, source_version: 'synthetic',
  })
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(ConversationDesk)
  app.mount(host); mounted.push(app)
  await settle()
  return host
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('B-UI-R7 conversation desk', () => {
  it('shows one conversation-first home with six domains and no legacy gate', async () => {
    const host = await mountDesk()
    const text = host.textContent ?? ''
    for (const label of ['成长记录', '学生支持', '冲突安全', '班级日常', '活动文化', '学校协同']) {
      expect(text).toContain(label)
    }
    expect(text).not.toContain('PIN')
    expect(text).not.toContain('解锁')
    expect(text).not.toContain('匿名预览')
  })

  it('sends directly, keeps body out of browser storage, and shows manual routing after failure', async () => {
    const host = await mountDesk()
    const failed: IntakeConversation = {
      ...conversation('failed'), revision: 2,
      turns: [{
        turn_id: 'turn-12345678', conversation_id: 'conversation-1234', sequence: 1,
        operation_id: 'operation-1234', teacher_message: '合成学生正文', assistant_message: null,
        clarification_questions: [], task_id: null, task_state: 'failed_before_dispatch',
        created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
      }],
    }
    const append = vi.spyOn(intakeApi, 'appendTurn').mockResolvedValue(failed)
    const storage = vi.spyOn(Storage.prototype, 'setItem')
    const textarea = host.querySelector('textarea')!
    textarea.value = '合成学生正文'
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    host.querySelector('form.composer')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await settle()

    expect(append).toHaveBeenCalledWith(expect.any(Object), '合成学生正文')
    expect(storage).not.toHaveBeenCalled()
    expect(host.textContent).toContain('无需再次调用 AI')
    expect(host.textContent).toContain('计划／日历')
  })

  it('can clear the default class without deleting roster history', async () => {
    const setHomeroom = vi.spyOn(intakeApi, 'setHomeroom').mockResolvedValue({
      homeroom_class: null, revision: 2, classes: ['一班', '二班'], source_revision: 'a'.repeat(64),
    })
    const host = await mountDesk()
    const select = host.querySelector<HTMLSelectElement>('.desk__tools select')!
    select.value = ''
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await settle()

    expect(setHomeroom).toHaveBeenCalledWith(expect.objectContaining({ revision: 1 }), null)
    expect(host.textContent).toContain('学生和历史关系没有删除')
  })

  it('does not describe an unknown result as still processing', async () => {
    const host = await mountDesk({
      ...conversation('failed'),
      turns: [{
        turn_id: 'turn-unknown-01', conversation_id: 'conversation-1234', sequence: 1,
        operation_id: 'operation-unknown-01', teacher_message: '合成未知结果正文', assistant_message: null,
        clarification_questions: [], task_id: 'task-unknown-01', task_state: 'result_unknown',
        created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
      }],
    })
    expect(host.textContent).toContain('可能已经发出')
    expect(host.textContent).toContain('不会自动重发')
  })

  it('waits for a running turn before accepting another message', async () => {
    const host = await mountDesk({
      ...conversation('ai_running'), revision: 2,
      turns: [{
        turn_id: 'turn-running-01', conversation_id: 'conversation-1234', sequence: 1,
        operation_id: 'operation-running-01', teacher_message: '合成运行中内容', assistant_message: null,
        clarification_questions: [], task_id: 'task-running-01', task_state: 'running',
        created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
      }],
    })

    expect(host.querySelector<HTMLTextAreaElement>('.composer textarea')!.disabled).toBe(true)
    expect(host.querySelector<HTMLButtonElement>('.composer button')!.disabled).toBe(true)
    expect(host.textContent).toContain('结果返回后可继续补充')
  })

  it('shows real today and upcoming work from the existing work graph', async () => {
    const item: WorkNode = {
      node_id: 'node-near-001', kind: 'task', classification: 'ordinary',
      title: '合成近期检查任务', details: null, status: 'pending',
      due_date: '2026-08-06T16:00:00+08:00', revision: 1,
      created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
    }
    const host = await mountDesk(conversation(), [item])

    expect(host.textContent).toContain('今日与接下来')
    expect(host.textContent).toContain('合成近期检查任务')
    expect(workApi.read).toHaveBeenCalledWith('week')
  })

  it('rejects an arbitrary destination before navigation', () => {
    expect(() => decodeHandoffDraft({
      contract_version: 'teacher_workspace_handoff.v1',
      handoff_id: 'handoff-12345', work_item_id: 'work-item-1234',
      conversation_id: 'conversation-1234', turn_id: 'turn-12345678',
      draft_id: 'draft-12345678', draft_revision: 1, domain: 'student_growth',
      handling_mode: 'record', intent: 'create', destination_key: 'https://example.invalid',
      adoption_id: 'adoption-1234', adoption_state: 'pending', content: {}, subject_refs: [],
      missing_fields: [], return_context: { destination_key: 'class_teacher.home', focus_ref: 'work-item-1234' },
    })).toThrow('无法识别')
  })
})
