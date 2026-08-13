import { createApp, h, nextTick, type App } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AppButton from '../components/design-system/AppButton.vue'

const mountedApps: App[] = []

function mountButton(
  props: Record<string, unknown> = {},
  onClick?: (event: MouseEvent) => void,
) {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp({
    setup: () => () => h(AppButton, { ...props, onClick }, { default: () => '确认并开始批改' }),
  })
  app.mount(host)
  mountedApps.push(app)
  return { host }
}

afterEach(() => {
  for (const app of mountedApps.splice(0)) app.unmount()
  document.body.innerHTML = ''
  vi.useRealTimers()
})

describe('AppButton', () => {
  it('keeps the existing hooks and renders no ripple by default', () => {
    const { host } = mountButton({ variant: 'primary' })
    const button = host.querySelector<HTMLButtonElement>('button.app-button')!
    expect(button).not.toBeNull()
    expect(button.getAttribute('data-variant')).toBe('primary')
    expect(button.getAttribute('type')).toBe('button')
    expect(button.textContent).toContain('确认并开始批改')
    expect(host.querySelector('.app-button__ripples')).toBeNull()

    button.click()
    expect(host.querySelector('.app-button__ripple')).toBeNull()
  })

  it('creates one ripple on click when ripple is enabled and removes it after the duration', async () => {
    vi.useFakeTimers()
    const clicks: MouseEvent[] = []
    const { host } = mountButton({ variant: 'primary', ripple: true }, (event) => clicks.push(event))
    const button = host.querySelector<HTMLButtonElement>('button.app-button')!
    expect(host.querySelector('.app-button__ripples')).not.toBeNull()

    button.click()
    await nextTick()
    expect(clicks).toHaveLength(1)
    expect(host.querySelectorAll('.app-button__ripple')).toHaveLength(1)

    await vi.advanceTimersByTimeAsync(600)
    await nextTick()
    expect(host.querySelectorAll('.app-button__ripple')).toHaveLength(0)
  })

  it('does not ripple while loading and keeps the loading hooks', () => {
    const { host } = mountButton({ variant: 'primary', ripple: true, loading: true })
    const button = host.querySelector<HTMLButtonElement>('button.app-button')!
    expect(button.disabled).toBe(true)
    expect(button.getAttribute('aria-busy')).toBe('true')
    expect(button.classList.contains('is-loading')).toBe(true)

    button.click()
    expect(host.querySelectorAll('.app-button__ripple')).toHaveLength(0)
  })

  it('does not ripple when disabled even with ripple enabled', () => {
    const { host } = mountButton({ variant: 'primary', ripple: true, disabled: true })
    const button = host.querySelector<HTMLButtonElement>('button.app-button')!
    button.click()
    expect(host.querySelectorAll('.app-button__ripple')).toHaveLength(0)
  })
})
