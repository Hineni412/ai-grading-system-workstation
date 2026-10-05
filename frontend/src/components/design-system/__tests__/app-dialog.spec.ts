import { beforeEach, describe, expect, it } from 'vitest';
import { createApp, defineComponent, h, nextTick } from 'vue';

import AppDialog from '../AppDialog.vue'

function mountDialog(description?: string): { app: ReturnType<typeof createApp>; el: HTMLElement } {
  const el = document.createElement('div')
  document.body.append(el)
  const harness = defineComponent({
    render() {
      return h(AppDialog, { open: true, title: '标题', ...(description ? { description } : {}) })
    },
  })
  const app = createApp(harness)
  app.mount(el)
  return { app, el }
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
    const { app } = mountDialog('这里是对话说明。')
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

})
