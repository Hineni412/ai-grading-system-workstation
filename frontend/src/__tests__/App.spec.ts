
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineAsyncComponent, defineComponent, h, nextTick } from 'vue'
import { createMemoryHistory } from 'vue-router'

import App from '../App.vue'
import type { ReviewItem } from '../api/review'
import { createAppRouter } from '../router'
import { useReviewDraftStore } from '../stores/review-drafts'

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

  it('keeps the dirty-draft unload warning active outside the grading route', async () => {
    const pinia = createPinia()
    const router = createAppRouter(createMemoryHistory())
    await router.push('/design-system')
    await router.isReady()
    const host = document.createElement('div')
    const app = createApp(App)
    app.use(pinia)
    app.use(router)
    app.mount(host)
    await settleUi()

    const cleanEvent = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(cleanEvent)
    expect(cleanEvent.defaultPrevented).toBe(false)

    const draftStore = useReviewDraftStore(pinia)
    const item = {
      session_id: 7,
      result_id: 11,
      detail_id: 21,
      question_id: 'Q1',
      student_code: 'ANON-1',
      student_name: '匿名学生1',
      class_name: '匿名班级',
      score_awarded: 3,
      max_score: 5,
      deduction_reason: null,
      error_category: null,
      error_summary: null,
      confidence_score: 70,
      needs_review: true,
      candidate_scores: [],
      metadata: {},
      media: {
        crop_url: '/api/crop',
        original_front_url: '/api/front',
        original_back_url: '/api/back',
        annotated_front_url: '/api/annotated-front',
        annotated_back_url: '/api/annotated-back',
      },
    } satisfies ReviewItem
    const draft = draftStore.ensureDraft(item)
    draftStore.updateScore(draft.key, '4')
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)

    expect(event.defaultPrevented).toBe(true)
    app.unmount()
  })

  it('surfaces a rejected lazy route and retries the real route factory', async () => {
    let attempts = 0
    const pinia = createPinia()
    const router = createAppRouter(createMemoryHistory())
    router.addRoute({
      path: '/lazy-route-failure',
      name: 'lazy-route-failure',
      component: async () => {
        attempts += 1
        if (attempts === 1) throw new Error('private lazy route path and token')
        return defineComponent(() => () => h('h1', { tabindex: '-1' }, '懒加载页面已恢复'))
      },
      meta: { title: '懒加载故障', description: '故障测试', breadcrumb: '懒加载故障' },
    })
    await router.push('/grading')
    await router.isReady()
    const host = document.createElement('div')
    const app = createApp(App)
    app.config.errorHandler = vi.fn()
    app.use(pinia)
    app.use(router)
    app.mount(host)
    await settleUi()

    await expect(router.push('/lazy-route-failure')).rejects.toThrow(
      'private lazy route path and token',
    )
    await settleUi()

    expect(host.querySelector('[role="alert"]')).not.toBeNull()
    expect(host.textContent).toContain('当前页面暂时无法显示')
    expect(host.textContent).not.toContain('private lazy route path and token')
    const retry = [...host.querySelectorAll<HTMLButtonElement>('button')].find(
      (button) => button.textContent === '重新加载当前页面',
    )!
    retry.click()
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/lazy-route-failure'))
    await settleUi()

    expect(host.querySelector('h1')?.textContent).toBe('懒加载页面已恢复')
    expect(attempts).toBe(2)
    app.unmount()
  })

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

})
