import { createApp, defineAsyncComponent, defineComponent, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../App.vue'
import { fetchSessions } from '../api/sessions'
import ComponentShowcase from '../components/design-system/ComponentShowcase.vue'
import { createAppRouter } from '../router'
import { useSessionStore } from '../stores/session'

vi.mock('../api/sessions', () => ({
  fetchSessions: vi.fn(async () => []),
}))

async function settleUi(): Promise<void> {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  document.body.innerHTML = ''
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      matches: false,
      media: '(max-width: 1279px)',
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  )
})

describe('App', () => {
  it('sanitizes a routed render failure and retries by remounting the current route', async () => {
    let attempts = 0
    const asyncRouteContent = defineAsyncComponent(async () => {
      attempts += 1
      if (attempts === 1) throw new Error('private lazy-load path and token')
      return defineComponent(() => () => h('h1', { tabindex: '-1' }, '已恢复当前页面'))
    })
    const recoveredRoute = defineComponent(() => () => h(asyncRouteContent))
    const pinia = createPinia()
    const router = createAppRouter(createMemoryHistory())
    router.addRoute({
      path: '/broken-route',
      name: 'broken-route',
      component: recoveredRoute,
      meta: { title: '故障页面', description: '故障测试', breadcrumb: '故障页面' },
    })
    await router.push('/broken-route')
    await router.isReady()
    const host = document.createElement('div')
    const app = createApp(App)
    app.config.errorHandler = vi.fn()
    app.use(pinia)
    app.use(router)
    app.mount(host)
    await settleUi()

    expect(host.querySelector('[role="alert"]')).not.toBeNull()
    expect(host.textContent).toContain('当前页面暂时无法显示')
    expect(host.textContent).not.toContain('private lazy-load path and token')
    const retry = [...host.querySelectorAll<HTMLButtonElement>('button')].find(
      (button) => button.textContent === '重新加载当前页面',
    )!
    retry.click()
    await settleUi()

    expect(host.querySelector('h1')?.textContent).toBe('已恢复当前页面')
    expect(attempts).toBe(2)
    app.unmount()
  })

  it('returns from a sanitized routed render failure to the workbench', async () => {
    const brokenRoute = defineComponent(() => () => {
      throw new Error('private render detail')
    })
    const pinia = createPinia()
    const router = createAppRouter(createMemoryHistory())
    router.addRoute({
      path: '/broken-render',
      name: 'broken-render',
      component: brokenRoute,
      meta: { title: '故障页面', description: '故障测试', breadcrumb: '故障页面' },
    })
    await router.push('/broken-render')
    await router.isReady()
    const host = document.createElement('div')
    const app = createApp(App)
    app.config.errorHandler = vi.fn()
    app.use(pinia)
    app.use(router)
    app.mount(host)
    await settleUi()

    const returnButton = [...host.querySelectorAll<HTMLButtonElement>('button')].find(
      (button) => button.textContent === '返回工作台',
    )!
    returnButton.click()
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/workbench'))
    await settleUi()

    expect(host.querySelector('#main-workspace h1')?.textContent).toBe('工作台')
    app.unmount()
  })

  it('mounts the P2-03 application shell with memory routing and Pinia', async () => {
    const pinia = createPinia()
    const router = createAppRouter(createMemoryHistory())
    await router.push('/workbench')
    await router.isReady()
    const host = document.createElement('div')
    const app = createApp(App)
    app.use(pinia)
    app.use(router)
    app.mount(host)
    await settleUi()

    expect(host.querySelector('[data-testid="app-shell"]')).not.toBeNull()
    expect(host.querySelector('[data-testid="app-topbar"]')).not.toBeNull()
    expect(host.querySelector('[data-testid="app-navigation"]')).not.toBeNull()
    expect(host.querySelector('main#main-workspace')).not.toBeNull()
    expect(host.querySelector('[data-testid="session-inspector"]')).not.toBeNull()
    const sessionStore = useSessionStore(pinia)
    expect(fetchSessions).toHaveBeenCalledTimes(1)
    expect(sessionStore.loadState).toBe('ready')
    expect(sessionStore.sessions).toEqual([])

    app.unmount()
  })

  it('keeps direct unit coverage for every P2-02 showcase section', async () => {
    const host = document.createElement('div')
    const app = createApp(ComponentShowcase)
    app.mount(host)
    await nextTick()

    expect(host.querySelector('[data-testid="design-system-showcase"]')).not.toBeNull()
    expect([...host.querySelectorAll('h2')].map((heading) => heading.textContent)).toEqual([
      '基础 Token',
      '按钮',
      '输入',
      '状态徽章',
      '空、加载与错误',
      '操作反馈',
    ])
    expect((host.querySelector('#exam-name') as HTMLInputElement | null)?.value).toBe(
      '2025—2026 学年度第二学期七年级数学期末质量监测与学情诊断测试',
    )
    expect((host.querySelector('#student-name') as HTMLInputElement | null)?.value).toBe(
      '阿布都热合曼·麦麦提艾力同学',
    )
    expect(host.querySelectorAll('[data-testid="status-badge"]')).toHaveLength(7)
    expect(host.querySelectorAll('[data-testid="state-panel"]')).toHaveLength(3)
    expect(host.querySelectorAll('[data-testid="feedback-banner"]')).toHaveLength(4)
    expect(host.querySelector('[aria-invalid="true"]')).not.toBeNull()
    expect(host.querySelector('[data-testid="disabled-field"] [disabled]')).not.toBeNull()

    app.unmount()
  })
})
