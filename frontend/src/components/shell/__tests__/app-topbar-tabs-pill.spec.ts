import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AppShell from '../../../layouts/AppShell.vue'
import { createAppRouter } from '../../../router'
import { useSessionStore } from '../../../stores/session'

async function settleUi(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

async function mountShell(path = '/teaching-prep?view=overview') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useSessionStore()
  await store.initialize(async () => [])
  vi.spyOn(store, 'initialize').mockResolvedValue()
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()

  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(AppShell)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  await settleUi()
  return { app, host, router }
}

function mockTabLayout(nav: HTMLElement): HTMLButtonElement[] {
  const buttons = [...nav.querySelectorAll<HTMLButtonElement>('button')]
  buttons.forEach((button, index) => {
    Object.defineProperty(button, 'offsetLeft', { configurable: true, value: index * 104 })
    Object.defineProperty(button, 'offsetWidth', { configurable: true, value: 100 })
  })
  return buttons
}

beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('workspace topbar tabs sliding pill', () => {
  it('keeps link semantics and hides the pill in jsdom where layout is zero', async () => {
    const { app, host } = await mountShell()

    const nav = host.querySelector<HTMLElement>('#workspace-topbar-tabs .tp-workspace-tabs')!
    expect(nav).not.toBeNull()
    const current = nav.querySelector('button[aria-current="page"]')
    expect(current?.classList.contains('is-current')).toBe(true)
    // jsdom offsetWidth 为 0：pill 直接退化为不渲染，不动画不报错
    expect(nav.querySelector('.tp-workspace-tabs__pill')).toBeNull()

    app.unmount()
  })

  it('positions the pill on the current tab and slides it when the route changes', async () => {
    const { app, host } = await mountShell()
    const nav = host.querySelector<HTMLElement>('#workspace-topbar-tabs .tp-workspace-tabs')!
    const buttons = mockTabLayout(nav)

    window.dispatchEvent(new Event('resize'))
    await settleUi()

    const pill = nav.querySelector<HTMLElement>('.tp-workspace-tabs__pill')!
    expect(pill).not.toBeNull()
    expect(pill.style.left).toBe('0px')
    expect(pill.style.width).toBe('100px')

    buttons[1]!.click()
    await vi.waitFor(() => {
      expect(buttons[1]!.getAttribute('aria-current')).toBe('page')
    })
    await settleUi()

    expect(pill.style.left).toBe('104px')

    app.unmount()
  })
})
