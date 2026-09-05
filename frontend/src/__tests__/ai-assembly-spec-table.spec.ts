import { createApp, h, nextTick, type App } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import type { AiAssemblySpec, AiAssemblySpecRow } from '../api/ai-assembly'
import AssemblySpecTable from '../components/question-assembly/AssemblySpecTable.vue'

const KP_FULL_1 = '八年级上册｜第一章 勾股定理｜1 探索勾股定理｜用勾股定理解三角形'
const KP_FULL_2 = '八年级上册｜第二章 实数｜1 认识无理数'
const KP_FULL_3 = '八年级上册｜第三章 位置与坐标｜1 确定位置'

function spec(patch: Partial<AiAssemblySpec> = {}): AiAssemblySpec {
  return {
    title: '勾股定理小测',
    rows: [
      {
        question_type: '选择题',
        count: 2,
        knowledge_points: [KP_FULL_1, KP_FULL_2],
        difficulty: 4,
        score: 5,
      },
    ],
    scope_knowledge_points: [KP_FULL_1, KP_FULL_2, KP_FULL_3],
    ...patch,
  }
}

const mountedApps: App[] = []

interface Mounted {
  host: HTMLElement
  edits: Array<{ rowIndex: number; patch: Partial<AiAssemblySpecRow> }>
}

function mountTable(
  props: {
    spec?: AiAssemblySpec
    selections?: Record<number, number[]>
    lockedQuestionIds?: number[]
  } = {},
): Mounted {
  const edits: Mounted['edits'] = []
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp({
    setup: () => () => h(AssemblySpecTable, {
      spec: props.spec ?? spec(),
      selections: props.selections ?? {},
      lockedQuestionIds: props.lockedQuestionIds ?? [],
      gapByRow: new Map(),
      onEditRow: (rowIndex: number, patch: Partial<AiAssemblySpecRow>) => {
        edits.push({ rowIndex, patch })
      },
    }),
  })
  app.mount(host)
  mountedApps.push(app)
  return { host, edits }
}

afterEach(() => {
  for (const app of mountedApps.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('AssemblySpecTable knowledge point chips', () => {
  it('renders one chip per knowledge point showing only the leaf name with full path as title', () => {
    const { host } = mountTable()
    const chips = host.querySelectorAll<HTMLElement>('.ai-spec-table__chip')
    expect(chips).toHaveLength(2)
    expect(chips[0]!.textContent).toContain('用勾股定理解三角形')
    expect(chips[0]!.textContent).not.toContain('八年级上册')
    expect(chips[0]!.getAttribute('title')).toBe(KP_FULL_1)
    expect(chips[1]!.textContent).toContain('认识无理数')
    expect(chips[1]!.getAttribute('title')).toBe(KP_FULL_2)
  })

  it('removes a chip via its × button', () => {
    const { host, edits } = mountTable()
    const remove = host.querySelector<HTMLButtonElement>('.ai-spec-table__chip-remove')!
    remove.click()
    expect(edits).toHaveLength(1)
    expect(edits[0]!.rowIndex).toBe(0)
    expect(edits[0]!.patch.knowledge_points).toEqual([KP_FULL_2])
  })

  it('resolves a uniquely matching leaf name back to the full path when adding', async () => {
    const { host, edits } = mountTable()
    const input = host.querySelector<HTMLInputElement>('input[list]')!
    input.value = '1 确定位置'
    input.dispatchEvent(new Event('input'))
    input.dispatchEvent(new Event('change'))
    await nextTick()
    expect(edits).toHaveLength(1)
    expect(edits[0]!.patch.knowledge_points).toEqual([KP_FULL_1, KP_FULL_2, KP_FULL_3])
    expect(input.value).toBe('')
  })

  it('keeps free text that does not match the catalog as-is', async () => {
    const { host, edits } = mountTable()
    const input = host.querySelector<HTMLInputElement>('input[list]')!
    input.value = '名录外知识点'
    input.dispatchEvent(new Event('input'))
    input.dispatchEvent(new Event('change'))
    await nextTick()
    expect(edits).toHaveLength(1)
    expect(edits[0]!.patch.knowledge_points).toEqual([KP_FULL_1, KP_FULL_2, '名录外知识点'])
  })

  it('offers catalog options as leaf names labelled with the full path', () => {
    const { host } = mountTable()
    const options = host.querySelectorAll<HTMLOptionElement>('datalist option')
    expect(options).toHaveLength(3)
    expect(options[0]!.value).toBe('用勾股定理解三角形')
    expect(options[0]!.getAttribute('label')).toBe(KP_FULL_1)
  })
})

describe('AssemblySpecTable lock column', () => {
  it('hides the whole lock column before selection', () => {
    const { host } = mountTable({ selections: {} })
    const headers = [...host.querySelectorAll('th')].map((th) => th.textContent)
    expect(headers).not.toContain('锁定')
    expect(host.querySelector('.ai-spec-table__lock')).toBeNull()
  })

  it('shows the lock column once selections exist', () => {
    const { host } = mountTable({ selections: { 0: [11, 12] }, lockedQuestionIds: [11] })
    const headers = [...host.querySelectorAll('th')].map((th) => th.textContent)
    expect(headers).toContain('锁定')
    expect(host.querySelector('.ai-spec-table__lock')?.textContent).toContain('1')
  })
})
