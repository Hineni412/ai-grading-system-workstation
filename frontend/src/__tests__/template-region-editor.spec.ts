import { createApp, nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TemplateRegionEditor, {
  type EditorState,
} from '../components/template-regions/TemplateRegionEditor.vue'

function editorState(): EditorState {
  return {
    revision: 1,
    regions: [{
      region_uuid: 'r1', page: 'front', region_order: 1,
      x: 10, y: 20, w: 100, h: 80,
      mapped_question_id: 'Q1', mapping_status: 'manual',
      multi_region_confirmed: false,
    }],
  }
}

async function mountEditor(readOnly = false) {
  const state = ref(editorState())
  const updated = vi.fn((value: EditorState) => { state.value = value })
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(TemplateRegionEditor, {
    modelValue: state.value,
    images: {
      front: { url: 'data:image/gif;base64,R0lGODlhAQABAAAAACw=', width: 1000, height: 1400 },
      back: { url: 'data:image/gif;base64,R0lGODlhAQABAAAAACw=', width: 2480, height: 3508 },
    },
    manualQuestionOptions: [{ value: '', label: '未绑定' }, { value: 'Q2', label: 'Q2' }],
    automaticCandidates: ['Q1', 'Q2'], issues: [], readOnly,
    'onUpdate:modelValue': updated,
  })
  app.mount(host)
  await nextTick()
  return { app, host, updated }
}

function pointer(type: string, values: Record<string, number>): Event {
  const event = new Event(type, { bubbles: true, cancelable: true })
  for (const [name, value] of Object.entries(values)) {
    Object.defineProperty(event, name, { value })
  }
  return event
}

beforeEach(() => { document.body.innerHTML = '' })

describe('TemplateRegionEditor adapter', () => {
  it('emits the full editor state after a public mapping interaction', async () => {
    const { app, host, updated } = await mountEditor()
    host.querySelector<HTMLButtonElement>('[data-action="drawer"]')!.click()
    const select = host.querySelector<HTMLSelectElement>('.mapping-select')!
    select.value = 'Q2'
    select.dispatchEvent(new Event('change', { bubbles: true }))

    expect(updated).toHaveBeenCalledOnce()
    expect(updated.mock.calls[0]![0].revision).toBe(2)
    expect(updated.mock.calls[0]![0].regions[0]!.mapped_question_id).toBe('Q2')
    app.unmount()
  })

  it('keeps page and zoom viewing available but disables confirmed editing', async () => {
    const { app, host, updated } = await mountEditor(true)
    host.querySelector<HTMLButtonElement>('[data-action="drawer"]')!.click()

    expect(host.querySelector<HTMLButtonElement>('[data-page="back"]')!.disabled).toBe(false)
    expect(host.querySelector<HTMLButtonElement>('[data-action="actual-size"]')!.disabled).toBe(false)
    expect(host.querySelector<HTMLButtonElement>('[data-action="finish"]')!.disabled).toBe(true)
    expect(host.querySelector<HTMLSelectElement>('.mapping-select')!.disabled).toBe(true)
    expect(updated).not.toHaveBeenCalled()
    app.unmount()
  })

  it('draws in original image pixels and supports undo through public controls', async () => {
    const { app, host, updated } = await mountEditor()
    const canvas = host.querySelector<SVGElement>('[data-role="canvas"]')!
    Object.defineProperty(canvas, 'getBoundingClientRect', {
      value: () => ({ x: 0, y: 0, width: 500, height: 700, top: 0, left: 0,
        right: 500, bottom: 700, toJSON: () => ({}) }),
    })
    Object.defineProperty(canvas, 'setPointerCapture', { value: vi.fn() })
    Object.defineProperty(canvas, 'hasPointerCapture', { value: () => true })
    Object.defineProperty(canvas, 'releasePointerCapture', { value: vi.fn() })
    host.querySelector<HTMLButtonElement>('[data-action="create"]')!.click()
    canvas.dispatchEvent(pointer('pointerdown', { button: 0, pointerId: 1, clientX: 50, clientY: 70 }))
    canvas.dispatchEvent(pointer('pointermove', { pointerId: 1, clientX: 150, clientY: 210 }))
    canvas.dispatchEvent(pointer('pointerup', { pointerId: 1, clientX: 150, clientY: 210 }))

    const created = updated.mock.calls.at(-1)![0]
    expect(created.regions.at(-1)).toMatchObject({ x: 100, y: 140, w: 200, h: 280 })
    host.querySelector<HTMLButtonElement>('[data-action="undo"]')!.click()
    expect(updated.mock.calls.at(-1)![0].regions).toHaveLength(1)
    app.unmount()
  })
})
