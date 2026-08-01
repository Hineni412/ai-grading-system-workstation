import { createApp, nextTick, type App } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { modelApprovalApi, type ModelPreview } from '../api/modelApproval'
import { vaultApi } from '../api/vault'
import {
  workApi,
  type WorkNode,
  type WorkPlanPreview,
  type WorkPlanResult,
  type WorkSnapshot,
} from '../api/work'
import { studentCardApi, type StudentCard, type StudentCardEntry } from '../api/studentCards'
import { supportApi } from '../api/support'
import SensitiveStudentWorkspace from '../components/SensitiveStudentWorkspace.vue'
import UnifiedWorkBoard from '../components/UnifiedWorkBoard.vue'
import ClassTeacherWorkbenchView from '../views/ClassTeacherWorkbenchView.vue'

const mounted: App[] = []

function studentCard(entries: StudentCardEntry[] = []): StudentCard {
  return {
    subject: {
      subject_id: 'subject-001',
      source_student_id: 'synthetic-student-001',
      display_name: '合成学生甲',
      class_label: '合成一班',
    },
    entries,
    existing_records: [],
    support_plans: [],
  }
}

function node(
  nodeId: string,
  kind: WorkNode['kind'],
  title: string,
  status: WorkNode['status'] = 'pending',
): WorkNode {
  return {
    node_id: nodeId,
    kind,
    classification: 'ordinary',
    title,
    details: null,
    status,
    due_date: '2026-08-07',
    revision: 1,
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
  }
}

function snapshot(withBranch = false): WorkSnapshot {
  const nodes = [
    node('goal-1', 'goal', '周五前完成家长会准备'),
    node('step-1', 'decision', '确认会议要求'),
    node('step-2', 'task', '准备会议材料', 'in_progress'),
    node('step-3', 'waiting', '等待场地确认', 'waiting'),
  ]
  if (withBranch) nodes.push(node('branch-1', 'communication', '联系摄影老师'))
  return {
    as_of: '2026-08-07',
    start_date: '2026-08-03',
    end_date: '2026-08-09',
    nodes,
    edges: [
      { source_node_id: 'goal-1', target_node_id: 'step-1', relation: 'contains' },
      { source_node_id: 'goal-1', target_node_id: 'step-2', relation: 'contains' },
      { source_node_id: 'goal-1', target_node_id: 'step-3', relation: 'contains' },
      { source_node_id: 'step-1', target_node_id: 'step-2', relation: 'next' },
      { source_node_id: 'step-2', target_node_id: 'step-3', relation: 'next' },
      ...(withBranch
        ? [{
            source_node_id: 'step-1',
            target_node_id: 'branch-1',
            relation: 'review_of' as const,
          }]
        : []),
    ],
    today: nodes,
    overdue: [],
    waiting: nodes.filter((item) => item.status === 'waiting'),
  }
}

function dependencySnapshot(): WorkSnapshot {
  const base = snapshot()
  const branchOne = node('branch-1', 'decision', '核对新的场地限制')
  const branchTwo = node('branch-2', 'communication', '按新限制联系负责人')
  return {
    ...base,
    nodes: [...base.nodes, branchOne, branchTwo],
    edges: [
      { source_node_id: 'goal-1', target_node_id: 'step-1', relation: 'contains' },
      { source_node_id: 'goal-1', target_node_id: 'step-2', relation: 'contains' },
      { source_node_id: 'goal-1', target_node_id: 'step-3', relation: 'contains' },
      { source_node_id: 'step-2', target_node_id: 'step-1', relation: 'depends_on' },
      { source_node_id: 'step-1', target_node_id: 'branch-1', relation: 'review_of' },
      { source_node_id: 'branch-1', target_node_id: 'branch-2', relation: 'next' },
    ],
    today: [...base.today, branchOne, branchTwo],
  }
}

function convergingBranchSnapshot(): WorkSnapshot {
  const base = snapshot()
  const branchA = node('branch-a', 'decision', '核对场地变更')
  const branchB = node('branch-b', 'communication', '核对人员变更')
  const shared = node('branch-shared', 'task', '按两项变更更新安排')
  return {
    ...base,
    nodes: [...base.nodes, branchA, branchB, shared],
    edges: [
      ...base.edges,
      { source_node_id: 'step-1', target_node_id: 'branch-a', relation: 'review_of' },
      { source_node_id: 'step-1', target_node_id: 'branch-b', relation: 'review_of' },
      { source_node_id: 'branch-a', target_node_id: 'branch-shared', relation: 'next' },
      { source_node_id: 'branch-b', target_node_id: 'branch-shared', relation: 'next' },
    ],
    today: [...base.today, branchA, branchB, shared],
  }
}

function goalAndStandaloneBranchSnapshot(): WorkSnapshot {
  const base = snapshot()
  const standalone = node('standalone-1', 'task', '独立准备材料')
  const goalBranch = node('goal-branch', 'decision', '根据目标进展调整范围')
  const standaloneBranch = node('standalone-branch', 'communication', '根据独立事项进展联系负责人')
  return {
    ...base,
    nodes: [...base.nodes, standalone, goalBranch, standaloneBranch],
    edges: [
      ...base.edges,
      { source_node_id: 'goal-1', target_node_id: 'goal-branch', relation: 'review_of' },
      { source_node_id: 'standalone-1', target_node_id: 'standalone-branch', relation: 'review_of' },
    ],
    today: [...base.today, standalone, goalBranch, standaloneBranch],
  }
}

function planPreview(sourceText: string, dueDate: string | null): WorkPlanPreview {
  return {
    preview_id: 'plan-preview-001',
    source_text: sourceText,
    final_due_date: dueDate,
    date_semantics: 'date-only',
    exact_payload: {
      purpose: 'ordinary_work_plan',
      task_text: sourceText,
      final_due_date: dueDate,
      instructions: ['按本次任务语境拆解并倒排，不使用固定模板。'],
    },
    fingerprint: 'a'.repeat(64),
    expires_at: '2026-08-01T01:00:00Z',
    model_provider: '合成配置',
    model_endpoint: 'https://model.invalid/v1',
    model_name: 'synthetic-work-planner',
    destination_fingerprint: 'd'.repeat(64),
    model_enabled: true,
    max_physical_requests: 1,
    physical_request_count: 0,
  }
}

function planResult(preview: WorkPlanPreview, progress = false): WorkPlanResult {
  return {
    preview_id: preview.preview_id,
    operation_id: 'work-model-operation-001',
    state: 'succeeded',
    physical_request_count: 1,
    error_category: null,
    questions: [],
    assumptions: ['日期按照教师给出的最终日期倒排'],
    plan: {
      nodes: [{
        draft_key: progress ? 'branch-1' : 'goal-1',
        kind: progress ? 'communication' : 'goal',
        title: progress ? '联系摄影老师' : preview.source_text,
        details: null,
        status: 'pending',
        due_date: preview.final_due_date,
      }],
      edges: [],
      assumptions: ['日期按照教师给出的最终日期倒排'],
    },
    plan_fingerprint: 'b'.repeat(64),
    teacher_confirmation_required: true,
    local_context: progress
      ? { mode: 'progress_update', parent_node_id: 'step-1', parent_revision: 1 }
      : { mode: 'new_work' },
  }
}

async function flushAll(): Promise<void> {
  for (let index = 0; index < 6; index += 1) {
    await Promise.resolve()
    await nextTick()
  }
}

function mount(component: Parameters<typeof createApp>[0], props = {}): HTMLElement {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(component, props)
  app.mount(host)
  mounted.push(app)
  return host
}

function setValue(control: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  control.value = value
  control.dispatchEvent(new Event('input', { bubbles: true }))
}

function button(host: HTMLElement, label: string): HTMLButtonElement {
  const found = [...host.querySelectorAll<HTMLButtonElement>('button')]
    .find((item) => item.textContent?.trim() === label)
  if (!found) throw new Error(`Missing button: ${label}`)
  return found
}

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('unified ordinary work graph', () => {
  it('locates the calendar on the system date without forcing a task deadline', async () => {
    vi.spyOn(workApi, 'query').mockResolvedValue(snapshot())
    const host = mount(UnifiedWorkBoard)
    await flushAll()

    const systemDate = new Date()
    const expected = [
      systemDate.getFullYear(),
      String(systemDate.getMonth() + 1).padStart(2, '0'),
      String(systemDate.getDate()).padStart(2, '0'),
    ].join('-')
    expect(host.querySelector('.week-day--selected')?.getAttribute('data-date')).toBe(expected)
    expect(host.querySelector<HTMLInputElement>('.quick-capture input[type="date"]')?.value)
      .toBe('')
  })

  it('uses a clickable calendar day to scope and prefill draft creation', async () => {
    vi.spyOn(workApi, 'query').mockResolvedValue(snapshot())
    const preview = vi.spyOn(workApi, 'previewPlan').mockImplementation(async (
      text,
      dueDate,
    ): Promise<WorkPlanPreview> => planPreview(text, dueDate))
    const invoke = vi.spyOn(workApi, 'invokePlan')
    const host = mount(UnifiedWorkBoard)
    await flushAll()

    const day = host.querySelector<HTMLButtonElement>('.week-day')
    const selectedDate = day?.dataset.date
    if (!day || !selectedDate) throw new Error('Missing clickable calendar day')
    day.click()
    await nextTick()

    expect(day.getAttribute('aria-pressed')).toBe('true')
    expect(host.querySelector<HTMLInputElement>('.quick-capture input[type="date"]')?.value)
      .toBe(selectedDate)
    expect(host.querySelector('.selected-day-scope')?.textContent).toContain('点击某天可指定日期')

    const input = host.querySelector<HTMLInputElement>('.quick-capture__text input')!
    setValue(input, '准备家长会')
    await nextTick()
    button(host, '生成 AI 发送预览').click()
    await flushAll()

    expect(preview).toHaveBeenCalledWith('准备家长会', selectedDate)
    expect(host.querySelector('.model-request-preview')?.textContent)
      .toContain('按本次任务语境拆解并倒排')
    expect(host.querySelector('.model-destination')?.textContent).toContain('合成配置')
    expect(host.querySelector('.model-destination')?.textContent).toContain('synthetic-work-planner')
    expect(invoke).not.toHaveBeenCalled()
  })

  it('renders three compact nodes with visible contains/next connectors', async () => {
    vi.spyOn(workApi, 'query').mockResolvedValue(snapshot())

    const host = mount(UnifiedWorkBoard)
    await flushAll()

    expect(host.querySelectorAll('.graph-flow__unit')).toHaveLength(3)
    expect(host.querySelector('.contains-arrow')?.textContent).toContain('包含')
    expect(host.querySelectorAll('.graph-flow__unit--has-next')).toHaveLength(2)
    expect(host.textContent).toContain('进行中')
    expect(host.textContent).toContain('等待中')
  })

  it('renders exact depends_on relations and every node in a multi-node progress branch', async () => {
    vi.spyOn(workApi, 'query').mockResolvedValue(dependencySnapshot())

    const host = mount(UnifiedWorkBoard)
    await flushAll()

    const dependency = host.querySelector<HTMLElement>('[data-relation="depends_on"]')
    expect(dependency?.textContent).toContain('准备会议材料 → 依赖于 → 确认会议要求')
    expect(host.querySelectorAll('.graph-flow__unit--has-next')).toHaveLength(0)
    expect(host.querySelectorAll('.branch-node')).toHaveLength(1)
    expect(host.querySelectorAll('.branch-flow .graph-node')).toHaveLength(2)
    expect(host.querySelector('.branch-flow')?.textContent).toContain('按新限制联系负责人')
    expect(host.querySelector('.standalone-graph')?.textContent ?? '').not.toContain('按新限制联系负责人')
  })

  it('renders one copy of a converging multi-root progress branch', async () => {
    vi.spyOn(workApi, 'query').mockResolvedValue(convergingBranchSnapshot())

    const host = mount(UnifiedWorkBoard)
    await flushAll()

    expect(host.querySelectorAll('.branch-node')).toHaveLength(1)
    expect(host.querySelectorAll('.branch-flow .graph-node')).toHaveLength(3)
    const sharedCards = [...host.querySelectorAll<HTMLElement>('.branch-flow .graph-node h4')]
      .filter((title) => title.textContent === '按两项变更更新安排')
    expect(sharedCards).toHaveLength(1)
  })

  it('renders confirmed progress branches created from goals and standalone work', async () => {
    vi.spyOn(workApi, 'query').mockResolvedValue(goalAndStandaloneBranchSnapshot())

    const host = mount(UnifiedWorkBoard)
    await flushAll()

    expect(host.querySelectorAll('.branch-node')).toHaveLength(2)
    expect(host.textContent).toContain('根据目标进展调整范围')
    expect(host.textContent).toContain('根据独立事项进展联系负责人')
  })

  it('recovers the same durable model operation after a page refresh', async () => {
    const preview = planPreview('完成班级工作', '2026-08-07')
    const result = planResult(preview)
    localStorage.setItem('class-teacher:ordinary-ai-plan-operations:v1', JSON.stringify([{
      operation_id: result.operation_id,
      mode: 'new_work',
    }]))
    vi.spyOn(workApi, 'query').mockResolvedValue(snapshot())
    const status = vi.spyOn(workApi, 'planStatus').mockResolvedValue(result)

    const host = mount(UnifiedWorkBoard)
    await flushAll()

    expect(status).toHaveBeenCalledWith(result.operation_id)
    expect(host.querySelector('.draft-preview')?.textContent).toContain('AI 已返回待教师复核的流程')
    expect(button(host, '教师确认并加入工作图')).not.toBeNull()
  })

  it('shows AI dependency edges before the teacher confirms persistence', async () => {
    const preview = planPreview('完成一次活动安排', '2026-08-07')
    const result = planResult(preview)
    result.plan = {
      assumptions: [],
      nodes: [
        { draft_key: 'goal', kind: 'goal', title: '完成活动安排', details: null, status: 'pending', due_date: '2026-08-07' },
        { draft_key: 'check', kind: 'decision', title: '核对实际要求', details: null, status: 'pending', due_date: '2026-08-06' },
        { draft_key: 'deliver', kind: 'task', title: '完成确认后的事项', details: null, status: 'pending', due_date: '2026-08-07' },
      ],
      edges: [
        { source_draft_key: 'goal', target_draft_key: 'check', relation: 'contains' },
        { source_draft_key: 'goal', target_draft_key: 'deliver', relation: 'contains' },
        { source_draft_key: 'deliver', target_draft_key: 'check', relation: 'depends_on' },
      ],
    }
    vi.spyOn(workApi, 'query').mockResolvedValue(snapshot())
    vi.spyOn(workApi, 'previewPlan').mockResolvedValue(preview)
    vi.spyOn(workApi, 'invokePlan').mockResolvedValue(result)

    const host = mount(UnifiedWorkBoard)
    await flushAll()
    setValue(host.querySelector<HTMLInputElement>('.quick-capture__text input')!, preview.source_text)
    await nextTick()
    button(host, '生成 AI 发送预览').click()
    await flushAll()
    button(host, '确认发送这份内容').click()
    await flushAll()

    expect(host.querySelector('.draft-preview [data-relation="depends_on"]')?.textContent)
      .toContain('完成确认后的事项 → 依赖于 → 核对实际要求')
  })

  it('previews latest progress before adding a review branch', async () => {
    vi.spyOn(workApi, 'query')
      .mockResolvedValueOnce(snapshot())
      .mockResolvedValueOnce(snapshot(true))
    const progressPreview = planPreview('联系摄影老师', '2026-08-07')
    vi.spyOn(workApi, 'previewProgressPlan').mockResolvedValue(progressPreview)
    const invoke = vi.spyOn(workApi, 'invokePlan')
      .mockResolvedValue(planResult(progressPreview, true))
    const confirm = vi.spyOn(workApi, 'confirmPlan')
      .mockRejectedValueOnce(new Error('synthetic response lost'))
      .mockResolvedValue({
        goal_id: null,
        parent_node_id: 'step-1',
        nodes: [node('branch-1', 'communication', '联系摄影老师')],
      })
    const host = mount(UnifiedWorkBoard)
    await flushAll()

    const progressButtons = [...host.querySelectorAll<HTMLButtonElement>('.graph-flow button')]
      .filter((item) => item.textContent?.trim() === '写最新情况')
    progressButtons[0]?.click()
    await nextTick()
    const input = host.querySelector<HTMLInputElement>('.progress-composer input:not([type])')
    if (!input) throw new Error('Missing progress input')
    setValue(input, '联系摄影老师')
    await nextTick()
    button(host, '生成 AI 调整预览').click()
    await flushAll()

    expect(host.querySelector('.model-request-preview')?.textContent).toContain('联系摄影老师')
    expect(invoke).not.toHaveBeenCalled()
    button(host, '确认发送这份内容').click()
    await flushAll()
    expect(invoke).toHaveBeenCalledOnce()
    expect(host.querySelector('.progress-preview')?.textContent).toContain('确认前不会写入工作图')
    expect(confirm).not.toHaveBeenCalled()
    button(host, '教师确认并加入工作图').click()
    await flushAll()
    expect(confirm).toHaveBeenCalledOnce()
    button(host, '教师确认并加入工作图').click()
    await flushAll()

    expect(confirm).toHaveBeenCalledTimes(2)
    expect(confirm.mock.calls[0]?.[1]).toBe(confirm.mock.calls[1]?.[1])
    expect(host.querySelector('.branch-node')?.textContent).toContain('联系摄影老师')
  })
})

describe('default homepage and sensitive entry', () => {
  it('loads ordinary work while locked and mounts no sensitive component', async () => {
    const query = vi.spyOn(workApi, 'query').mockResolvedValue(snapshot())
    const listCards = vi.spyOn(studentCardApi, 'list')
    vi.spyOn(vaultApi, 'status').mockResolvedValue({
      initialized: true,
      locked: true,
      idle_timeout_seconds: 300,
      retry_after_seconds: 0,
      format_version: 1,
      protection_mode: 'pin_dpapi_current_user_v2',
      protection_state: 'active',
      legacy_upgrade_available: false,
    })

    const host = mount(ClassTeacherWorkbenchView)
    await flushAll()

    expect(query).toHaveBeenCalledOnce()
    expect(host.querySelector('.goal-graph')).not.toBeNull()
    expect(host.querySelector('.student-workspace')).toBeNull()
    expect(listCards).not.toHaveBeenCalled()
    expect(host.textContent).not.toContain('行动账本')
    expect(host.textContent).not.toContain('今天要推进什么')
    expect(host.textContent).not.toContain('不让“等回复”变成遗忘')
    expect(host.textContent).not.toContain('关注与教师决定')
  })

  it('uses the six-digit PIN path and only then mounts the compact student cards', async () => {
    vi.spyOn(workApi, 'query').mockResolvedValue(snapshot())
    vi.spyOn(vaultApi, 'status').mockImplementation(async (token) => ({
      initialized: true,
      locked: !token,
      idle_timeout_seconds: 300,
      retry_after_seconds: 0,
      format_version: 1,
      protection_mode: 'pin_dpapi_current_user_v2',
      protection_state: 'active',
      legacy_upgrade_available: false,
    }))
    const unlock = vi.spyOn(vaultApi, 'unlockPin').mockResolvedValue({
      session_token: 'synthetic-session',
      idle_timeout_seconds: 300,
      recovery_key: null,
    })
    vi.spyOn(vaultApi, 'lock').mockResolvedValue({})
    vi.spyOn(vaultApi, 'touch').mockResolvedValue({})
    vi.spyOn(studentCardApi, 'list').mockResolvedValue([studentCard()])
    const modelPrepare = vi.spyOn(modelApprovalApi, 'prepare')

    const host = mount(ClassTeacherWorkbenchView)
    await flushAll()
    const pin = host.querySelector<HTMLInputElement>('input[inputmode="numeric"]')
    if (!pin) throw new Error('Missing PIN input')
    setValue(pin, '482615')
    await nextTick()
    button(host, '解锁敏感区').click()
    await flushAll()

    expect(unlock).toHaveBeenCalledWith('482615')
    expect(host.querySelector('.student-workspace')).not.toBeNull()
    expect(host.textContent).toContain('合成学生甲')
    expect(host.textContent).toContain('教师原话')
    expect(modelPrepare).not.toHaveBeenCalled()
    expect(host.textContent).not.toContain('今天要推进什么')
    expect(host.textContent).not.toContain('不让“等回复”变成遗忘')
    expect(host.textContent).not.toContain('关注与教师决定')
  })

  it('lets an unlocked legacy vault explicitly switch to a six-digit PIN', async () => {
    vi.spyOn(workApi, 'query').mockResolvedValue(snapshot())
    let upgraded = false
    vi.spyOn(vaultApi, 'status').mockImplementation(async (token) => ({
      initialized: true,
      locked: upgraded || !token,
      idle_timeout_seconds: 300,
      retry_after_seconds: 0,
      format_version: 1,
      protection_mode: upgraded ? 'pin_dpapi_current_user_v2' : 'legacy_password_v1',
      protection_state: upgraded ? 'active' : null,
      legacy_upgrade_available: !upgraded,
    }))
    vi.spyOn(vaultApi, 'unlock').mockResolvedValue({
      session_token: 'synthetic-session',
      idle_timeout_seconds: 300,
      recovery_key: null,
    })
    const upgrade = vi.spyOn(vaultApi, 'upgradeLegacyToPin').mockImplementation(async () => {
      upgraded = true
      return {}
    })
    vi.spyOn(vaultApi, 'lock').mockResolvedValue({})
    vi.spyOn(vaultApi, 'touch').mockResolvedValue({})
    vi.spyOn(studentCardApi, 'list').mockResolvedValue([])

    const host = mount(ClassTeacherWorkbenchView)
    await flushAll()
    expect(host.textContent).toContain('解锁后可直接改为 6 位数字 PIN')
    const oldPassword = host.querySelector<HTMLInputElement>('input[autocomplete="current-password"]')
    if (!oldPassword) throw new Error('Missing legacy password input')
    setValue(oldPassword, 'short-old-password')
    await nextTick()
    button(host, '解锁敏感区').click()
    await flushAll()

    button(host, '改为 6 位 PIN').click()
    await nextTick()
    const inputs = [...host.querySelectorAll<HTMLInputElement>('.pin-upgrade__form input')]
    setValue(inputs[0]!, 'short-old-password')
    setValue(inputs[1]!, '482615')
    setValue(inputs[2]!, '482615')
    await nextTick()
    button(host, '确认更改并锁定').click()
    await flushAll()

    expect(upgrade).toHaveBeenCalledWith('synthetic-session', 'short-old-password', '482615')
    expect(host.textContent).toContain('已改为 6 位 PIN')
    expect(host.textContent).toContain('输入 6 位 PIN')
    expect(host.querySelector('.student-workspace')).toBeNull()
  })
})

describe('exact anonymous model preview', () => {
  it('provides a roster entrance before AI discussion', async () => {
    vi.spyOn(studentCardApi, 'list')
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([studentCard()])
    const createSubject = vi.spyOn(supportApi, 'createSubject').mockResolvedValue({
      subject_id: 'subject-001',
      revision: 1,
      source_student_id: 'synthetic-student-001',
      display_name: '合成学生甲',
      class_label: '合成一班',
    })
    const host = mount(SensitiveStudentWorkspace, { sessionToken: 'synthetic-session' })
    await flushAll()

    expect(host.textContent).toContain('班级学生名单')
    expect(button(host, '与 AI 讨论学生情况').disabled).toBe(true)
    const rosterInputs = host.querySelectorAll<HTMLInputElement>('.roster-form input')
    setValue(rosterInputs[0]!, '合成学生甲')
    setValue(rosterInputs[2]!, '合成一班')
    await nextTick()
    button(host, '加入名单').click()
    await flushAll()

    expect(createSubject).toHaveBeenCalledWith(
      'synthetic-session',
      expect.objectContaining({
        display_name: '合成学生甲',
        class_label: '合成一班',
      }),
    )
    expect(host.textContent).toContain('学生已加入本机加密名单')
    expect(button(host, '与 AI 讨论学生情况').disabled).toBe(false)
  })

  it('keeps same-name classmates distinct when their school ids differ', async () => {
    const secondCard = studentCard()
    secondCard.subject = {
      ...secondCard.subject,
      subject_id: 'subject-002',
      source_student_id: 'synthetic-student-002',
    }
    vi.spyOn(studentCardApi, 'list')
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([studentCard(), secondCard])
    const createSubject = vi.spyOn(supportApi, 'createSubject').mockResolvedValue({
      subject_id: 'subject-created',
      revision: 1,
      source_student_id: 'synthetic-student-001',
      display_name: '同名学生',
      class_label: '合成一班',
    })
    const host = mount(SensitiveStudentWorkspace, { sessionToken: 'synthetic-session' })
    await flushAll()

    const batch = host.querySelector<HTMLTextAreaElement>('.roster-batch textarea')!
    setValue(batch, '同名学生，S001，合成一班\n同名学生，S002，合成一班')
    await nextTick()
    button(host, '批量加入名单').click()
    await flushAll()

    expect(createSubject).toHaveBeenCalledTimes(2)
    expect(createSubject.mock.calls.map((call) => call[1].source_student_id))
      .toEqual(['S001', 'S002'])
  })

  it('shows the exact payload and disables dispatch when live model is off', async () => {
    const preview: ModelPreview = {
      preview_id: 'preview-001',
      purpose: 'student_support_note',
      classification: 'restricted',
      exact_payload: {
        purpose: 'student_support_note',
        student_alias: '学生A',
        task_text: '学生A希望调整作业节奏',
        instructions: '只生成待教师复核的中性草稿。',
      },
      removed_categories: ['姓名或称呼'],
      fingerprint: 'a'.repeat(64),
      expires_at: '2026-08-01T01:00:00Z',
      model_name: '未启用真实模型',
      model_enabled: false,
      max_physical_requests: 1,
      estimated_cost: null,
    }
    vi.spyOn(studentCardApi, 'list').mockResolvedValue([studentCard()])
    vi.spyOn(modelApprovalApi, 'prepare').mockResolvedValue(preview)
    const confirm = vi.spyOn(modelApprovalApi, 'confirm')
    const host = mount(SensitiveStudentWorkspace, { sessionToken: 'synthetic-session' })
    await flushAll()

    const input = host.querySelector<HTMLTextAreaElement>('.student-capture textarea')
    if (!input) throw new Error('Missing student note input')
    setValue(input, '张三同学希望调整作业节奏')
    await nextTick()
    button(host, '生成匿名发送预览').click()
    await flushAll()

    expect(host.querySelector('.model-preview')?.textContent).toContain('学生A希望调整作业节奏')
    const disabled = button(host, '真实模型未启用，不会发送或产生费用')
    expect(disabled.disabled).toBe(true)
    disabled.click()
    expect(confirm).not.toHaveBeenCalled()
  })

  it('returns follow-up questions to the teacher before another exact preview', async () => {
    const preview: ModelPreview = {
      preview_id: 'preview-follow-up',
      purpose: 'student_support_note',
      classification: 'restricted',
      exact_payload: {
        purpose: 'student_support_note',
        student_alias: '学生A',
        task_text: '学生A最近安排有困难',
        instructions: '只生成待教师复核的中性草稿。',
      },
      removed_categories: ['姓名或称呼'],
      fingerprint: 'b'.repeat(64),
      expires_at: '2026-08-01T01:00:00Z',
      model_name: 'synthetic-fake-model',
      model_enabled: true,
      max_physical_requests: 1,
      estimated_cost: null,
    }
    vi.spyOn(studentCardApi, 'list').mockResolvedValue([studentCard()])
    const prepare = vi.spyOn(modelApprovalApi, 'prepare').mockResolvedValue(preview)
    vi.spyOn(modelApprovalApi, 'confirm').mockResolvedValue({
      preview_id: preview.preview_id,
      operation_id: 'model-follow-up-001',
      state: 'succeeded',
      physical_request_count: 1,
      error_category: null,
      draft_text: '{"kind":"follow_up"}',
      response_kind: 'follow_up',
      follow_up_questions: ['困难主要发生在哪一天？'],
      proposal: null,
      teacher_confirmation_required: true,
    })
    const host = mount(SensitiveStudentWorkspace, { sessionToken: 'synthetic-session' })
    await flushAll()
    const note = host.querySelector<HTMLTextAreaElement>('.student-capture textarea')!
    setValue(note, '合成学生甲最近安排有困难')
    await nextTick()
    button(host, '生成匿名发送预览').click()
    await flushAll()
    button(host, '确认发送这份内容').click()
    await flushAll()

    expect(host.querySelector('.follow-up')?.textContent).toContain('困难主要发生在哪一天？')
    const answer = host.querySelector<HTMLTextAreaElement>('.follow-up textarea')!
    setValue(answer, '主要是周三和周四')
    await nextTick()
    button(host, '生成下一轮匿名预览').click()
    await flushAll()

    expect(prepare).toHaveBeenCalledTimes(2)
    expect(prepare.mock.calls[1]?.[1]).toContain('AI 追问：')
    expect(prepare.mock.calls[1]?.[1]).toContain('困难主要发生在哪一天？')
    expect(prepare.mock.calls[1]?.[1]).toContain('教师回答：主要是周三和周四')
    expect(host.querySelector('.model-preview')).not.toBeNull()
  })

  it('reuses the model operation id after a lost response', async () => {
    const preview: ModelPreview = {
      preview_id: 'preview-retry',
      purpose: 'student_support_note',
      classification: 'restricted',
      exact_payload: {
        purpose: 'student_support_note',
        student_alias: '学生A',
        task_text: '学生A希望拆分近期任务',
        instructions: '只生成待教师复核的中性草稿。',
      },
      removed_categories: ['姓名或称呼'],
      fingerprint: 'd'.repeat(64),
      expires_at: '2026-08-01T01:00:00Z',
      model_name: 'synthetic-fake-model',
      model_enabled: true,
      max_physical_requests: 1,
      estimated_cost: null,
    }
    vi.spyOn(studentCardApi, 'list').mockResolvedValue([studentCard()])
    vi.spyOn(modelApprovalApi, 'prepare').mockResolvedValue(preview)
    const confirm = vi.spyOn(modelApprovalApi, 'confirm')
      .mockRejectedValueOnce(new Error('synthetic response lost'))
      .mockResolvedValue({
        preview_id: preview.preview_id,
        operation_id: 'model-retry-001',
        state: 'succeeded',
        physical_request_count: 1,
        error_category: null,
        draft_text: '{"kind":"follow_up"}',
        response_kind: 'follow_up',
        follow_up_questions: ['还需要补充哪一天？'],
        proposal: null,
        teacher_confirmation_required: true,
      })
    const host = mount(SensitiveStudentWorkspace, { sessionToken: 'synthetic-session' })
    await flushAll()
    const note = host.querySelector<HTMLTextAreaElement>('.student-capture textarea')!
    setValue(note, '合成学生甲希望拆分近期任务')
    await nextTick()
    button(host, '生成匿名发送预览').click()
    await flushAll()

    button(host, '确认发送这份内容').click()
    await flushAll()
    button(host, '确认发送这份内容').click()
    await flushAll()

    expect(confirm).toHaveBeenCalledTimes(2)
    expect(confirm.mock.calls[0]?.[2]).toBe(confirm.mock.calls[1]?.[2])
    expect(host.querySelector('.follow-up')).not.toBeNull()
  })

  it('requires teacher confirmation before persisting portrait and SOP', async () => {
    const preview: ModelPreview = {
      preview_id: 'preview-proposal',
      purpose: 'student_support_note',
      classification: 'restricted',
      exact_payload: {
        purpose: 'student_support_note',
        student_alias: '学生A',
        task_text: '学生A希望拆分近期任务',
        instructions: '只生成待教师复核的中性草稿。',
      },
      removed_categories: ['姓名或称呼'],
      fingerprint: 'c'.repeat(64),
      expires_at: '2026-08-01T01:00:00Z',
      model_name: 'synthetic-fake-model',
      model_enabled: true,
      max_physical_requests: 1,
      estimated_cost: null,
    }
    const savedEntry: StudentCardEntry = {
      entry_id: 'entry-001',
      subject_id: 'subject-001',
      teacher_quote: '合成学生甲希望拆分近期任务',
      portrait: {
        summary: '需要共同拆分近期任务',
        strengths: ['愿意主动说明'],
        needs: ['任务拆分'],
        open_questions: [],
      },
      sop: {
        title: '任务拆分流程',
        steps: ['确认任务', '拆分步骤', '约定复查'],
        review_date: '2026-08-10',
      },
      model_draft: '合成草稿',
      projection_state: 'applied',
      created_at: '2026-08-01T00:00:00Z',
    }
    vi.spyOn(studentCardApi, 'list')
      .mockResolvedValueOnce([studentCard()])
      .mockResolvedValueOnce([studentCard([savedEntry])])
    vi.spyOn(modelApprovalApi, 'prepare').mockResolvedValue(preview)
    vi.spyOn(modelApprovalApi, 'confirm').mockResolvedValue({
      preview_id: preview.preview_id,
      operation_id: 'model-proposal-001',
      state: 'succeeded',
      physical_request_count: 1,
      error_category: null,
      draft_text: '合成草稿',
      response_kind: 'proposal',
      follow_up_questions: [],
      proposal: {
        portrait: savedEntry.portrait,
        sop: savedEntry.sop,
      },
      teacher_confirmation_required: true,
    })
    const save = vi.spyOn(studentCardApi, 'confirm')
      .mockRejectedValueOnce(new Error('synthetic response lost'))
      .mockResolvedValue(savedEntry)
    const host = mount(SensitiveStudentWorkspace, { sessionToken: 'synthetic-session' })
    await flushAll()
    const note = host.querySelector<HTMLTextAreaElement>('.student-capture textarea')!
    setValue(note, savedEntry.teacher_quote)
    await nextTick()
    button(host, '生成匿名发送预览').click()
    await flushAll()
    expect(save).not.toHaveBeenCalled()
    button(host, '确认发送这份内容').click()
    await flushAll()

    expect(host.querySelector('.structure-confirmation')?.textContent).toContain('模型草稿不能直接落库')
    expect(save).not.toHaveBeenCalled()
    button(host, '教师确认并写入加密学生卡').click()
    await flushAll()
    expect(save).toHaveBeenCalledOnce()
    button(host, '教师确认并写入加密学生卡').click()
    await flushAll()

    expect(save).toHaveBeenCalledTimes(2)
    expect(save.mock.calls[0]?.[4]).toBe(save.mock.calls[1]?.[4])
    expect(host.querySelector('.student-card')?.textContent).toContain('需要共同拆分近期任务')
    expect(host.querySelector('.student-card')?.textContent).toContain('任务拆分流程')
    expect(host.textContent).toContain('匿名任务与 SOP 引用')
  })
})
