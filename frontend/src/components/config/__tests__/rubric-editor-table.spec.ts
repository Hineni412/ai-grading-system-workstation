import { createApp, nextTick } from 'vue'
import { describe, expect, it } from 'vitest'

import type { ConfigEditorIssue, ConfigEditorRow } from '../../../api/config-workspace'
import RubricEditorTable from '../RubricEditorTable.vue'

function row(overrides: Partial<ConfigEditorRow> = {}): ConfigEditorRow {
  return {
    row_id: 'row-q12-p1-s1', question_id: 'Q12', part_id: 'P1', step_id: 'S1',
    part_label: '第 1 问', question_type: 'proof', core_goal: '完整证明'.repeat(40), score: 3,
    standard_answer: '标准答案'.repeat(80), accepted_answers: ['等价答案 A'], match_rule: '按关键要素匹配',
    answer_only_max_score: null, require_final_answer: true,
    required_elements: [], deduction_rules: [], part_deduction_rules: [],
    final_answer_rule: '', ...overrides,
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
  it('groups process blocks by part and displays the configured rules without opening an editor', async () => {
    const mounted = await mountTable({ rows: [
      row({ response_mode: 'process_required', answer_only_max_score: 1, allow_alternative_methods: true }),
      row({ row_id: 's2', step_id: 'S2', response_mode: 'process_required', answer_only_max_score: 1 }),
      row({ row_id: 'p2', part_id: 'Q12(P2)', response_mode: 'short_answer_points', allow_alternative_methods: false }),
      row({ row_id: 'obj', question_id: 'Q1', part_id: 'Q1', question_type: 'choice', response_mode: 'exact_objective' }),
    ] })
    const groups = mounted.host.querySelectorAll('.rubric-part')
    expect(groups).toHaveLength(3)
    expect(groups[0]?.querySelector('h3')?.textContent).toBe('Q12 第1问')
    expect(groups[0]?.querySelectorAll('.rubric-unit-card')).toHaveLength(2)
    expect(groups[0]?.querySelector('.rubric-part__rules')?.textContent).toContain('只有正确答案、无有效过程：1 分')
    expect(groups[0]?.textContent).toContain('0—3 分，按目标完成程度给分')
    expect(groups[1]?.querySelector('.rubric-part__rules')?.textContent).toContain('各答案项分别给分')
    expect(groups[1]?.querySelector('.rubric-part__rules')?.textContent).toContain('限定方法')
    expect(groups[2]?.querySelector('.rubric-part__rules')?.textContent).toContain('答对得满分')
    expect(groups[2]?.querySelector('[data-edit-field="answer_only_max_score"]')).toBeNull()
    mounted.unmount()
  })

  it('clears a policy validation error independently of the block score', async () => {
    const mounted = await mountTable()
    const cap = mounted.host.querySelector<HTMLInputElement>('[data-edit-field="answer_only_max_score"]')!
    const score = mounted.host.querySelector<HTMLInputElement>('[data-edit-field="score"]')!
    cap.value = '2.5'; cap.dispatchEvent(new Event('change', { bubbles: true }))
    score.value = '4'; score.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    expect(cap.getAttribute('aria-invalid')).toBe('true')
    expect(mounted.host.textContent).toContain('仅答案最高分必须')
    cap.value = ''; cap.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    expect(cap.getAttribute('aria-invalid')).toBe('false')
    expect(mounted.emitted).not.toContainEqual(expect.objectContaining({ answer_only_max_score: 2.5 }))
    mounted.unmount()
  })

  it('renders every scoring point as a compact editable card', async () => {
    const mounted = await mountTable()

    expect(mounted.host.querySelectorAll('.rubric-unit-card')).toHaveLength(2)
    expect(mounted.host.querySelectorAll('.rubric-unit-card__preview')).toHaveLength(2)
    expect(mounted.host.querySelectorAll('.rubric-unit-card__score-badge')[0]?.textContent)
      .toContain('3 分')
    expect(mounted.host.querySelectorAll('.rubric-unit-card__score-badge')[1]?.textContent)
      .toContain('97 分')
  })

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
    expect(mounted.host.textContent).toContain('本评分点沿用同一小问首个评分点的评分策略')
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

  it('shows the part answer and shared deduction policy only on the first scoring card', async () => {
    const mounted = await mountTable({ rows: [
      row({
        part_deduction_rules: ['缺少关键证明义务时扣对应步骤分'],
      }),
      row({
        row_id: 'row-q12-p1-s2',
        step_id: 'S2',
        standard_answer: '',
        accepted_answers: [],
        part_deduction_rules: [],
      }),
    ] })
    const first = mounted.host.querySelector<HTMLElement>('[data-row-id="row-q12-p1-s1"]')!
    const second = mounted.host.querySelector<HTMLElement>('[data-row-id="row-q12-p1-s2"]')!

    expect(first.textContent).toContain('标准答案')
    expect(first.textContent).toContain('小问统一扣分规则')
    expect(first.querySelector('[data-edit-field="part_deduction_rules"]')).not.toBeNull()
    expect(second.textContent).not.toContain('标准答案')
    expect(second.textContent).not.toContain('小问统一扣分规则')
    expect(second.querySelector('[data-edit-field="standard_answer"]')).toBeNull()
    expect(second.querySelector('[data-edit-field="accepted_answers"]')).toBeNull()

    const policy = first.querySelector<HTMLTextAreaElement>(
      '[data-edit-field="part_deduction_rules"]',
    )!
    policy.value = '缺少最终结论扣 1 分'
    policy.dispatchEvent(new Event('change', { bubbles: true }))
    expect(mounted.emitted).toContainEqual({
      row_id: 'row-q12-p1-s1',
      part_deduction_rules: ['缺少最终结论扣 1 分'],
    })
  })

  it('emits an explicit null when the answer-only limit is cleared', async () => {
    const mounted = await mountTable({ rows: [row({ answer_only_max_score: 2 })] })
    const answerOnly = mounted.host.querySelector<HTMLInputElement>(
      '[aria-label="Q12 P1 仅答案最高分"]',
    )!

    answerOnly.value = ''
    answerOnly.dispatchEvent(new Event('change', { bubbles: true }))

    expect(mounted.emitted).toEqual([
      { row_id: 'row-q12-p1-s1', answer_only_max_score: null },
    ])
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
    await nextTick()
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

  it('keeps long content in the card without requiring a horizontal ledger', async () => {
    const mounted = await mountTable()
    expect(mounted.host.querySelector('.rubric-ledger__viewport')).toBeNull()
    expect(mounted.host.querySelector('.rubric-ledger__cards')?.getAttribute('aria-label'))
      .toBe('评分依据评分点卡片')
    expect(mounted.host.textContent).toContain('完整证明'.repeat(40))
  })

  it('distinguishes objective previews without removing any scoring-point editors', async () => {
    const mounted = await mountTable({ rows: [
      row({
        row_id: 'row-q1-p1-s1',
        question_id: 'Q1',
        question_type: 'choice',
        standard_answer: 'B',
        score: 4,
      }),
      row({
        row_id: 'row-q12-p1-s1',
        question_id: 'Q12',
        question_type: 'proof',
        score: 96,
      }),
    ] })

    const objective = mounted.host.querySelector<HTMLElement>('[data-row-id="row-q1-p1-s1"]')!
    const proof = mounted.host.querySelector<HTMLElement>('[data-row-id="row-q12-p1-s1"]')!
    expect(objective.classList.contains('rubric-unit-card--objective')).toBe(true)
    expect(proof.classList.contains('rubric-unit-card--objective')).toBe(false)
    expect(objective.querySelector('.rubric-unit-card__preview')).not.toBeNull()
    expect(proof.querySelector('.rubric-unit-card__preview')).not.toBeNull()
    expect(objective.querySelector<HTMLTextAreaElement>('[data-edit-field="standard_answer"]')
      ?.getAttribute('rows')).toBe('3')
    expect(proof.querySelector<HTMLTextAreaElement>('[data-edit-field="standard_answer"]')
      ?.getAttribute('rows')).toBe('3')
    for (const field of ['standard_answer', 'required_elements', 'deduction_rules', 'accepted_answers']) {
      expect(objective.querySelector(`[data-edit-field="${field}"]`)).not.toBeNull()
      expect(proof.querySelector(`[data-edit-field="${field}"]`)).not.toBeNull()
    }
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

  it('applies choice score per question and fill score as each question total', async () => {
    const mounted = await mountTable({ rows: [
      row({ row_id: 'choice-1', question_id: 'Q1', question_type: 'choice', score: 2 }),
      row({ row_id: 'choice-2', question_id: 'Q2', question_type: 'choice', score: 2 }),
      row({ row_id: 'fill-1a', question_id: 'Q3', question_type: 'fill_blank', score: 1 }),
      row({ row_id: 'fill-1b', question_id: 'Q3', question_type: 'fill_blank', step_id: 'S2', score: 1 }),
    ] })
    const choice = mounted.host.querySelector<HTMLInputElement>('[aria-label="选择题每题分值"]')!
    choice.value = '3'
    choice.dispatchEvent(new Event('input', { bubbles: true }))
    const fill = mounted.host.querySelector<HTMLInputElement>('[aria-label="填空题每题总分"]')!
    fill.value = '5'
    fill.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    const applyButtons = [...mounted.host.querySelectorAll<HTMLButtonElement>('.rubric-ledger__bulk-scores button')]
    applyButtons[0]!.click()
    applyButtons[1]!.click()

    expect(mounted.emitted).toEqual([
      { row_id: 'choice-1', score: 3 },
      { row_id: 'choice-2', score: 3 },
      { row_id: 'fill-1a', score: 3 },
      { row_id: 'fill-1b', score: 2 },
    ])
  })

  it.each(['', 'NaN', 'Infinity', '-1', '100.1', '2.5'])(
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
