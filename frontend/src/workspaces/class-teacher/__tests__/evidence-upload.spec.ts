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
const rosterItem = { subject_id: 's1', revision: 1, source_student_id: 'student:S001', display_name: '合成学生', class_label: '一班' }
const previewPayload = {
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
afterEach(() => { apps.splice(0).forEach((app) => app.unmount()); document.body.innerHTML = ''; vi.restoreAllMocks() })

describe('B UI 学业证据成绩表上传', () => {
  it('lets the teacher preview, review unmatched rows and confirm a score sheet', async () => {
    vi.spyOn(supportApi, 'listSubjects').mockResolvedValue([rosterItem])
    const preview = vi.spyOn(supportApi, 'previewSpreadsheet').mockResolvedValue(previewPayload)
    const confirm = vi.spyOn(supportApi, 'confirmEvidence').mockResolvedValue({ import_id: 'i1', duplicate: false, created_assessments: 1, created_results: 1, model_enabled: false, physical_request_count: 0 })
    const host = await mount(EvidenceUploadPanel)

    clickByText(host, '上传大考成绩表'); await settle()
    chooseFile(host, new File(['姓名,数学成绩\n合成学生,88'], '期中考试.csv', { type: 'text/csv' }))
    await vi.waitFor(() => expect(preview).toHaveBeenCalledExactlyOnceWith('期中考试.csv', expect.any(String), null))
    await settle()

    expect(host.textContent).toContain('共识别 3 行：可登记 1 条')
    expect(host.textContent).toContain('花名册中找不到，不登记')
    expect(host.textContent).toContain('没有有效分数，不登记')
    const titleInput = host.querySelector<HTMLInputElement>('.session-form input')!
    expect(titleInput.value).toBe('期中考试')
    selectOption(host, '学期类别', '七上')

    clickByText(host, '核对无误，确认登记')
    await vi.waitFor(() => expect(confirm).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      source_kind: 'confirmed_spreadsheet',
      teacher_confirmed: true,
      assessments: [expect.objectContaining({
        title: '期中考试',
        subject_name: '数学',
        results: [{ subject_id: 's1', result_state: 'normal', score: 88, rank: null, class_rank: null }],
      })],
    })))
    await settle()
    expect(host.textContent).toContain('已登记 1 条学生成绩（场次：期中考试）')
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
    vi.spyOn(supportApi, 'previewSpreadsheet').mockResolvedValue(previewPayload)
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

  it('guesses term and rank columns and blocks confirmation until term is chosen', async () => {
    vi.spyOn(supportApi, 'listSubjects').mockResolvedValue([rosterItem])
    vi.spyOn(supportApi, 'previewSpreadsheet').mockResolvedValue({
      file_name: '2025-2026学年第一学期期中（初一年级）.xlsx', sheet_names: ['Sheet1'], selected_sheet: 'Sheet1',
      headers: ['序号', '姓名', '语文-得分', '语文-班次', '语文-校次'],
      rows: [{ 序号: '1', 姓名: '合成学生', '语文-得分': '96.5', '语文-班次': '8', '语文-校次': '107' }],
      preview_row_count: 1, truncated: false,
      raw_file_retained: false as const, temporary_file_created: false as const,
    })
    const confirm = vi.spyOn(supportApi, 'confirmEvidence').mockResolvedValue({ import_id: 'i2', duplicate: false, created_assessments: 1, created_results: 1, model_enabled: false, physical_request_count: 0 })
    const host = await mount(EvidenceUploadPanel)

    clickByText(host, '上传大考成绩表'); await settle()
    chooseFile(host, new File(['x'], '2025-2026学年第一学期期中（初一年级）.xlsx'))
    await vi.waitFor(() => expect(host.textContent).toContain('核对无误，确认登记'))

    // 文件名含“第一学期+初一”，学期类别应预填为七上
    const termSelect = [...host.querySelectorAll('label')].find((item) => item.textContent?.includes('学期类别'))!.querySelector('select')!
    expect(termSelect.value).toBe('七上')
    const classRankSelect = [...host.querySelectorAll('label')].find((item) => item.textContent?.includes('班次列'))!.querySelector('select')!
    const gradeRankSelect = [...host.querySelectorAll('label')].find((item) => item.textContent?.includes('校次列'))!.querySelector('select')!
    expect(classRankSelect.value).toBe('语文-班次')
    expect(gradeRankSelect.value).toBe('语文-校次')
    expect(host.textContent).toContain('登记给 合成学生')

    // 手动清空学期类别后禁止确认
    selectOption(host, '学期类别', '')
    await settle()
    expect(host.textContent).toContain('请先选择学期类别')
    selectOption(host, '学期类别', '七上')
    await settle()

    // 校次列已自动选中，未填年级人数时禁止确认
    expect(host.textContent).toContain('请填写年级人数')
    setInput(host, '年级人数', '320')
    await settle()
    expect(host.textContent).not.toContain('请填写年级人数')

    clickByText(host, '核对无误，确认登记')
    await vi.waitFor(() => expect(confirm).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      assessments: [expect.objectContaining({
        subject_name: '语文',
        rank_scope: 'grade',
        participant_count: 320,
        cohort_key: 'same-grade-cohort',
        ranking_rule_version: 'school-export-v1',
        session: expect.objectContaining({
          grade: '七年级',
          term: '上学期',
          academic_year: expect.any(String),
          comparison_series: 'class-regular',
        }),
        results: [{ subject_id: 's1', result_state: 'normal', score: 96.5, rank: 107, class_rank: 8 }],
      })],
    })))
  })
})
