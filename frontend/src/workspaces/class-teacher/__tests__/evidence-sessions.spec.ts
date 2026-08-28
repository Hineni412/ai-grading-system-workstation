import { createApp, nextTick, type App, type Component } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/errors'
import { studentR1Api, type AcademicSessionSummary } from '../api/r1'
import { supportApi } from '../api/support'
import EvidenceSessionsPanel from '../students/EvidenceSessionsPanel.vue'

const apps: App[] = []
async function mount(component: Component, props: Record<string, unknown> = {}) {
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
function clickExact(host: HTMLElement, text: string) {
  const button = [...host.querySelectorAll('button')].find((item) => item.textContent?.trim() === text)
  if (!button) throw new Error(`button not found (exact): ${text}`)
  button.dispatchEvent(new MouseEvent('click', { bubbles: true })); return button
}
async function settle() {
  await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()
}
function setInput(host: HTMLElement, labelText: string, value: string) {
  const label = [...host.querySelectorAll('label')].find((item) => item.textContent?.includes(labelText))
  const input = label?.querySelector('input')
  if (!input) throw new Error(`input not found: ${labelText}`)
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
}
function selectOption(host: HTMLElement, labelText: string, value: string) {
  const label = [...host.querySelectorAll('label')].find((item) => item.textContent?.includes(labelText))
  const select = label?.querySelector('select')
  if (!select) throw new Error(`select not found: ${labelText}`)
  select.value = value
  select.dispatchEvent(new Event('change', { bubbles: true }))
}
function apiError(status: number, message: string) {
  const kind = status === 409 ? 'conflict' : 'validation'
  return new ApiError({ kind, status, code: 'test_error', message, details: {}, requestId: 'r1', retryable: false })
}
afterEach(() => { apps.splice(0).forEach((app) => app.unmount()); document.body.innerHTML = ''; vi.restoreAllMocks() })
beforeEach(() => {
  // 抽屉打开时会读取 class-trend 汇总全局设置的科目并集；默认按空走势处理，单测可再覆盖
  vi.spyOn(studentR1Api, 'classTrend').mockResolvedValue({ sessions: [] })
})

const sessionA = {
  session_id: 'sess-1', title: '合成期中', occurred_on: '2026-04-20', comparison_series: 'class-regular',
  subject_names: ['数学', '语文'], member_count: 84, metadata_complete: true,
  grade: '七年级', term: '下学期', exam_type: '期中考试', academic_year: '2025-2026',
}
const sessionB = {
  session_id: 'sess-2', title: '合成月考', occurred_on: '2026-03-10', comparison_series: 'class-regular',
  subject_names: [], member_count: 42, metadata_complete: false,
  grade: null, term: null, exam_type: null, academic_year: null,
}
function overviewOf(sessions: AcademicSessionSummary[]) {
  return { sessions, latest_session: null, attention_students: [], attention_pending_count: 0 }
}
const deletePreviewPayload = {
  session_id: 'sess-1', title: '合成期中',
  counts: { results: 84, assessments: 2, imports: 1, attention_cards: 3 },
  preview_version: 'pv1', confirmation_phrase: '删除 合成期中',
}

describe('B UI 已登记成绩管理', () => {
  it('lists registered sessions with term, date, nature, subjects and result count', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue(overviewOf([sessionA, sessionB]))
    const host = await mount(EvidenceSessionsPanel, { open: true })
    await vi.waitFor(() => expect(host.textContent).toContain('合成期中'))

    expect(host.textContent).toContain('已登记成绩管理')
    expect(host.textContent).toContain('七下 · 2026-04-20 · 期中考试')
    expect(host.textContent).toContain('数学、语文 · 84 条成绩')
    expect(host.textContent).toContain('合成月考')
    expect(host.textContent).toContain('未指定 · 2026-03-10 · 性质未标注')
    expect(host.textContent).toContain('未标注学科 · 42 条成绩')
  })

  it('shows guidance when no session has been registered yet', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue(overviewOf([]))
    const host = await mount(EvidenceSessionsPanel, { open: true })
    await vi.waitFor(() => expect(host.textContent).toContain('还没有已登记的成绩场次'))
    expect(host.textContent).toContain('大考成绩表')
  })

  it('corrects a session and refreshes the list', async () => {
    const overview = vi.spyOn(studentR1Api, 'academicOverview')
      .mockResolvedValueOnce(overviewOf([sessionA]))
      .mockResolvedValueOnce(overviewOf([{ ...sessionA, title: '合成期中（更正）', grade: '八年级', term: '上学期', exam_type: '期末考试' }]))
    const update = vi.spyOn(supportApi, 'updateEvidenceSession').mockResolvedValue({ session_id: 'sess-1', updated: true })
    const changed = vi.fn()
    const host = await mount(EvidenceSessionsPanel, { open: true, onChanged: changed })
    await vi.waitFor(() => expect(host.textContent).toContain('合成期中'))

    clickByText(host, '更正'); await settle()
    const termSelect = [...host.querySelectorAll('label')].find((item) => item.textContent?.includes('学期类别'))!.querySelector('select')!
    expect(termSelect.value).toBe('七下')
    setInput(host, '考试名称', '合成期中（更正）')
    selectOption(host, '学期类别', '八上')
    selectOption(host, '考试性质', '期末考试')
    clickByText(host, '保存更正')
    await vi.waitFor(() => expect(update).toHaveBeenCalledExactlyOnceWith('sess-1', {
      title: '合成期中（更正）',
      grade: '八年级',
      term: '上学期',
      exam_type: '期末考试',
      occurred_on: '2026-04-20',
      academic_year: '2025-2026',
    }))
    await vi.waitFor(() => expect(host.textContent).toContain('场次信息已更正'))

    expect(overview).toHaveBeenCalledTimes(2)
    expect(changed).toHaveBeenCalledOnce()
    expect(host.textContent).toContain('八上 · 2026-04-20 · 期末考试')
  })

  it('explains when the correction conflicts with an existing session', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue(overviewOf([sessionA]))
    vi.spyOn(supportApi, 'updateEvidenceSession').mockRejectedValue(apiError(409, 'duplicate'))
    const host = await mount(EvidenceSessionsPanel, { open: true })
    await vi.waitFor(() => expect(host.textContent).toContain('合成期中'))

    clickByText(host, '更正'); await settle()
    clickByText(host, '保存更正')
    await vi.waitFor(() => expect(host.textContent).toContain('已存在相同的场次，可考虑删除本场次后重新上传'))
  })

  it('deletes a session after showing the impact preview and checking the phrase', async () => {
    const overview = vi.spyOn(studentR1Api, 'academicOverview')
      .mockResolvedValueOnce(overviewOf([sessionA]))
      .mockResolvedValueOnce(overviewOf([]))
    const preview = vi.spyOn(supportApi, 'previewDeleteEvidenceSession').mockResolvedValue(deletePreviewPayload)
    const remove = vi.spyOn(supportApi, 'deleteEvidenceSession').mockResolvedValue({ session_id: 'sess-1', deleted: true, counts: deletePreviewPayload.counts })
    const changed = vi.fn()
    const host = await mount(EvidenceSessionsPanel, { open: true, onChanged: changed })
    await vi.waitFor(() => expect(host.textContent).toContain('合成期中'))

    clickByText(host, '删除')
    await vi.waitFor(() => expect(host.textContent).toContain('84 条成绩、2 个科目、1 个批次、3 张关注卡'))
    expect(preview).toHaveBeenCalledExactlyOnceWith('sess-1')
    expect(host.textContent).toContain('删除 合成期中')

    setInput(host, '输入确认短语', '删除 合成期中')
    clickByText(host, '确认删除')
    await vi.waitFor(() => expect(remove).toHaveBeenCalledExactlyOnceWith('sess-1', 'pv1', '删除 合成期中'))
    await vi.waitFor(() => expect(host.textContent).toContain('已删除场次「合成期中」及其 84 条成绩'))

    expect(overview).toHaveBeenCalledTimes(2)
    expect(changed).toHaveBeenCalledOnce()
    expect(host.textContent).toContain('还没有已登记的成绩场次')
  })

  it('rejects the deletion when the confirmation phrase does not match', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue(overviewOf([sessionA]))
    vi.spyOn(supportApi, 'previewDeleteEvidenceSession').mockResolvedValue(deletePreviewPayload)
    const remove = vi.spyOn(supportApi, 'deleteEvidenceSession').mockRejectedValue(apiError(422, 'mismatch'))
    const host = await mount(EvidenceSessionsPanel, { open: true })
    await vi.waitFor(() => expect(host.textContent).toContain('合成期中'))

    clickByText(host, '删除')
    await vi.waitFor(() => expect(host.textContent).toContain('84 条成绩、2 个科目、1 个批次、3 张关注卡'))
    setInput(host, '输入确认短语', '随便写的')
    clickByText(host, '确认删除')
    await vi.waitFor(() => expect(host.textContent).toContain('确认短语不符，请按上方提示原样输入后再确认'))

    expect(remove).toHaveBeenCalledOnce()
  })

  it('renders nothing while closed and shows a dialog when open', async () => {
    const overview = vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue(overviewOf([sessionA]))
    const host = await mount(EvidenceSessionsPanel)
    expect(host.querySelector('[role="dialog"]')).toBeNull()
    expect(host.textContent).toBe('')
    expect(overview).not.toHaveBeenCalled()
  })

  it('emits close on Escape and on the backdrop', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue(overviewOf([sessionA]))
    const close = vi.fn()
    const host = await mount(EvidenceSessionsPanel, { open: true, onClose: close })
    await vi.waitFor(() => expect(host.querySelector('[role="dialog"]')).toBeTruthy())

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(close).toHaveBeenCalledOnce()

    host.querySelector<HTMLElement>('.sessions-backdrop')!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(close).toHaveBeenCalledTimes(2)
  })

  it('saves only the filled max scores and reports the effect', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue(overviewOf([sessionA]))
    vi.spyOn(studentR1Api, 'sessionClassResults').mockResolvedValue({
      session_id: 'sess-1', title: '合成期中', occurred_on: '2026-04-20', participant_count: 40,
      subjects: [
        { subject_name: '数学', max_score: null, stats: { subject_name: '数学', count: 2, average: 70, maximum: 90, minimum: 50, average_rank: 120, top50_count: 1, top100_count: 2, front30pct_count: 1, bands: [] } },
        { subject_name: '语文', max_score: 100, stats: { subject_name: '语文', count: 2, average: 80, maximum: 95, minimum: 60, average_rank: 100, top50_count: 1, top100_count: 1, front30pct_count: 1, bands: [] } },
      ],
      students: [],
    })
    const save = vi.spyOn(supportApi, 'updateEvidenceSessionMaxScores').mockResolvedValue({ session_id: 'sess-1', updated: true, subjects: ['数学'] })
    const changed = vi.fn()
    const host = await mount(EvidenceSessionsPanel, { open: true, onChanged: changed })
    await vi.waitFor(() => expect(host.textContent).toContain('合成期中'))

    clickExact(host, '满分')
    await vi.waitFor(() => expect(host.textContent).toContain('数学满分（当前 未定标，留空不改）'))
    expect(host.textContent).toContain('年级人数（当前 40，留空不改）')

    const saveButton = [...host.querySelectorAll('button')].find((item) => item.textContent?.includes('保存满分'))!
    expect(saveButton.disabled).toBe(true)

    setInput(host, '数学满分', '120')
    await nextTick()
    expect(saveButton.disabled).toBe(false)
    clickByText(host, '保存满分')
    // 年级人数留空：不发送该字段。
    await vi.waitFor(() => expect(save).toHaveBeenCalledExactlyOnceWith('sess-1', { 数学: 120 }))
    await vi.waitFor(() => expect(host.textContent).toContain('满分已保存，分数段与得分率统计即刻生效'))
    expect(changed).toHaveBeenCalledOnce()
  })

  it('saves the participant count together with max scores', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue(overviewOf([sessionA]))
    vi.spyOn(studentR1Api, 'sessionClassResults').mockResolvedValue({
      session_id: 'sess-1', title: '合成期中', occurred_on: '2026-04-20', participant_count: null,
      subjects: [
        { subject_name: '数学', max_score: null, stats: { subject_name: '数学', count: 2, average: 70, maximum: 90, minimum: 50, average_rank: 120, top50_count: 1, top100_count: 2, front30pct_count: 1, bands: [] } },
      ],
      students: [],
    })
    const save = vi.spyOn(supportApi, 'updateEvidenceSessionMaxScores').mockResolvedValue({ session_id: 'sess-1', updated: true, subjects: ['数学'], participant_count: 320 })
    const host = await mount(EvidenceSessionsPanel, { open: true })
    await vi.waitFor(() => expect(host.textContent).toContain('合成期中'))

    clickExact(host, '满分')
    await vi.waitFor(() => expect(host.textContent).toContain('年级人数（当前 未填，留空不改）'))

    setInput(host, '数学满分', '120')
    setInput(host, '年级人数', '320')
    clickByText(host, '保存满分')
    await vi.waitFor(() => expect(save).toHaveBeenCalledExactlyOnceWith('sess-1', { 数学: 120 }, 320))
    await vi.waitFor(() => expect(host.textContent).toContain('满分已保存，分数段与得分率统计即刻生效'))
  })

  it('saves the participant count alone when no max score is filled', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue(overviewOf([sessionA]))
    vi.spyOn(studentR1Api, 'sessionClassResults').mockResolvedValue({
      session_id: 'sess-1', title: '合成期中', occurred_on: '2026-04-20', participant_count: null,
      subjects: [
        { subject_name: '数学', max_score: 100, stats: { subject_name: '数学', count: 2, average: 70, maximum: 90, minimum: 50, average_rank: 120, top50_count: 1, top100_count: 2, front30pct_count: 1, bands: [] } },
      ],
      students: [],
    })
    const save = vi.spyOn(supportApi, 'updateEvidenceSessionMaxScores').mockResolvedValue({ session_id: 'sess-1', updated: true, subjects: [], participant_count: 320 })
    const host = await mount(EvidenceSessionsPanel, { open: true })
    await vi.waitFor(() => expect(host.textContent).toContain('合成期中'))

    clickExact(host, '满分')
    await vi.waitFor(() => expect(host.textContent).toContain('数学满分（当前 100，留空不改）'))
    const saveButton = [...host.querySelectorAll('button')].find((item) => item.textContent?.includes('保存满分'))!
    expect(saveButton.disabled).toBe(true)

    setInput(host, '年级人数', '320')
    await nextTick()
    expect(saveButton.disabled).toBe(false)
    clickByText(host, '保存满分')
    await vi.waitFor(() => expect(save).toHaveBeenCalledExactlyOnceWith('sess-1', {}, 320))
  })

  it('explains when the max score save is rejected', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue(overviewOf([sessionA]))
    vi.spyOn(studentR1Api, 'sessionClassResults').mockResolvedValue({
      session_id: 'sess-1', title: '合成期中', occurred_on: '2026-04-20', participant_count: null,
      subjects: [{ subject_name: '数学', max_score: null, stats: { subject_name: '数学', count: 2, average: 70, maximum: 90, minimum: 50, average_rank: 120, top50_count: 1, top100_count: 2, front30pct_count: 1, bands: [] } }],
      students: [],
    })
    vi.spyOn(supportApi, 'updateEvidenceSessionMaxScores').mockRejectedValue(apiError(422, 'invalid'))
    const host = await mount(EvidenceSessionsPanel, { open: true })
    await vi.waitFor(() => expect(host.textContent).toContain('合成期中'))

    clickExact(host, '满分')
    await vi.waitFor(() => expect(host.textContent).toContain('数学满分'))
    setInput(host, '数学满分', '-5')
    clickByText(host, '保存满分')
    await vi.waitFor(() => expect(host.textContent).toContain('满分与年级人数需要是正数，且科目必须属于这场次'))
  })

  it('saves only the filled global settings and reports the covered sessions', async () => {
    vi.spyOn(studentR1Api, 'academicOverview').mockResolvedValue(overviewOf([sessionA, sessionB]))
    vi.spyOn(studentR1Api, 'classTrend').mockResolvedValue({
      sessions: [
        { session_id: 'sess-1', occurred_on: '2026-04-20', title: '合成期中', term: '下学期', grade: '七年级', subjects: [
          { subject_name: '数学', average: 70, count: 2, max_score: null, average_rank: 120, top50_count: 1, top100_count: 2, front30pct_count: 1 },
          { subject_name: '英语', average: 80, count: 2, max_score: null, average_rank: 100, top50_count: 1, top100_count: 1, front30pct_count: 1 },
          { subject_name: '总分', average: 150, count: 2, max_score: null, average_rank: 90, top50_count: 1, top100_count: 2, front30pct_count: 1 },
        ] },
        { session_id: 'sess-2', occurred_on: '2026-03-10', title: '合成月考', term: null, grade: null, subjects: [
          { subject_name: '数学', average: 72, count: 2, max_score: null, average_rank: 110, top50_count: 1, top100_count: 2, front30pct_count: 1 },
        ] },
      ],
    })
    const save = vi.spyOn(supportApi, 'updateGlobalEvidenceSettings').mockResolvedValue({ sessions_updated: 2, subjects: { 数学: 120 }, participant_count: 320 })
    const changed = vi.fn()
    const host = await mount(EvidenceSessionsPanel, { open: true, onChanged: changed })
    await vi.waitFor(() => expect(host.textContent).toContain('合成期中'))

    clickByText(host, '全局设置')
    // 科目表为各场次 canonical 科目并集；总分满分自动求和，不在输入列表
    await vi.waitFor(() => expect(host.textContent).toContain('数学满分（留空不改）'))
    expect(host.textContent).toContain('英语满分（留空不改）')
    expect(host.textContent).not.toContain('总分满分（留空不改）')
    expect(host.textContent).toContain('保存后覆盖全部已登记场次的对应设置')
    expect(host.textContent).toContain('总分满分按各科满分之和自动计算')

    const saveButton = [...host.querySelectorAll('button')].find((item) => item.textContent?.includes('保存全局设置'))!
    expect(saveButton.disabled).toBe(true)

    // 只填写数学满分与年级人数：英语留空不提交
    setInput(host, '数学满分', '120')
    setInput(host, '年级人数（留空不改）', '320')
    await nextTick()
    expect(saveButton.disabled).toBe(false)
    clickByText(host, '保存全局设置')
    await vi.waitFor(() => expect(save).toHaveBeenCalledExactlyOnceWith({ 数学: 120 }, 320))
    await vi.waitFor(() => expect(host.textContent).toContain('全局设置已保存，已覆盖 2 场考试'))
    expect(changed).toHaveBeenCalledOnce()
  })
})
