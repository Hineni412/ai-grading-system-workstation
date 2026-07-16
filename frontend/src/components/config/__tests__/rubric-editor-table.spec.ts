import { createApp, nextTick } from 'vue'
import { describe, expect, it } from 'vitest'

import type { ConfigEditorIssue, ConfigEditorRow } from '../../../api/config-workspace'
import RubricEditorTable from '../RubricEditorTable.vue'

function row(overrides: Partial<ConfigEditorRow> = {}): ConfigEditorRow {
  return {
    row_id: 'row-q12-p1-s1', question_id: 'Q12', part_id: 'P1', step_id: 'S1',
    part_label: '第 1 问', question_type: 'proof', core_goal: '完整证明'.repeat(40), score: 3,
    standard_answer: '标准答案'.repeat(80), accepted_answers: ['等价答案 A'], match_rule: '按关键要素匹配',
    knowledge: '', answer_only_max_score: null, require_final_answer: true,
    required_elements: [], deduction_rules: [], final_answer_rule: '', ...overrides,
  }
}

async function mountTable(options: {
  rows?: ConfigEditorRow[]
  totalScore?: number
  issues?: ConfigEditorIssue[]
} = {}) {
  const host = document.createElement('div')
  document.body.append(host)
  const emitted: unknown[] = []
  const app = createApp(RubricEditorTable, {
    rows: options.rows ?? [row(), row({ row_id: 'row-q12-p1-s2', step_id: 'S2', score: 97 })],
    totalScore: options.totalScore ?? 100,
    issues: options.issues ?? [],
    onEdit: (edit: unknown) => emitted.push(edit),
  })
  app.mount(host)
  await nextTick()
  return { host, emitted, unmount: () => app.unmount() }
}

describe('RubricEditorTable', () => {
  it('addresses score, standard answer and accepted answers by hidden row id', async () => {
    const mounted = await mountTable()
    const score = mounted.host.querySelector<HTMLInputElement>('[aria-label="Q12 P1 S1 分值"]')!
    score.value = '4'
    score.dispatchEvent(new Event('change', { bubbles: true }))
    const answer = mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 标准答案"]')!
    answer.value = '新的完整证明'
    answer.dispatchEvent(new Event('change', { bubbles: true }))
    const accepted = mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 等价答案"]')!
    accepted.value = '答案 A\n\n答案 B'
    accepted.dispatchEvent(new Event('change', { bubbles: true }))

    expect(mounted.emitted).toEqual([
      { row_id: 'row-q12-p1-s1', score: 4 },
      { row_id: 'row-q12-p1-s1', standard_answer: '新的完整证明' },
      { row_id: 'row-q12-p1-s1', accepted_answers: ['答案 A', '答案 B'] },
    ])
    expect(mounted.host.textContent).toContain('按关键要素匹配')
    expect(mounted.host.querySelector('[data-row-id="row-q12-p1-s1"]')).not.toBeNull()
  })

  it('edits evidence, deductions and first-row whole-question policy by row id', async () => {
    const mounted = await mountTable()
    const evidence = mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 证据要求/关键步骤"]')!
    evidence.value = '列式\n关键结论'
    evidence.dispatchEvent(new Event('change', { bubbles: true }))
    const deductions = mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 扣分规则"]')!
    deductions.value = '漏写过程扣 1 分'
    deductions.dispatchEvent(new Event('change', { bubbles: true }))
    const required = mounted.host.querySelector<HTMLInputElement>('[aria-label="Q12 P1 要求最终答案"]')!
    required.click()
    const answerOnly = mounted.host.querySelector<HTMLInputElement>('[aria-label="Q12 P1 仅答案最高分"]')!
    answerOnly.value = '2'
    answerOnly.dispatchEvent(new Event('change', { bubbles: true }))
    const finalRule = mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 最终答案规则"]')!
    finalRule.value = '单位必须完整'
    finalRule.dispatchEvent(new Event('change', { bubbles: true }))

    expect(mounted.emitted).toEqual(expect.arrayContaining([
      { row_id: 'row-q12-p1-s1', required_elements: ['列式', '关键结论'] },
      { row_id: 'row-q12-p1-s1', deduction_rules: ['漏写过程扣 1 分'] },
      { row_id: 'row-q12-p1-s1', require_final_answer: false },
      { row_id: 'row-q12-p1-s1', answer_only_max_score: 2 },
      { row_id: 'row-q12-p1-s1', final_answer_rule: '单位必须完整' },
    ]))
    expect(mounted.host.querySelectorAll('[aria-label="Q12 P1 要求最终答案"]')).toHaveLength(1)
    expect(mounted.host.textContent).toContain('本评分单元策略见首行')
  })

  it('renders and edits policy controls on the first row of every part', async () => {
    const mounted = await mountTable({ rows: [
      row(),
      row({ row_id: 'row-q12-p1-s2', step_id: 'S2' }),
      row({ row_id: 'row-q12-p2-s1', part_id: 'P2', part_label: '第 2 问', step_id: 'S1',
        require_final_answer: false, answer_only_max_score: 1, final_answer_rule: '写明单位' }),
    ] })

    expect(mounted.host.querySelectorAll('[aria-label="Q12 P1 要求最终答案"]')).toHaveLength(1)
    const secondPart = mounted.host.querySelector<HTMLInputElement>(
      '[aria-label="Q12 P2 要求最终答案"]',
    )!
    expect(secondPart).not.toBeNull()
    secondPart.click()
    expect(mounted.emitted).toContainEqual({
      row_id: 'row-q12-p2-s1', require_final_answer: true,
    })
  })

  it('focuses the expanded issue field and keeps unlocatable issues global', async () => {
    const mounted = await mountTable({ issues: [
      { code: 'required_missing', severity: 'error', row_id: 'row-q12-p1-s1',
        field: 'required_elements', message: '补充关键步骤' },
      { code: 'unknown_policy', severity: 'error', row_id: null,
        field: 'future_policy', message: '核对整题策略' },
    ] })
    expect(mounted.host.querySelector('[role="alert"]')?.textContent).toContain('核对整题策略')
    mounted.host.querySelector<HTMLButtonElement>('[data-issue-field="required_elements"]')!.click()
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Q12 P1 S1 证据要求/关键步骤')
  })

  it('shows blocking total and normal warnings, then focuses the issue field', async () => {
    const mounted = await mountTable({
      totalScore: 99,
      issues: [
        { code: 'total', severity: 'error', row_id: null, field: 'score', message: '总分必须为 100' },
        { code: 'answer', severity: 'warning', row_id: 'row-q12-p1-s1', field: 'standard_answer', message: '请核对标准答案' },
      ],
    })
    expect(mounted.host.querySelector('[data-save-blocked="true"]')?.textContent).toContain('99')
    expect(mounted.host.querySelector('[role="alert"]')?.textContent).toContain('总分必须为 100')
    mounted.host.querySelector<HTMLButtonElement>('[data-issue-row-id="row-q12-p1-s1"]')!.click()
    await nextTick()
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Q12 P1 S1 标准答案')
  })

  it('keeps long content in a labelled internal horizontal viewport with sticky identity columns', async () => {
    const mounted = await mountTable()
    const viewport = mounted.host.querySelector<HTMLElement>('.rubric-ledger__viewport')!
    expect(viewport.getAttribute('aria-label')).toBe('评分依据编辑表')
    expect(viewport.getAttribute('tabindex')).toBe('0')
    expect(mounted.host.querySelectorAll('.rubric-ledger__sticky').length).toBeGreaterThanOrEqual(4)
    expect(mounted.host.textContent).toContain('完整证明'.repeat(40))
  })

  it('never interpolates malformed issue rows or unknown fields into a focus selector', async () => {
    const maliciousRow = 'bad\"]'
    const mounted = await mountTable({
      issues: [
        { code: 'row', severity: 'warning', row_id: maliciousRow, field: 'score', message: '恶意行' },
        { code: 'field', severity: 'warning', row_id: 'row-q12-p1-s1', field: 'unknown_field', message: '未知字段' },
      ],
    })
    const score = mounted.host.querySelector<HTMLInputElement>('[aria-label="Q12 P1 S1 分值"]')!
    score.focus()
    const issueButtons = mounted.host.querySelectorAll<HTMLButtonElement>('[data-issue-row-id]')

    expect(() => issueButtons[0]!.click()).not.toThrow()
    expect(document.activeElement).toBe(score)
    expect(() => issueButtons[1]!.click()).not.toThrow()
    expect(document.activeElement).toBe(score)
  })

  it.each(['', 'NaN', 'Infinity', '-1', '100.1'])(
    'rejects an out-of-contract rubric score %s without emitting it',
    async (raw) => {
      const mounted = await mountTable()
      const score = mounted.host.querySelector<HTMLInputElement>('[aria-label="Q12 P1 S1 分值"]')!
      score.value = raw
      score.dispatchEvent(new Event('input', { bubbles: true }))
      score.dispatchEvent(new Event('change', { bubbles: true }))
      await nextTick()
      expect(mounted.emitted).toEqual([])
      expect(score.getAttribute('aria-invalid')).toBe('true')
      expect(mounted.host.querySelector('[role="alert"]')?.textContent).toContain('0 至 100')
    },
  )
})
