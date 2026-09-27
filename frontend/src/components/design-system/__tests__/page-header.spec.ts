import { createApp, defineComponent, h, nextTick } from 'vue'
import { describe, expect, it } from 'vitest'

import PageHeader from '../PageHeader.vue'

async function mount(options: { titleId?: string; slots?: Record<string, () => unknown> } = {}) {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(defineComponent({
    setup: () => () =>
      h(PageHeader, { title: '考试配置', titleId: options.titleId }, options.slots),
  }))
  app.mount(host)
  await nextTick()
  return { host, unmount: () => app.unmount() }
}

describe('PageHeader', () => {
  it('renders a focusable h1 with the optional id and keeps slots inline', async () => {
    const { host, unmount } = await mount({
      titleId: 'config-title',
      slots: { meta: () => '草稿', actions: () => h('button', { type: 'button' }, '保存') },
    })
    const h1 = host.querySelector('h1')
    expect(h1?.textContent).toBe('考试配置')
    expect(h1?.getAttribute('tabindex')).toBe('-1')
    expect(h1?.id).toBe('config-title')
    expect(host.querySelector('.page-header__meta')?.textContent).toContain('草稿')
    expect(host.querySelector('.page-header__actions button')?.textContent).toBe('保存')
    unmount()
  })

  it('omits the id and empty slot containers when not provided', async () => {
    const { host, unmount } = await mount()
    expect(host.querySelector('h1')?.hasAttribute('id')).toBe(false)
    expect(host.querySelector('.page-header__meta')).toBeNull()
    expect(host.querySelector('.page-header__actions')).toBeNull()
    unmount()
  })

  it('marks the header scrolled once the sentinel leaves the workspace top', async () => {
    const { host, unmount } = await mount()
    const header = host.querySelector('.page-header')!
    expect(header.classList.contains('page-header--scrolled')).toBe(false)
    unmount()
  })
})
