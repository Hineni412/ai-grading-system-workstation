import { createApp, nextTick } from 'vue'
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
      media: '(max-width: 1023px)',
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  )
})

describe('App', () => {
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
