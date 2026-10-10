import { describe, expect, it } from 'vitest'
import { createApp, nextTick } from 'vue'

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
  const validity: boolean[] = []
  const app = createApp(RubricEditorTable, {
    rows: options.rows ?? [row(), row({ row_id: 'row-q12-p1-s2', step_id: 'S2', score: 97 })],
    totalScore: options.totalScore ?? 100,
    issues: options.issues ?? [],
    onEdit: (edit: unknown) => emitted.push(edit),
    onValidity: (valid: boolean) => validity.push(valid),
  })
  app.mount(host)
  await nextTick()
  return { host, emitted, validity, unmount: () => app.unmount() }
}

describe('RubricEditorTable', () => {

  it('accepts decimal answer caps and can clear them independently of the block score', async () => {
    const mounted = await mountTable()
    const cap = mounted.host.querySelector<HTMLInputElement>('[data-edit-field="answer_only_max_score"]')!
    const score = mounted.host.querySelector<HTMLInputElement>('[data-edit-field="score"]')!
    cap.value = '2.5'; cap.dispatchEvent(new Event('change', { bubbles: true }))
    score.value = '4'; score.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    expect(cap.getAttribute('aria-invalid')).toBe('false')
    cap.value = ''; cap.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    expect(cap.getAttribute('aria-invalid')).toBe('false')
    expect(mounted.emitted).toContainEqual(expect.objectContaining({ answer_only_max_score: 2.5 }))
    mounted.unmount()
  })

  it('edits evidence, deductions and first-row whole-question policy by row id', async () => {
    const mounted = await mountTable({ rows: [row({ answer_kind: 'conditions' }), row({ row_id: 'row-q12-p1-s2', step_id: 'S2', score: 97 })] })
    const preview = mounted.host.querySelector<HTMLButtonElement>('.rubric-unit-card__preview')!
    expect(preview.textContent).toContain('按条件判对')
    expect(preview.querySelector('.rubric-unit-card__preview-fields')).toBeNull()
    preview.click()
    await nextTick()
    expect(mounted.host.querySelector('.rubric-unit-card__exceptions')).toBeNull()
    mounted.host.querySelector<HTMLElement>('.rubric-unit-card__editor')!
      .dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(mounted.emitted).toEqual([])
    const evidence = mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 判对条件"]')!
    evidence.value = '列式\n关键结论'
    evidence.dispatchEvent(new Event('change', { bubbles: true }))
    const deductions = mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 不算达成"]')!
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
    expect(mounted.host.querySelectorAll('.rubric-part__common-editor')).toHaveLength(1)
    expect(mounted.host.querySelector('.rubric-unit-card__editor [data-edit-field="standard_answer"]')).toBeNull()
    expect(mounted.host.textContent).toContain('参考答案只是示例')
    mounted.unmount()
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

  it('shows the collapsed goal, conditions summary and rule counts', async () => {
    const mounted = await mountTable({ rows: [
      row({
        core_goal: '证明全等',
        required_elements: ['列出全等条件', '写出对应边相等'],
        equivalent_rules: ['用其他判定同样给分'],
        deduction_rules: ['漏写条件不得分', '只写结论不得分'],
      }),
    ] })
    const preview = mounted.host.querySelector<HTMLButtonElement>('.rubric-unit-card__preview')!
    expect(preview.textContent).toContain('怎样算达成：列出全等条件；写出对应边相等')
    expect(preview.textContent).toContain('也算达成 1 条')
    expect(preview.textContent).toContain('不算达成 2 条')
    mounted.unmount()
  })

  it('collapses on blank-area clicks, not on controls or drags from controls', async () => {
    const mounted = await mountTable()
    const card = mounted.host.querySelector<HTMLElement>('[data-row-id="row-q12-p1-s1"]')!
    const editor = card.querySelector<HTMLElement>('.rubric-unit-card__editor')!
    const goal = card.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 评分点"]')!
    card.querySelector<HTMLButtonElement>('.rubric-unit-card__preview')!.click()
    await nextTick()
    expect(editor.querySelector('button')).toBeNull()

    goal.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    card.querySelector<HTMLInputElement>('[aria-label="Q12 P1 S1 分值"]')!
      .dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(editor.style.display).not.toBe('none')

    goal.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    editor.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(editor.style.display).not.toBe('none')

    card.querySelector<HTMLElement>('.rubric-unit-card__fields')!
      .dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(editor.style.display).toBe('none')
    mounted.unmount()
  })

  it('treats a non-checkbox label caption as blank area', async () => {
    const mounted = await mountTable()
    const card = mounted.host.querySelector<HTMLElement>('[data-row-id="row-q12-p1-s1"]')!
    const editor = card.querySelector<HTMLElement>('.rubric-unit-card__editor')!
    const preview = card.querySelector<HTMLButtonElement>('.rubric-unit-card__preview')!
    preview.click()
    await nextTick()
    const evidence = card.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 怎样算达成"]')!
    const caption = [...editor.querySelectorAll<HTMLElement>('.rubric-unit-card__fields label > span')]
      .find((span) => span.textContent === '怎样算达成')!
    caption.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(editor.style.display).toBe('none')
    expect(document.activeElement).not.toBe(evidence)
    expect(document.activeElement).toBe(preview)
    mounted.unmount()
  })

  it('focuses the goal textarea on open and returns focus after Esc', async () => {
    const mounted = await mountTable()
    const card = mounted.host.querySelector<HTMLElement>('[data-row-id="row-q12-p1-s1"]')!
    const editor = card.querySelector<HTMLElement>('.rubric-unit-card__editor')!
    const preview = card.querySelector<HTMLButtonElement>('.rubric-unit-card__preview')!
    const goal = card.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 评分点"]')!
    preview.click()
    await nextTick()
    expect(document.activeElement).toBe(goal)
    editor.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()
    expect(editor.style.display).toBe('none')
    expect(document.activeElement).toBe(preview)
    mounted.unmount()
  })

  it('keeps a checkbox label toggling without collapsing', async () => {
    const mounted = await mountTable({ rows: [
      row({ row_id: 'choice-1', question_id: 'Q1', question_type: 'choice', score: 2, require_final_answer: true }),
    ] })
    const card = mounted.host.querySelector<HTMLElement>('[data-row-id="choice-1"]')!
    card.querySelector<HTMLButtonElement>('.rubric-unit-card__preview')!.click()
    await nextTick()
    card.querySelector<HTMLElement>('.rubric-ledger__policy-check')!
      .dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(card.querySelector<HTMLElement>('.rubric-unit-card__editor')!.style.display).not.toBe('none')
    expect(mounted.emitted).toEqual([{ row_id: 'choice-1', require_final_answer: false }])
    mounted.unmount()
  })

  it('emits goal and equivalent-rule edits, rejects a blank goal', async () => {
    const mounted = await mountTable()
    mounted.host.querySelector<HTMLButtonElement>('.rubric-unit-card__preview')!.click()
    await nextTick()
    const goal = mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 评分点"]')!
    goal.value = '新的评分点'
    goal.dispatchEvent(new Event('change', { bubbles: true }))
    const equivalent = mounted.host.querySelector<HTMLTextAreaElement>('[aria-label="Q12 P1 S1 也算达成"]')!
    equivalent.value = '等价写法一\n等价写法二'
    equivalent.dispatchEvent(new Event('change', { bubbles: true }))
    expect(mounted.emitted).toEqual(expect.arrayContaining([
      { row_id: 'row-q12-p1-s1', core_goal: '新的评分点' },
      { row_id: 'row-q12-p1-s1', equivalent_rules: ['等价写法一', '等价写法二'] },
    ]))

    goal.value = '   '
    goal.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    expect(mounted.emitted).toHaveLength(2)
    expect(mounted.host.textContent).toContain('评分点不能为空。')
    expect(mounted.validity[mounted.validity.length - 1]).toBe(false)
    mounted.unmount()
  })

})
