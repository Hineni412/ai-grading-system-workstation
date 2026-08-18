import { createApp, nextTick, type App, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { studentR1Api } from '../api/r1'
import { supportApi } from '../api/support'
import AcademicOverviewPanel from '../students/AcademicOverviewPanel.vue'
import StudentSurface from '../students/StudentSurface.vue'
import SupportOverviewPanel from '../students/SupportOverviewPanel.vue'

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

const subject = { subject_id:'subject-1234', display_name:'合成学生', source_student_id:'student:S001', class_label:'一班', support_record_count:2, support_plan_count:1, confirmed_entry_count:2, projection_state:'none', attention_pending_count:0, last_confirmed_at:'2026-08-01' }

describe('student surface keeps the selected student and shows class defaults', () => {
  it('keeps the current student in the URL when switching student sub-panels', async () => {
    vi.spyOn(studentR1Api, 'header').mockResolvedValue(subject)
    vi.spyOn(supportApi, 'listRecords').mockResolvedValue([])
    const navigate = vi.fn()
    const host = await mount(StudentSurface, { panel:'support', subjectId:'subject-1234', onNavigate:navigate })
    await vi.waitFor(() => expect(host.textContent).toContain('当前学生：'))

    clickByText(host, '学业证据')
    await nextTick()
    expect(navigate).toHaveBeenCalledWith('academic', 'subject-1234')

    clickByText(host, '学生目录')
    await nextTick()
    expect(navigate).toHaveBeenCalledWith('directory', 'subject-1234')
  })

  it('shows the class support overview instead of a reselect dead end', async () => {
    const overview = vi.spyOn(studentR1Api, 'supportOverview').mockResolvedValue({
      follow_ups:[
        { subject_id:'subject-due', display_name:'合成到期', class_label:'一班', next_review_at:'2026-08-20T00:00:00+00:00', last_record_at:'2026-08-01', active_record_count:3, due_soon:true },
        { subject_id:'subject-plain', display_name:'合成普通', class_label:'一班', next_review_at:null, last_record_at:'2026-08-05', active_record_count:1, due_soon:false },
      ],
      recent_records:[
        { record_id:'record-1', subject_id:'subject-plain', display_name:'合成普通', class_label:'一班', record_kind:'fact', observed_at:'2026-08-05', excerpt:'合成事实摘要' },
      ],
      has_records:true,
      review_soon_days:14,
    })
    const navigate = vi.fn()
    vi.spyOn(studentR1Api, 'header').mockResolvedValue({ ...subject, subject_id:'subject-due', display_name:'合成到期' })
    vi.spyOn(supportApi, 'listRecords').mockResolvedValue([])
    const host = await mount(StudentSurface, { panel:'support', subjectId:null, onNavigate:navigate })

    expect(overview).toHaveBeenCalledOnce()
    expect(host.textContent).toContain('全班支持总览')
    expect(host.textContent).toContain('近期要跟进')
    expect(host.textContent).toContain('合成到期')
    expect(host.textContent).toContain('复查日期 2026-08-20')
    expect(host.textContent).toContain('全班最近支持记录')
    expect(host.textContent).toContain('合成事实摘要')
    expect(host.textContent).not.toContain('请重新选择学生')

    clickByText(host, '合成到期')
    await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()
    expect(navigate).toHaveBeenCalledWith('support', 'subject-due')
  })

  it('shows first-run guidance when the class has no support records yet', async () => {
    vi.spyOn(studentR1Api, 'supportOverview').mockResolvedValue({ follow_ups:[], recent_records:[], has_records:false, review_soon_days:14 })
    const host = await mount(SupportOverviewPanel, {})
    expect(host.textContent).toContain('还没有支持记录')
    expect(host.textContent).toContain('从首页对话记录学生情况')
  })

  it('shows the class academic overview with sessions, stats and pending attention', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue({
      sessions:[{ session_id:'session-1', title:'合成期中', occurred_on:'2026-08-03', comparison_series:'学期大考', subject_names:['数学'], member_count:2, metadata_complete:true }],
      latest_session:{ session_id:'session-1', title:'合成期中', occurred_on:'2026-08-03', subjects:[{ subject_name:'数学', count:2, average:70, maximum:90, minimum:50, bands:[{ label:'90-100%', count:1 },{ label:'60%以下', count:1 }] }] },
      attention_students:[{ subject_id:'subject-1234', display_name:'合成学生', class_label:'一班', pending_count:1 }],
      attention_pending_count:1,
    })
    const select = vi.fn()
    const host = await mount(AcademicOverviewPanel, { onSelect:select })

    expect(host.textContent).toContain('全班学业概览')
    expect(host.textContent).toContain('合成期中')
    expect(host.textContent).toContain('登记 2 人次')
    expect(host.textContent).toContain('均分 70')
    expect(host.textContent).toContain('90-100% 1人')
    expect(host.textContent).toContain('未决关注卡')
    expect(host.textContent).toContain('1 张待决定')

    clickByText(host, '合成学生')
    await nextTick()
    expect(select).toHaveBeenCalledWith('subject-1234')
  })

  it('shows upload guidance when no assessment session exists yet', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue({ sessions:[], latest_session:null, attention_students:[], attention_pending_count:0 })
    const host = await mount(AcademicOverviewPanel, {})
    expect(host.textContent).toContain('还没有登记大考成绩')
    expect(host.textContent).toContain('上传入口')
    expect(host.textContent).toContain('当前没有等待教师决定的关注卡')
  })
})
