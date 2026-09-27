import { createApp, nextTick, ref } from 'vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import TemplateRegionEditor, { type EditorState } from '../components/template-regions/TemplateRegionEditor.vue';

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

  it('draws in original image pixels, auto-binds the next question and stays in create mode', async () => {
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

    const created = updated.mock.calls[updated.mock.calls.length - 1]![0]
    expect(created.regions[created.regions.length - 1]).toMatchObject({
      x: 100, y: 140, w: 200, h: 280,
      mapped_question_id: 'Q2', mapping_status: 'auto',
    })
    expect(host.querySelector<HTMLButtonElement>('[data-action="create"]')?.classList)
      .toContain('is-active')

    canvas.dispatchEvent(pointer('pointerdown', {
      button: 0, pointerId: 2, clientX: 180, clientY: 240,
    }))
    canvas.dispatchEvent(pointer('pointermove', {
      pointerId: 2, clientX: 230, clientY: 310,
    }))
    canvas.dispatchEvent(pointer('pointerup', {
      pointerId: 2, clientX: 230, clientY: 310,
    }))
    expect(updated.mock.calls[updated.mock.calls.length - 1]![0].regions).toHaveLength(3)

    host.querySelector<HTMLButtonElement>('[data-action="undo"]')!.click()
    expect(updated.mock.calls[updated.mock.calls.length - 1]![0].regions).toHaveLength(2)
    app.unmount()
  })

})
