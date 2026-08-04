import { createApp, nextTick, type App } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { homeIntakeApi, type HomeIntakeDraft } from '../api/homeIntake'
import { studentR1Api } from '../api/r1'
import IntakeDraftWorkspace from '../affairs/IntakeDraftWorkspace.vue'

const apps: App[] = []

async function flush() {
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
}

afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount())
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('focused intake draft workspace', () => {
  it('combines dates into cards and replaces the downstream selection root', async () => {
    const draft: HomeIntakeDraft = {
      draft_id: 'a'.repeat(32), version: 2, state: 'open', route: 'sensitive',
      result_kind: 'affair_recommendation', source_operation_id: 'operation-2',
      content_fingerprint: 'f'.repeat(64), created_at: '2026-08-04T00:00:00Z',
      updated_at: '2026-08-04T01:00:00Z', adopted_affair_id: null,
      operation: {
        operation_id: 'operation-2', route: 'sensitive', state: 'succeeded',
        result_kind: 'affair_recommendation', follow_up_questions: [], can_follow_up: true,
        assistant_message: null, validation_issue: null, error_category: null,
        round_number: 2, round_physical_request_count: 1, cumulative_physical_request_count: 2,
        physical_request_count: 1, teacher_confirmation_required: true,
        result_fingerprint: 'f'.repeat(64), local_context: { source_text: '甲和乙发生冲突' },
        result: { kind: 'affair_recommendation', value: {
          kind: 'affair_recommendation', transaction_type: '学生事务', template_key: 'baseline.student_conflict',
          title: '学生矛盾处理', summary: '先核实，再跟进。', reasons: [], assumptions: [], to_verify: [],
          student_aliases: ['学生A', '学生B'], risk_level: 'elevated', emergency_prompt: '如有即时风险，先人工处置。',
          model_advice: '教师可结合现场情况调整全部建议。',
          steps: [
            { key: 'one', title: '确认现场', details: '确认当前状态。', depends_on: [], required: true, waivable: false, safety_required: true },
            { key: 'two', title: '分别记录', details: '记录双方表述。', depends_on: ['one'], required: true, waivable: false, safety_required: false },
            { key: 'three', title: '持续跟进', details: '记录后续变化。', depends_on: [], required: true, waivable: false, safety_required: false },
          ],
          edges: [],
          calendar_items: [
            { key: 'calendar.one', step_key: 'one', title: '确认现场', due_date: '2026-08-04', depends_on: [] },
            { key: 'calendar.two', step_key: 'two', title: '分别记录', due_date: '2026-08-04', depends_on: ['calendar.one'] },
            { key: 'calendar.three', step_key: 'three', title: '持续跟进', due_date: '2026-08-07', depends_on: ['calendar.two'] },
          ],
        } },
      },
    }
    vi.spyOn(homeIntakeApi, 'getDraft').mockResolvedValue(draft)
    vi.spyOn(studentR1Api, 'currentRoster').mockResolvedValue({ items: [
      { source_key: 'one', subject_id: 'student-one', display_name: '甲', class_label: '九班', state: 'active' },
      { source_key: 'two', subject_id: 'student-two', display_name: '乙', class_label: '九班', state: 'active' },
    ], active_count: 2, historical_count: 0, replayed: false })
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(IntakeDraftWorkspace, { draftId: draft.draft_id, token: 'token', module: { load: vi.fn() } })
    app.mount(host)
    apps.push(app)
    await flush()

    expect(host.querySelector('.draft-head')?.textContent).toContain('学生矛盾处理')
    expect(host.querySelector('.draft-head')?.textContent).toContain('第 2 版 · 草稿已自动保存')
    expect(document.activeElement).toBe(host.querySelector('.draft-head h2'))
    expect(host.querySelector('.draft-context')).toBeNull()
    expect(host.querySelector<HTMLDetailsElement>('.ai-details')?.open).toBe(false)
    expect(host.querySelector('.ai-details')?.textContent).toContain('技术状态：已成功生成')
    expect(host.querySelector('.ai-details')?.textContent).toContain('本轮 1 次请求')
    expect(host.querySelector('.notice--emergency')?.textContent).toContain('如有即时风险')
    expect(host.textContent).not.toContain('初步日历安排')
    expect(host.querySelectorAll('.timeline-day')).toHaveLength(2)
    expect(host.querySelector('.timeline-day')?.querySelectorAll('.flow-track>li')).toHaveLength(2)
    expect(host.textContent?.match(/2026-08-04/g)).toHaveLength(3)
    const studentLinks = host.querySelector<HTMLDetailsElement>('.student-links')!
    expect(studentLinks.open).toBe(false)
    expect(studentLinks.querySelector('summary')?.textContent).toContain('已关联 2 人')
    expect(host.querySelector('.decision-bar')).not.toBeNull()
    expect(host.querySelector('.decision-bar')?.textContent).toContain('调整选中步骤')
    expect(host.querySelector('.decision-bar')?.textContent).toContain('最后确认，保存方案')

    const cards = [...host.querySelectorAll<HTMLButtonElement>('.flow-track button')]
    cards[0]!.click()
    await nextTick()
    expect(cards.map((card) => card.getAttribute('aria-pressed'))).toEqual(['true', 'true', 'true'])
    cards[1]!.click()
    await nextTick()
    expect(cards.map((card) => card.getAttribute('aria-pressed'))).toEqual(['false', 'true', 'true'])
    cards[1]!.click()
    await nextTick()
    expect(cards.map((card) => card.getAttribute('aria-pressed'))).toEqual(['false', 'false', 'false'])
  })
})
