import { createApp, nextTick, type App, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

const chartOptions: Array<Record<string, unknown>> = []
vi.mock('echarts/core', () => ({
  use: vi.fn(),
  init: () => ({
    setOption: (option: Record<string, unknown>) => chartOptions.push(option),
    on: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  }),
}))

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
afterEach(() => { apps.splice(0).forEach((app) => app.unmount()); document.body.innerHTML=''; window.history.replaceState({}, '', '/'); vi.restoreAllMocks(); chartOptions.length=0 })

const subject = { subject_id:'subject-1234', display_name:'合成学生', source_student_id:'student:S001', class_label:'一班', support_record_count:2, support_plan_count:1, confirmed_entry_count:2, projection_state:'none', attention_pending_count:0, last_confirmed_at:'2026-08-01' }

describe('student surface keeps the selected student and shows class defaults', () => {
  it('keeps the current student in the URL when switching student sub-panels', async () => {
    vi.spyOn(studentR1Api, 'header').mockResolvedValue(subject)
    vi.spyOn(supportApi, 'listRecords').mockResolvedValue([])
    vi.spyOn(supportApi, 'listSupportPlans').mockResolvedValue([])
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
    vi.spyOn(supportApi, 'listSupportPlans').mockResolvedValue([])
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

  it('shows the class academic overview with session picker, charts, heat table and pending attention', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue({
      sessions:[
        { session_id:'session-1', title:'合成期末', occurred_on:'2026-08-18', short_label:'七下期末', comparison_series:'学期大考', subject_names:['数学'], member_count:2, metadata_complete:true, grade:'七年级', term:'下学期' },
        { session_id:'session-0', title:'合成期中', occurred_on:'2026-04-20', short_label:'七下期中', comparison_series:'学期大考', subject_names:['数学'], member_count:2, metadata_complete:true, grade:'七年级', term:'下学期' },
      ],
      latest_session:{ session_id:'session-1', title:'合成期末', occurred_on:'2026-08-18', short_label:'七下期末', subjects:[] },
      attention_students:[{ subject_id:'subject-1234', display_name:'合成学生', class_label:'一班', pending_count:1 }],
      attention_pending_count:1,
    })
    const classResults = vi.spyOn(studentR1Api, 'sessionClassResults').mockResolvedValue({
      session_id:'session-1', title:'合成期末', occurred_on:'2026-08-18', participant_count:null,
      subjects:[
        { subject_name:'数学', max_score:120, stats:{ subject_name:'数学', count:2, average:70, maximum:90, minimum:50, average_rank:120, top50_count:1, top100_count:2, front30pct_count:1, bands:[{ label:'90-100%', count:1 },{ label:'60%以下', count:1 }] } },
        { subject_name:'语文', max_score:100, stats:{ subject_name:'语文', count:2, average:80, maximum:95, minimum:60, average_rank:null, top50_count:0, top100_count:0, front30pct_count:null, bands:[] } },
        { subject_name:'总分', max_score:null, stats:{ subject_name:'总分', count:2, average:150, maximum:180, minimum:120, average_rank:40, top50_count:1, top100_count:2, front30pct_count:1, bands:[] } },
      ],
      students:[{ subject_id:'subject-1234', display_name:'合成学生', class_label:'一班', total_rank:40, results:{ 数学:{ score:90, rank:100, class_rank:3, relative_position:0.8, max_score:120, result_state:'normal' } } }],
    })
    vi.spyOn(studentR1Api, 'classTrend').mockResolvedValue({
      sessions:[
        { session_id:'session-0', occurred_on:'2026-04-20', title:'合成期中', short_label:'七下期中', term:'下学期', grade:'七年级', subjects:[{ subject_name:'总分', average:230, count:2, max_score:null, average_rank:180, top50_count:1, top100_count:8, front30pct_count:20 }] },
        { session_id:'session-1', occurred_on:'2026-08-18', title:'合成期末', short_label:'七下期末', term:'下学期', grade:'七年级', subjects:[{ subject_name:'总分', average:250, count:2, max_score:null, average_rank:150, top50_count:2, top100_count:10, front30pct_count:22 }] },
      ],
    })
    const select = vi.fn()
    const host = await mount(AcademicOverviewPanel, { onSelect:select })
    await vi.waitFor(() => expect(host.textContent).toContain('各科班级平均校次'))

    expect(host.textContent).toContain('全班学业概览')
    // 场次选择器：短标签 + 日期 + 名称
    expect(host.textContent).toContain('七下期末 · 2026-08-18 · 合成期末')
    expect(host.textContent).toContain('历次位次走势')
    expect(host.textContent).toContain('分数段 · 合成期末')
    expect(host.textContent).toContain('学生 × 科目校次')
    expect(host.textContent).toContain('以下科目暂无校次数据：语文')
    expect(host.textContent).toContain('未决关注卡')
    expect(host.textContent).toContain('1 张待决定')
    expect(classResults).toHaveBeenCalledWith('session-1')
    const barOption = chartOptions.find((option) => Array.isArray(option.series) && (option.series as Array<{ name?: string }>).some((item) => item.name === '平均校次'))
    expect(barOption).toBeTruthy()
    // x 轴反向（名次越靠前条形越长）；总分不单独成条，改为竖直虚线参考线
    expect((barOption?.xAxis as { inverse?: boolean }).inverse).toBe(true)
    expect((barOption?.yAxis as { data: string[] }).data).toEqual(['数学', '语文'])
    interface BarSeriesOption {
      data: unknown[]
      markLine?: {
        lineStyle?: { type?: string }
        label?: { formatter?: string }
        data?: Array<{ xAxis: number }>
      }
    }
    const barSeries = (barOption?.series as BarSeriesOption[])[0]
    expect(barSeries?.data).toEqual([120, null])
    expect(barSeries?.markLine?.lineStyle?.type).toBe('dashed')
    expect(barSeries?.markLine?.label?.formatter).toBe('总分 40')
    expect(barSeries?.markLine?.data).toEqual([{ xAxis: 40 }])
    const barTooltip = (barOption?.tooltip as { formatter: (params: Array<{ dataIndex: number }>) => string })
    expect(barTooltip.formatter([{ dataIndex: 0 }])).toContain('均分 70')

    const lineOption = chartOptions.find((option) => Array.isArray(option.series) && (option.series as Array<{ name?: string }>).some((item) => item.name === '总分平均校次'))
    const lineSeries = lineOption?.series as Array<{ name: string; yAxisIndex?: number; data: unknown[]; lineStyle?: { width?: number }; label?: { show?: boolean } }>
    expect(lineSeries.map((item) => item.name)).toEqual(['总分平均校次', '前100名人数'])
    expect(lineSeries[0]?.data).toEqual([180, 150])
    expect(lineSeries[1]?.data).toEqual([8, 10])
    // 总分平均校次加粗并直接标数值；前100名人数弱化为细线
    expect(lineSeries[0]?.lineStyle?.width).toBe(3)
    expect(lineSeries[0]?.label?.show).toBe(true)
    expect(lineSeries[1]?.lineStyle?.width).toBe(1)
    const yAxis = lineOption?.yAxis as Array<{ name?: string; inverse?: boolean; min?: number; max?: number; minInterval?: number }>
    expect(yAxis).toHaveLength(2)
    expect(yAxis[0]?.inverse).toBe(true)
    expect(yAxis[1]?.name).toContain('前 100 名')
    // 纵轴按数据动态取范围：左轴 150~180 → 留边距后 140~190 且整数刻度；右轴从 0 起到 12
    expect(yAxis[0]?.min).toBe(140)
    expect(yAxis[0]?.max).toBe(190)
    expect(yAxis[0]?.minInterval).toBe(1)
    expect(yAxis[1]?.min).toBe(0)
    expect(yAxis[1]?.max).toBe(12)
    const lineTooltip = (lineOption?.tooltip as { formatter: (params: Array<{ dataIndex: number }>) => string })
    expect(lineTooltip.formatter([{ dataIndex: 1 }])).toContain('总分均分：250')
    // 走势图 x 轴用短标签，图表内不带日期
    expect((lineOption?.xAxis as { data: string[] }).data).toEqual(['七下期中', '七下期末'])
    expect(lineTooltip.formatter([{ dataIndex: 1 }])).not.toContain('2026-08-18')

    // 分数段：每科一条 100% 堆叠条，段序固定，含每段人数与占比的 tooltip
    const bandsOption = chartOptions.find((option) => Array.isArray(option.series) && (option.series as Array<{ stack?: string }>).some((item) => item.stack === 'bands'))
    expect(bandsOption).toBeTruthy()
    expect((bandsOption?.yAxis as { data: string[] }).data).toEqual(['数学'])
    const bandsSeries = bandsOption?.series as Array<{ name: string; data: number[] }>
    expect(bandsSeries.map((item) => item.name)).toEqual(['90-100%', '80-89%', '70-79%', '60-69%', '60%以下', '未定标'])
    expect(bandsSeries.find((item) => item.name === '90-100%')?.data).toEqual([50])
    expect(bandsSeries.find((item) => item.name === '60%以下')?.data).toEqual([50])
    const bandsTooltip = (bandsOption?.tooltip as { formatter: (params: Array<{ seriesName: string; dataIndex: number }>) => string })
    const bandsTip = bandsTooltip.formatter([{ seriesName: '90-100%', dataIndex: 0 }])
    expect(bandsTip).toContain('满分 120 · 均分 70 · 最高 90 · 最低 50 · 2 人')
    expect(bandsTip).toContain('90-100%：1 人（50%）')

    const picker = host.querySelector<HTMLSelectElement>('select[aria-label="查看场次"]')!
    picker.value = 'session-0'
    picker.dispatchEvent(new Event('change', { bubbles: true }))
    await vi.waitFor(() => expect(classResults).toHaveBeenCalledWith('session-0'))

    const row = [...host.querySelectorAll('tbody tr')].find((item) => item.textContent?.includes('合成学生'))!
    row.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(select).toHaveBeenCalledWith('subject-1234')
  })

  it('shows the grade distribution chart when the session collected grade levels', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue({
      sessions:[{ session_id:'session-1', title:'合成期末', occurred_on:'2026-08-18', comparison_series:'学期大考', subject_names:['数学'], member_count:2, metadata_complete:true, grade:'七年级', term:'下学期' }],
      latest_session:{ session_id:'session-1', title:'合成期末', occurred_on:'2026-08-18', subjects:[] },
      attention_students:[],
      attention_pending_count:0,
    })
    vi.spyOn(studentR1Api, 'sessionClassResults').mockResolvedValue({
      session_id:'session-1', title:'合成期末', occurred_on:'2026-08-18', participant_count:null,
      subjects:[
        { subject_name:'数学', max_score:120, stats:{ subject_name:'数学', count:2, average:70, maximum:90, minimum:50, average_rank:120, top50_count:1, top100_count:2, front30pct_count:1, bands:[{ label:'90-100%', count:1 }], grade_counts:[{ label:'A+', count:1 },{ label:'A', count:0 },{ label:'B+', count:0 },{ label:'B', count:1 },{ label:'C+', count:0 },{ label:'C', count:0 },{ label:'其他', count:0 }] } },
        { subject_name:'语文', max_score:100, stats:{ subject_name:'语文', count:2, average:80, maximum:95, minimum:60, average_rank:null, top50_count:0, top100_count:0, front30pct_count:null, bands:[], grade_counts:null } },
        { subject_name:'总分', max_score:220, stats:{ subject_name:'总分', count:2, average:150, maximum:180, minimum:120, average_rank:40, top50_count:1, top100_count:2, front30pct_count:1, bands:[], grade_counts:[{ label:'A+', count:0 },{ label:'A', count:1 },{ label:'B+', count:0 },{ label:'B', count:1 },{ label:'C+', count:0 },{ label:'C', count:0 },{ label:'其他', count:0 }] } },
      ],
      students:[],
    })
    vi.spyOn(studentR1Api, 'classTrend').mockResolvedValue({ sessions:[] })
    const host = await mount(AcademicOverviewPanel, {})
    await vi.waitFor(() => expect(host.textContent).toContain('等级分布 · 合成期末'))

    // 无等级数据的科目不进入等级图；总分排在类目轴末尾即最上方
    const gradeOption = chartOptions.find((option) => Array.isArray(option.series) && (option.series as Array<{ stack?: string }>).some((item) => item.stack === 'bands'))
    expect(gradeOption).toBeTruthy()
    expect((gradeOption?.yAxis as { data: string[] }).data).toEqual(['数学', '总分'])
    const gradeSeries = gradeOption?.series as Array<{ name: string; data: number[] }>
    // 段序固定 A+→C；本场「其他」计数全 0，不进入图例
    expect(gradeSeries.map((item) => item.name)).toEqual(['A+', 'A', 'B+', 'B', 'C+', 'C'])
    expect(gradeSeries.find((item) => item.name === 'A+')?.data).toEqual([50, 0])
    expect(gradeSeries.find((item) => item.name === 'A')?.data).toEqual([0, 50])
    const gradeTooltip = (gradeOption?.tooltip as { formatter: (params: Array<{ seriesName: string; dataIndex: number }>) => string })
    const gradeTip = gradeTooltip.formatter([{ seriesName: 'A+', dataIndex: 0 }])
    expect(gradeTip).toContain('满分 120 · 均分 70 · 最高 90 · 最低 50 · 2 人')
    expect(gradeTip).toContain('A+：1 人（50%）')
    expect(host.textContent).toContain('每科按等级')
    expect(host.textContent).not.toContain('本场次未采集等级')
  })

  it('keeps the 其他 segment only when some subject actually has 其他 grades', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue({
      sessions:[{ session_id:'session-1', title:'合成期末', occurred_on:'2026-08-18', comparison_series:'学期大考', subject_names:['数学'], member_count:2, metadata_complete:true }],
      latest_session:{ session_id:'session-1', title:'合成期末', occurred_on:'2026-08-18', subjects:[] },
      attention_students:[],
      attention_pending_count:0,
    })
    vi.spyOn(studentR1Api, 'sessionClassResults').mockResolvedValue({
      session_id:'session-1', title:'合成期末', occurred_on:'2026-08-18', participant_count:null,
      subjects:[
        { subject_name:'数学', max_score:120, stats:{ subject_name:'数学', count:2, average:70, maximum:90, minimum:50, average_rank:120, top50_count:1, top100_count:2, front30pct_count:1, bands:[], grade_counts:[{ label:'A+', count:1 },{ label:'A', count:0 },{ label:'B+', count:0 },{ label:'B', count:0 },{ label:'C+', count:0 },{ label:'C', count:0 },{ label:'其他', count:1 }] } },
      ],
      students:[],
    })
    vi.spyOn(studentR1Api, 'classTrend').mockResolvedValue({ sessions:[] })
    const host = await mount(AcademicOverviewPanel, {})
    await vi.waitFor(() => expect(host.textContent).toContain('等级分布 · 合成期末'))

    const gradeOption = chartOptions.find((option) => Array.isArray(option.series) && (option.series as Array<{ stack?: string }>).some((item) => item.stack === 'bands'))
    const gradeSeries = gradeOption?.series as Array<{ name: string; data: number[] }>
    // 数学有 1 人落在「其他」：段保留且排在固定段序末尾
    expect(gradeSeries.map((item) => item.name)).toEqual(['A+', 'A', 'B+', 'B', 'C+', 'C', '其他'])
    expect(gradeSeries.find((item) => item.name === '其他')?.data).toEqual([50])
  })

  it('falls back to score-rate bands with guidance when no grade levels were collected', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue({
      sessions:[{ session_id:'session-1', title:'合成期末', occurred_on:'2026-08-18', comparison_series:'学期大考', subject_names:['数学'], member_count:2, metadata_complete:true }],
      latest_session:{ session_id:'session-1', title:'合成期末', occurred_on:'2026-08-18', subjects:[] },
      attention_students:[],
      attention_pending_count:0,
    })
    vi.spyOn(studentR1Api, 'sessionClassResults').mockResolvedValue({
      session_id:'session-1', title:'合成期末', occurred_on:'2026-08-18', participant_count:null,
      subjects:[{ subject_name:'数学', max_score:120, stats:{ subject_name:'数学', count:2, average:70, maximum:90, minimum:50, average_rank:120, top50_count:1, top100_count:2, front30pct_count:1, bands:[{ label:'90-100%', count:1 }], grade_counts:null } }],
      students:[],
    })
    vi.spyOn(studentR1Api, 'classTrend').mockResolvedValue({ sessions:[] })
    const host = await mount(AcademicOverviewPanel, {})
    await vi.waitFor(() => expect(host.textContent).toContain('分数段 · 合成期末'))

    expect(host.textContent).toContain('本场次未采集等级，重新上传含等级列的表格后可按等级显示')
    const bandsOption = chartOptions.find((option) => Array.isArray(option.series) && (option.series as Array<{ stack?: string }>).some((item) => item.stack === 'bands'))
    const bandsSeries = bandsOption?.series as Array<{ name: string }>
    expect(bandsSeries.map((item) => item.name)).toEqual(['90-100%', '80-89%', '70-79%', '60-69%', '60%以下', '未定标'])
  })

  it('shows the bands guidance when no subject has a max score yet', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue({
      sessions:[{ session_id:'session-1', title:'合成期中', occurred_on:'2026-08-03', comparison_series:'学期大考', subject_names:['数学'], member_count:2, metadata_complete:true }],
      latest_session:{ session_id:'session-1', title:'合成期中', occurred_on:'2026-08-03', subjects:[] },
      attention_students:[],
      attention_pending_count:0,
    })
    vi.spyOn(studentR1Api, 'sessionClassResults').mockResolvedValue({
      session_id:'session-1', title:'合成期中', occurred_on:'2026-08-03', participant_count:null,
      subjects:[{ subject_name:'数学', max_score:null, stats:{ subject_name:'数学', count:2, average:70, maximum:90, minimum:50, average_rank:null, top50_count:0, top100_count:0, front30pct_count:null, bands:[] } }],
      students:[],
    })
    vi.spyOn(studentR1Api, 'classTrend').mockResolvedValue({ sessions:[] })
    const host = await mount(AcademicOverviewPanel, {})
    await vi.waitFor(() => expect(host.textContent).toContain('在成绩管理中补填满分后显示分数段'))
  })

  it('shows upload guidance when no assessment session exists yet', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue({ sessions:[], latest_session:null, attention_students:[], attention_pending_count:0 })
    vi.spyOn(studentR1Api, 'classTrend').mockResolvedValue({ sessions:[] })
    const host = await mount(AcademicOverviewPanel, {})
    expect(host.textContent).toContain('还没有登记大考成绩')
    expect(host.textContent).toContain('上传入口')
    expect(host.textContent).toContain('当前没有等待教师决定的关注卡')
  })
})
