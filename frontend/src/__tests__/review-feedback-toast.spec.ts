import { createApp, h, nextTick, reactive, type App } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ReviewFeedbackToast from '../components/review/ReviewFeedbackToast.vue'

type Tone = 'success' | 'warning' | 'error'

const mountedApps: App[] = []

function mountToast(message = '确认失败，教师草稿已保留。', tone: Tone = 'error') {
  const state = reactive({ message, tone })
  const dismiss = vi.fn(() => { state.message = '' })
  const host = document.createElement('div')
  const app = createApp({
    setup: () => () => h(ReviewFeedbackToast, {
      message: state.message,
      tone: state.tone,
      onDismiss: dismiss,
    }),
  })
  app.mount(host)
  mountedApps.push(app)
  return { dismiss, host, state }
}

afterEach(() => {
  for (const app of mountedApps.splice(0)) app.unmount()
  vi.useRealTimers()
  document.querySelectorAll('[data-testid="review-feedback-toast"]').forEach((node) => node.remove())
})

describe('ReviewFeedbackToast', () => {
  it('teleports one error alert to body and closes without focusing it', async () => {
    const { dismiss, host } = mountToast()
    const toast = document.body.querySelector<HTMLElement>('[data-testid="review-feedback-toast"]')!

    expect(toast).not.toBeNull()
    expect(host.contains(toast)).toBe(false)
    expect(toast.getAttribute('role')).toBe('alert')
    expect(toast.getAttribute('aria-live')).toBe('assertive')
    expect(toast.getAttribute('aria-atomic')).toBe('true')
    expect(toast).not.toBe(document.activeElement)

    toast.querySelector<HTMLButtonElement>('[aria-label="关闭通知"]')!.click()
    await nextTick()

    expect(dismiss).toHaveBeenCalledTimes(1)
    expect(document.body.querySelector('[data-testid="review-feedback-toast"]')).toBeNull()
  })

  it('dismisses success after 4000ms but keeps warning and error until closed', async () => {
    vi.useFakeTimers()
    const success = mountToast('教师最终分已确认。', 'success')
    const successToast = document.body.querySelector<HTMLElement>('[data-testid="review-feedback-toast"]')!
    expect(successToast.getAttribute('role')).toBe('status')
    expect(successToast.getAttribute('aria-live')).toBe('polite')

    await vi.advanceTimersByTimeAsync(3999)
    expect(success.dismiss).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(success.dismiss).toHaveBeenCalledTimes(1)

    for (const tone of ['warning', 'error'] as const) {
      const sticky = mountToast('需要教师处理。', tone)
      await vi.advanceTimersByTimeAsync(10000)
      expect(sticky.dismiss).not.toHaveBeenCalled()
    }
  })

  it('restarts the success timer and keeps one toast when a new message replaces the old one', async () => {
    vi.useFakeTimers()
    const mounted = mountToast('第一次成功', 'success')
    await vi.advanceTimersByTimeAsync(3000)

    mounted.state.message = '第二次成功'
    await nextTick()

    expect(document.body.querySelectorAll('[data-testid="review-feedback-toast"]')).toHaveLength(1)
    expect(document.body.textContent).toContain('第二次成功')
    await vi.advanceTimersByTimeAsync(3999)
    expect(mounted.dismiss).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(mounted.dismiss).toHaveBeenCalledTimes(1)
  })
})
