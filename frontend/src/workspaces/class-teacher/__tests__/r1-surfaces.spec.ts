import { createApp, nextTick, ref, type App, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

interface CapturedChartOption { series?: Array<{ data: unknown[] }> }
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
import SupportReviewPanel from '../students/SupportReviewPanel.vue'
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

    await vi.waitFor(() => expect(host.textContent).toContain('先把事情说清楚，再决定怎么处理'))
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
    const directory = vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items:[{ subject_id:'s1', display_name:'合成学生', source_student_id:'student:S001', class_label:'一班', support_record_count:3, support_plan_count:1, confirmed_entry_count:2, projection_state:'none', attention_pending_count:0, last_confirmed_at:'2026-08-01' }], cursor:null, total:1, page_size:20 })
    const rosterSource = vi.spyOn(studentR1Api, 'rosterSource').mockResolvedValue({ items:[{ source_key:'student:S001', student_code:'S001', display_name:'合成学生', class_label:'一班', subject_id:'s1', roster_state:'active', opaque_ref:'ref', student_revision:'r1' }], classes:['一班'], source_revision:'r1', total:1, cursor:null })
    const records = vi.spyOn(studentR1Api, 'records')
    const host = await mount(StudentDirectoryPanel, {})
    expect(host.textContent).toContain('点击卡片进入学生当前档案')
    expect(host.textContent).toContain('合成学生')
    expect(host.textContent).toContain('一班')
    expect(host.textContent).toContain('当前档案已建立')
    expect(directory).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ classLabel:'一班', state:'active' }))
    expect(directory.mock.calls[0]?.[0]).not.toHaveProperty('rosterState')
    expect(rosterSource).toHaveBeenCalledExactlyOnceWith({ classLabel:'一班', pageSize:100 })
    expect(records).not.toHaveBeenCalled()
  })

  it('recovers the saved homeroom inside the student page after a temporary roster read failure', async () => {
    vi.spyOn(intakeApi, 'homeroom').mockResolvedValue({ homeroom_class:'一班', revision:1, classes:['一班'], source_revision:'r'.repeat(64) })
    vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items:[{ subject_id:'s1', display_name:'合成学生', source_student_id:'student:S001', class_label:'一班', support_record_count:3, support_plan_count:1, confirmed_entry_count:2, projection_state:'none', attention_pending_count:0, last_confirmed_at:'2026-08-01' }], cursor:null, total:1, page_size:20 })
    vi.spyOn(studentR1Api, 'rosterSource')
      .mockRejectedValueOnce(new Error('temporary unavailable'))
      .mockResolvedValue({ items:[{ source_key:'student:S001', student_code:'S001', display_name:'合成学生', class_label:'一班', subject_id:'s1', roster_state:'active', opaque_ref:'ref', student_revision:'r1' }], classes:['一班'], source_revision:'r1', total:1, cursor:null })

    const host = await mount(StudentDirectoryPanel, {})

    expect(host.textContent).toContain('学生基本信息暂时无法读取')
    clickByText(host, '重新读取学生名单')
    await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick(); await nextTick()

    expect(host.textContent).toContain('合成学生')
    expect(host.textContent).not.toContain('学生基本信息暂时无法读取')
  })

  it('restores the selected student drawer when returning to the student page', async () => {
    const subject = { subject_id:'subject-1234', display_name:'合成学生', source_student_id:'student:S001', class_label:'一班', support_record_count:2, support_plan_count:1, confirmed_entry_count:2, projection_state:'applied', attention_pending_count:0, last_confirmed_at:'2026-08-01' }
    vi.spyOn(intakeApi, 'homeroom').mockResolvedValue({ homeroom_class:'一班', revision:1, classes:['一班'], source_revision:'r'.repeat(64) })
    vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items:[subject], cursor:null, total:1, page_size:20 })
    vi.spyOn(studentR1Api, 'rosterSource').mockResolvedValue({ items:[{ source_key:'student:S001', student_code:'S001', display_name:'合成学生', class_label:'一班', subject_id:'subject-1234', roster_state:'active', opaque_ref:'ref', student_revision:'r1' }], classes:['一班'], source_revision:'r1', total:1, cursor:null })
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
    vi.spyOn(studentR1Api, 'rosterSource').mockResolvedValue({ items:[{ source_key:'student:S002', student_code:'S002', display_name:'待整理学生', class_label:'一班', subject_id:null, roster_state:'available', opaque_ref:'ref', student_revision:'r1' }], classes:['一班'], source_revision:'r1', total:1, cursor:null })
    const createSubject = vi.spyOn(supportApi, 'createSubject').mockResolvedValue({ subject_id:'new-subject', revision:1, source_student_id:'student:S002', display_name:'待整理学生', class_label:'一班' })
    const select = vi.fn()
    const host = await mount(StudentDirectoryPanel, { onSelect:select })

    clickByText(host, '待整理学生'); await nextTick()
    await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick()

    expect(createSubject).toHaveBeenCalledExactlyOnceWith({
      source_student_id:'student:S002', display_name:'待整理学生', class_label:'一班',
    })
    expect(select).toHaveBeenCalledWith(expect.objectContaining({
      subject_id:'new-subject', display_name:'待整理学生', confirmed_entry_count:0,
    }))
  })

  it('opens one current student dossier without starting a new AI task', async () => {
    const subject = { subject_id:'subject-1234', display_name:'合成学生', source_student_id:'S001', class_label:'一班', support_record_count:3, support_plan_count:1, confirmed_entry_count:2, projection_state:'applied', attention_pending_count:0, last_confirmed_at:'2026-08-01' }
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

  it('continues a student-scoped AI conversation and applies the proposal in place', async () => {
    const subject = { subject_id:'subject-1234', revision:1, display_name:'合成学生', source_student_id:'S001', class_label:'一班', support_record_count:0, support_plan_count:0, confirmed_entry_count:1, projection_state:'applied', attention_pending_count:0, last_confirmed_at:'2026-08-01' }
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
    vi.spyOn(studentR1Api, 'studentCard').mockResolvedValueOnce(before).mockResolvedValueOnce(after)
    const collecting = { conversation_id:'profile-conversation', revision:1, state:'collecting', homeroom_class:'一班', focused_subject_id:'subject-1234', focused_subject_revision:'1', created_at:'2026-08-08', updated_at:'2026-08-08', turns:[], handoffs:[] }
    const ready = {
      ...collecting, revision:2, state:'handoff_ready',
      turns:[{ turn_id:'turn-profile', conversation_id:'profile-conversation', sequence:1, operation_id:'operation-profile', teacher_message:'最近开始主动分工', assistant_message:'已整理到同伴关系。', clarification_questions:['遇到意见不同时会怎样？'], task_id:'task-profile', task_state:'proposal_ready', created_at:'2026-08-08', updated_at:'2026-08-08' }],
      handoffs:[{ handoff_id:'handoff-profile', draft_id:'draft-profile', work_item_id:'work-profile', turn_id:'turn-profile', domain:'student_growth' as const, handling_mode:'record' as const, intent:'append', destination_key:'class_teacher.student.record', draft_revision:1, adoption_state:'pending' as const, missing_fields:[], subject_ref_count:1, auto_open_allowed:true }],
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
    vi.spyOn(intakeApi, 'conversation').mockResolvedValue({ ...ready, state:'teacher_confirmed' })

    const host = await mount(StudentOverviewPanel, { subject })
    const textarea = host.querySelector('textarea')!
    textarea.value = '最近开始主动分工'
    textarea.dispatchEvent(new Event('input', { bubbles:true }))
    host.querySelector('form.composer')!.dispatchEvent(new Event('submit', { bubbles:true, cancelable:true }))
    await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick(); await nextTick()

    expect(host.textContent).toContain('把新认识并入当前档案')
    expect(host.textContent).toContain('同伴与人际关系')
    clickByText(host, '应用到当前档案')
    await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick(); await nextTick()

    expect(adopt).toHaveBeenCalledWith(expect.objectContaining({ handoff_id:'handoff-profile' }), '1')
    expect(host.textContent).toContain('当前学生档案已经更新')
    expect(host.textContent).toContain('在小组任务中开始主动分工')
  })

  it('discards a proposed student profile update without changing the current profile', async () => {
    const subject = { subject_id:'subject-1234', revision:1, display_name:'合成学生', source_student_id:'S001', class_label:'一班', support_record_count:0, support_plan_count:0, confirmed_entry_count:1, projection_state:'applied', attention_pending_count:0, last_confirmed_at:'2026-08-01' }
    vi.spyOn(studentR1Api, 'studentCard').mockResolvedValue({
      subject, entries:[], existing_records:[], support_plans:[],
      current_profile:{ entry_id:'entry-1', revision:1, summary:'原有档案内容。', dimensions:[], open_questions:[], support_focus:[], updated_at:'2026-08-01' },
    })
    const collecting = { conversation_id:'profile-conversation', revision:1, state:'collecting', homeroom_class:'一班', focused_subject_id:'subject-1234', focused_subject_revision:'1', created_at:'2026-08-08', updated_at:'2026-08-08', turns:[], handoffs:[] }
    const ready = {
      ...collecting, revision:2, state:'handoff_ready', turns:[],
      handoffs:[{ handoff_id:'handoff-profile', draft_id:'draft-profile', work_item_id:'work-profile', turn_id:'turn-profile', domain:'student_growth' as const, handling_mode:'record' as const, intent:'append', destination_key:'class_teacher.student.record', draft_revision:1, adoption_state:'pending' as const, missing_fields:[], subject_ref_count:1, auto_open_allowed:true }],
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

  it('academic charts obey server segment status and preserve a real zero', async () => {
    vi.spyOn(studentR1Api, 'academic').mockResolvedValue({
      contract_version:'academic_analysis_v1', source_version:'a'.repeat(64), ruleset_version:'academic_ruleset_v1',
      sessions:[{ session_id:'x', title:'第一次', occurred_on:'2026-06-01', metadata_complete:true, evidence:[{ evidence_version_id:'e1', subject_name:'数学', result_state:'normal', score:0, occurred_on:'2026-06-01' }] },{ session_id:'y', title:'第二次', occurred_on:'2026-07-01', metadata_complete:true, evidence:[{ evidence_version_id:'e2', subject_name:'数学', result_state:'absent', score:null, occurred_on:'2026-07-01' }] }],
      series:[{ subject_name:'数学', points:[{evidence_version_id:'e1',subject_name:'数学',result_state:'normal',score:0,occurred_on:'2026-06-01',relative_position:0.2},{evidence_version_id:'e2',subject_name:'数学',result_state:'absent',score:null,occurred_on:'2026-07-01',relative_position:null}], segments:[{dimensions:{rank:{status:'not_comparable'},score:{status:'not_comparable'}}}] }],
      rank_change_pairs:[], relative_subject_signals:[], recent_changes:[], insufficient_reasons:['不可比'], attention_cards:[],
    })
    const host = await mount(AcademicAnalysisPanel, { subject:{subject_id:'s1',display_name:'合成学生',source_student_id:'S1',class_label:null,support_record_count:0,support_plan_count:0,confirmed_entry_count:0,projection_state:'none',attention_pending_count:0,last_confirmed_at:null} })
    expect(host.textContent).toContain('缺考不是 0 分')
    expect(host.textContent).toContain('考试系列')
    expect(host.textContent).toContain('只看可比证据')
    expect(host.textContent).toContain('缺考')
    expect(host.textContent).toContain('数学 · 正常 · 0')
    const secondPoint = chartOptions[0]!.series![0]!.data[1] as { value: unknown[] }
    expect(secondPoint.value[1]).toBeNull()
  })

  it('asks the server to apply comparability filters instead of inferring from normal results', async () => {
    const options = { series:['数学单元'], subjects:['数学'] }
    const academic = vi.spyOn(studentR1Api, 'academic')
      .mockResolvedValueOnce({
        contract_version:'academic_analysis_v1', source_version:'a'.repeat(64), ruleset_version:'academic_ruleset_v1',
        filter_options:options, applied_filters:{ time_range:'all', comparison_series:null, subject_name:null, comparable_only:false },
        sessions:[
          { session_id:'x', title:'第一次', occurred_on:'2026-06-01', comparison_series:'数学单元', metadata_complete:true, evidence:[{ evidence_version_id:'e1', session_id:'x', subject_name:'数学', result_state:'normal', score:60, occurred_on:'2026-06-01', relative_position:0.2, is_comparable:false }] },
          { session_id:'y', title:'第二次', occurred_on:'2026-07-01', comparison_series:'数学单元', metadata_complete:true, evidence:[{ evidence_version_id:'e2', session_id:'y', subject_name:'数学', result_state:'normal', score:70, occurred_on:'2026-07-01', relative_position:0.3, is_comparable:false }] },
        ],
        series:[{ subject_name:'数学', points:[{evidence_version_id:'e1',session_id:'x',subject_name:'数学',result_state:'normal',score:60,occurred_on:'2026-06-01',relative_position:0.2,is_comparable:false},{evidence_version_id:'e2',session_id:'y',subject_name:'数学',result_state:'normal',score:70,occurred_on:'2026-07-01',relative_position:0.3,is_comparable:false}], segments:[{overall_status:'reference_only',dimensions:{rank:{status:'reference_only'},score:{status:'reference_only'}}}] }],
        rank_change_pairs:[], relative_subject_signals:[{subject_name:'数学',signal:'inconsistent',eligible_session_count:3}], recent_changes:[], insufficient_reasons:[], attention_cards:[],
      })
      .mockResolvedValueOnce({
        contract_version:'academic_analysis_v1', source_version:'a'.repeat(64), ruleset_version:'academic_ruleset_v1',
        filter_options:options, applied_filters:{ time_range:'all', comparison_series:null, subject_name:null, comparable_only:true },
        sessions:[], series:[], rank_change_pairs:[], relative_subject_signals:[], recent_changes:[], insufficient_reasons:['筛选后没有可直接比较的证据'], attention_cards:[],
      })
    const host = await mount(AcademicAnalysisPanel, { subject:{subject_id:'s1',display_name:'合成学生',source_student_id:'S1',class_label:null,support_record_count:0,support_plan_count:0,confirmed_entry_count:0,projection_state:'none',attention_pending_count:0,last_confirmed_at:null} })

    const checkbox = host.querySelector<HTMLInputElement>('.filters input[type="checkbox"]')!
    checkbox.checked = true; checkbox.dispatchEvent(new Event('change',{bubbles:true}))
    await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick()

    expect(academic).toHaveBeenLastCalledWith('s1', expect.objectContaining({ comparableOnly:true }))
    expect(host.textContent).not.toContain('3 个合格场次')
    const filteredTimeline = chartOptions[chartOptions.length - 1]!.series![0]!.data
    expect(filteredTimeline).toEqual([])
  })


  it('saving a support record locally makes zero AI requests', async () => {
    vi.spyOn(supportApi, 'listRecords').mockResolvedValue([])
    const create = vi.spyOn(supportApi, 'createRecord').mockResolvedValue({ record_id:'r1',subject_id:'s1',record_kind:'observation',state:'active',current_revision:1,content:'合成事实',scene:'日常观察',source:'教师本人观察',counterexample:null,observed_at:'2026-08-01',review_at:null,expires_at:null })
    const subject = {subject_id:'s1',display_name:'合成学生',source_student_id:'S1',class_label:null,support_record_count:0,support_plan_count:0,confirmed_entry_count:0,projection_state:'none',attention_pending_count:0,last_confirmed_at:null}
    const host = await mount(SupportReviewPanel, { subject })
    const content = host.querySelector<HTMLTextAreaElement>('.editor textarea')!
    content.value='合成事实'; content.dispatchEvent(new Event('input',{bubbles:true}))
    clickByText(host, '确认保存记录'); await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick()
    expect(create).toHaveBeenCalledOnce()
    expect(host.textContent).toContain('系统没有调用 AI')
  })
})
