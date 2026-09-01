import { createApp, nextTick, ref, type App, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

interface CapturedChartOption { series?: Array<{ data: unknown[] }>; xAxis?: { data?: string[] }; yAxis?: { inverse?: boolean } }
interface DeviationOption {
  series: Array<{ name: string; data: Array<{ value: number; itemStyle: { color: string }; label: { formatter: string } }>; markLine?: { data: Array<{ xAxis: number }> } }>
  yAxis: { data: string[] }
  xAxis: { min: number; max: number }
}
const chartOptions: CapturedChartOption[] = []
vi.mock('echarts/core', () => ({
  use: vi.fn(),
  init: () => ({
    setOption: (option: CapturedChartOption) => chartOptions.push(option),
    on: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  }),
}))

import { intakeApi } from '../api/intake'
import { studentR1Api } from '../api/r1'
import { supportApi } from '../api/support'
import type { WorkNode, WorkNodeDetail } from '../api/work'
import WorkNodeInspector from '../ordinary/WorkNodeInspector.vue'
import AcademicAnalysisPanel from '../students/AcademicAnalysisPanel.vue'
import StudentDirectoryPanel from '../students/StudentDirectoryPanel.vue'
import StudentOverviewPanel from '../students/StudentOverviewPanel.vue'
import StudentSurface from '../students/StudentSurface.vue'
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
afterEach(() => { apps.splice(0).forEach((app) => app.unmount()); document.body.innerHTML=''; window.history.replaceState({}, '', '/'); vi.restoreAllMocks(); chartOptions.length=0 })

describe('B UI R1 surfaces', () => {
  it('opens the R7 conversation desk without consulting a PIN or vault gate', async () => {
    window.history.replaceState({}, '', '/class-teacher?surface=home')
    vi.spyOn(intakeApi, 'homeroom').mockResolvedValue({ homeroom_class:null, revision:0, classes:[], source_revision:'r'.repeat(64) })
    vi.spyOn(intakeApi, 'listConversations').mockResolvedValue([])
    vi.spyOn(intakeApi, 'startConversation').mockResolvedValue({
      conversation_id:'conversation-1234', revision:1, state:'collecting', homeroom_class:null,
      created_at:'2026-08-05T00:00:00Z', updated_at:'2026-08-05T00:00:00Z', turns:[], handoffs:[],
    })

    const host = await mount(ClassTeacherWorkbenchView, {})

    await vi.waitFor(() => expect(host.querySelector('.composer textarea')).toBeTruthy())
    expect(host.textContent).not.toContain('PIN')
    expect(host.textContent).not.toContain('解锁')
  })

  it('opens the existing workspace from a home card while keeping the conversation home intact', async () => {
    window.history.replaceState({}, '', '/class-teacher?surface=home')
    vi.spyOn(intakeApi, 'homeroom').mockResolvedValue({ homeroom_class:null, revision:0, classes:[], source_revision:'r'.repeat(64) })
    vi.spyOn(intakeApi, 'listConversations').mockResolvedValue([])
    vi.spyOn(intakeApi, 'startConversation').mockResolvedValue({
      conversation_id:'conversation-1234', revision:1, state:'collecting', homeroom_class:null,
      created_at:'2026-08-05T00:00:00Z', updated_at:'2026-08-05T00:00:00Z', turns:[], handoffs:[],
    })
    const host = await mount(ClassTeacherWorkbenchView, {})

    await vi.waitFor(() => expect(host.querySelector('form.composer')).toBeTruthy())
    clickByText(host, '成长记录')
    await nextTick()
    const query = new URLSearchParams(window.location.search)
    expect(query.get('surface')).toBe('students')
    expect(query.get('panel')).toBe('academic')
  })

  it('lets ordinary work leave and return to the calendar without deleting its history', async () => {
    const node: WorkNode = { node_id:'ordinary-1', kind:'task', classification:'ordinary', title:'准备开学材料', details:null, status:'pending', due_date:'2026-08-25', revision:1, created_at:'2026-08-01', updated_at:'2026-08-01', projection_type:null }
    const selected = ref<WorkNodeDetail | null>({ node, upstream:[], downstream:[], progress_events:[], collection_summary:null, pending_ai_branches:[], allowed_commands:['update_status'], projection_id:null })
    const command = vi.fn(async (_node: WorkNode, _name: string, fields: Record<string, unknown>) => {
      selected.value = { ...selected.value!, node: { ...selected.value!.node, status: fields.status as WorkNode['status'], revision: selected.value!.node.revision + 1 } }
      return {}
    })
    const module = { selected, command, clearSelection:vi.fn() }
    const host = await mount(WorkNodeInspector, { module })

    clickByText(host, '移出日历'); await nextTick()
    clickByText(host, '确认移出'); await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick()
    expect(command).toHaveBeenCalledWith(node, 'update_status', { status:'cancelled' })
    expect(host.textContent).toContain('历史记录仍然保留')
    clickByText(host, '恢复到日历'); await nextTick()
    expect(command).toHaveBeenLastCalledWith(expect.objectContaining({ status:'cancelled' }), 'update_status', { status:'pending' })
  })

  it('explains when an ordinary calendar change is rejected', async () => {
    const node: WorkNode = { node_id:'ordinary-2', kind:'task', classification:'ordinary', title:'准备材料', details:null, status:'pending', due_date:'2026-08-25', revision:1, created_at:'2026-08-01', updated_at:'2026-08-01', projection_type:null }
    const selected = ref<WorkNodeDetail | null>({ node, upstream:[], downstream:[], progress_events:[], collection_summary:null, pending_ai_branches:[], allowed_commands:['update_status'], projection_id:null })
    const host = await mount(WorkNodeInspector, { module:{ selected, command:vi.fn().mockRejectedValue(new Error('conflict')), clearSelection:vi.fn() } })

    clickByText(host, '移出日历'); await nextTick()
    clickByText(host, '确认移出'); await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick()
    expect(host.textContent).toContain('请刷新后再试')
  })

  it('directory calls only the lightweight directory interface before selection', async () => {
    vi.spyOn(intakeApi, 'homeroom').mockResolvedValue({ homeroom_class:'一班', revision:1, classes:['一班'], source_revision:'r'.repeat(64) })
    const directory = vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items:[{ subject_id:'s1', student_ref:'s1', display_name:'合成学生', source_student_id:'student:S001', class_label:'一班', support_record_count:3, support_plan_count:1, confirmed_entry_count:2, projection_state:'none', attention_pending_count:0, last_confirmed_at:'2026-08-01' }], cursor:null, total:1, page_size:20 })
    const rosterSource = vi.spyOn(studentR1Api, 'rosterSource').mockResolvedValue({ items:[{ source_key:'student:S001', student_code:'S001', display_name:'合成学生', class_label:'一班', subject_id:'s1', roster_state:'active', roster_ref:'ref', student_revision:'r1' },{ source_key:'student:S002', student_code:'S002', display_name:'未建档学生', class_label:'一班', subject_id:null, roster_state:'active', roster_ref:'ref2', student_revision:'r2' }], classes:['一班'], source_revision:'r1', total:2, cursor:null })
    const records = vi.spyOn(studentR1Api, 'records')
    const host = await mount(StudentDirectoryPanel, {})
    expect(host.textContent).toContain('点击卡片进入学生当前档案')
    expect(host.textContent).toContain('合成学生')
    expect(host.textContent).toContain('一班')
    expect(host.textContent).toContain('进行中方案 1')
    expect(host.textContent).toContain('还没有档案 · 点卡片开始建立')
    expect(host.textContent).not.toContain('当前档案已建立')
    expect(directory).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ classLabel:'一班', state:'active' }))
    expect(directory.mock.calls[0]?.[0]).not.toHaveProperty('rosterState')
    expect(rosterSource).toHaveBeenCalledExactlyOnceWith({ classLabel:'一班', pageSize:100 })
    expect(records).not.toHaveBeenCalled()
  })

  it('recovers the saved homeroom inside the student page after a temporary roster read failure', async () => {
    vi.spyOn(intakeApi, 'homeroom').mockResolvedValue({ homeroom_class:'一班', revision:1, classes:['一班'], source_revision:'r'.repeat(64) })
    vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items:[{ subject_id:'s1', student_ref:'s1', display_name:'合成学生', source_student_id:'student:S001', class_label:'一班', support_record_count:3, support_plan_count:1, confirmed_entry_count:2, projection_state:'none', attention_pending_count:0, last_confirmed_at:'2026-08-01' }], cursor:null, total:1, page_size:20 })
    vi.spyOn(studentR1Api, 'rosterSource')
      .mockRejectedValueOnce(new Error('temporary unavailable'))
      .mockResolvedValue({ items:[{ source_key:'student:S001', student_code:'S001', display_name:'合成学生', class_label:'一班', subject_id:'s1', roster_state:'active', roster_ref:'ref', student_revision:'r1' }], classes:['一班'], source_revision:'r1', total:1, cursor:null })

    const host = await mount(StudentDirectoryPanel, {})

    expect(host.textContent).toContain('学生基本信息暂时无法读取')
    clickByText(host, '重新读取学生名单')
    await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick(); await nextTick()

    expect(host.textContent).toContain('合成学生')
    expect(host.textContent).not.toContain('学生基本信息暂时无法读取')
  })

  it('restores the selected student drawer when returning to the student page', async () => {
    const subject = { subject_id:'subject-1234', student_ref:'subject-1234', display_name:'合成学生', source_student_id:'student:S001', class_label:'一班', support_record_count:2, support_plan_count:1, confirmed_entry_count:2, projection_state:'applied', attention_pending_count:0, last_confirmed_at:'2026-08-01' }
    vi.spyOn(intakeApi, 'homeroom').mockResolvedValue({ homeroom_class:'一班', revision:1, classes:['一班'], source_revision:'r'.repeat(64) })
    vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items:[subject], cursor:null, total:1, page_size:20 })
    vi.spyOn(studentR1Api, 'rosterSource').mockResolvedValue({ items:[{ source_key:'student:S001', student_code:'S001', display_name:'合成学生', class_label:'一班', subject_id:'subject-1234', roster_state:'active', roster_ref:'ref', student_revision:'r1' }], classes:['一班'], source_revision:'r1', total:1, cursor:null })
    const header = vi.spyOn(studentR1Api, 'header').mockResolvedValue(subject)
    vi.spyOn(studentR1Api, 'studentCard').mockResolvedValue({
      subject, entries:[], existing_records:[], support_plans:[],
      current_profile:{ entry_id:'entry-1', revision:1, summary:'能按计划完成任务。', dimensions:[], open_questions:[], support_focus:[], updated_at:'2026-08-01' },
    })

    const host = await mount(StudentSurface, { panel:'directory', subjectId:'subject-1234' })

    expect(header).toHaveBeenCalledExactlyOnceWith('subject-1234')
    expect(host.querySelector('[role="dialog"]')).toBeTruthy()
    expect(host.textContent).toContain('合成学生')
    expect(host.textContent).toContain('能按计划完成任务。')
  })

  it('opens an empty current dossier for a roster student without confirmed records', async () => {
    vi.spyOn(intakeApi, 'homeroom').mockResolvedValue({ homeroom_class:'一班', revision:1, classes:['一班'], source_revision:'r'.repeat(64) })
    vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items:[], cursor:null, total:0, page_size:100 })
    vi.spyOn(studentR1Api, 'rosterSource').mockResolvedValue({ items:[{ source_key:'student:S002', student_code:'S002', display_name:'待整理学生', class_label:'一班', subject_id:null, roster_state:'available', roster_ref:'ref', student_revision:'r1' }], classes:['一班'], source_revision:'r1', total:1, cursor:null })
    const createSubject = vi.spyOn(supportApi, 'createSubject').mockResolvedValue({ subject_id:'new-subject', student_ref:'new-subject', revision:1, source_student_id:'student:S002', display_name:'待整理学生', class_label:'一班' })
    const select = vi.fn()
    const host = await mount(StudentDirectoryPanel, { onSelect:select })

    clickByText(host, '待整理学生'); await nextTick()
    await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick()

    expect(createSubject).toHaveBeenCalledExactlyOnceWith({
      source_student_id:'student:S002', display_name:'待整理学生', class_label:'一班',
    })
    expect(select).toHaveBeenCalledWith(expect.objectContaining({
      subject_id:'new-subject', student_ref:'new-subject', display_name:'待整理学生', confirmed_entry_count:0,
    }))
  })

  it('opens one current student dossier without starting a new AI task', async () => {
    const subject = { subject_id:'subject-1234', student_ref:'subject-1234', display_name:'合成学生', source_student_id:'S001', class_label:'一班', support_record_count:3, support_plan_count:1, confirmed_entry_count:2, projection_state:'applied', attention_pending_count:0, last_confirmed_at:'2026-08-01' }
    const start = vi.spyOn(intakeApi, 'startStudentConversation')
    const card = vi.spyOn(studentR1Api, 'studentCard').mockResolvedValue({
      subject,
      entries:[{
        entry_id:'entry-1', teacher_confirmed_at:'2026-08-01',
        portrait:{ summary:'做事认真，数学步骤完整。', strengths:['能按计划完成任务'], needs:['需要巩固计算准确性'], open_questions:['近期状态是否稳定'] },
        sop:{ title:'两周支持计划', steps:['每周核对一次错题'], review_date:'2026-08-15' },
      }],
      current_profile:{
        entry_id:'entry-1', revision:2, summary:'做事认真，数学步骤完整。', updated_at:'2026-08-01',
        dimensions:[{ key:'learning_ability', label:'学习与能力', items:['能按计划完成任务'] }],
        open_questions:['近期状态是否稳定'],
        support_focus:[{ key:'math_support', title:'计算准确性支持', need:'巩固计算准确性', effective_methods:[], next_actions:['每周核对一次错题'] }],
      },
      existing_records:[], support_plans:[],
    })

    const close = vi.fn()
    const open = vi.fn()
    const host = await mount(StudentOverviewPanel, {
      subject, onClose:close, onOpen:open,
    })

    expect(card).toHaveBeenCalledExactlyOnceWith('subject-1234')
    expect(start).not.toHaveBeenCalled()
    expect(host.textContent).toContain('学生当前档案')
    expect(host.querySelector('[role="dialog"]')).toBeTruthy()
    expect(host.textContent).toContain('当前概览')
    expect(host.textContent).toContain('成长与支持')
    expect(host.textContent).toContain('学业证据')
    expect(host.textContent).toContain('告诉我最近又了解到了什么')
    expect(host.textContent).toContain('做事认真，数学步骤完整。')
    expect(host.textContent).toContain('计算准确性支持')

    clickByText(host, '成长与支持')
    await nextTick()
    expect(host.querySelector('[aria-label="成长与支持"]')?.getAttribute('style')).not.toContain('display: none')

    clickByText(host, '学业证据')
    await nextTick()
    clickByText(host, '打开完整学业证据')
    expect(open).toHaveBeenCalledWith('academic')

    window.dispatchEvent(new KeyboardEvent('keydown', { key:'Escape' }))
    expect(close).toHaveBeenCalledOnce()
  })

  it('offers an AI entry on the overview tab that jumps to the support desk', async () => {
    const subject = { subject_id:'subject-1234', student_ref:'subject-1234', display_name:'合成学生', source_student_id:'S001', class_label:'一班', support_record_count:3, support_plan_count:1, confirmed_entry_count:2, projection_state:'applied', attention_pending_count:0, last_confirmed_at:'2026-08-01' }
    vi.spyOn(studentR1Api, 'studentCard').mockResolvedValue({
      subject,
      entries:[],
      current_profile:{
        entry_id:'entry-1', revision:2, summary:'做事认真，数学步骤完整。', updated_at:'2026-08-01',
        dimensions:[{ key:'learning_ability', label:'学习与能力', items:['能按计划完成任务'] }],
        open_questions:[],
        support_focus:[],
      },
      existing_records:[], support_plans:[],
    })

    const host = await mount(StudentOverviewPanel, { subject })

    clickByText(host, '向 AI 补充这名学生的情况')
    await nextTick()
    expect(host.querySelector('[aria-label="成长与支持"]')?.getAttribute('style')).not.toContain('display: none')
  })

  it('surfaces a pending profile handoff at the top of an independently opened dossier', async () => {
    const subject = { subject_id:'subject-1234', student_ref:'subject-1234', display_name:'合成学生', source_student_id:'S001', class_label:'一班', support_record_count:3, support_plan_count:1, confirmed_entry_count:2, projection_state:'applied', attention_pending_count:0, last_confirmed_at:'2026-08-01' }
    vi.spyOn(studentR1Api, 'studentCard').mockResolvedValue({
      subject,
      entries:[],
      current_profile:{ entry_id:'entry-1', revision:2, summary:'做事认真。', updated_at:'2026-08-01', dimensions:[], open_questions:[], support_focus:[] },
      existing_records:[], support_plans:[],
    })
    vi.spyOn(intakeApi, 'pendingStudentHandoffs').mockResolvedValue([
      { handoff_id:'handoff-pending', adoption_state:'pending', updated_at:'2026-08-08', summary:'拟更新摘要', subject_id:'subject-1234', student_ref:'subject-1234' },
    ])
    vi.spyOn(intakeApi, 'handoff').mockResolvedValue({
      contract_version:'teacher_workspace_handoff.v1', handoff_id:'handoff-pending', work_item_id:'work-pending', conversation_id:'conversation-pending', turn_id:'turn-pending', draft_id:'draft-pending', draft_revision:1,
      domain:'student_growth', handling_mode:'record', intent:'append', destination_key:'class_teacher.student.record', adoption_id:'adoption-pending', adoption_state:'opened',
      content:{ summary:'拟更新摘要', profile_update:{ summary:'拟更新摘要', dimensions:[{ key:'learning_ability', label:'学习与能力', items:['开始主动检查步骤'] }], open_questions:[], support_focus:[] } },
      subject_refs:[{ kind:'student', id:'subject-1234', revision:'2' }], missing_fields:[], return_context:{ destination_key:'class_teacher.home', focus_ref:'work-pending' },
    })
    const adopt = vi.spyOn(intakeApi, 'adopt').mockResolvedValue({ saved:true })

    const host = await mount(StudentOverviewPanel, { subject })
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()

    expect(host.textContent).toContain('有一轮 AI 整理的档案更新待核对')

    clickByText(host, '应用更新')
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()

    expect(adopt).toHaveBeenCalledWith(expect.objectContaining({ handoff_id:'handoff-pending' }), '2')
    expect(host.textContent).not.toContain('有一轮 AI 整理的档案更新待核对')
    expect(host.textContent).toContain('当前学生档案已经更新')
  })

  it('discards a pending profile handoff from the dossier banner', async () => {
    const subject = { subject_id:'subject-1234', student_ref:'subject-1234', display_name:'合成学生', source_student_id:'S001', class_label:'一班', support_record_count:3, support_plan_count:1, confirmed_entry_count:2, projection_state:'applied', attention_pending_count:0, last_confirmed_at:'2026-08-01' }
    vi.spyOn(studentR1Api, 'studentCard').mockResolvedValue({
      subject,
      entries:[],
      current_profile:{ entry_id:'entry-1', revision:2, summary:'做事认真。', updated_at:'2026-08-01', dimensions:[], open_questions:[], support_focus:[] },
      existing_records:[], support_plans:[],
    })
    vi.spyOn(intakeApi, 'pendingStudentHandoffs').mockResolvedValue([
      { handoff_id:'handoff-pending', adoption_state:'opened', updated_at:'2026-08-08', summary:'拟更新摘要', subject_id:'subject-1234', student_ref:'subject-1234' },
    ])
    vi.spyOn(intakeApi, 'handoff').mockResolvedValue({
      contract_version:'teacher_workspace_handoff.v1', handoff_id:'handoff-pending', work_item_id:'work-pending', conversation_id:'conversation-pending', turn_id:'turn-pending', draft_id:'draft-pending', draft_revision:1,
      domain:'student_growth', handling_mode:'record', intent:'append', destination_key:'class_teacher.student.record', adoption_id:'adoption-pending', adoption_state:'opened',
      content:{ summary:'拟更新摘要', profile_update:{ summary:'拟更新摘要', dimensions:[], open_questions:[], support_focus:[] } },
      subject_refs:[{ kind:'student', id:'subject-1234', revision:'2' }], missing_fields:[], return_context:{ destination_key:'class_teacher.home', focus_ref:'work-pending' },
    })
    const discard = vi.spyOn(intakeApi, 'discard').mockResolvedValue({ discarded:true })

    const host = await mount(StudentOverviewPanel, { subject })
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()

    expect(host.textContent).toContain('有一轮 AI 整理的档案更新待核对')
    clickByText(host, '放弃')
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()

    expect(discard).toHaveBeenCalledExactlyOnceWith('handoff-pending')
    expect(host.textContent).not.toContain('有一轮 AI 整理的档案更新待核对')
  })

  it('keeps the banner when the handoff ref uses the legacy class|code identity', async () => {
    // 对话交接里的 ref 是「班级|学号」稳定标识，抽屉编号是 hex 档案编号：
    // 后端已按身份映射命中，前端不再用原始 ref id 与抽屉编号直接比较而误杀。
    const subject = { subject_id:'bc7e7d6e45ee40369efc7c94d4948e25', student_ref:'9|20250926', display_name:'合成学生', source_student_id:'S001', class_label:'九班', support_record_count:3, support_plan_count:1, confirmed_entry_count:2, projection_state:'applied', attention_pending_count:0, last_confirmed_at:'2026-08-01' }
    vi.spyOn(studentR1Api, 'studentCard').mockResolvedValue({
      subject,
      entries:[],
      current_profile:{ entry_id:'entry-1', revision:2, summary:'做事认真。', updated_at:'2026-08-01', dimensions:[], open_questions:[], support_focus:[] },
      existing_records:[], support_plans:[],
    })
    vi.spyOn(intakeApi, 'pendingStudentHandoffs').mockResolvedValue([
      { handoff_id:'handoff-mixed', adoption_state:'opened', updated_at:'2026-08-08', summary:'拟更新摘要', subject_id:'bc7e7d6e45ee40369efc7c94d4948e25', student_ref:'9|20250926' },
    ])
    vi.spyOn(intakeApi, 'handoff').mockResolvedValue({
      contract_version:'teacher_workspace_handoff.v1', handoff_id:'handoff-mixed', work_item_id:'work-mixed', conversation_id:'conversation-mixed', turn_id:'turn-mixed', draft_id:'draft-mixed', draft_revision:1,
      domain:'student_growth', handling_mode:'record', intent:'append', destination_key:'class_teacher.student.record', adoption_id:'adoption-mixed', adoption_state:'opened',
      content:{ summary:'拟更新摘要', profile_update:{ summary:'拟更新摘要', dimensions:[{ key:'learning_ability', label:'学习与能力', items:['开始主动检查步骤'] }], open_questions:[], support_focus:[] } },
      subject_refs:[{ kind:'student', id:'9|20250926', revision:'2' }], missing_fields:[], return_context:{ destination_key:'class_teacher.home', focus_ref:'work-mixed' },
    })
    const adopt = vi.spyOn(intakeApi, 'adopt').mockResolvedValue({ saved:true })

    const host = await mount(StudentOverviewPanel, { subject })
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()

    expect(host.textContent).toContain('有一轮 AI 整理的档案更新待核对')
    clickByText(host, '应用更新')
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()
    expect(adopt).toHaveBeenCalledWith(expect.objectContaining({ handoff_id:'handoff-mixed' }), '2')
  })

  it('auto-adopts a student-scoped profile update on settle and offers one-click revert', async () => {
    const subject = { subject_id:'subject-1234', student_ref:'subject-1234', revision:1, display_name:'合成学生', source_student_id:'S001', class_label:'一班', support_record_count:0, support_plan_count:0, confirmed_entry_count:1, projection_state:'applied', attention_pending_count:0, last_confirmed_at:'2026-08-01' }
    const before = {
      subject, entries:[], existing_records:[], support_plans:[],
      current_profile:{ entry_id:'entry-1', revision:1, summary:'愿意参与小组任务。', dimensions:[], open_questions:[], support_focus:[], updated_at:'2026-08-01' },
    }
    const after = {
      ...before,
      current_profile:{
        entry_id:'entry-1', revision:2, summary:'在小组任务中开始主动分工。', updated_at:'2026-08-08',
        dimensions:[{ key:'peer_relationships', label:'同伴与人际关系', items:['近期开始主动分工'] }],
        open_questions:['遇到意见不同时能否继续参与'], support_focus:[],
      },
    }
    vi.spyOn(studentR1Api, 'studentCard').mockResolvedValueOnce(before).mockResolvedValueOnce(after).mockResolvedValue(before)
    const collecting = { conversation_id:'profile-conversation', revision:1, state:'collecting', homeroom_class:'一班', focused_subject_id:'subject-1234', focused_subject_revision:'1', created_at:'2026-08-08', updated_at:'2026-08-08', turns:[], handoffs:[] }
    const pendingHandoff = { handoff_id:'handoff-profile', draft_id:'draft-profile', work_item_id:'work-profile', turn_id:'turn-profile', domain:'student_growth' as const, handling_mode:'record' as const, intent:'append', destination_key:'class_teacher.student.record', draft_revision:1, adoption_state:'pending' as const, missing_fields:[], subject_ref_count:1, subject_id:'subject-1234', auto_open_allowed:true }
    const ready = {
      ...collecting, revision:2, state:'handoff_ready',
      turns:[{ turn_id:'turn-profile', conversation_id:'profile-conversation', sequence:1, operation_id:'operation-profile', teacher_message:'最近开始主动分工', assistant_message:'已整理到同伴关系。', clarification_questions:['遇到意见不同时会怎样？'], task_id:'task-profile', task_state:'proposal_ready', created_at:'2026-08-08', updated_at:'2026-08-08' }],
      handoffs:[pendingHandoff],
    }
    vi.spyOn(intakeApi, 'startStudentConversation').mockResolvedValue(collecting)
    vi.spyOn(intakeApi, 'appendTurn').mockResolvedValue(ready)
    vi.spyOn(intakeApi, 'handoff').mockResolvedValue({
      contract_version:'teacher_workspace_handoff.v1', handoff_id:'handoff-profile', work_item_id:'work-profile', conversation_id:'profile-conversation', turn_id:'turn-profile', draft_id:'draft-profile', draft_revision:1,
      domain:'student_growth', handling_mode:'record', intent:'append', destination_key:'class_teacher.student.record', adoption_id:'adoption-profile', adoption_state:'opened',
      content:{ summary:'最近开始主动分工', profile_update:after.current_profile },
      subject_refs:[{ kind:'student', id:'subject-1234', revision:'1' }], missing_fields:[], return_context:{ destination_key:'class_teacher.home', focus_ref:'work-profile' },
    })
    const adopt = vi.spyOn(intakeApi, 'adopt').mockResolvedValue({ saved:true })
    const revert = vi.spyOn(intakeApi, 'revertProfile').mockResolvedValue({ adoption_state:'reverted', profile_state:'restored' })
    vi.spyOn(intakeApi, 'conversation')
      .mockResolvedValueOnce({ ...ready, state:'teacher_confirmed', handoffs:[{ ...pendingHandoff, adoption_state:'adopted' as const }] })
      .mockResolvedValue({ ...ready, state:'teacher_confirmed', handoffs:[{ ...pendingHandoff, adoption_state:'reverted' as const }] })

    const host = await mount(StudentOverviewPanel, { subject })
    const textarea = host.querySelector('textarea')!
    textarea.value = '最近开始主动分工'
    textarea.dispatchEvent(new Event('input', { bubbles:true }))
    host.querySelector('form.composer')!.dispatchEvent(new Event('submit', { bubbles:true, cancelable:true }))
    await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick(); await nextTick()

    expect(adopt).toHaveBeenCalledWith(expect.objectContaining({ handoff_id:'handoff-profile' }), '1')
    expect(host.textContent).toContain('原始记录仍保留')
    expect(host.textContent).toContain('已自动并入当前档案')
    expect(host.textContent).toContain('在小组任务中开始主动分工')

    clickByText(host, '撤回这次更新')
    await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick(); await nextTick()

    expect(revert).toHaveBeenCalledExactlyOnceWith('handoff-profile')
    expect(host.textContent).toContain('已撤回，档案回到本轮更新前')
  })

  it('discards a proposed student profile update without changing the current profile', async () => {
    const subject = { subject_id:'subject-1234', student_ref:'subject-1234', revision:1, display_name:'合成学生', source_student_id:'S001', class_label:'一班', support_record_count:0, support_plan_count:0, confirmed_entry_count:1, projection_state:'applied', attention_pending_count:0, last_confirmed_at:'2026-08-01' }
    vi.spyOn(studentR1Api, 'studentCard').mockResolvedValue({
      subject, entries:[], existing_records:[], support_plans:[],
      current_profile:{ entry_id:'entry-1', revision:1, summary:'原有档案内容。', dimensions:[], open_questions:[], support_focus:[], updated_at:'2026-08-01' },
    })
    const collecting = { conversation_id:'profile-conversation', revision:1, state:'collecting', homeroom_class:'一班', focused_subject_id:'subject-1234', focused_subject_revision:'1', created_at:'2026-08-08', updated_at:'2026-08-08', turns:[], handoffs:[] }
    const ready = {
      ...collecting, revision:2, state:'handoff_ready', turns:[],
      handoffs:[{ handoff_id:'handoff-profile', draft_id:'draft-profile', work_item_id:'work-profile', turn_id:'turn-profile', domain:'student_growth' as const, handling_mode:'record' as const, intent:'append', destination_key:'class_teacher.student.record', draft_revision:1, adoption_state:'pending' as const, missing_fields:[], subject_ref_count:1, subject_id:'subject-1234', auto_open_allowed:true }],
    }
    vi.spyOn(intakeApi, 'startStudentConversation').mockResolvedValue(collecting)
    vi.spyOn(intakeApi, 'appendTurn').mockResolvedValue(ready)
    vi.spyOn(intakeApi, 'handoff').mockResolvedValue({
      contract_version:'teacher_workspace_handoff.v1', handoff_id:'handoff-profile', work_item_id:'work-profile', conversation_id:'profile-conversation', turn_id:'turn-profile', draft_id:'draft-profile', draft_revision:1,
      domain:'student_growth', handling_mode:'record', intent:'append', destination_key:'class_teacher.student.record', adoption_id:'adoption-profile', adoption_state:'opened',
      content:{ summary:'拟更新内容', profile_update:{ summary:'拟更新内容', dimensions:[], open_questions:[], support_focus:[] } },
      subject_refs:[{ kind:'student', id:'subject-1234', revision:'1' }], missing_fields:[], return_context:{ destination_key:'class_teacher.home', focus_ref:'work-profile' },
    })
    const discard = vi.spyOn(intakeApi, 'discard').mockResolvedValue({ discarded:true })
    vi.spyOn(intakeApi, 'adopt').mockRejectedValue(new Error('synthetic auto-adopt unavailable'))
    vi.spyOn(intakeApi, 'conversation').mockResolvedValue({ ...ready, state:'collecting', handoffs:[{ ...ready.handoffs[0]!, adoption_state:'discarded' }] })

    const host = await mount(StudentOverviewPanel, { subject })
    const textarea = host.querySelector('textarea')!
    textarea.value = '这是一条新情况'
    textarea.dispatchEvent(new Event('input', { bubbles:true }))
    host.querySelector('form.composer')!.dispatchEvent(new Event('submit', { bubbles:true, cancelable:true }))
    await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick(); await nextTick()
    clickByText(host, '放弃本轮更新')
    await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick(); await nextTick()

    expect(discard).toHaveBeenCalledExactlyOnceWith('handoff-profile')
    expect(host.textContent).toContain('当前档案没有改变')
    expect(host.textContent).toContain('原有档案内容')
    expect(host.textContent).not.toContain('拟更新内容')
  })

  it('loads a home conversation proposal without a second composer', async () => {
    const subject = { subject_id:'subject-1234', student_ref:'subject-1234', revision:1, display_name:'合成学生', source_student_id:'S001', class_label:'一班', support_record_count:0, support_plan_count:0, confirmed_entry_count:1, projection_state:'applied', attention_pending_count:0, last_confirmed_at:'2026-08-01' }
    vi.spyOn(studentR1Api, 'studentCard').mockResolvedValue({
      subject, entries:[], existing_records:[], support_plans:[],
      current_profile:{ entry_id:'entry-1', revision:1, summary:'原有档案内容。', dimensions:[], open_questions:[], support_focus:[], updated_at:'2026-08-01' },
    })
    const ready = {
      conversation_id:'home-conversation', revision:2, state:'handoff_ready', homeroom_class:'一班', focused_subject_id:null, focused_subject_revision:null, created_at:'2026-08-08', updated_at:'2026-08-08',
      turns:[{ turn_id:'turn-home', conversation_id:'home-conversation', sequence:1, operation_id:'operation-home', teacher_message:'家庭情况', assistant_message:'已整理到当前档案。', clarification_questions:['在校表现？'], task_id:'task-home', task_state:'response_persisted', created_at:'2026-08-08', updated_at:'2026-08-08' }],
      handoffs:[{ handoff_id:'handoff-home', draft_id:'draft-home', work_item_id:'work-home', turn_id:'turn-home', domain:'student_support' as const, handling_mode:'record' as const, intent:'append', destination_key:'class_teacher.student.record', draft_revision:1, adoption_state:'pending' as const, missing_fields:[], subject_ref_count:1, subject_id:'subject-1234', auto_open_allowed:false }],
    }
    vi.spyOn(intakeApi, 'conversation').mockResolvedValue(ready)
    vi.spyOn(intakeApi, 'handoff').mockResolvedValue({
      contract_version:'teacher_workspace_handoff.v1', handoff_id:'handoff-home', work_item_id:'work-home', conversation_id:'home-conversation', turn_id:'turn-home', draft_id:'draft-home', draft_revision:1,
      domain:'student_support', handling_mode:'record', intent:'append', destination_key:'class_teacher.student.record', adoption_id:'adoption-home', adoption_state:'opened',
      content:{ summary:'拟更新家庭沟通', profile_update:{ summary:'家长情绪冲突需要关注在校情绪。', dimensions:[{ key:'family', label:'家庭沟通与身心状态', items:['家长情绪冲突'] }], open_questions:['在校表现？'], support_focus:[] } },
      subject_refs:[{ kind:'student', id:'subject-1234', revision:'1' }], missing_fields:[], return_context:{ destination_key:'class_teacher.home', focus_ref:'work-home' },
    })
    const start = vi.spyOn(intakeApi, 'startStudentConversation')
    const adopt = vi.spyOn(intakeApi, 'adopt').mockResolvedValue({ saved:true })
    vi.spyOn(studentR1Api, 'studentCard').mockResolvedValueOnce({
      subject, entries:[], existing_records:[], support_plans:[],
      current_profile:{ entry_id:'entry-1', revision:1, summary:'原有档案内容。', dimensions:[], open_questions:[], support_focus:[], updated_at:'2026-08-01' },
    }).mockResolvedValueOnce({
      subject, entries:[], existing_records:[], support_plans:[],
      current_profile:{ entry_id:'entry-1', revision:2, summary:'家长情绪冲突需要关注在校情绪。', dimensions:[{ key:'family', label:'家庭沟通与身心状态', items:['家长情绪冲突'] }], open_questions:['在校表现？'], support_focus:[], updated_at:'2026-08-08' },
    })

    const host = await mount(StudentOverviewPanel, { subject, conversationId:'home-conversation' })
    await vi.waitFor(() => expect(host.textContent).toContain('把新认识并入当前档案'))
    expect(host.querySelector('form.composer')).toBeNull()
    expect(start).not.toHaveBeenCalled()
    expect(host.textContent).not.toContain('告诉我最近又了解到了什么')
    clickByText(host, '应用到当前档案')
    await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick(); await nextTick()
    expect(adopt).toHaveBeenCalledWith(expect.objectContaining({ handoff_id:'handoff-home' }), '1')
  })

  it('opens the current student dossier from a home conversation card', async () => {
    window.history.replaceState({}, '', '/class-teacher?surface=home')
    const subject = { subject_id:'subject-1234', student_ref:'subject-1234', display_name:'合成学生', source_student_id:'S001', class_label:'一班', support_record_count:0, support_plan_count:0, confirmed_entry_count:1, projection_state:'applied', attention_pending_count:0, last_confirmed_at:'2026-08-01' }
    const ready = {
      conversation_id:'conversation-1234', revision:2, state:'handoff_ready', homeroom_class:'一班',
      created_at:'2026-08-08T00:00:00Z', updated_at:'2026-08-08T00:00:00Z',
      turns:[{ turn_id:'turn-profile', conversation_id:'conversation-1234', sequence:1, operation_id:'operation-profile', teacher_message:'家庭情况', assistant_message:'已整理到当前档案。', clarification_questions:['在校表现？'], task_id:'task-profile', task_state:'response_persisted', created_at:'2026-08-08T00:00:00Z', updated_at:'2026-08-08T00:00:00Z' }],
      handoffs:[{ handoff_id:'handoff-profile', draft_id:'draft-profile', work_item_id:'work-profile', turn_id:'turn-profile', domain:'student_support' as const, handling_mode:'record' as const, intent:'append', destination_key:'class_teacher.student.record', draft_revision:1, adoption_state:'pending' as const, missing_fields:[], subject_ref_count:1, subject_id:'subject-1234', auto_open_allowed:false }],
    }
    vi.spyOn(intakeApi, 'homeroom').mockResolvedValue({ homeroom_class:'一班', revision:1, classes:['一班'], source_revision:'r'.repeat(64) })
    vi.spyOn(intakeApi, 'listConversations').mockResolvedValue([])
    vi.spyOn(intakeApi, 'startConversation').mockResolvedValue(ready)
    vi.spyOn(intakeApi, 'conversation').mockResolvedValue(ready)
    vi.spyOn(intakeApi, 'handoff').mockResolvedValue({
      contract_version:'teacher_workspace_handoff.v1', handoff_id:'handoff-profile', work_item_id:'work-profile', conversation_id:'conversation-1234', turn_id:'turn-profile', draft_id:'draft-profile', draft_revision:1,
      domain:'student_support', handling_mode:'record', intent:'append', destination_key:'class_teacher.student.record', adoption_id:'adoption-profile', adoption_state:'opened',
      content:{ summary:'拟更新家庭沟通', profile_update:{ summary:'家长情绪冲突需要关注在校情绪。', dimensions:[{ key:'family', label:'家庭沟通与身心状态', items:['家长情绪冲突'] }], open_questions:['在校表现？'], support_focus:[] } },
      subject_refs:[{ kind:'student', id:'subject-1234', revision:'1' }], missing_fields:[], return_context:{ destination_key:'class_teacher.home', focus_ref:'work-profile' },
    })
    vi.spyOn(studentR1Api, 'header').mockResolvedValue(subject)
    vi.spyOn(studentR1Api, 'studentCard').mockResolvedValue({
      subject, entries:[], existing_records:[], support_plans:[],
      current_profile:{ entry_id:'entry-1', revision:1, summary:'原有档案。', dimensions:[], open_questions:[], support_focus:[], updated_at:'2026-08-01' },
    })

    const host = await mount(ClassTeacherWorkbenchView, {})
    await vi.waitFor(() => expect(host.textContent).toContain('学生个人档案'))
    clickByText(host, '学生个人档案')
    await vi.waitFor(() => expect(host.textContent).toContain('把新认识并入当前档案'))
    expect(host.querySelectorAll('form.composer').length).toBe(1)
    expect(host.textContent).toContain('家长情绪冲突需要关注在校情绪')
    expect(host.textContent).not.toContain('告诉我最近又了解到了什么')
    expect(host.textContent).not.toContain('登记草稿')
  })

  it('opens a not-yet-created student profile preview from a roster opaque ref', async () => {
    window.history.replaceState({}, '', '/class-teacher?surface=home')
    const opaqueRef = 'a'.repeat(64)
    const rosterRevision = 'b'.repeat(64)
    const headerSubject = { subject_id:opaqueRef, student_ref:opaqueRef, display_name:'合成新生', source_student_id:'S009', class_label:'一班', support_record_count:0, support_plan_count:0, confirmed_entry_count:0, projection_state:'none', attention_pending_count:0, last_confirmed_at:null, profile_state:'not_created' as const }
    const ready = {
      conversation_id:'conversation-opaque', revision:2, state:'handoff_ready', homeroom_class:'一班',
      created_at:'2026-08-08T00:00:00Z', updated_at:'2026-08-08T00:00:00Z',
      turns:[{ turn_id:'turn-opaque', conversation_id:'conversation-opaque', sequence:1, operation_id:'operation-opaque', teacher_message:'新转入学生情况', assistant_message:'已整理到当前档案。', clarification_questions:[], task_id:'task-opaque', task_state:'response_persisted', created_at:'2026-08-08T00:00:00Z', updated_at:'2026-08-08T00:00:00Z' }],
      handoffs:[{ handoff_id:'handoff-opaque', draft_id:'draft-opaque', work_item_id:'work-opaque', turn_id:'turn-opaque', domain:'student_support' as const, handling_mode:'record' as const, intent:'create', destination_key:'class_teacher.student.record', draft_revision:1, adoption_state:'pending' as const, missing_fields:[], subject_ref_count:1, subject_id:'subject-1234', auto_open_allowed:false }],
    }
    vi.spyOn(intakeApi, 'homeroom').mockResolvedValue({ homeroom_class:'一班', revision:1, classes:['一班'], source_revision:'r'.repeat(64) })
    vi.spyOn(intakeApi, 'listConversations').mockResolvedValue([])
    vi.spyOn(intakeApi, 'startConversation').mockResolvedValue(ready)
    vi.spyOn(intakeApi, 'conversation').mockResolvedValue(ready)
    vi.spyOn(intakeApi, 'handoff').mockResolvedValue({
      contract_version:'teacher_workspace_handoff.v1', handoff_id:'handoff-opaque', work_item_id:'work-opaque', conversation_id:'conversation-opaque', turn_id:'turn-opaque', draft_id:'draft-opaque', draft_revision:1,
      domain:'student_support', handling_mode:'record', intent:'create', destination_key:'class_teacher.student.record', adoption_id:'adoption-opaque', adoption_state:'opened',
      content:{ summary:'拟建立档案', profile_update:{ summary:'新转入学生，待建立档案。', dimensions:[{ key:'learning_ability', label:'学习与能力', items:['待了解'] }], open_questions:[], support_focus:[] } },
      subject_refs:[{ kind:'student', id:opaqueRef, revision:rosterRevision }], missing_fields:[], return_context:{ destination_key:'class_teacher.home', focus_ref:'work-opaque' },
    })
    vi.spyOn(studentR1Api, 'header').mockResolvedValue(headerSubject)
    const createdCard = { subject:{ ...headerSubject, profile_state:'created' as const }, entries:[], existing_records:[], support_plans:[], current_profile:{ entry_id:'entry-1', revision:1, summary:'新转入学生，待建立档案。', dimensions:[], open_questions:[], support_focus:[], updated_at:'2026-08-08' }, profile_state:'created' as const }
    vi.spyOn(studentR1Api, 'studentCard')
      .mockResolvedValueOnce({ subject:headerSubject, entries:[], existing_records:[], support_plans:[], current_profile:null, profile_state:'not_created' })
      .mockResolvedValue(createdCard)
    const adopt = vi.spyOn(intakeApi, 'adopt').mockResolvedValue({ saved:true })

    const host = await mount(ClassTeacherWorkbenchView, {})
    await vi.waitFor(() => expect(host.textContent).toContain('学生个人档案'))
    clickByText(host, '学生个人档案')
    await vi.waitFor(() => expect(host.textContent).toContain('档案尚未建立'))
    expect(host.textContent).toContain('确认保存后会自动创建档案')
    expect(host.textContent).toContain('把新认识并入当前档案')
    expect(host.textContent).not.toContain('学生档案暂时无法打开')

    clickByText(host, '应用到当前档案')
    await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick(); await nextTick()
    expect(adopt).toHaveBeenCalledWith(expect.objectContaining({ handoff_id:'handoff-opaque' }), rosterRevision)
    await vi.waitFor(() => expect(host.textContent).toContain('当前学生档案已经更新'))
  })

  it('renders the academic profile card, rank trend and subject heatmap', async () => {
    vi.spyOn(studentR1Api, 'academic').mockResolvedValue({
      contract_version:'academic_analysis_v1', source_version:'a'.repeat(64), ruleset_version:'academic_ruleset_v1',
      sessions:[
        { session_id:'a', title:'七上期中', occurred_on:'2025-11-01', short_label:'七上期中', grade:'七年级', term:'上学期', exam_type:'期中考试', metadata_complete:true, evidence:[
          { evidence_version_id:'t1', subject_name:'总分', measure_role:'total_score', result_state:'normal', score:70, occurred_on:'2025-11-01', rank:200, participant_count:400, relative_position:0.5 },
          { evidence_version_id:'c1', subject_name:'语文', measure_role:'subject_score', result_state:'normal', score:80, occurred_on:'2025-11-01', rank:100, participant_count:400, relative_position:0.75 },
          { evidence_version_id:'m1', subject_name:'数学', measure_role:'subject_score', result_state:'normal', score:75, occurred_on:'2025-11-01', rank:100, participant_count:400, relative_position:0.75 },
        ] },
        { session_id:'b', title:'七上期末', occurred_on:'2026-01-10', short_label:'七上期末', grade:'七年级', term:'上学期', exam_type:'期末考试', metadata_complete:true, evidence:[
          { evidence_version_id:'t2', subject_name:'总分', measure_role:'total_score', result_state:'normal', score:88, occurred_on:'2026-01-10', rank:40, participant_count:400, relative_position:0.9 },
          { evidence_version_id:'c2', subject_name:'语文', measure_role:'subject_score', result_state:'normal', score:92, occurred_on:'2026-01-10', rank:20, participant_count:400, relative_position:0.95 },
          { evidence_version_id:'m2', subject_name:'数学', measure_role:'subject_score', result_state:'absent', score:null, occurred_on:'2026-01-10', rank:null, participant_count:400, relative_position:null },
        ] },
      ],
      series:[], rank_change_pairs:[], relative_subject_signals:[], recent_changes:[], insufficient_reasons:[], attention_cards:[],
      filter_options:{ series:['class-regular'], subjects:['语文','数学'] },
      profile:{
        current:{ session_title:'七上期末', occurred_on:'2026-01-10', term_label:'七上', short_label:'七上期末', grade:'七年级', term:'上学期', score:88, rank:40, class_rank:3, participant_count:400, top_ratio:0.1, previous:{ session_title:'七上期中', occurred_on:'2025-11-01', term_label:'七上', short_label:'七上期中', rank:200, participant_count:400 }, rank_delta:160 },
        trend:{ label:'improving', step_deltas:[0.4], session_count:2 },
        stability:{ label:'volatile', swing_ratio:0.4, session_count:2 },
        skew:{ label:'skewed', strongest:[{ subject_name:'语文', rank:20, relative_position:0.95 }], weakest:[{ subject_name:'数学', rank:300, relative_position:0.25 }], gap_ratio:0.7 },
        subjects:[
          { subject_name:'语文', latest:{ occurred_on:'2026-01-10', term_label:'七上', short_label:'七上期末', grade_level:'A', rank:20, participant_count:400, relative_position:0.95, result_state:'normal' }, rank_delta:80, points:[{ occurred_on:'2025-11-01', term_label:'七上', short_label:'七上期中', rank:100, relative_position:0.75, result_state:'normal' },{ occurred_on:'2026-01-10', term_label:'七上', short_label:'七上期末', grade_level:'A', rank:20, relative_position:0.95, result_state:'normal' }], attention:false },
          { subject_name:'数学', latest:{ occurred_on:'2026-01-10', term_label:'七上', short_label:'七上期末', rank:300, participant_count:400, relative_position:0.25, result_state:'normal' }, rank_delta:-200, points:[{ occurred_on:'2025-11-01', term_label:'七上', short_label:'七上期中', rank:100, relative_position:0.75, result_state:'normal' },{ occurred_on:'2026-01-10', term_label:'七上', short_label:'七上期末', rank:300, relative_position:0.25, result_state:'normal' }], attention:true },
        ],
        total_trend:[
          { occurred_on:'2025-11-01', term_label:'七上', short_label:'七上期中', session_title:'七上期中', rank:200, participant_count:400, relative_position:0.5, result_state:'normal' },
          { occurred_on:'2026-01-10', term_label:'七上', short_label:'七上期末', session_title:'七上期末', rank:40, participant_count:400, relative_position:0.9, result_state:'normal' },
        ],
        basis:{ total_session_count:2, grade:'七年级' },
      },
    })
    vi.spyOn(studentR1Api, 'sessionClassResults').mockRejectedValue(new Error('合成无班级数据'))
    const host = await mount(AcademicAnalysisPanel, { subject:{subject_id:'s1', student_ref:'s1',display_name:'合成学生',source_student_id:'S1',class_label:null,support_record_count:0,support_plan_count:0,confirmed_entry_count:0,projection_state:'none',attention_pending_count:0,last_confirmed_at:null} })
    expect(host.textContent).toContain('合成学生 的大考表现')
    expect(host.textContent).toContain('第 40 名')
    expect(host.textContent).toContain('前 10%')
    expect(host.textContent).toContain('进步 160 名')
    expect(host.textContent).toContain('趋势：持续进步')
    expect(host.textContent).toContain('稳定性：波动大')
    expect(host.textContent).toContain('偏科：明显偏科')
    expect(host.textContent).toContain('历次大考学科校次')
    expect(host.textContent).toContain('缺考')
    // 进退步改为箭头徽章：↑ 进步 / ↓ 退步
    expect(host.textContent).toContain('↑80')
    expect(host.textContent).toContain('↓200')
    expect(host.textContent).toContain('需要关注')
    // 科目简评：数学前 75% 对比总分前 10% 判为劣势；语文、数学历次极差 ≥ 20 个百分点判为波动
    expect(host.textContent).toContain('科目简评')
    expect(host.textContent).toContain('劣势：数学')
    expect(host.textContent).toContain('波动：语文、数学')
    expect(host.textContent).not.toContain('优势：')
    // 学科明细卡两行式：第一行科目名+等级徽章+大号最近校次+箭头徽章，第二行迷你时间线
    const row1 = host.querySelector('.subject .subject__row1')
    expect(row1).toBeTruthy()
    expect(row1?.tagName).not.toBe('BUTTON')
    expect(row1?.querySelector('.mark')?.textContent).toContain('需要关注')
    const mathCard = [...host.querySelectorAll('.subject')].find((item) => item.textContent?.includes('数学'))!
    expect(mathCard.querySelector('.subject__rank-big')?.textContent).toContain('第 300 名')
    const chineseCard = [...host.querySelectorAll('.subject')].find((item) => item.textContent?.includes('语文'))!
    // 等级徽章取最新有效点的等级，配色与总览等级分布图一致
    const badge = chineseCard.querySelector('.grade-badge')
    expect(badge?.textContent).toBe('A')
    expect((badge as HTMLElement).style.background).toBe('rgb(46, 125, 91)')
    expect(mathCard.querySelector('.grade-badge')).toBeNull()
    expect(host.querySelectorAll('svg.spark').length).toBe(2)
    const timelines = [...host.querySelectorAll('svg.timeline')]
    expect(timelines.length).toBe(2)
    expect([...chineseCard.querySelectorAll('.tl-label')].map((item) => item.textContent)).toEqual(['七上期中·100', '七上期末·20'])
    expect(host.querySelector('.subject .detail')).toBeNull()
    const trend = chartOptions[0]!
    expect(trend.yAxis?.inverse).toBe(true)
    expect(trend.series![0]!.data).toEqual([200, 40])
    // 走势图 x 轴用「七上期中」式短标签，不带日期
    expect(trend.xAxis?.data).toEqual(['七上期中', '七上期末'])
    // 画像卡对比文案用短标签
    expect(host.textContent).toContain('较七上期中（第 200 名）')
    // 校次表行首用精确短标签徽章，每场次一行
    const heatRows = [...host.querySelectorAll('.heat tbody tr')]
    expect(heatRows).toHaveLength(2)
    expect(heatRows.map((row) => row.querySelector('.term')?.textContent)).toEqual(['七上期中', '七上期末'])
  })

  it('lists every session in the heat table even when they share the same occurred_on', async () => {
    // 回归：登记日期都等于上传当天时，校次表曾把多场合并成一行
    const session = (id: string, title: string, label: string, examType: string, term: string, rank: number) => ({
      session_id: id, title, occurred_on: '2026-08-26', short_label: label, grade: '七年级', term, exam_type: examType, metadata_complete: true,
      evidence: [{ evidence_version_id: `ev-${id}`, subject_name: '数学', measure_role: 'subject_score', result_state: 'normal', score: 80, occurred_on: '2026-08-26', rank, participant_count: 400, relative_position: 0.5 }],
    })
    vi.spyOn(studentR1Api, 'academic').mockResolvedValue({
      contract_version: 'academic_analysis_v1', source_version: 'a'.repeat(64), ruleset_version: 'academic_ruleset_v1',
      sessions: [
        session('s-mid7', '七上期中', '七上期中', '期中考试', '上学期', 100),
        session('s-fin7', '七上期末', '七上期末', '期末考试', '上学期', 40),
        session('s-mid8', '七下期中', '七下期中', '期中考试', '下学期', 60),
      ],
      series: [], rank_change_pairs: [], relative_subject_signals: [], recent_changes: [], insufficient_reasons: [], attention_cards: [],
    })
    const host = await mount(AcademicAnalysisPanel, { subject: { subject_id: 's1', display_name: '合成学生', source_student_id: 'S1', class_label: null, support_record_count: 0, support_plan_count: 0, confirmed_entry_count: 0, projection_state: 'none', attention_pending_count: 0, last_confirmed_at: null } })

    const rows = [...host.querySelectorAll('.heat tbody tr')]
    // 三场同日期仍然全部列出，按学期链排序（期中<期末<下学期）
    expect(rows).toHaveLength(3)
    expect(rows.map((row) => row.querySelector('.term')?.textContent)).toEqual(['七上期中', '七上期末', '七下期中'])
    expect(rows.map((row) => row.querySelector('td')?.textContent?.trim())).toEqual(['100', '40', '60'])
  })

  it('loads the full academic scope by default without filter controls', async () => {
    const baseAnalysis = {
      contract_version:'academic_analysis_v1', source_version:'a'.repeat(64), ruleset_version:'academic_ruleset_v1',
      sessions:[], series:[], rank_change_pairs:[], relative_subject_signals:[], recent_changes:[], insufficient_reasons:['场次不足'], attention_cards:[],
      filter_options:{ series:['class-regular'], subjects:['数学'] },
    }
    const academic = vi.spyOn(studentR1Api, 'academic').mockResolvedValue(baseAnalysis)
    const host = await mount(AcademicAnalysisPanel, { subject:{subject_id:'s1', student_ref:'s1',display_name:'合成学生',source_student_id:'S1',class_label:null,support_record_count:0,support_plan_count:0,confirmed_entry_count:0,projection_state:'none',attention_pending_count:0,last_confirmed_at:null} })
    expect(academic).toHaveBeenCalledWith('s1')
    expect(host.textContent).toContain('还没有带校次的总分成绩')
    expect(host.querySelector('.filters')).toBeNull()
    expect(host.textContent).not.toContain('academic_ruleset_v1')
  })

  const radarSubject = { subject_id:'s1', student_ref:'s1', display_name:'合成学生', source_student_id:'S1', class_label:null, support_record_count:0, support_plan_count:0, confirmed_entry_count:0, projection_state:'none', attention_pending_count:0, last_confirmed_at:null }
  function radarAnalysis(maxScore: number | null) {
    return {
      contract_version:'academic_analysis_v1', source_version:'a'.repeat(64), ruleset_version:'academic_ruleset_v1',
      sessions:[
        { session_id:'s-latest', title:'七上期末', occurred_on:'2026-01-10', grade:'七年级', term:'上学期', metadata_complete:true, evidence:[
          { evidence_version_id:'c2', subject_name:'语文', measure_role:'subject_score', result_state:'normal', score:90, occurred_on:'2026-01-10', rank:20, max_score:maxScore, relative_position:0.9 },
          { evidence_version_id:'m2', subject_name:'数学', measure_role:'subject_score', result_state:'normal', score:60, occurred_on:'2026-01-10', rank:100, max_score:null, relative_position:0.5 },
          { evidence_version_id:'t2', subject_name:'总分', measure_role:'total_score', result_state:'normal', score:150, occurred_on:'2026-01-10', rank:40, max_score:null },
        ] },
      ],
      series:[], rank_change_pairs:[], relative_subject_signals:[], recent_changes:[], insufficient_reasons:[], attention_cards:[],
      profile:{
        current:null,
        trend:{ label:'insufficient', step_deltas:[], session_count:1 },
        stability:{ label:'insufficient', swing_ratio:null, session_count:1 },
        skew:{ label:'insufficient', strongest:[], weakest:[], gap_ratio:null },
        subjects:[{ subject_name:'语文', latest:{ occurred_on:'2026-01-10', term_label:'七上', rank:20, result_state:'normal' }, rank_delta:null, points:[
          { occurred_on:'2025-11-01', term_label:'七上', rank:100, result_state:'normal' },
          { occurred_on:'2026-01-10', term_label:'七上', rank:20, result_state:'normal' },
        ], attention:false }],
        basis:{ total_session_count:1, grade:'七年级' },
      },
    }
  }

  it('renders the z-score radar and deviation bars when class stats exist', async () => {
    vi.spyOn(studentR1Api, 'academic').mockResolvedValue(radarAnalysis(120))
    vi.spyOn(studentR1Api, 'sessionClassResults').mockResolvedValue({
      session_id:'s-latest', title:'七上期末', occurred_on:'2026-01-10', participant_count:null,
      subjects:[
        { subject_name:'语文', max_score:120, stats:{ subject_name:'语文', count:40, average:60, stddev:15, maximum:110, minimum:20, average_rank:60, top50_count:5, top100_count:10, front30pct_count:12, bands:[] } },
        { subject_name:'数学', max_score:null, stats:{ subject_name:'数学', count:40, average:70, stddev:10, maximum:100, minimum:30, average_rank:80, top50_count:4, top100_count:9, front30pct_count:11, bands:[] } },
      ],
      students:[],
    })
    const host = await mount(AcademicAnalysisPanel, { subject:radarSubject })
    await vi.waitFor(() => expect(chartOptions.some((option) => (option as { radar?: unknown }).radar)).toBe(true))

    // 语文 Z = (90−60)/15 = 2；数学 Z = (60−70)/10 = −1；轴范围 ±2.5
    const radar = chartOptions.find((option) => (option as { radar?: unknown }).radar) as unknown as {
      radar: { indicator: Array<{ name: string; min: number; max: number }>; radius?: string }
      series: Array<{ data: Array<{ name: string; value: number[] }> }>
    }
    expect(radar.radar.indicator).toEqual([
      { name:'语文', min:-2.5, max:2.5 },
      { name:'数学', min:-2.5, max:2.5 },
    ])
    expect(radar.radar.radius).toBe('68%')
    expect(radar.series[0]!.data).toEqual([
      { name:'学生标准分', value:[2, -1] },
      // 0 基准圆环即班级平均
      { name:'班级平均（Z=0）', value:[0, 0], lineStyle:{ type:'dashed' }, symbol:'none' },
    ])
    expect(host.textContent).toContain('学科标准分雷达')
    // 偏离个人均线（校次口径）：百分位均值 0.7，语文 +20 个百分点（相对优势绿），数学 −20（相对劣势红）
    const deviation = chartOptions.find((option) => Array.isArray((option as { series?: unknown }).series)
      && ((option as { series: Array<{ name?: string }> }).series).some((item) => item.name === '偏离个人均线')) as DeviationOption | undefined
    expect(deviation).toBeTruthy()
    expect(deviation!.yAxis.data).toEqual(['语文', '数学'])
    expect(deviation!.series[0]!.data.map((item) => item.value)).toEqual([20, -20])
    expect(deviation!.series[0]!.data.map((item) => item.itemStyle.color)).toEqual(['#2e7d5b', '#b03a2e'])
    expect(deviation!.series[0]!.data.map((item) => item.label.formatter)).toEqual(['+20', '-20'])
    expect(deviation!.series[0]!.markLine?.data).toEqual([{ xAxis: 0 }])
    expect(host.textContent).toContain('偏离个人均线')
    // 该合成数据的学科卡小折线取 profile.subjects[].points，无相对位置的点按口径不渲染
    expect(host.querySelector('svg.spark')).toBeNull()
  })

  it('skips subjects without a class stddev and degrades to guidance when none qualify', async () => {
    vi.spyOn(studentR1Api, 'academic').mockResolvedValue(radarAnalysis(120))
    vi.spyOn(studentR1Api, 'sessionClassResults').mockResolvedValue({
      session_id:'s-latest', title:'七上期末', occurred_on:'2026-01-10', participant_count:null,
      subjects:[
        // 语文只有 1 个有效分数：stddev 为 null，不参与标准分
        { subject_name:'语文', max_score:120, stats:{ subject_name:'语文', count:1, average:90, stddev:null, maximum:90, minimum:90, average_rank:null, top50_count:0, top100_count:0, front30pct_count:null, bands:[] } },
        { subject_name:'数学', max_score:null, stats:{ subject_name:'数学', count:40, average:70, stddev:10, maximum:100, minimum:30, average_rank:80, top50_count:4, top100_count:9, front30pct_count:11, bands:[] } },
      ],
      students:[],
    })
    const host = await mount(AcademicAnalysisPanel, { subject:radarSubject })
    await vi.waitFor(() => expect(chartOptions.some((option) => (option as { radar?: unknown }).radar)).toBe(true))

    // 只有数学进入雷达；语文计入跳过提示
    const radar = chartOptions.find((option) => (option as { radar?: unknown }).radar) as unknown as {
      radar: { indicator: Array<{ name: string }> }
    }
    expect(radar.radar.indicator).toEqual([{ name:'数学', min:-2, max:2 }])
    expect(host.textContent).toContain('部分科目因缺班级标准差未显示')
    // 偏离图改用年级名次百分位后不再依赖班级统计：两科照常渲染
    expect(chartOptions.some((option) => Array.isArray((option as { series?: unknown }).series)
      && ((option as { series: Array<{ name?: string }> }).series).some((item) => item.name === '偏离个人均线'))).toBe(true)
  })

  it('degrades the radar to guidance when class results are unavailable', async () => {
    vi.spyOn(studentR1Api, 'academic').mockResolvedValue(radarAnalysis(null))
    vi.spyOn(studentR1Api, 'sessionClassResults').mockRejectedValue(new Error('合成失败'))
    const host = await mount(AcademicAnalysisPanel, { subject:radarSubject })
    await vi.waitFor(() => expect(host.textContent).toContain('暂无足够的班级统计数据生成标准分雷达'))
    expect(chartOptions.some((option) => (option as { radar?: unknown }).radar)).toBe(false)
  })
})
