import { createApp, nextTick, type App } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import FileTreeFolder from './FileTreeFolder.vue'

const apps: App[] = []

function mount(props: Record<string, unknown>, options: { header?: string; children?: string } = {}) {
  const header = options.header ?? ''
  const children = options.children ?? '<p class="demo-child">子级条目</p>'
  const headerSlot = header ? `<template #header="{ expanded }">${header}</template>` : ''
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp({
    components: { FileTreeFolder },
    setup: () => ({ props }),
    template: `<FileTreeFolder v-bind="props">${headerSlot}${children}</FileTreeFolder>`,
  })
  app.mount(host)
  apps.push(app)
  return host
}

function trigger(host: HTMLElement): HTMLButtonElement {
  const button = host.querySelector<HTMLButtonElement>('.file-tree-folder__trigger')
  if (!button) throw new Error('trigger not found')
  return button
}

afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount())
  document.body.innerHTML = ''
})

describe('FileTreeFolder', () => {
  it('renders the folder name and children immediately when defaultExpanded', () => {
    const host = mount({ name: '课件', defaultExpanded: true })

    expect(host.textContent).toContain('课件')
    expect(host.querySelector('.demo-child')).not.toBeNull()
    expect(host.querySelector('.file-tree-folder__indicator')).not.toBeNull()
    expect(trigger(host).getAttribute('aria-expanded')).toBe('true')
  })

  it('stays collapsed by default and toggles children on header click', async () => {
    const host = mount({ name: '书' })

    expect(host.querySelector('.demo-child')).toBeNull()
    expect(trigger(host).getAttribute('aria-expanded')).toBe('false')

    trigger(host).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(host.querySelector('.demo-child')).not.toBeNull()
    expect(trigger(host).getAttribute('aria-expanded')).toBe('true')

    trigger(host).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(host.querySelector('.demo-child')).toBeNull()
    expect(trigger(host).getAttribute('aria-expanded')).toBe('false')
  })

  it('passes the expanded state to the header slot', async () => {
    const host = mount(
      { name: '课时树' },
      { header: '<span class="demo-state">{{ expanded ? "已展开" : "已折叠" }}</span>' },
    )

    expect(host.querySelector('.demo-state')?.textContent).toBe('已折叠')
    trigger(host).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(host.querySelector('.demo-state')?.textContent).toBe('已展开')
  })
})
