import { createApp, nextTick, ref, type App, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { intakeApi } from '../api/intake'
import { projectionR1Api, studentR1Api } from '../api/r1'
import { followUpApi } from '../api/support'
import { workApi, type WorkNode, type WorkNodeDetail } from '../api/work'
import WorkNodeInspector from '../ordinary/WorkNodeInspector.vue'
import ClassTeacherWorkbenchView from '../views/ClassTeacherWorkbenchView.vue'

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
afterEach(() => { apps.splice(0).forEach((app) => app.unmount()); document.body.innerHTML=''; window.history.replaceState({}, '', '/'); vi.restoreAllMocks() })

const followUpNode: WorkNode = {
  node_id: 'restricted-1', kind: 'restricted_projection', classification: 'restricted_projection',
  title: '学生支持待跟进', details: null, status: 'pending', due_date: '2026-08-10',
  revision: 1, created_at: '2026-08-01', updated_at: '2026-08-01', projection_type: 'student_support',
}

function followUpDetail(): WorkNodeDetail {
  return {
    node: followUpNode, upstream: [], downstream: [], progress_events: [],
    collection_summary: null, pending_ai_branches: [],
    allowed_commands: ['open_restricted_projection'], projection_id: 'projection-001',
  }
}

function fakeModule(selected: ReturnType<typeof ref<WorkNodeDetail | null>>) {
  return {
    selected,
    snapshot: ref(null),
    load: vi.fn(async () => true),
    inspect: vi.fn(async () => {}),
    command: vi.fn(async () => ({ projection_id: 'projection-001' })),
    clearSelection: vi.fn(),
  }
}

describe('学生支持待跟进提醒', () => {
  it('renders postpone and dismiss actions on the support follow-up card', async () => {
    const selected = ref<WorkNodeDetail | null>(followUpDetail())
    const module = fakeModule(selected)
    const postpone = vi.spyOn(followUpApi, 'postpone').mockResolvedValue({})
    const host = await mount(WorkNodeInspector, { module })

    expect(host.textContent).toContain('延后提醒')
    expect(host.textContent).toContain('不再跟进')

    const input = host.querySelector('input[type="date"]') as HTMLInputElement
    input.value = '2026-08-25'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    clickByText(host, '延后到这一天')
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()

    expect(postpone).toHaveBeenCalledWith('projection-001', '2026-08-25')
    expect(module.load).toHaveBeenCalled()
    expect(host.textContent).toContain('提醒已延后')
  })

  it('dismisses a follow-up only after a second confirmation', async () => {
    const selected = ref<WorkNodeDetail | null>(followUpDetail())
    const module = fakeModule(selected)
    const dismiss = vi.spyOn(followUpApi, 'dismiss').mockResolvedValue({})
    const host = await mount(WorkNodeInspector, { module })

    clickByText(host, '不再跟进'); await nextTick()
    expect(dismiss).not.toHaveBeenCalled()
    expect(host.textContent).toContain('学生档案内容不受影响')

    clickByText(host, '确认不再跟进')
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()

    expect(dismiss).toHaveBeenCalledWith('projection-001')
    expect(module.clearSelection).toHaveBeenCalled()
  })

  it('shows an error instead of postponing when the request fails', async () => {
    const selected = ref<WorkNodeDetail | null>(followUpDetail())
    const module = fakeModule(selected)
    vi.spyOn(followUpApi, 'postpone').mockRejectedValue(new Error('gone'))
    const host = await mount(WorkNodeInspector, { module })

    const input = host.querySelector('input[type="date"]') as HTMLInputElement
    input.value = '2026-08-25'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    clickByText(host, '延后到这一天')
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()

    expect(host.textContent).toContain('延后没有完成')
  })

  it('opens the student support page directly from the home week strip', async () => {
    window.history.replaceState({}, '', '/class-teacher?surface=home')
    vi.spyOn(intakeApi, 'homeroom').mockResolvedValue({ homeroom_class:null, revision:0, classes:[], source_revision:'r'.repeat(64) })
    vi.spyOn(intakeApi, 'listConversations').mockResolvedValue([])
    vi.spyOn(intakeApi, 'startConversation').mockResolvedValue({
      conversation_id:'conversation-1234', revision:1, state:'collecting', homeroom_class:null,
      created_at:'2026-08-05T00:00:00Z', updated_at:'2026-08-05T00:00:00Z', turns:[], handoffs:[],
    })
    vi.spyOn(workApi, 'read').mockResolvedValue({
      as_of: '2026-08-05', start_date: '2026-08-03', end_date: '2026-08-09',
      nodes: [followUpNode], edges: [], today: [], overdue: [], waiting: [],
      summary: { today: 0, overdue: 0, waiting: 0, review_due: 1 },
      view: 'week', cursor: null, source_version: 's'.repeat(64),
    })
    vi.spyOn(workApi, 'detail').mockResolvedValue(followUpDetail())
    vi.spyOn(projectionR1Api, 'resolve').mockResolvedValue({
      surface: 'students', panel: 'support', target_kind: 'student_card',
      target_id: 'record-001', subject_id: 'stu-12345678', gone: false,
    })
    vi.spyOn(studentR1Api, 'header').mockResolvedValue({
      subject_id: 'stu-12345678', display_name: '合成学生', source_student_id: 'src-001',
      class_label: '合成一班', support_record_count: 1, support_plan_count: 0,
      confirmed_entry_count: 1, projection_state: 'none', attention_pending_count: 0,
      last_confirmed_at: null,
    } as never)

    const host = await mount(ClassTeacherWorkbenchView, {})
    await vi.waitFor(() => expect(host.textContent).toContain('本周事务'))
    await vi.waitFor(() => expect(
      [...host.querySelectorAll('.week-strip button')].some((item) => item.textContent?.includes('学生支持待跟进')),
    ).toBe(true))

    clickByText(host, '学生支持待跟进')
    await vi.waitFor(() => {
      const query = new URLSearchParams(window.location.search)
      expect(query.get('surface')).toBe('students')
      expect(query.get('panel')).toBe('support')
      expect(query.get('student')).toBe('stu-12345678')
    })
  })

  it('explains when the follow-up source no longer exists', async () => {
    window.history.replaceState({}, '', '/class-teacher?surface=home')
    vi.spyOn(intakeApi, 'homeroom').mockResolvedValue({ homeroom_class:null, revision:0, classes:[], source_revision:'r'.repeat(64) })
    vi.spyOn(intakeApi, 'listConversations').mockResolvedValue([])
    vi.spyOn(intakeApi, 'startConversation').mockResolvedValue({
      conversation_id:'conversation-1234', revision:1, state:'collecting', homeroom_class:null,
      created_at:'2026-08-05T00:00:00Z', updated_at:'2026-08-05T00:00:00Z', turns:[], handoffs:[],
    })
    vi.spyOn(workApi, 'read').mockResolvedValue({
      as_of: '2026-08-05', start_date: '2026-08-03', end_date: '2026-08-09',
      nodes: [followUpNode], edges: [], today: [], overdue: [], waiting: [],
      summary: { today: 0, overdue: 0, waiting: 0, review_due: 1 },
      view: 'week', cursor: null, source_version: 's'.repeat(64),
    })
    vi.spyOn(workApi, 'detail').mockResolvedValue(followUpDetail())
    vi.spyOn(projectionR1Api, 'resolve').mockResolvedValue({ gone: true })

    const host = await mount(ClassTeacherWorkbenchView, {})
    await vi.waitFor(() => expect(
      [...host.querySelectorAll('.week-strip button')].some((item) => item.textContent?.includes('学生支持待跟进')),
    ).toBe(true))

    clickByText(host, '学生支持待跟进')
    await vi.waitFor(() => expect(host.textContent).toContain('提醒已自动关闭'))
    const query = new URLSearchParams(window.location.search)
    expect(query.get('surface')).toBe('students')
    expect(query.get('student')).toBeNull()
  })
})

describe('日历普通工作彻底删除', () => {
  it('deletes an ordinary work node only after a second confirmation', async () => {
    const ordinaryNode: WorkNode = {
      node_id: 'ordinary-1', kind: 'task', classification: 'ordinary',
      title: '合成普通班务', details: null, status: 'pending', due_date: '2026-08-10',
      revision: 1, created_at: '2026-08-01', updated_at: '2026-08-01',
    }
    const detail: WorkNodeDetail = {
      node: ordinaryNode, upstream: [], downstream: [], progress_events: [],
      collection_summary: null, pending_ai_branches: [],
      allowed_commands: ['update_status', 'reschedule', 'record_progress', 'delete'],
      projection_id: null,
    }
    const selected = ref<WorkNodeDetail | null>(detail)
    const module = { ...fakeModule(selected), command: vi.fn(async () => ({ deleted: true })) }
    const host = await mount(WorkNodeInspector, { module })

    clickByText(host, '彻底删除'); await nextTick()
    expect(module.command).not.toHaveBeenCalled()

    clickByText(host, '确认删除')
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()

    expect(module.command).toHaveBeenCalledWith(ordinaryNode, 'delete', {})
    expect(module.clearSelection).toHaveBeenCalled()
  })
})
