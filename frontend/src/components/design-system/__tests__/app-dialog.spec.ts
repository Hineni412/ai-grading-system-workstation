import { beforeEach, describe, expect, it } from 'vitest';
import { createApp, defineComponent, h, nextTick, ref } from 'vue';

import AppDialog from '../AppDialog.vue'

function mountDialog(options: { description?: string; dismissible?: boolean } = {}) {
  const el = document.createElement('div')
  document.body.append(el)
  const open = ref(true)
  const dismissible = ref(options.dismissible ?? true)
  const updates: boolean[] = []
  const harness = defineComponent({
    render() {
      return h(AppDialog, {
        open: open.value,
        title: '标题',
        description: options.description,
        dismissible: dismissible.value,
        'onUpdate:open': (value: boolean) => { updates.push(value); open.value = value },
      })
    },
  })
  const app = createApp(harness)
  app.mount(el)
  return { app, el, open, dismissible, updates }
}

function dialog(): HTMLElement {
  const found = document.body.querySelector<HTMLElement>('[role="dialog"]')
  if (!found) throw new Error('dialog not open')
  return found
}

async function settle(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

beforeEach(() => {
  document.body.innerHTML = ''
})

describe('AppDialog', () => {

  it('links aria-describedby to the rendered description', async () => {
    const { app } = mountDialog({ description: '这里是对话说明。' })
    await settle()

    const content = dialog()
    const description = content.querySelector('.app-dialog__description')
    expect(description?.textContent).toBe('这里是对话说明。')
    const describedBy = content.getAttribute('aria-describedby')
    expect(describedBy).toBeTruthy()
    expect(document.getElementById(describedBy!)).toBe(description)
    app.unmount()
  })

  it('has no aria-describedby when no description is given', async () => {
    const { app } = mountDialog()
    await settle()

    const content = dialog()
    expect(content.hasAttribute('aria-describedby')).toBe(false)
    app.unmount()
  })

  it('keeps a non-dismissible dialog open and restores its close button when dismissal is allowed', async () => {
    const { app, dismissible, updates } = mountDialog({ dismissible: false })
    await settle()

    dialog().querySelector<HTMLButtonElement>('[aria-label="关闭"]')!.click()
    await settle()
    expect(document.body.querySelector('[role="dialog"]')).not.toBeNull()
    expect(updates).toEqual([])
    expect(dialog().querySelector<HTMLButtonElement>('[aria-label="关闭"]')?.disabled).toBe(true)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    document.body.dispatchEvent(new MouseEvent('pointerdown', { button: 0, bubbles: true }))
    await settle()
    expect(updates).toEqual([])

    dismissible.value = true
    await settle()
    dialog().querySelector<HTMLButtonElement>('[aria-label="关闭"]')!.click()
    await settle()
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
    expect(updates).toEqual([false])
    app.unmount()
  })

  it('allows the parent to close a non-dismissible dialog', async () => {
    const { app, open, updates } = mountDialog({ dismissible: false })
    await settle()
    expect(document.body.querySelector('[role="dialog"]')).not.toBeNull()

    open.value = false
    await settle()
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
    expect(updates).toEqual([])
    app.unmount()
  })

})
