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
