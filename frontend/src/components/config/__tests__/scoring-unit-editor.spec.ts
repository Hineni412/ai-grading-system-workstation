import { createApp, nextTick } from 'vue'
import { describe, expect, it } from 'vitest'

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
  it('loads the current subquestions and scoring steps and preserves the question total', async () => {
    const mounted = await mountEditor()
    expect(mounted.host.textContent).toContain('6 / 6 分')
    expect(mounted.host.textContent).toContain('第 1 小问')
    expect(mounted.host.textContent).toContain('步骤 2')

    mounted.host.querySelector<HTMLButtonElement>('button[name="保存本题结构"]')!.click()
    await nextTick()
    expect(mounted.commands).toEqual([{
      kind: 'replace_question_structure', question_id: 'Q12',
      parts: [
        { part_id: 'P1', steps: [
          { step_id: 'S1', score: 2, core_goal: '写出条件' },
          { step_id: 'S2', score: 1, core_goal: '完成推理' },
        ] },
        { part_id: 'P2', steps: [
          { step_id: 'S1', score: 3, core_goal: '得出结论' },
        ] },
      ],
    }])
  })

  it('uses compact step cards instead of stretching every step across a full row', async () => {
    const mounted = await mountEditor()
    const grids = mounted.host.querySelectorAll('.scoring-unit-editor__step-grid')

    expect(grids).toHaveLength(2)
    expect(grids[0]?.querySelectorAll('.scoring-unit-editor__step-card')).toHaveLength(2)
    expect(grids[1]?.querySelectorAll('.scoring-unit-editor__step-card')).toHaveLength(1)
    expect(mounted.host.textContent).toContain('2 个步骤点')
    expect(mounted.host.textContent).not.toContain('先调整小问')
  })

  it('lets the teacher add and remove steps but blocks an unassigned score', async () => {
    const mounted = await mountEditor()
    const addStep = [...mounted.host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('添加步骤点'))!
    addStep.click()
    await nextTick()
    expect(mounted.host.textContent).toContain('步骤 3')
    expect(mounted.host.querySelector<HTMLButtonElement>('button[name="保存本题结构"]')!.disabled).toBe(true)
    expect(mounted.host.querySelector('[role="alert"]')?.textContent).toContain('需要填写大于 0')

    const remove = mounted.host.querySelector<HTMLButtonElement>('[aria-label="删除第 1 小问步骤 3"]')!
    remove.click()
    await nextTick()
    expect(mounted.host.querySelector<HTMLButtonElement>('button[name="保存本题结构"]')!.disabled).toBe(false)
  })

  it('requests AI for this question only and explains that scores need review', async () => {
    const mounted = await mountEditor({ scoreReviewRequired: true })
    expect(mounted.host.textContent).toContain('请逐项确认步骤并重新赋分')
    mounted.host.querySelector<HTMLButtonElement>('button[name="单题AI重试"]')!.click()
    expect(mounted.retries).toEqual(['Q12'])
  })
})
