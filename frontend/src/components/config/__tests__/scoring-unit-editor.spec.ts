import { createApp, nextTick } from 'vue'
import { describe, expect, it } from 'vitest'

import type { ConfigEditorCommand, ManualPartInput } from '../../../api/config-workspace'
import ScoringUnitEditor from '../ScoringUnitEditor.vue'

async function mountEditor(parts: ManualPartInput[] = []) {
  const host = document.createElement('div')
  document.body.append(host)
  const commands: ConfigEditorCommand[] = []
  const refinements: ConfigEditorCommand[] = []
  const app = createApp(ScoringUnitEditor, {
    questionId: 'Q12', parts,
    onCommand: (command: ConfigEditorCommand) => commands.push(command),
    onRefine: (command: ConfigEditorCommand) => refinements.push(command),
  })
  app.mount(host)
  await nextTick()
  return { host, commands, refinements }
}

describe('ScoringUnitEditor', () => {
  it('emits a bounded split command with the selected style', async () => {
    const mounted = await mountEditor()
    const count = mounted.host.querySelector<HTMLInputElement>('[aria-label="Q12 拆分数量"]')!
    count.value = '20'
    count.dispatchEvent(new Event('input', { bubbles: true }))
    mounted.host.querySelector<HTMLInputElement>('[aria-label="按空格拆分"]')!.click()
    mounted.host.querySelector<HTMLButtonElement>('button[name="应用拆分"]')!.click()
    await nextTick()
    expect(mounted.commands).toEqual([
      { kind: 'split', question_id: 'Q12', count: 20, style: 'blank' },
    ])
    expect(count.min).toBe('2')
    expect(count.max).toBe('20')
  })

  it('requires unique part ids, non-negative finite scores and can request refine', async () => {
    const mounted = await mountEditor([
      { part_id: 'P1', score: 40, core_goal: '第一问' },
      { part_id: 'P1', score: 60, core_goal: '第二问' },
    ])
    mounted.host.querySelector<HTMLButtonElement>('button[name="替换评分单元"]')!.click()
    await nextTick()
    expect(mounted.host.querySelector('[role="alert"]')?.textContent).toContain('不能重复')
    expect(mounted.commands).toHaveLength(0)

    const ids = mounted.host.querySelectorAll<HTMLInputElement>('[aria-label$="评分单元 ID"]')
    ids[1]!.value = 'P2'
    ids[1]!.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    mounted.host.querySelector<HTMLButtonElement>('button[name="AI 完善评分单元"]')!.click()
    await nextTick()
    expect(mounted.refinements).toEqual([{
      kind: 'replace_parts', question_id: 'Q12',
      parts: [
        { part_id: 'P1', score: 40, core_goal: '第一问' },
        { part_id: 'P2', score: 60, core_goal: '第二问' },
      ],
    }])
  })

  it('disables invalid default parts and enforces positive bounded scores and nonblank goals', async () => {
    const mounted = await mountEditor()
    const replace = mounted.host.querySelector<HTMLButtonElement>('button[name="替换评分单元"]')!
    const refine = mounted.host.querySelector<HTMLButtonElement>('button[name="AI 完善评分单元"]')!
    expect(replace.disabled).toBe(true)
    expect(refine.disabled).toBe(true)
    expect(mounted.host.querySelector('[role="alert"]')?.textContent).toContain('大于 0 且不超过 100')

    const mountedInvalid = await mountEditor([
      { part_id: 'P1', score: 101, core_goal: '有效目标' },
      { part_id: 'P2', score: 1, core_goal: '   ' },
    ])
    expect(mountedInvalid.host.querySelector<HTMLButtonElement>('button[name="替换评分单元"]')!.disabled).toBe(true)
    expect(mountedInvalid.host.querySelector('[role="alert"]')?.textContent).toContain('大于 0 且不超过 100')
    expect(mountedInvalid.commands).toEqual([])
  })
})
