import { createApp, nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { intakeApi, type IntakeConversation } from '../api/intake'
import { workApi } from '../api/work'
import ConversationDesk from '../intake/ConversationDesk.vue'

const mounted: Array<ReturnType<typeof createApp>> = []

async function settle(): Promise<void> {
  await Promise.resolve(); await Promise.resolve(); await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()
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

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('ConversationDesk AI 整理气泡打字机', () => {
  it('keeps the assistant bubble hooks and settles on the full AI text in jsdom', async () => {
    const host = await mountDesk({
      conversation_id: 'conversation-1234', revision: 2, state: 'handoff_ready', homeroom_class: '一班',
      created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
      turns: [{
        turn_id: 'turn-typewriter', conversation_id: 'conversation-1234', sequence: 1,
        operation_id: 'operation-typewriter', teacher_message: '合成教师原文', assistant_message: '合成AI整理全文，逐字播放后定格。',
        clarification_questions: [], task_id: 'task-typewriter', task_state: 'proposal_ready',
        created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
      }],
      handoffs: [],
    })

    const bubble = host.querySelector('.assistant[data-state="proposal_ready"]')
    expect(bubble).not.toBeNull()
    expect(bubble?.querySelector(':scope > span')?.textContent).toBe('AI 整理')
    const typed = bubble?.querySelector('p.typewriter-text')
    expect(typed?.textContent).toBe('合成AI整理全文，逐字播放后定格。')
    expect(bubble?.querySelector('.typewriter-text__cursor')).toBeNull()
  })
})

function failedConversation(taskState: string): IntakeConversation {
  return {
    conversation_id: 'conversation-1234', revision: 2, state: 'failed', homeroom_class: '一班',
    created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
    turns: [{
      turn_id: 'turn-failed', conversation_id: 'conversation-1234', sequence: 1,
      operation_id: 'operation-failed', teacher_message: '合成教师原文', assistant_message: null,
      clarification_questions: [], task_id: 'task-failed', task_state: taskState,
      created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
    }],
    handoffs: [],
  }
}

describe('ConversationDesk 失败轮重试', () => {
  it('offers 重新整理 on invalid_result and resends the original text on click', async () => {
    const failed = failedConversation('invalid_result')
    const host = await mountDesk(failed)
    expect(host.textContent).toContain('返回内容未通过校验')
    const button = [...host.querySelectorAll('button')].find((item) => item.textContent?.includes('重新整理'))
    expect(button).toBeTruthy()

    const retried: IntakeConversation = {
      ...failed,
      revision: 3,
      state: 'handoff_ready',
      turns: [
        ...failed.turns,
        {
          turn_id: 'turn-retry', conversation_id: 'conversation-1234', sequence: 2,
          operation_id: 'operation-retry', teacher_message: '合成教师原文', assistant_message: '已整理为一项合成事务。',
          clarification_questions: [], task_id: 'task-retry', task_state: 'response_persisted',
          created_at: '2026-08-05T00:01:00Z', updated_at: '2026-08-05T00:01:00Z',
        },
      ],
    }
    const append = vi.spyOn(intakeApi, 'appendTurn').mockResolvedValue(retried)
    button!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(append).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ conversation_id: 'conversation-1234', revision: 2 }),
      '合成教师原文',
    )
    expect(host.querySelector('.assistant[data-state="response_persisted"]')).not.toBeNull()
    expect([...host.querySelectorAll('button')].some((item) => item.textContent?.includes('重新整理'))).toBe(false)
  })

  it('explains truncation distinctly and still offers the retry button', async () => {
    const host = await mountDesk(failedConversation('truncated_result'))

    expect(host.textContent).toContain('模型输出达到长度上限被截断')
    expect(host.textContent).not.toContain('返回内容未通过校验')
    expect([...host.querySelectorAll('button')].some((item) => item.textContent?.includes('重新整理'))).toBe(true)
  })
})

describe('ConversationDesk 回车发送', () => {
  function readyConversation(): IntakeConversation {
    return {
      conversation_id: 'conversation-1234', revision: 2, state: 'handoff_ready', homeroom_class: '一班',
      created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
      turns: [{
        turn_id: 'turn-1', conversation_id: 'conversation-1234', sequence: 1,
        operation_id: 'operation-1', teacher_message: '合成教师原文', assistant_message: '已整理。',
        clarification_questions: [], task_id: 'task-1', task_state: 'response_persisted',
        created_at: '2026-08-05T00:00:00Z', updated_at: '2026-08-05T00:00:00Z',
      }],
      handoffs: [],
    }
  }

  it('sends on plain Enter, keeps Shift+Enter as newline, and ignores IME composition', async () => {
    const host = await mountDesk(readyConversation())
    const sent: IntakeConversation = { ...readyConversation(), revision: 3 }
    const append = vi.spyOn(intakeApi, 'appendTurn').mockResolvedValue(sent)
    const textarea = host.querySelector<HTMLTextAreaElement>('#class-teacher-message')!
    expect(textarea).toBeTruthy()

    textarea.value = '周五要开家长会'
    textarea.dispatchEvent(new Event('input'))
    await nextTick()

    textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true, cancelable: true }))
    await settle()
    expect(append).not.toHaveBeenCalled()

    textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true, cancelable: true }))
    await settle()
    expect(append).not.toHaveBeenCalled()

    textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    await settle()
    expect(append).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ conversation_id: 'conversation-1234' }),
      '周五要开家长会',
    )
  })
})
