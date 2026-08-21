import { createApp, nextTick, type App, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/errors'
import { supportApi } from '../api/support'
import EvidenceUploadPanel from '../students/EvidenceUploadPanel.vue'

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
async function settle() {
  await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0)); await nextTick()
}
function chooseFile(host: HTMLElement, file: File) {
  const input = host.querySelector<HTMLInputElement>('input[type="file"]')
  if (!input) throw new Error('file input not found')
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  input.dispatchEvent(new Event('change', { bubbles: true }))
}
function apiError(message: string) {
  return new ApiError({ kind: 'validation', status: 422, code: 'assessment_invalid', message, details: {}, requestId: 'r1', retryable: false })
}
function selectOption(host: HTMLElement, labelText: string, value: string) {
  const label = [...host.querySelectorAll('label')].find((item) => item.textContent?.includes(labelText))
  const select = label?.querySelector('select')
  if (!select) throw new Error(`select not found: ${labelText}`)
  select.value = value
  select.dispatchEvent(new Event('change', { bubbles: true }))
}
function setInput(host: HTMLElement, labelText: string, value: string) {
  const label = [...host.querySelectorAll('label')].find((item) => item.textContent?.includes(labelText))
  const input = label?.querySelector('input')
  if (!input) throw new Error(`input not found: ${labelText}`)
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
}
const rosterItem = { subject_id: 's1', revision: 1, source_student_id: 'student:S001', display_name: '合成学生', class_label: '七9班' }
const singlePreviewPayload = {
  file_name: '期中考试.csv', sheet_names: ['CSV'], selected_sheet: 'CSV',
  headers: ['姓名', '数学成绩'],
  rows: [
    { 姓名: '合成学生', 数学成绩: '88' },
    { 姓名: '陌生名字', 数学成绩: '70' },
    { 姓名: '合成学生', 数学成绩: '' },
  ],
  preview_row_count: 3, truncated: false,
  raw_file_retained: false as const, temporary_file_created: false as const,
}
const widePreviewPayload = {
  file_name: '2025-2026学年第一学期期中（初一年级）-七年级9班.xlsx',
  sheet_names: ['简表'], selected_sheet: '简表',
  headers: ['序号', '准考证号', '姓名', '语文-得分', '语文-班次', '语文-校次', '数学-得分', '数学-班次', '数学-校次', '总分-得分', '总分-班次', '总分-校次'],
  rows: [
    { 序号: '1', 准考证号: '37001', 姓名: '合成学生', '语文-得分': '96.5', '语文-班次': '8', '语文-校次': '107', '数学-得分': '100', '数学-班次': '2', '数学-校次': '30', '总分-得分': '500', '总分-班次': '3', '总分-校次': '40' },
    { 序号: '2', 准考证号: '37002', 姓名: '陌生名字', '语文-得分': '80', '语文-班次': '20', '语文-校次': '250', '数学-得分': '', '数学-班次': '', '数学-校次': '', '总分-得分': '430', '总分-班次': '15', '总分-校次': '180' },
  ],
  preview_row_count: 2, truncated: false,
  raw_file_retained: false as const, temporary_file_created: false as const,
}
const confirmResult = { import_id: 'i1', duplicate: false, created_assessments: 1, created_results: 1, model_enabled: false, physical_request_count: 0 }
afterEach(() => { apps.splice(0).forEach((app) => app.unmount()); document.body.innerHTML = ''; vi.restoreAllMocks() })

describe('B UI 学业证据成绩表上传', () => {
  it('lets the teacher preview and confirm a single-subject sheet with auto profile creation', async () => {
    vi.spyOn(supportApi, 'listSubjects').mockResolvedValue([rosterItem])
    const preview = vi.spyOn(supportApi, 'previewSpreadsheet').mockResolvedValue(singlePreviewPayload)
    const confirm = vi.spyOn(supportApi, 'confirmEvidence').mockResolvedValue(confirmResult)
    const host = await mount(EvidenceUploadPanel)

    clickByText(host, '上传大考成绩表'); await settle()
    chooseFile(host, new File(['姓名,数学成绩\n合成学生,88'], '期中考试.csv', { type: 'text/csv' }))
    await vi.waitFor(() => expect(preview).toHaveBeenCalledExactlyOnceWith('期中考试.csv', expect.any(String), null))
    await settle()

    expect(host.textContent).toContain('共识别 3 行：可登记 2 人')
    expect(host.textContent).toContain('其中 1 人没有档案，将新建空档案并登记')
    expect(host.textContent).toContain('新建空档案并登记')
    expect(host.textContent).toContain('没有有效分数，不登记')
    // 单列回退模式仍需要学科名称（已从列头剥出"数学"）
    const subjectInput = host.querySelector<HTMLInputElement>('.session-form input[maxlength="120"]')!
    expect(subjectInput.value).toBe('数学')
    selectOption(host, '学期类别', '七上')

    clickByText(host, '核对无误，确认登记')
    await vi.waitFor(() => expect(confirm).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      source_kind: 'confirmed_spreadsheet',
      teacher_confirmed: true,
      assessments: [expect.objectContaining({
        title: '期中考试',
        subject_name: '数学',
        measure_role: 'subject_score',
        results: [
          { subject_id: 's1', result_state: 'normal', score: 88, rank: null, class_rank: null },
          { subject_identity: { display_name: '陌生名字', class_label: '七9班', student_code: null }, result_state: 'normal', score: 70, rank: null, class_rank: null },
        ],
      })],
    })))
    await settle()
    expect(host.textContent).toContain('已登记 1 条学生成绩')
    expect(host.textContent).toContain('新建 1 个空档案')
    expect(host.textContent).toContain('继续上传下一份')
  })

  it('explains when the spreadsheet cannot be parsed', async () => {
    vi.spyOn(supportApi, 'listSubjects').mockResolvedValue([rosterItem])
    vi.spyOn(supportApi, 'previewSpreadsheet').mockRejectedValue(apiError('CSV 文件编码无法识别'))
    const confirm = vi.spyOn(supportApi, 'confirmEvidence')
    const host = await mount(EvidenceUploadPanel)

    clickByText(host, '上传大考成绩表'); await settle()
    chooseFile(host, new File(['garbage'], '期中考试.csv', { type: 'text/csv' }))
    await vi.waitFor(() => expect(host.textContent).toContain('CSV 文件编码无法识别'))

    expect(host.textContent).not.toContain('核对无误，确认登记')
    expect(confirm).not.toHaveBeenCalled()
  })

  it('treats an identical re-upload as already registered instead of an error', async () => {
    vi.spyOn(supportApi, 'listSubjects').mockResolvedValue([rosterItem])
    vi.spyOn(supportApi, 'previewSpreadsheet').mockResolvedValue(singlePreviewPayload)
    vi.spyOn(supportApi, 'confirmEvidence').mockResolvedValue({ import_id: 'i0', duplicate: true, created_assessments: 0, created_results: 0, model_enabled: false, physical_request_count: 0 })
    const host = await mount(EvidenceUploadPanel)

    clickByText(host, '上传大考成绩表'); await settle()
    chooseFile(host, new File(['姓名,数学成绩\n合成学生,88'], '期中考试.csv', { type: 'text/csv' }))
    await vi.waitFor(() => expect(host.textContent).toContain('核对无误，确认登记'))
    selectOption(host, '学期类别', '七上')
    clickByText(host, '核对无误，确认登记')
    await vi.waitFor(() => expect(host.textContent).toContain('这份成绩表之前已经登记过，本次没有重复写入'))

    expect(host.textContent).not.toContain('没有登记成功')
  })

  it('auto-detects all subjects with ranks and registers every subject at once', async () => {
    vi.spyOn(supportApi, 'listSubjects').mockResolvedValue([rosterItem])
    vi.spyOn(supportApi, 'previewSpreadsheet').mockResolvedValue(widePreviewPayload)
    const confirm = vi.spyOn(supportApi, 'confirmEvidence').mockResolvedValue(confirmResult)
    const host = await mount(EvidenceUploadPanel)

    clickByText(host, '上传大考成绩表'); await settle()
    chooseFile(host, new File(['x'], '2025-2026学年第一学期期中（初一年级）-七年级9班.xlsx'))
    await vi.waitFor(() => expect(host.textContent).toContain('核对无误，确认登记'))

    // 无需任何选列：科目、姓名列、班次/校次全部自动识别
    expect(host.querySelector('.session-form select:not([disabled])')).toBeTruthy()
    expect(host.textContent).toContain('已自动识别 3 个科目：语文、数学、总分')
    expect(host.textContent).toContain('班次、校次列已自动绑定')
    // 文件名含"第一学期+初一"，学期类别预填为七上
    const termSelect = [...host.querySelectorAll('label')].find((item) => item.textContent?.includes('学期类别'))!.querySelector('select')!
    expect(termSelect.value).toBe('七上')
    expect(host.textContent).toContain('共识别 2 行：可登记 2 人')

    // 手动清空学期类别后禁止确认
    selectOption(host, '学期类别', '')
    await settle()
    expect(host.textContent).toContain('请先选择学期类别')
    selectOption(host, '学期类别', '七上')
    await settle()

    // 未填年级人数时给出提示但不阻塞
    expect(host.textContent).toContain('未填年级人数')
    setInput(host, '年级人数', '320')
    await settle()

    clickByText(host, '核对无误，确认登记')
    await vi.waitFor(() => expect(confirm).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      source_kind: 'confirmed_spreadsheet',
      teacher_confirmed: true,
      assessments: [
        expect.objectContaining({
          subject_name: '语文',
          measure_role: 'subject_score',
          rank_scope: 'grade',
          participant_count: 320,
          cohort_key: 'same-grade-cohort',
          session: expect.objectContaining({ grade: '七年级', term: '上学期', comparison_series: 'class-regular' }),
          results: [
            { subject_id: 's1', result_state: 'normal', score: 96.5, rank: 107, class_rank: 8 },
            { subject_identity: { display_name: '陌生名字', class_label: '七9班', student_code: '37002' }, result_state: 'normal', score: 80, rank: 250, class_rank: 20 },
          ],
        }),
        expect.objectContaining({
          subject_name: '数学',
          measure_role: 'subject_score',
          results: [
            { subject_id: 's1', result_state: 'normal', score: 100, rank: 30, class_rank: 2 },
          ],
        }),
        expect.objectContaining({
          subject_name: '总分',
          measure_role: 'total_score',
          rank_scope: 'grade',
          results: [
            { subject_id: 's1', result_state: 'normal', score: 500, rank: 40, class_rank: 3 },
            { subject_identity: { display_name: '陌生名字', class_label: '七9班', student_code: '37002' }, result_state: 'normal', score: 430, rank: 180, class_rank: 15 },
          ],
        }),
      ],
    })))
  })
})
