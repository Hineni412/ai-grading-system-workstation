import { createApp, defineComponent, h, nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import FeedbackBanner from '../FeedbackBanner.vue'
import StatePanel from '../StatePanel.vue'

async function mount(component: ReturnType<typeof defineComponent>) {
  const host = document.createElement('div')
  createApp(component).mount(host)
  await nextTick()
  return host
}

describe('StatePanel', () => {
  it('announces loading and keeps a stable three-line skeleton', async () => {
    const host = await mount(
      defineComponent({
        setup: () => () =>
          h(StatePanel, {
            kind: 'loading',
            title: '正在读取待复核记录',
            description: '已保留当前筛选条件。',
          }),
      }),
    )

    const panel = host.querySelector('[data-testid="state-panel"]')
    expect(panel?.getAttribute('aria-busy')).toBe('true')
    expect(host.querySelectorAll('.state-panel__skeleton')).toHaveLength(3)
  })

  it('explains an empty state without exposing a retry action', async () => {
    const host = await mount(
      defineComponent({
        setup: () => () =>
          h(StatePanel, {
            kind: 'empty',
            title: '暂无待复核答卷',
            description: '可以调整筛选条件，或返回批改进度查看处理状态。',
          }),
      }),
    )

    expect(host.textContent).toContain('调整筛选条件')
    expect(host.querySelector('button')).toBeNull()
  })

  it('reports failure impact and emits retry', async () => {
    const onRetry = vi.fn()
    const host = await mount(
      defineComponent({
        setup: () => () =>
          h(StatePanel, {
            kind: 'error',
            title: '复核记录加载失败',
            description: '当前列表没有更新。',
            detail: '教师已填写的最终分仍保存在草稿中。',
            retryLabel: '重新加载记录',
            onRetry,
          }),
      }),
    )

    expect(host.querySelector('[role="alert"]')?.textContent).toContain('仍保存在草稿中')
    host.querySelector('button')?.click()
    expect(onRetry).toHaveBeenCalledOnce()
  })
})

describe('FeedbackBanner', () => {
  it.each([
    ['info', 'status'],
    ['success', 'status'],
    ['warning', 'alert'],
    ['error', 'alert'],
  ] as const)('uses %s feedback with a %s live region', async (tone, role) => {
    const host = await mount(
      defineComponent({
        setup: () => () =>
          h(FeedbackBanner, {
            tone,
            title: `${tone} 标题`,
            description: `${tone} 说明`,
          }),
      }),
    )

    const banner = host.querySelector('[data-testid="feedback-banner"]')
    expect(banner?.getAttribute('data-tone')).toBe(tone)
    expect(banner?.getAttribute('role')).toBe(role)
    expect(banner?.textContent).toContain(`${tone} 说明`)
  })

  it('emits the optional action and dismiss events', async () => {
    const onAction = vi.fn()
    const onDismiss = vi.fn()
    const host = await mount(
      defineComponent({
        setup: () => () =>
          h(FeedbackBanner, {
            tone: 'error',
            title: '保存失败',
            description: '本次修改尚未保存。',
            actionLabel: '重新保存',
            dismissible: true,
            onAction,
            onDismiss,
          }),
      }),
    )

    const buttons = host.querySelectorAll('button')
    expect([...buttons].map((button) => button.textContent)).toEqual(['重新保存', '关闭提示'])
    buttons[0]?.click()
    buttons[1]?.click()
    expect(onAction).toHaveBeenCalledOnce()
    expect(onDismiss).toHaveBeenCalledOnce()
  })
})
