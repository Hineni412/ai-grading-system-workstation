import { createApp, defineComponent, h, nextTick, ref, type App, type Component } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  homeIntakeApi,
  type HomeIntakeHandoff,
  type HomeIntakeOperation,
  type HomeIntakePreview,
} from '../api/homeIntake'
import { affairR1Api, studentR1Api, type DirectorySubject } from '../api/r1'
import { sopApi } from '../api/sop'
import { supportApi } from '../api/support'
import AffairsSurface from '../affairs/AffairsSurface.vue'
import QuickWorkCapture from '../ordinary/QuickWorkCapture.vue'
import StudentSurface from '../students/StudentSurface.vue'

const apps: App[] = []
async function mount(component: Component, props: Record<string, unknown>) {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(component, props)
  app.mount(host)
  apps.push(app)
  await flush()
  return host
}
async function flush() {
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
}
function click(host: HTMLElement, label: string): HTMLButtonElement {
  const button = [...host.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.includes(label))
  if (!button) throw new Error(`missing button ${label}`)
  button.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  return button
}
function enter(control: HTMLInputElement | HTMLTextAreaElement, value: string) {
  control.value = value
  control.dispatchEvent(new Event('input', { bubbles: true }))
}
function labeledControl<T extends HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(host: HTMLElement, label: string): T {
  const wrapper = [...host.querySelectorAll<HTMLLabelElement>('label')].find((item) => item.textContent?.includes(label))
  const control = wrapper?.querySelector<T>('input, textarea, select')
  if (!control) throw new Error(`missing control ${label}`)
  return control
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })
  return { promise, resolve, reject }
}

function intakePreview(overrides: Partial<HomeIntakePreview> = {}): HomeIntakePreview {
  return {
    preview_id: 'preview-1', route: 'ordinary', recommended_route: 'ordinary_plan',
    date_interpretation: { status: 'pending', source: 'not_provided', resolved_date: null, selected_date: null, candidates: [], pending_reason: null },
    emergency_guidance: null, round_number: 1, prior_operations: [], round_physical_request_count: 0,
    cumulative_physical_request_count: 0, physical_request_count: 0, dispatch_ready: true, local_only: false,
    blocked_categories: [], removed_categories: [], student_aliases: [], exact_payload: { task_text: '普通班务' }, fingerprint: 'f'.repeat(64),
    expires_at: null, model_provider: 'fake', model_endpoint: null, model_name: 'fake', destination_fingerprint: 'd'.repeat(64),
    model_enabled: true, max_physical_requests: null, estimated_cost: null, source_text: '普通班务', final_due_date: null, date_semantics: 'date-only',
    ...overrides,
  }
}
function intakeOperation(overrides: Partial<HomeIntakeOperation> = {}): HomeIntakeOperation {
  return {
    operation_id: 'operation-1', route: 'ordinary', state: 'succeeded', result_kind: 'plain_text',
    result: { kind: 'plain_text', value: { text: '安全说明文本' } }, follow_up_questions: [], can_follow_up: true,
    assistant_message: null, validation_issue: null, error_category: null, round_number: 1, round_physical_request_count: 1, cumulative_physical_request_count: 1,
    physical_request_count: 1, teacher_confirmation_required: true, result_fingerprint: null, local_context: {}, ...overrides,
  }
}
function workflowRecommendation() {
  return {
    kind: 'affair_recommendation' as const,
    transaction_type: '学生事务', template_key: 'baseline.student_conflict', title: '学生矛盾处理初稿',
    summary: '先确保安全，再分别记录和核实。', reasons: [], assumptions: [],
    to_verify: ['是否有人受伤'], student_aliases: ['学生A', '学生B'], risk_level: 'elevated',
    emergency_prompt: '如有迫近危险，先人工处置。',
    steps: [
      { key: 'safety_check', title: '确认安全', details: '先确认冲突已经停止。', depends_on: [], required: true, waivable: false, safety_required: true },
      { key: 'fact_check', title: '核实事实', details: '分别记录并核对。', depends_on: ['safety_check'], required: true, waivable: false, safety_required: false },
    ],
    edges: [{ source_key: 'safety_check', target_key: 'fact_check', relation: 'depends_on' }],
    calendar_items: [
      { key: 'calendar.safety_check', step_key: 'safety_check', title: '确认安全', due_date: '2026-08-03', depends_on: [] },
      { key: 'calendar.fact_check', step_key: 'fact_check', title: '核实事实', due_date: '2026-08-04', depends_on: ['calendar.safety_check'] },
    ],
  }
}
const moduleStub = () => ({ load: vi.fn() })
const handoff: HomeIntakeHandoff = {
  id: 'handoff-1', destination: 'affair', sourceText: '教师原始记录，保持原样。', route: 'sensitive', interpretedDate: null,
  aiReference: { ...workflowRecommendation(), summary: '建议按学校流程核对事实', reasons: ['先核实'] },
}
const directorySubject: DirectorySubject = {
  subject_id: 's1', display_name: '合成学生', source_student_id: 'S1', class_label: '一班',
  support_record_count: 0, support_plan_count: 0, confirmed_entry_count: 0,
  projection_state: 'none', attention_pending_count: 0, last_confirmed_at: null,
}
const studentHandoff: HomeIntakeHandoff = {
  ...handoff,
  destination: 'student_support',
  aiReference: { ...handoff.aiReference, kind: 'student_support_recommendation' },
}
function studentRoot(handoffValue: HomeIntakeHandoff | null = studentHandoff, clearOnPersisted = false) {
  return defineComponent({
    setup() {
      const panel = ref<'directory' | 'support'>('directory')
      const activeHandoff = ref(handoffValue)
      return () => h(StudentSurface, {
        token: 'token', panel: panel.value, status: null, handoff: activeHandoff.value,
        onNavigate: (next: 'directory' | 'support' | 'academic' | 'security') => {
          if (next === 'directory' || next === 'support') panel.value = next
        },
        onHandoffPersisted: () => {
          if (clearOnPersisted) activeHandoff.value = null
        },
      })
    },
  })
}

function savedTeacherRecord(overrides: Partial<Awaited<ReturnType<typeof supportApi.createRecord>>> = {}) {
  return {
    record_id: 'r1', subject_id: 's1', record_kind: 'teacher_observation', state: 'active', current_revision: 1,
    content: '教师原始记录，保持原样。', scene: '日常观察', source: '教师本人观察', counterexample: null,
    observed_at: '2026-08-03', review_at: null, expires_at: null, ...overrides,
  }
}

afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount())
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('homepage intake flow', () => {
  it('shows ordinary saved drafts even when the sensitive vault has no session token', async () => {
    const listDrafts = vi.spyOn(homeIntakeApi, 'listDrafts').mockResolvedValue([{
      draft_id: 'a'.repeat(32), version: 1, route: 'ordinary', result_kind: 'ordinary_plan',
      title: '九月一日开学准备', source_text: '九月一日开学', student_aliases: [], updated_at: '2026-08-04T01:00:00Z',
    }])
    const host = await mount(QuickWorkCapture, { module: moduleStub() })

    expect(listDrafts).toHaveBeenCalledWith(undefined)
    expect(host.textContent).toContain('继续处理未完成事务')
    expect(host.textContent).toContain('九月一日开学准备')
  })

  it('restores local student names on saved affair cards', async () => {
    vi.spyOn(homeIntakeApi, 'listDrafts').mockResolvedValue([{
      draft_id: 'b'.repeat(32), version: 3, route: 'sensitive', result_kind: 'affair_recommendation',
      title: '学生A与学生B课堂冲突处理', source_text: '张三丰和李四发生冲突',
      student_aliases: ['学生A', '学生B'], updated_at: '2026-08-04T01:00:00Z',
    }])
    vi.spyOn(studentR1Api, 'currentRoster').mockResolvedValue({
      items: [
        { source_key:'prefix', subject_id:'prefix', display_name:'张三', class_label:'九班', state:'active' },
        { source_key:'one', subject_id:'one', display_name:'张三丰', class_label:'九班', state:'active' },
        { source_key:'two', subject_id:'two', display_name:'李四', class_label:'九班', state:'active' },
      ], active_count:3, historical_count:0, replayed:false,
    })
    const host = await mount(QuickWorkCapture, { module: moduleStub(), token:'token' })

    expect(host.textContent).toContain('张三丰与李四课堂冲突处理')
    expect(host.querySelector('.draft-students')?.textContent).toBe('张三丰、李四')
    expect(host.textContent).not.toContain('学生A与学生B')
  })

  it('keeps parsed plan visible when automatic draft persistence fails', async () => {
    vi.spyOn(homeIntakeApi, 'preview').mockResolvedValue(intakePreview())
    vi.spyOn(homeIntakeApi, 'dispatch').mockResolvedValue(intakeOperation({
      result_kind: 'ordinary_plan', result_fingerprint: 'p'.repeat(64),
      draft_persistence_error: 'home_intake_draft_save_failed',
      draft_persistence_message: 'AI 方案已经返回，但自动保存草稿失败。请保留当前页面并重试保存，不要重新发起模型请求。',
      result: { kind: 'ordinary_plan', value: {
        assumptions: [], edges: [],
        nodes: [{
          draft_key: 'goal', kind: 'goal', title: '九月一日开学', details: '先准备开学事项',
          rationale: null, status: 'pending', due_date: '2026-09-01',
        }],
      } },
    }))
    const host = await mount(QuickWorkCapture, { module: moduleStub() })
    enter(host.querySelector<HTMLTextAreaElement>('#home-intake-text')!, '九月一日开学')
    click(host, '交给 AI 整理')
    await flush()

    expect(host.textContent).toContain('AI 方案已经返回，但自动保存草稿失败')
    expect(host.textContent).toContain('九月一日开学')
    expect(host.textContent).toContain('先准备开学事项')
    expect(host.textContent).toContain('重试保存草稿（不会再次调用 AI）')
    expect(click(host, '确认方案，写入工作图与日历').disabled).toBe(true)
  })

  it('routes a saved AI result to the focused draft without rendering it again on the homepage', async () => {
    vi.spyOn(homeIntakeApi, 'preview').mockResolvedValue(intakePreview())
    vi.spyOn(homeIntakeApi, 'dispatch').mockResolvedValue(intakeOperation({
      draft_id: 'a'.repeat(32), draft_version: 1,
      result_kind: 'ordinary_plan', result_fingerprint: 'p'.repeat(64),
      result: { kind: 'ordinary_plan', value: {
        assumptions: [], edges: [],
        nodes: [{ draft_key: 'goal', kind: 'goal', title: '九月一日开学', details: '准备开学事项', rationale: null, status: 'pending', due_date: '2026-09-01' }],
      } },
    }))
    const openDraft = vi.fn()
    const host = await mount(QuickWorkCapture, { module: moduleStub(), onOpenDraft: openDraft })
    enter(host.querySelector<HTMLTextAreaElement>('#home-intake-text')!, '九月一日开学')
    click(host, '交给 AI 整理')
    await flush()

    expect(openDraft).toHaveBeenCalledWith('a'.repeat(32))
    expect(host.querySelector('.plan')).toBeNull()
    expect(host.textContent).not.toContain('AI 初步执行方案')
    expect(host.textContent).not.toContain('确认方案，写入工作图与日历')
  })

  it('uses one broad textarea with no date/type controls and directly dispatches a prevention-theme ordinary plan', async () => {
    const preview = vi.spyOn(homeIntakeApi, 'preview').mockResolvedValue(intakePreview({
      source_text: '开展防欺凌主题班会', final_due_date: '2026-08-07',
      date_interpretation: { status: 'resolved', source: 'relative_weekday', resolved_date: '2026-08-07', selected_date: null, candidates: ['2026-08-07'], pending_reason: null },
    }))
    const dispatch = vi.spyOn(homeIntakeApi, 'dispatch').mockResolvedValue(intakeOperation({
      result_kind: 'ordinary_plan', result_fingerprint: 'p'.repeat(64), can_follow_up: false,
      result: { kind: 'ordinary_plan', value: {
        assumptions: ['班会时长为一课时'],
        nodes: [
          { draft_key: 'goal', kind: 'goal', title: '完成防欺凌主题班会', details: '准备案例和讨论问题', rationale: '先建立预防意识', status: 'pending', due_date: '2026-08-07' },
          { draft_key: 'task', kind: 'task', title: '准备材料', details: null, rationale: null, status: 'pending', due_date: '2026-08-06' },
        ],
        edges: [{ source_draft_key: 'goal', target_draft_key: 'task', relation: 'contains' }],
      } },
    }))
    const confirm = vi.spyOn(homeIntakeApi, 'confirmPlan').mockResolvedValue({})
    const workModule = moduleStub()
    const host = await mount(QuickWorkCapture, { module: workModule })

    expect(host.querySelectorAll('textarea')).toHaveLength(1)
    expect(host.querySelector('input[type="date"]')).toBeNull()
    expect(host.querySelector('select')).toBeNull()
    const source = host.querySelector<HTMLTextAreaElement>('#home-intake-text')!
    enter(source, '开展防欺凌主题班会')
    click(host, '交给 AI 整理')
    await flush()

    expect(preview).toHaveBeenCalledWith('开展防欺凌主题班会', undefined)
    expect(dispatch).toHaveBeenCalledOnce()
    expect(host.querySelector<HTMLDetailsElement>('.technical-details')?.open).toBe(false)
    expect(host.textContent).toContain('日期已明确')
    expect(host.textContent).toContain('草案，尚未写入')
    expect(host.textContent).toContain('准备案例和讨论问题')
    expect(host.textContent).toContain('先建立预防意识')
    expect(host.textContent).toContain('班会时长为一课时')
    expect(host.textContent).toContain('goal → task（contains）')
    expect(source.value).toBe('开展防欺凌主题班会')

    expect(host.textContent).toContain('AI 初步执行方案')
    click(host, '确认方案，写入工作图与日历')
    await flush()
    expect(confirm).toHaveBeenCalledOnce()
    expect(workModule.load).toHaveBeenCalledWith('today')
    expect(source.value).toBe('')
  })

  it('filters the existing student library and replaces the current homeroom roster', async () => {
    vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items: [], cursor: null, total: 0, page_size: 20 })
    vi.spyOn(studentR1Api, 'currentRoster').mockResolvedValue({ items:[], active_count:0, historical_count:0, replayed:false })
    const source = vi.spyOn(studentR1Api, 'rosterSource').mockResolvedValue({
      items: [{ source_key: '1', student_code: 'A001', display_name: '王明', class_label: '一班', subject_id: null, roster_state: 'available' }],
      classes: ['一班', '二班'], source_revision: 'r'.repeat(64), total: 1, cursor: null,
    })
    const replace = vi.spyOn(studentR1Api, 'replaceCurrentRoster').mockResolvedValue({
      items: [{ source_key: '1', subject_id: 's1', display_name: '王明', class_label: '一班', state: 'active' }],
      active_count: 1, historical_count: 2, replayed: false,
    })
    const host = await mount(StudentSurface, { token: 'token', panel: 'directory', status: null })
    const select = labeledControl<HTMLSelectElement>(host, '按班级筛选')
    select.value = '一班'; select.dispatchEvent(new Event('change', { bubbles: true }))
    click(host, '查看筛选结果'); await flush()
    click(host, '将筛选结果设为我班学生'); await flush()
    expect(source).toHaveBeenLastCalledWith('token', { q: '', classLabel: '一班', pageSize: 100 })
    expect(replace).toHaveBeenCalledWith('token', expect.objectContaining({ expectedSourceRevision: 'r'.repeat(64), classLabel: '一班' }))
    expect(host.textContent).toContain('旧档案不会删除')
  })

  it('shows emergency guidance and exact anonymous preview with zero requests before teacher confirmation', async () => {
    vi.spyOn(homeIntakeApi, 'preview').mockResolvedValue(intakePreview({
      route: 'emergency', recommended_route: 'affair', exact_payload: { task_text: '学生A和学生B正在打架' },
      student_aliases: ['学生A', '学生B'], removed_categories: ['student_identity'], max_physical_requests: 1,
      model_provider: 'synthetic.invalid', model_name: 'synthetic-safe-model', model_endpoint: 'https://synthetic.invalid/v1',
      destination_fingerprint: 'd'.repeat(64),
      emergency_guidance: { priority: 'before_ai', title: '先处理现场安全，不要等待 AI', steps: ['立即通知学校值班负责人', '必要时联系 110 或 120'] },
    }))
    const dispatch = vi.spyOn(homeIntakeApi, 'dispatch').mockResolvedValue(intakeOperation({ route: 'emergency' }))
    const host = await mount(QuickWorkCapture, { module: moduleStub(), token: 'vault-token' })
    enter(host.querySelector<HTMLTextAreaElement>('#home-intake-text')!, '王小明和张伟正在打架')
    click(host, '交给 AI 整理')
    await flush()

    expect(host.textContent).toContain('先处理现场安全，不要等待 AI')
    expect(host.textContent).not.toContain('学生A和学生B正在打架')
    expect(host.textContent).toContain('确认后仅调用 1 次')
    const destination = host.querySelector<HTMLElement>('.destination-receipt')!
    const confirmButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('确认匿名发送并生成方案'))!
    expect(destination.textContent).toContain('提供方synthetic.invalid')
    expect(destination.textContent).toContain('模型synthetic-safe-model')
    expect(destination.textContent).toContain('服务地址（不含凭据）https://synthetic.invalid/v1')
    expect(destination.textContent).not.toContain('目的地指纹')
    expect(destination.compareDocumentPosition(confirmButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(host.textContent).toContain('若模型目的地在点击前变化')
    expect(dispatch).not.toHaveBeenCalled()
    expect(document.activeElement).toBe(host.querySelector('.progress'))
    expect(host.querySelector('.progress')?.getAttribute('aria-live')).toBe('polite')

    click(host, '确认匿名发送并生成方案')
    await flush()
    expect(dispatch).toHaveBeenCalledOnce()
  })

  it('revises only after the teacher selects an unsuitable card and supplies feedback', async () => {
    vi.spyOn(homeIntakeApi, 'preview').mockResolvedValue(intakePreview({ route: 'sensitive', recommended_route: 'affair' }))
    vi.spyOn(studentR1Api, 'currentRoster').mockResolvedValue({ items: [], active_count: 0, historical_count: 0, replayed: false })
    const dispatch = vi.spyOn(homeIntakeApi, 'dispatch')
      .mockResolvedValueOnce(intakeOperation({ route: 'sensitive', result_kind: 'affair_recommendation', result: { kind: 'affair_recommendation', value: workflowRecommendation() }, follow_up_questions: ['是否有人受伤'], can_follow_up: true, result_fingerprint: 'r'.repeat(64) }))
      .mockResolvedValueOnce(intakeOperation({ operation_id: 'operation-2', route: 'sensitive', result_kind: 'affair_recommendation', result: { kind: 'affair_recommendation', value: workflowRecommendation() }, round_number: 2, cumulative_physical_request_count: 2, result_fingerprint: 's'.repeat(64) }))
    const follow = vi.spyOn(homeIntakeApi, 'followUpPreview').mockResolvedValue(intakePreview({ preview_id: 'preview-2', route: 'sensitive', recommended_route: 'affair', round_number: 2, prior_operations: ['operation-1'], cumulative_physical_request_count: 1 }))
    const host = await mount(QuickWorkCapture, { module: moduleStub(), token: 'token' })
    enter(host.querySelector<HTMLTextAreaElement>('#home-intake-text')!, '安排活动')
    click(host, '交给 AI 整理')
    await flush()
    click(host, '确认匿名发送并生成方案')
    await flush()

    expect(follow).not.toHaveBeenCalled()
    expect(host.textContent).toContain('学生矛盾处理初稿')
    expect(host.textContent).not.toContain('AI 返回内容未通过校验')
    expect(host.textContent).not.toContain('写入单节点工作')
    click(host, '确认安全')
    await nextTick()
    expect(host.querySelectorAll('.workflow-cards button.selected')).toHaveLength(2)
    const answer = host.querySelector<HTMLTextAreaElement>('.follow-up textarea')!
    enter(answer, '安全步骤已经完成，请调整后续安排')
    expect(follow).not.toHaveBeenCalled()
    click(host, '发送标记和补充，重新调整')
    await flush()
    expect(follow).toHaveBeenCalledWith('operation-1', '安全步骤已经完成，请调整后续安排', 'token', undefined, ['safety_check', 'fact_check'], ['calendar.safety_check', 'calendar.fact_check'])
    expect(dispatch).toHaveBeenCalledTimes(1)
  })

  it('locks source editing while busy, response-lost, result-unknown, and in-progress while querying only the same handle', async () => {
    const pendingPreview = deferred<HomeIntakePreview>()
    vi.spyOn(homeIntakeApi, 'preview').mockReturnValue(pendingPreview.promise)
    const dispatch = vi.spyOn(homeIntakeApi, 'dispatch').mockRejectedValue(new Error('response lost'))
    const status = vi.spyOn(homeIntakeApi, 'status')
      .mockResolvedValueOnce(intakeOperation({
        state: 'result_unknown', result_kind: null, result: null, can_follow_up: false,
        teacher_confirmation_required: false,
      }))
      .mockResolvedValueOnce(intakeOperation({
        state: 'in_progress', result_kind: null, result: null, can_follow_up: false,
        teacher_confirmation_required: false,
      }))
      .mockResolvedValueOnce(intakeOperation({
        state: 'unavailable', result_kind: null, result: null, can_follow_up: false,
        error_category: 'model_disabled', round_physical_request_count: 0,
        cumulative_physical_request_count: 0, physical_request_count: 0,
        teacher_confirmation_required: false,
      }))
    const host = await mount(QuickWorkCapture, { module: moduleStub() })
    const source = host.querySelector<HTMLTextAreaElement>('#home-intake-text')!
    enter(source, '保留这段普通班务')
    click(host, '交给 AI 整理')
    await nextTick()

    expect(source.disabled).toBe(true)
    enter(source, '忙碌时不应替换原文')
    await nextTick()
    expect(source.value).toBe('保留这段普通班务')
    expect(host.querySelector<HTMLButtonElement>('.discard')?.disabled ?? true).toBe(true)
    expect(host.querySelector<HTMLButtonElement>('.capture__actions button')?.disabled).toBe(true)

    pendingPreview.resolve(intakePreview({ source_text: '保留这段普通班务' }))
    await flush()
    const operationId = dispatch.mock.calls[0]?.[1]
    expect(operationId).toBeTruthy()
    expect(source.disabled).toBe(true)
    expect(host.textContent).toContain('发送响应未能确认')
    enter(source, '响应丢失时也不能替换')
    await nextTick()
    expect(source.value).toBe('保留这段普通班务')

    click(host, '查看刚才这次调用')
    await flush()
    expect(status).toHaveBeenLastCalledWith(operationId, undefined)
    expect(source.disabled).toBe(true)
    enter(source, '结果未知时不能替换')
    await nextTick()
    expect(source.value).toBe('保留这段普通班务')

    click(host, '查看刚才这次调用')
    await flush()
    expect(source.disabled).toBe(true)
    enter(source, '处理中不能替换')
    await nextTick()
    expect(source.value).toBe('保留这段普通班务')

    click(host, '查看刚才这次调用')
    await flush()
    expect(status).toHaveBeenCalledTimes(3)
    expect(status.mock.calls.every(([id]) => id === operationId)).toBe(true)
    expect(dispatch).toHaveBeenCalledOnce()
    expect(source.disabled).toBe(false)

    enter(source, '终态后由教师修改并重试')
    await nextTick()
    expect(source.value).toBe('终态后由教师修改并重试')
    expect(host.querySelector('.progress')).toBeNull()
  })

  it('keeps source locked during generation and saves the reviewed plan directly', async () => {
    const preview = intakePreview({
      route: 'sensitive', recommended_route: 'affair',
      source_text: '王小明：绑定到本轮操作的教师原文',
      exact_payload: { task_text: '匿名预览内容' },
    })
    vi.spyOn(homeIntakeApi, 'preview').mockResolvedValue(preview)
    const pendingDispatch = deferred<HomeIntakeOperation>()
    vi.spyOn(homeIntakeApi, 'dispatch').mockReturnValue(pendingDispatch.promise)
    vi.spyOn(studentR1Api, 'currentRoster').mockResolvedValue({ items: [
      { source_key: '1', subject_id: 's1', display_name: '王小明', class_label: '一班', state: 'active' },
      { source_key: '2', subject_id: 's2', display_name: '王明', class_label: '一班', state: 'active' },
    ], active_count: 2, historical_count: 0, replayed: false })
    const adopt = vi.spyOn(homeIntakeApi, 'adopt').mockResolvedValue({})
    const host = await mount(QuickWorkCapture, { module: moduleStub(), token: 'token' })
    const source = host.querySelector<HTMLTextAreaElement>('#home-intake-text')!
    enter(source, '王小明：绑定到本轮操作的教师原文')
    click(host, '交给 AI 整理')
    await flush()

    click(host, '确认匿名发送并生成方案')
    await nextTick()
    expect(source.disabled).toBe(true)
    enter(source, '不应混入交接的新文本')
    await nextTick()
    expect(source.value).toBe('王小明：绑定到本轮操作的教师原文')

    pendingDispatch.resolve(intakeOperation({
      route: 'sensitive', result_kind: 'affair_recommendation',
      result: { kind: 'affair_recommendation', value: workflowRecommendation() }, result_fingerprint: 'r'.repeat(64),
    }))
    await flush()
    expect(labeledControl<HTMLInputElement>(host, '王小明').checked).toBe(true)
    click(host, '采用并保存方案')
    await flush()
    expect(adopt).toHaveBeenCalledWith(expect.objectContaining({ operation_id: 'operation-1' }), expect.any(String), ['s1'], 'token')
  })

  it('reuses one manual-fallback operation id after an uncertain write response', async () => {
    vi.spyOn(homeIntakeApi, 'preview').mockResolvedValue(intakePreview({ source_text: '保留这段普通班务' }))
    vi.spyOn(homeIntakeApi, 'dispatch').mockResolvedValue(intakeOperation({
      state: 'unavailable', result_kind: null, result: null, can_follow_up: false,
      error_category: 'model_disabled', round_physical_request_count: 0,
      cumulative_physical_request_count: 0, physical_request_count: 0,
      teacher_confirmation_required: false,
    }))
    const fallback = vi.spyOn(homeIntakeApi, 'confirmManualFallback')
      .mockRejectedValue(new Error('write uncertain'))
    const host = await mount(QuickWorkCapture, { module: moduleStub() })
    const source = host.querySelector<HTMLTextAreaElement>('#home-intake-text')!
    enter(source, '保留这段普通班务')
    click(host, '交给 AI 整理')
    await flush()

    click(host, '教师确认，写入单节点工作')
    await flush()
    const fallbackOperationId = fallback.mock.calls[0]?.[1]
    expect(fallbackOperationId).toBeTruthy()
    expect(source.disabled).toBe(true)
    enter(source, '不应清除兜底写入编号')
    await nextTick()
    expect(source.value).toBe('保留这段普通班务')

    click(host, '教师确认，写入单节点工作')
    await flush()
    expect(fallback).toHaveBeenCalledTimes(2)
    expect(fallback.mock.calls[1]?.[1]).toBe(fallbackOperationId)
    expect(fallback.mock.calls[1]?.[2]).toBe('保留这段普通班务')
    expect(host.textContent).toContain('本次写入编号仍保留')
  })

  it('renders conflict and pending date states without adding a date picker', async () => {
    const preview = vi.spyOn(homeIntakeApi, 'preview')
      .mockResolvedValueOnce(intakePreview({ dispatch_ready: false, exact_payload: null, fingerprint: null, date_interpretation: { status: 'conflict', source: 'multiple_or_selected_conflict', resolved_date: null, selected_date: null, candidates: ['2026-08-03', '2026-08-04'], pending_reason: null } }))
      .mockResolvedValueOnce(intakePreview({ date_interpretation: { status: 'pending', source: 'incomplete_week', resolved_date: null, selected_date: null, candidates: [], pending_reason: 'incomplete_week' } }))
    vi.spyOn(homeIntakeApi, 'dispatch').mockResolvedValue(intakeOperation())
    const host = await mount(QuickWorkCapture, { module: moduleStub() })
    const source = host.querySelector<HTMLTextAreaElement>('#home-intake-text')!
    enter(source, '周三或周四处理')
    click(host, '交给 AI 整理')
    await flush()
    expect(host.textContent).toContain('本地日期理解：有冲突')
    expect(host.textContent).toContain('相互冲突的日期')

    enter(source, '下周处理')
    click(host, '交给 AI 整理')
    await flush()
    expect(preview).toHaveBeenCalledTimes(2)
    expect(host.querySelector<HTMLDetailsElement>('.technical-details')?.open).toBe(false)
    expect(host.textContent).toContain('日期待定')
    expect(host.querySelector('input[type="date"]')).toBeNull()
  })
})

describe('memory-only recommendation handoffs', () => {
  beforeEach(() => {
    vi.spyOn(studentR1Api, 'currentRoster').mockResolvedValue({ items:[], active_count:0, historical_count:0, replayed:false })
    vi.spyOn(studentR1Api, 'rosterSource').mockResolvedValue({ items:[], classes:[], source_revision:'test', total:0, cursor:null })
  })
  it('prefills affair creation separately from AI reference and never auto-creates', async () => {
    vi.spyOn(affairR1Api, 'list').mockResolvedValue([])
    vi.spyOn(sopApi, 'listTemplates').mockResolvedValue([{ template_version_id: 'template-1', revision: 1, template_key: 'baseline', version: 1, title: '学校核实流程', steps: [], workflow_scope: 'school_confirmed', risk_level: 'elevated', emergency_prompt: null, school_config_gaps: [], model_enabled: false, physical_request_count: 0, frozen: true, created_at: '2026-08-01' }])
    const create = vi.spyOn(sopApi, 'createAffair')
    const host = await mount(AffairsSurface, { token: 'token', handoff })

    expect(host.textContent).toContain('创建事务前由教师补全')
    expect(host.textContent).toContain('AI 建议，仅供参考')
    expect(host.textContent).toContain('先核实')
    expect(labeledControl<HTMLTextAreaElement>(host, '教师原文').value).toBe('教师原始记录，保持原样。')
    expect(click(host, '教师确认，创建事务').disabled).toBe(true)
    expect(create).not.toHaveBeenCalled()
  })

  it('saves retained affair advice only as a non-driving AI suggestion', async () => {
    vi.spyOn(affairR1Api, 'list').mockResolvedValue([])
    vi.spyOn(sopApi, 'listTemplates').mockResolvedValue([{ template_version_id: 'template-1', revision: 1, template_key: 'baseline', version: 1, title: '学校核实流程', steps: [], workflow_scope: 'school_confirmed', risk_level: 'elevated', emergency_prompt: null, school_config_gaps: [], model_enabled: false, physical_request_count: 0, frozen: true, created_at: '2026-08-01' }])
    const created = { affair_id: 'affair-1' } as unknown as Awaited<ReturnType<typeof sopApi.createAffair>>
    vi.spyOn(sopApi, 'createAffair').mockResolvedValue(created)
    const retain = vi.spyOn(sopApi, 'recordAiSuggestion').mockResolvedValue(created)
    const host = await mount(AffairsSurface, { token: 'token', handoff })

    enter(labeledControl<HTMLTextAreaElement>(host, '匿名参与者编号'), '学生A\n家长A')
    const checkbox = host.querySelector<HTMLInputElement>('.retain-reference input')!
    checkbox.checked = true
    checkbox.dispatchEvent(new Event('change', { bubbles: true }))
    click(host, '教师确认，创建事务')
    await flush()

    expect(sopApi.createAffair).toHaveBeenCalledWith(
      'token',
      expect.objectContaining({ participant_refs: ['学生A', '家长A'] }),
      expect.any(String),
    )
    expect(retain).toHaveBeenCalledWith('token', created, '建议按学校流程核对事实', expect.any(String))
  })

  it('retries an uncertain affair creation with the same operation id and required participants', async () => {
    vi.spyOn(affairR1Api, 'list').mockResolvedValue([])
    vi.spyOn(sopApi, 'listTemplates').mockResolvedValue([{ template_version_id: 'template-1', revision: 1, template_key: 'baseline', version: 1, title: '学校核实流程', steps: [], workflow_scope: 'school_confirmed', risk_level: 'elevated', emergency_prompt: null, school_config_gaps: [], model_enabled: false, physical_request_count: 0, frozen: true, created_at: '2026-08-01' }])
    const created = { affair_id: 'affair-1' } as unknown as Awaited<ReturnType<typeof sopApi.createAffair>>
    const create = vi.spyOn(sopApi, 'createAffair')
      .mockRejectedValueOnce(new Error('response lost'))
      .mockResolvedValueOnce(created)
    const host = await mount(AffairsSurface, { token: 'token', handoff })
    enter(labeledControl<HTMLTextAreaElement>(host, '匿名参与者编号'), '学生A')

    click(host, '教师确认，创建事务')
    await flush()
    const operationId = create.mock.calls[0]?.[2]
    expect(operationId).toBeTruthy()
    expect(host.textContent).toContain('只重试同一写入')
    expect(labeledControl<HTMLTextAreaElement>(host, '匿名参与者编号').disabled).toBe(true)

    click(host, '重试确认同一事务写入')
    await flush()
    expect(create).toHaveBeenCalledTimes(2)
    expect(create.mock.calls[1]?.[2]).toBe(operationId)
    expect(create.mock.calls[1]?.[1].participant_refs).toEqual(['学生A'])
  })

  it('recovers only the failed affair AI reference without recreating the affair', async () => {
    vi.spyOn(affairR1Api, 'list').mockResolvedValue([])
    vi.spyOn(sopApi, 'listTemplates').mockResolvedValue([{ template_version_id: 'template-1', revision: 1, template_key: 'baseline', version: 1, title: '学校核实流程', steps: [], workflow_scope: 'school_confirmed', risk_level: 'elevated', emergency_prompt: null, school_config_gaps: [], model_enabled: false, physical_request_count: 0, frozen: true, created_at: '2026-08-01' }])
    const created = { affair_id: 'affair-1' } as unknown as Awaited<ReturnType<typeof sopApi.createAffair>>
    const create = vi.spyOn(sopApi, 'createAffair').mockResolvedValue(created)
    const retain = vi.spyOn(sopApi, 'recordAiSuggestion')
      .mockRejectedValueOnce(new Error('secondary failed'))
      .mockResolvedValueOnce(created)
    const persisted = vi.fn()
    const host = await mount(AffairsSurface, { token: 'token', handoff, onHandoffPersisted: persisted })
    enter(labeledControl<HTMLTextAreaElement>(host, '匿名参与者编号'), '学生A')
    const checkbox = host.querySelector<HTMLInputElement>('.retain-reference input')!
    checkbox.checked = true
    checkbox.dispatchEvent(new Event('change', { bubbles: true }))

    click(host, '教师确认，创建事务')
    await flush()
    expect(create).toHaveBeenCalledOnce()
    expect(persisted).not.toHaveBeenCalled()
    expect(host.textContent).toContain('只重试 AI 参考')
    const aiOperationId = retain.mock.calls[0]?.[3]

    click(host, '只重试保存 AI 参考')
    await flush()
    expect(create).toHaveBeenCalledOnce()
    expect(retain).toHaveBeenCalledTimes(2)
    expect(retain.mock.calls[1]?.[3]).toBe(aiOperationId)
    expect(persisted).toHaveBeenCalledOnce()
  })

  it('can explicitly abandon a failed affair AI reference and finish without a duplicate write', async () => {
    vi.spyOn(affairR1Api, 'list').mockResolvedValue([])
    vi.spyOn(sopApi, 'listTemplates').mockResolvedValue([{ template_version_id: 'template-1', revision: 1, template_key: 'baseline', version: 1, title: '学校核实流程', steps: [], workflow_scope: 'school_confirmed', risk_level: 'elevated', emergency_prompt: null, school_config_gaps: [], model_enabled: false, physical_request_count: 0, frozen: true, created_at: '2026-08-01' }])
    const created = { affair_id: 'affair-1' } as unknown as Awaited<ReturnType<typeof sopApi.createAffair>>
    const create = vi.spyOn(sopApi, 'createAffair').mockResolvedValue(created)
    vi.spyOn(sopApi, 'recordAiSuggestion').mockRejectedValue(new Error('secondary failed'))
    const persisted = vi.fn()
    const host = await mount(AffairsSurface, { token: 'token', handoff, onHandoffPersisted: persisted })
    enter(labeledControl<HTMLTextAreaElement>(host, '匿名参与者编号'), '学生A')
    const checkbox = host.querySelector<HTMLInputElement>('.retain-reference input')!
    checkbox.checked = true
    checkbox.dispatchEvent(new Event('change', { bubbles: true }))
    click(host, '教师确认，创建事务')
    await flush()

    click(host, '放弃 AI 参考并完成')
    await flush()
    expect(create).toHaveBeenCalledOnce()
    expect(persisted).toHaveBeenCalledOnce()
  })

  it('requires student selection, prefills teacher text and keeps AI reference separate without saving', async () => {
    const subject: DirectorySubject = { subject_id: 's1', display_name: '合成学生', source_student_id: 'S1', class_label: '一班', support_record_count: 0, support_plan_count: 0, confirmed_entry_count: 0, projection_state: 'none', attention_pending_count: 0, last_confirmed_at: null }
    vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items: [subject], cursor: null, total: 1, page_size: 20 })
    vi.spyOn(supportApi, 'listRecords').mockResolvedValue([])
    const create = vi.spyOn(supportApi, 'createRecord')
    const studentHandoff: HomeIntakeHandoff = { ...handoff, destination: 'student_support', aiReference: { ...handoff.aiReference, kind: 'student_support_recommendation' } }
    const Root = defineComponent({
      setup() {
        const panel = ref<'directory' | 'support'>('directory')
        return () => h(StudentSurface, {
          token: 'token', panel: panel.value, status: null, handoff: studentHandoff,
          onNavigate: (next: 'directory' | 'support' | 'academic' | 'security') => {
            if (next === 'directory' || next === 'support') panel.value = next
          },
        })
      },
    })
    const host = await mount(Root, {})
    expect(host.textContent).toContain('请先选择对应学生')
    expect(create).not.toHaveBeenCalled()

    click(host, '合成学生')
    await flush()
    expect(host.textContent).toContain('已选择 合成学生')
    expect(host.textContent).toContain('AI 建议，仅供参考')
    expect(host.querySelector<HTMLTextAreaElement>('.editor textarea')?.value).toBe('教师原始记录，保持原样。')
    expect(create).not.toHaveBeenCalled()
  })

  it('stores retained student advice separately as an unconfirmed AI draft', async () => {
    const subject: DirectorySubject = { subject_id: 's1', display_name: '合成学生', source_student_id: 'S1', class_label: '一班', support_record_count: 0, support_plan_count: 0, confirmed_entry_count: 0, projection_state: 'none', attention_pending_count: 0, last_confirmed_at: null }
    const saved = { record_id:'r1', subject_id:'s1', record_kind:'teacher_observation', state:'active', current_revision:1, content:'教师原始记录，保持原样。', scene:'日常观察', source:'教师本人观察', counterexample:null, observed_at:'2026-08-03', review_at:null, expires_at:null }
    vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items: [subject], cursor: null, total: 1, page_size: 20 })
    vi.spyOn(supportApi, 'listRecords').mockResolvedValue([])
    const create = vi.spyOn(supportApi, 'createRecord')
      .mockResolvedValueOnce(saved)
      .mockResolvedValueOnce({ ...saved, record_id: 'r2', record_kind: 'ai_draft', content: '建议按学校流程核对事实' })
    const studentHandoff: HomeIntakeHandoff = { ...handoff, destination: 'student_support', aiReference: { ...handoff.aiReference, kind: 'student_support_recommendation' } }
    const Root = defineComponent({
      setup() {
        const panel = ref<'directory' | 'support'>('directory')
        return () => h(StudentSurface, {
          token: 'token', panel: panel.value, status: null, handoff: studentHandoff,
          onNavigate: (next: 'directory' | 'support' | 'academic' | 'security') => {
            if (next === 'directory' || next === 'support') panel.value = next
          },
        })
      },
    })
    const host = await mount(Root, {})
    click(host, '合成学生')
    await flush()
    const checkbox = host.querySelector<HTMLInputElement>('.handoff-prefill .retain-reference input')!
    checkbox.checked = true
    checkbox.dispatchEvent(new Event('change', { bubbles: true }))
    click(host, '仅保存到本机')
    await flush()

    expect(create).toHaveBeenCalledTimes(2)
    expect(create.mock.calls[1]?.[2]).toMatchObject({
      record_kind: 'ai_draft',
      content: '建议按学校流程核对事实',
      source: 'AI 参考建议（教师选择保留）',
    })
    expect(create.mock.calls[0]?.[3]).toEqual(expect.any(String))
    expect(create.mock.calls[1]?.[3]).toEqual(expect.any(String))
    expect(create.mock.calls[1]?.[3]).not.toBe(create.mock.calls[0]?.[3])
  })

  it('retries an uncertain handoff teacher record with the same operation id', async () => {
    vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items: [directorySubject], cursor: null, total: 1, page_size: 20 })
    vi.spyOn(supportApi, 'listRecords').mockResolvedValue([])
    const saved = savedTeacherRecord()
    const create = vi.spyOn(supportApi, 'createRecord')
      .mockRejectedValueOnce(new Error('response lost'))
      .mockResolvedValueOnce(saved)
    const host = await mount(studentRoot(), {})
    click(host, '合成学生')
    await flush()

    click(host, '仅保存到本机')
    await flush()
    const operationId = create.mock.calls[0]?.[3]
    expect(operationId).toBeTruthy()
    expect(host.textContent).toContain('只重试同一写入')
    expect(host.querySelector<HTMLTextAreaElement>('.editor textarea')!.disabled).toBe(true)

    click(host, '重试确认同一教师记录写入')
    await flush()
    expect(create).toHaveBeenCalledTimes(2)
    expect(create.mock.calls[1]?.[3]).toBe(operationId)
    expect(create.mock.calls[1]?.[2]).toMatchObject({
      record_kind: 'teacher_observation',
      content: '教师原始记录，保持原样。',
    })
  })

  it('keeps an active handoff from selecting or resetting to an unrelated record', async () => {
    const unrelated = savedTeacherRecord({ record_id: 'old-record', content: '无关旧记录' })
    vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items: [directorySubject], cursor: null, total: 1, page_size: 20 })
    vi.spyOn(supportApi, 'listRecords').mockResolvedValue([unrelated])
    const create = vi.spyOn(supportApi, 'createRecord').mockResolvedValue(savedTeacherRecord())
    const revise = vi.spyOn(supportApi, 'reviseRecord')
    const host = await mount(studentRoot(), {})
    click(host, '合成学生')
    await flush()

    const unrelatedButton = [...host.querySelectorAll<HTMLButtonElement>('.record-list button')]
      .find((button) => button.textContent?.includes('无关旧记录'))!
    expect(unrelatedButton.disabled).toBe(true)
    expect(click(host, '新建本机记录').disabled).toBe(true)
    unrelatedButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(host.querySelector<HTMLTextAreaElement>('.editor textarea')!.value).toBe('教师原始记录，保持原样。')

    click(host, '仅保存到本机')
    await flush()
    expect(revise).not.toHaveBeenCalled()
    expect(create).toHaveBeenCalledWith(
      'token',
      's1',
      expect.objectContaining({ content: '教师原始记录，保持原样。', record_kind: 'teacher_observation' }),
      expect.any(String),
    )
  })

  it('recovers only a failed student AI draft and never resaves the teacher record', async () => {
    vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items: [directorySubject], cursor: null, total: 1, page_size: 20 })
    vi.spyOn(supportApi, 'listRecords').mockResolvedValue([])
    const saved = savedTeacherRecord()
    const create = vi.spyOn(supportApi, 'createRecord')
      .mockResolvedValueOnce(saved)
      .mockRejectedValueOnce(new Error('secondary failed'))
      .mockResolvedValueOnce(savedTeacherRecord({ record_id: 'ai-1', record_kind: 'ai_draft', content: '建议按学校流程核对事实' }))
    const revise = vi.spyOn(supportApi, 'reviseRecord')
    const host = await mount(studentRoot(), {})
    click(host, '合成学生')
    await flush()
    const checkbox = host.querySelector<HTMLInputElement>('.handoff-prefill .retain-reference input')!
    checkbox.checked = true
    checkbox.dispatchEvent(new Event('change', { bubbles: true }))

    click(host, '仅保存到本机')
    await flush()
    expect(host.textContent).toContain('不要重复保存或修订教师记录')
    expect(create).toHaveBeenCalledTimes(2)
    const aiOperationId = create.mock.calls[1]?.[3]

    click(host, '只重试保存 AI 参考')
    await flush()
    expect(create).toHaveBeenCalledTimes(3)
    expect(create.mock.calls.filter((call) => call[2].record_kind === 'teacher_observation')).toHaveLength(1)
    expect(create.mock.calls[2]?.[2].record_kind).toBe('ai_draft')
    expect(create.mock.calls[2]?.[3]).toBe(aiOperationId)
    expect(revise).not.toHaveBeenCalled()
  })

  it('can explicitly abandon a failed student AI draft and finish without another write', async () => {
    vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items: [directorySubject], cursor: null, total: 1, page_size: 20 })
    vi.spyOn(supportApi, 'listRecords').mockResolvedValue([])
    const create = vi.spyOn(supportApi, 'createRecord')
      .mockResolvedValueOnce(savedTeacherRecord())
      .mockRejectedValueOnce(new Error('secondary failed'))
    const persisted = vi.fn()
    const Root = defineComponent({
      setup() {
        const panel = ref<'directory' | 'support'>('directory')
        return () => h(StudentSurface, {
          token: 'token', panel: panel.value, status: null, handoff: studentHandoff,
          onNavigate: (next: 'directory' | 'support' | 'academic' | 'security') => {
            if (next === 'directory' || next === 'support') panel.value = next
          },
          onHandoffPersisted: persisted,
        })
      },
    })
    const host = await mount(Root, {})
    click(host, '合成学生')
    await flush()
    const checkbox = host.querySelector<HTMLInputElement>('.handoff-prefill .retain-reference input')!
    checkbox.checked = true
    checkbox.dispatchEvent(new Event('change', { bubbles: true }))
    click(host, '仅保存到本机')
    await flush()

    click(host, '放弃 AI 参考并完成')
    await flush()
    expect(create).toHaveBeenCalledTimes(2)
    expect(persisted).toHaveBeenCalledOnce()
  })

  it('keeps the exact prepared preview visible and usable when production cleanup clears the handoff prop', async () => {
    const saved = savedTeacherRecord()
    vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items: [directorySubject], cursor: null, total: 1, page_size: 20 })
    vi.spyOn(supportApi, 'listRecords')
      .mockResolvedValueOnce([])
      .mockResolvedValue([saved])
    vi.spyOn(supportApi, 'createRecord').mockResolvedValue(saved)
    const prepared = { review_id: 'review-1', preview_id: 'preview-1', fingerprint: 'f'.repeat(64), exact_payload: { record_text: '逐字匿名预览' } }
    vi.spyOn(studentR1Api, 'prepareReview').mockResolvedValue(prepared)
    const confirm = vi.spyOn(studentR1Api, 'confirmReview').mockResolvedValue({ review_id: 'review-1', base_revision_number: 1, state: 'completed', turns: [] })
    const host = await mount(studentRoot(studentHandoff, true), {})
    click(host, '合成学生')
    await flush()

    click(host, '保存并准备 AI 讨论')
    await flush()
    expect(host.querySelector('.handoff-prefill')).toBeNull()
    expect(host.textContent).toContain('逐字匿名预览')
    expect(studentR1Api.prepareReview).toHaveBeenCalledWith('token', 'r1', 1, '')

    click(host, '确认本轮只发送一次')
    await flush()
    expect(confirm).toHaveBeenCalledWith('token', prepared)
  })
})
