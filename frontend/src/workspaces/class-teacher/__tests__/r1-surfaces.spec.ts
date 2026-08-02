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

import { studentR1Api } from '../api/r1'
import { supportApi } from '../api/support'
import { vaultApi } from '../api/vault'
import type { WorkNode, WorkNodeDetail, WorkSnapshot } from '../api/work'
import { workApi } from '../api/work'
import TodaySurface from '../ordinary/TodaySurface.vue'
import QuickWorkCapture from '../ordinary/QuickWorkCapture.vue'
import AcademicAnalysisPanel from '../students/AcademicAnalysisPanel.vue'
import SecurityPanel from '../students/SecurityPanel.vue'
import StudentDirectoryPanel from '../students/StudentDirectoryPanel.vue'
import SupportReviewPanel from '../students/SupportReviewPanel.vue'

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
afterEach(() => { apps.splice(0).forEach((app) => app.unmount()); document.body.innerHTML=''; vi.restoreAllMocks(); chartOptions.length=0 })

describe('B UI R1 surfaces', () => {
  it('opens a restricted work item only through the protected projection command', async () => {
    const node: WorkNode = { node_id:'n1', kind:'restricted_projection', classification:'restricted_projection', title:'学生事项待复查', details:null, status:'pending', due_date:'2026-08-02', revision:1, created_at:'2026-08-01', updated_at:'2026-08-01', projection_type:'attention_followup' }
    const snapshot = ref<WorkSnapshot | null>({ as_of:'2026-08-01', start_date:'2026-08-01', end_date:'2026-08-01', nodes:[node], edges:[], today:[node], overdue:[], waiting:[], review_due:[node], summary:{today:1,overdue:0,waiting:0,review_due:1}, view:'today', cursor:null, source_version:'v1' })
    const detail = ref<WorkNodeDetail | null>(null)
    const command = vi.fn().mockResolvedValue({ projection_id:'projection-001' })
    const module = { snapshot, selected:detail, loading:ref(false), error:ref(''), active:ref([node]), load:vi.fn(), inspect:vi.fn(async()=>{ detail.value={node,upstream:[],downstream:[],progress_events:[],collection_summary:null,pending_ai_branches:[],allowed_commands:['open_restricted_projection'],projection_id:'projection-001'} }), command, clearSelection:()=>{detail.value=null} }
    const host = await mount(TodaySurface, { module })
    expect(host.textContent).toContain('待复查')
    expect(host.textContent).toContain('等待中')
    clickByText(host, '学生事项待复查'); await nextTick()
    clickByText(host, '解锁并打开受保护事项'); await nextTick()
    expect(command).toHaveBeenCalledWith(node, 'open_restricted_projection', {})
  })

  it('directory calls only the lightweight directory interface before selection', async () => {
    const directory = vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items:[{ subject_id:'s1', display_name:'合成学生', source_student_id:'S001', class_label:'一班', support_record_count:3, support_plan_count:1, confirmed_entry_count:2, projection_state:'applied', attention_pending_count:0, last_confirmed_at:'2026-08-01' }], cursor:null, total:1, page_size:20 })
    const records = vi.spyOn(studentR1Api, 'records')
    const host = await mount(StudentDirectoryPanel, { token:'synthetic-token' })
    expect(host.textContent).toContain('本页不读取支持正文')
    expect(host.textContent).toContain('合成学生')
    expect(directory).toHaveBeenCalledOnce()
    expect(records).not.toHaveBeenCalled()
  })

  it('academic charts obey server segment status and preserve a real zero', async () => {
    vi.spyOn(studentR1Api, 'academic').mockResolvedValue({
      contract_version:'academic_analysis_v1', source_version:'a'.repeat(64), ruleset_version:'academic_ruleset_v1',
      sessions:[{ session_id:'x', title:'第一次', occurred_on:'2026-06-01', metadata_complete:true, evidence:[{ evidence_version_id:'e1', subject_name:'数学', result_state:'normal', score:0, occurred_on:'2026-06-01' }] },{ session_id:'y', title:'第二次', occurred_on:'2026-07-01', metadata_complete:true, evidence:[{ evidence_version_id:'e2', subject_name:'数学', result_state:'absent', score:null, occurred_on:'2026-07-01' }] }],
      series:[{ subject_name:'数学', points:[{evidence_version_id:'e1',subject_name:'数学',result_state:'normal',score:0,occurred_on:'2026-06-01',relative_position:0.2},{evidence_version_id:'e2',subject_name:'数学',result_state:'absent',score:null,occurred_on:'2026-07-01',relative_position:null}], segments:[{dimensions:{rank:{status:'not_comparable'},score:{status:'not_comparable'}}}] }],
      rank_change_pairs:[], relative_subject_signals:[], recent_changes:[], insufficient_reasons:['不可比'], attention_cards:[],
    })
    const host = await mount(AcademicAnalysisPanel, { token:'t', subject:{subject_id:'s1',display_name:'合成学生',source_student_id:'S1',class_label:null,support_record_count:0,support_plan_count:0,confirmed_entry_count:0,projection_state:'none',attention_pending_count:0,last_confirmed_at:null} })
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
    const host = await mount(AcademicAnalysisPanel, { token:'t', subject:{subject_id:'s1',display_name:'合成学生',source_student_id:'S1',class_label:null,support_record_count:0,support_plan_count:0,confirmed_entry_count:0,projection_state:'none',attention_pending_count:0,last_confirmed_at:null} })

    const checkbox = host.querySelector<HTMLInputElement>('.filters input[type="checkbox"]')!
    checkbox.checked = true; checkbox.dispatchEvent(new Event('change',{bubbles:true}))
    await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick()

    expect(academic).toHaveBeenLastCalledWith('t', 's1', expect.objectContaining({ comparableOnly:true }))
    expect(host.textContent).not.toContain('3 个合格场次')
    const filteredTimeline = chartOptions[chartOptions.length - 1]!.series![0]!.data
    expect(filteredTimeline).toEqual([])
  })

  it('PIN change immediately asks the shell to lock every sensitive view', async () => {
    vi.spyOn(vaultApi, 'listBackups').mockResolvedValue([])
    const change = vi.spyOn(vaultApi, 'changePin').mockResolvedValue({})
    const locked = vi.fn()
    const host = await mount(SecurityPanel, { token:'token', status:null, subject:null, onLocked:locked })
    const inputs = host.querySelectorAll<HTMLInputElement>('input')
    inputs[0]!.value='123456'; inputs[0]!.dispatchEvent(new Event('input',{bubbles:true}))
    inputs[1]!.value='654321'; inputs[1]!.dispatchEvent(new Event('input',{bubbles:true}))
    clickByText(host, '更改并锁定全部会话'); await new Promise((resolve)=>setTimeout(resolve,0))
    expect(change).toHaveBeenCalledWith('token','123456','654321')
    expect(locked).toHaveBeenCalledOnce()
  })

  it('requires the teacher to type both server phrases before deleting backups', async () => {
    vi.spyOn(vaultApi, 'listBackups').mockResolvedValue([])
    vi.spyOn(supportApi, 'previewSubjectDeletion').mockResolvedValue({
      subject_id:'s1', affected_backup_count:1, affected_backups:[], shared_object_count:0,
      shared_objects:[], delete_confirmation_phrase:'确认完整删除学生支持数据',
      backup_confirmation_phrase:'确认销毁受影响的班主任专用备份', preview_version:'v1',
    })
    const remove = vi.spyOn(supportApi, 'deleteSubject').mockResolvedValue({})
    vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items:[], cursor:null, total:0, page_size:1 })
    const subject = {subject_id:'s1',display_name:'合成学生',source_student_id:'S1',class_label:null,support_record_count:0,support_plan_count:0,confirmed_entry_count:0,projection_state:'none',attention_pending_count:0,last_confirmed_at:null}
    const host = await mount(SecurityPanel, { token:'token', status:null, subject })
    clickByText(host, '查看删除影响'); await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick()
    const phraseInputs = [...host.querySelectorAll<HTMLLabelElement>('.danger label')]
    const deleteInput = phraseInputs[0]!.querySelector<HTMLInputElement>('input')!
    const backupInput = phraseInputs[1]!.querySelector<HTMLInputElement>('input')!
    deleteInput.value='确认完整删除学生支持数据'; deleteInput.dispatchEvent(new Event('input',{bubbles:true})); await nextTick()
    const deleteButton = clickByText(host, '永久删除并清理投影') as HTMLButtonElement
    expect(deleteButton.disabled).toBe(true)
    expect(remove).not.toHaveBeenCalled()
    backupInput.value='确认销毁受影响的班主任专用备份'; backupInput.dispatchEvent(new Event('input',{bubbles:true})); await nextTick()
    expect(deleteButton.disabled).toBe(false)
    deleteButton.click(); await new Promise((resolve)=>setTimeout(resolve,0))
    expect(remove).toHaveBeenCalledWith(
      'token', 's1', expect.objectContaining({ preview_version:'v1' }),
      '确认销毁受影响的班主任专用备份',
      expect.any(String),
    )
  })

  it('states that the current vault is unchanged when restore verification fails', async () => {
    vi.spyOn(vaultApi, 'listBackups').mockResolvedValue([{ backup_id:'b1', file_name:'synthetic.ctbackup', created_at:'2026-08-01T00:00:00Z', size_bytes:100, status:'verified' }])
    vi.spyOn(vaultApi, 'previewRestore').mockRejectedValue(new Error('synthetic failure'))
    const host = await mount(SecurityPanel, { token:'token', status:null, subject:null })
    const select = host.querySelector<HTMLSelectElement>('.top section:nth-child(2) select')!
    select.value='synthetic.ctbackup'; select.dispatchEvent(new Event('change',{bubbles:true}))
    const secret = host.querySelector<HTMLInputElement>('.top section:nth-child(2) input[type="password"]')!
    secret.value='synthetic-secret'; secret.dispatchEvent(new Event('input',{bubbles:true}))
    clickByText(host, '验证并预览'); await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick()
    expect(host.textContent).toContain('当前库保持不变')
    expect(secret.value).toBe('')
  })

  it('locks sensitive views when restore may have succeeded but the response is lost', async () => {
    vi.spyOn(vaultApi, 'listBackups').mockResolvedValue([{ backup_id:'b1', file_name:'synthetic.ctbackup', created_at:'2026-08-01T00:00:00Z', size_bytes:100, status:'verified' }])
    vi.spyOn(vaultApi, 'previewRestore').mockResolvedValue({
      backup_id:'b1', source_instance_id:'synthetic', created_at:'2026-08-01T00:00:00Z', format_version:2,
      scope:'complete', preview_token:'preview-token', expires_in_seconds:60, requires_complete_replacement:true,
      source_relation:'same_instance', backup_schema_version:22, current_schema_version:22, migration_required:false,
      backup_scope_counts:{subjects:1}, current_scope_counts:{subjects:1}, mode:'complete_replace', will_replace_current:true,
      will_lock_after_confirm:true, confirmation_phrase:'确认完整替换当前班主任保险箱',
    })
    vi.spyOn(vaultApi, 'confirmRestore').mockRejectedValue(new Error('synthetic response loss'))
    const locked = vi.fn()
    const host = await mount(SecurityPanel, { token:'token', status:null, subject:null, onLocked:locked })
    const select = host.querySelector<HTMLSelectElement>('.top section:nth-child(2) select')!
    select.value='synthetic.ctbackup'; select.dispatchEvent(new Event('change',{bubbles:true}))
    const secret = host.querySelector<HTMLInputElement>('.top section:nth-child(2) input[type="password"]')!
    secret.value='synthetic-secret'; secret.dispatchEvent(new Event('input',{bubbles:true}))
    clickByText(host, '验证并预览'); await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick()
    const phrase = host.querySelector<HTMLInputElement>('.top section:nth-child(2) .warning input')!
    phrase.value='确认完整替换当前班主任保险箱'; phrase.dispatchEvent(new Event('input',{bubbles:true})); await nextTick()
    clickByText(host, '确认完整替换'); await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick()

    expect(locked).toHaveBeenCalledWith(expect.stringContaining('结果暂未确认'))
    expect(host.textContent).not.toContain('恢复失败；当前库保持不变')
  })

  it('reuses the same deletion operation after an unknown result', async () => {
    vi.spyOn(vaultApi, 'listBackups').mockResolvedValue([])
    vi.spyOn(supportApi, 'previewSubjectDeletion').mockResolvedValue({
      subject_id:'s1', affected_backup_count:0, affected_backups:[], shared_object_count:0,
      shared_objects:[], delete_confirmation_phrase:'确认完整删除学生支持数据',
      backup_confirmation_phrase:null, preview_version:'v1',
    })
    const remove = vi.spyOn(supportApi, 'deleteSubject')
      .mockRejectedValueOnce(new Error('synthetic response loss'))
      .mockResolvedValueOnce({ deleted:true })
    vi.spyOn(studentR1Api, 'directory').mockResolvedValue({ items:[], cursor:null, total:0, page_size:1 })
    const subjectDeleted = vi.fn()
    const subject = {subject_id:'s1',display_name:'合成学生',source_student_id:'S1',class_label:null,support_record_count:0,support_plan_count:0,confirmed_entry_count:0,projection_state:'none',attention_pending_count:0,last_confirmed_at:null}
    const host = await mount(SecurityPanel, { token:'token', status:null, subject, onSubjectDeleted:subjectDeleted })
    clickByText(host, '查看删除影响'); await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick()
    const phrase = host.querySelector<HTMLInputElement>('.danger .warning input')!
    phrase.value='确认完整删除学生支持数据'; phrase.dispatchEvent(new Event('input',{bubbles:true})); await nextTick()
    clickByText(host, '永久删除并清理投影'); await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick()
    expect(host.textContent).toContain('删除结果暂未确认')
    const firstOperationId = remove.mock.calls[0]![4]

    clickByText(host, '查询同一删除操作'); await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick()
    expect(remove.mock.calls[1]![4]).toBe(firstOperationId)
    expect(subjectDeleted).toHaveBeenCalledOnce()
  })

  it('quick capture stops at exact preview until the teacher confirms dispatch', async () => {
    vi.spyOn(workApi, 'previewPlan').mockResolvedValue({ preview_id:'p1', source_text:'普通班务', final_due_date:'2026-08-03', date_semantics:'date-only', exact_payload:{text:'普通班务'}, fingerprint:'f', expires_at:'2026-08-01', model_provider:'fake', model_endpoint:null, model_name:'fake', destination_fingerprint:'d', model_enabled:true, max_physical_requests:1, physical_request_count:0 })
    const invoke = vi.spyOn(workApi, 'invokePlan')
    const host = await mount(QuickWorkCapture, { module:{ load:vi.fn() } })
    const input = host.querySelector<HTMLInputElement>('input[placeholder]')!
    input.value='普通班务'; input.dispatchEvent(new Event('input',{bubbles:true}))
    clickByText(host, '生成匿名发送预览'); await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick()
    expect(host.textContent).toContain('发送前逐字核对')
    expect(invoke).not.toHaveBeenCalled()
  })

  it('saving a support record locally makes zero AI requests', async () => {
    vi.spyOn(supportApi, 'listRecords').mockResolvedValue([])
    const create = vi.spyOn(supportApi, 'createRecord').mockResolvedValue({ record_id:'r1',subject_id:'s1',record_kind:'observation',state:'active',current_revision:1,content:'合成事实',scene:'日常观察',source:'教师本人观察',counterexample:null,observed_at:'2026-08-01',review_at:null,expires_at:null })
    const prepare = vi.spyOn(studentR1Api, 'prepareReview')
    const subject = {subject_id:'s1',display_name:'合成学生',source_student_id:'S1',class_label:null,support_record_count:0,support_plan_count:0,confirmed_entry_count:0,projection_state:'none',attention_pending_count:0,last_confirmed_at:null}
    const host = await mount(SupportReviewPanel, { token:'t', subject })
    const content = host.querySelector<HTMLTextAreaElement>('.editor textarea')!
    content.value='合成事实'; content.dispatchEvent(new Event('input',{bubbles:true}))
    clickByText(host, '仅保存到本机'); await new Promise((resolve)=>setTimeout(resolve,0)); await nextTick()
    expect(create).toHaveBeenCalledOnce()
    expect(prepare).not.toHaveBeenCalled()
    expect(host.textContent).toContain('模型请求为 0')
  })
})
