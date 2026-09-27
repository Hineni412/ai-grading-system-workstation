import { describe, expect, it } from 'vitest'
import { createApp, nextTick } from 'vue'

import type { ConfigEditorCommand, ConfigEditorRow } from '../../../api/config-workspace'
import ScoringUnitEditor from '../ScoringUnitEditor.vue'

const baseRow = {
  question_id: 'Q12', question_type: 'proof', part_label: '第1问',
  standard_answer: '', accepted_answers: [], match_rule: '', answer_only_max_score: 1,
  require_final_answer: true, required_elements: [], deduction_rules: [],
  part_deduction_rules: [], final_answer_rule: '',
}
const rows: ConfigEditorRow[] = [
  { ...baseRow, row_id: 'r1', part_id: 'Q12(P1)', step_id: 'S1', core_goal: '写出条件', score: 2 },
  { ...baseRow, row_id: 'r2', part_id: 'Q12(P1)', step_id: 'S2', core_goal: '完成推理', score: 1 },
  { ...baseRow, row_id: 'r3', part_id: 'Q12(P2)', part_label: '第2问', step_id: 'S1', core_goal: '得出结论', score: 3 },
]

async function mountEditor(options: { rows?: ConfigEditorRow[]; scoreReviewRequired?: boolean } = {}) {
  const host = document.createElement('div')
  document.body.append(host)
  const commands: ConfigEditorCommand[] = []
  const retries: string[] = []
  const app = createApp(ScoringUnitEditor, {
    questionId: 'Q12', rows: options.rows ?? rows,
    scoreReviewRequired: options.scoreReviewRequired ?? false,
    onCommand: (command: ConfigEditorCommand) => commands.push(command),
    onRetry: (questionId: string) => retries.push(questionId),
  })
  app.mount(host)
  await nextTick()
  return { host, commands, retries }
}

describe('ScoringUnitEditor', () => {

  it('lets the teacher add and remove steps but blocks an unassigned score', async () => {
    const mounted = await mountEditor()
    const addStep = [...mounted.host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('添加步骤点'))!
    addStep.click()
    await nextTick()
    expect(mounted.host.textContent).toContain('步骤 3')
    expect(mounted.host.querySelector<HTMLButtonElement>('button[name="保存本题结构"]')!.disabled).toBe(true)
    expect(mounted.host.querySelector('[role="alert"]')?.textContent).toContain('需要填写评分目标')

    const remove = mounted.host.querySelector<HTMLButtonElement>('[aria-label="删除第 1 小问步骤 3"]')!
    remove.click()
    await nextTick()
    expect(mounted.host.querySelector<HTMLButtonElement>('button[name="保存本题结构"]')!.disabled).toBe(false)
  })

  it('preserves a remaining step identity after deleting the preceding step', async () => {
    const mounted = await mountEditor()
    mounted.host.querySelector<HTMLButtonElement>('[aria-label="删除第 1 小问步骤 1"]')!.click()
    await nextTick()
    const score = mounted.host.querySelector<HTMLInputElement>('[aria-label="Q12 第 1 小问步骤 1 分值"]')!
    score.value = '3'
    score.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    mounted.host.querySelector<HTMLButtonElement>('button[name="保存本题结构"]')!.click()
    await nextTick()
    const command = mounted.commands[0]
    expect(command?.kind).toBe('replace_question_structure')
    if (command?.kind !== 'replace_question_structure') throw new Error('missing saved structure')
    expect(command.parts[0]?.steps[0]?.step_id).toBe('S2')
    expect(command.parts[0]?.part_id).toBe('Q12(P1)')
  })
})
