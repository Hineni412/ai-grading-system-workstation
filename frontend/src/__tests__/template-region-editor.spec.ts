import { createApp, h, nextTick, ref } from 'vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import TemplateRegionEditor, { type EditorState } from '../components/template-regions/TemplateRegionEditor.vue';

function editorState(): EditorState {
  return {
    revision: 1,
    active_page: 'front',
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
  const saveStatus = ref('草稿')
  const issues = ref<Array<{ code: string; message: string }>>([])
  const host = document.createElement('div')
  document.body.append(host)
  const stableProps = {
    images: {
      front: { url: 'data:image/gif;base64,R0lGODlhAQABAAAAACw=', width: 1000, height: 1400 },
      back: { url: 'data:image/gif;base64,R0lGODlhAQABAAAAACw=', width: 2480, height: 3508 },
    },
    manualQuestionOptions: [{ value: '', label: '未绑定' }, { value: 'Q2', label: 'Q2' }],
    automaticCandidates: ['Q1', 'Q2'], readOnly,
    'onUpdate:modelValue': updated,
  }
  const app = createApp({ render: () => h(TemplateRegionEditor, {
    ...stableProps, modelValue: state.value, activePage: state.value.active_page,
    saveStatus: saveStatus.value, issues: issues.value,
  }) })
  app.mount(host)
  await nextTick()
  return { app, host, updated, state, saveStatus, issues }
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

  it('keeps drawing after selecting, moving, resizing, page echo and save updates, until Escape', async () => {
    const { app, host, state, saveStatus, issues } = await mountEditor()
    const canvas = host.querySelector<SVGElement>('[data-role="canvas"]')!
    Object.defineProperty(canvas, 'getBoundingClientRect', { value: () => ({
      x: 0, y: 0, width: 1000, height: 1400, top: 0, left: 0,
    }) })
    Object.defineProperty(canvas, 'setPointerCapture', { value: vi.fn() })
    Object.defineProperty(canvas, 'hasPointerCapture', { value: () => true })
    Object.defineProperty(canvas, 'releasePointerCapture', { value: vi.fn() })
    const create = host.querySelector<HTMLButtonElement>('[data-action="create"]')!
    const drag = (target: Element, x: number, y: number, toX = x, toY = y) => {
      target.dispatchEvent(pointer('pointerdown', { button: 0, pointerId: 1, clientX: x, clientY: y }))
      canvas.dispatchEvent(pointer('pointermove', { pointerId: 1, clientX: toX, clientY: toY }))
      canvas.dispatchEvent(pointer('pointerup', { pointerId: 1, clientX: toX, clientY: toY }))
    }
    create.click()
    drag(host.querySelector('.region-box')!, 40, 50)
    expect(create.classList).toContain('is-active')
    drag(host.querySelector('.region-box')!, 40, 50, 80, 90)
    expect(state.value.regions[0]).toMatchObject({ x: 50, y: 60 })
    drag(host.querySelector('[data-handle="se"]')!, 150, 140, 170, 160)
    expect(state.value.regions[0]).toMatchObject({ w: 120, h: 100 })
    expect(create.classList).toContain('is-active')
    host.querySelector<HTMLButtonElement>('[data-page="back"]')!.click()
    drag(canvas, 200, 200, 300, 300)
    const drawn = host.querySelector('.region-box')
    saveStatus.value = '草稿已保存'
    await nextTick(); await nextTick()
    expect(host.querySelector('.region-box')).toBe(drawn)
    expect(create.classList).toContain('is-active')
    expect(host.querySelector('[data-role="status-save"]')?.textContent).toContain('草稿已保存')
    drag(canvas, 400, 400, 500, 500)
    expect(state.value.regions.filter(region => region.page === 'back')).toHaveLength(2)
    issues.value = [{ code: 'unbound', message: '请核对绑定' }]
    await nextTick(); await nextTick()
    expect(create.classList).toContain('is-active')
    canvas.dispatchEvent(pointer('pointerdown', { button: 0, pointerId: 1, clientX: 600, clientY: 600 }))
    canvas.dispatchEvent(pointer('pointermove', { pointerId: 1, clientX: 700, clientY: 700 }))
    host.querySelector('[data-role="editor-root"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    canvas.dispatchEvent(pointer('pointerup', { pointerId: 1, clientX: 700, clientY: 700 }))
    expect(create.classList).not.toContain('is-active')
    expect(state.value.regions).toHaveLength(3)
    drag(canvas, 600, 600, 700, 700)
    expect(state.value.regions).toHaveLength(3)
    app.unmount()
  })

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
