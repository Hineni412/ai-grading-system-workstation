import { createApp, defineComponent, nextTick, ref, type App } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import TypewriterText from './TypewriterText.vue'

const apps: App[] = []

function mount(props: Record<string, unknown>) {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(TypewriterText, props)
  app.mount(host)
  apps.push(app)
  return host
}

afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount())
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('TypewriterText', () => {
  it('renders the full text immediately when requestAnimationFrame is unavailable (jsdom)', () => {
    const host = mount({ text: '合成AI整理全文', tag: 'p' })

    const paragraph = host.querySelector('p.typewriter-text')
    expect(paragraph).not.toBeNull()
    expect(paragraph?.textContent).toBe('合成AI整理全文')
    expect(host.querySelector('.typewriter-text__cursor')).toBeNull()
  })

  it('renders the full text immediately when speed is zero', () => {
    vi.stubGlobal('requestAnimationFrame', vi.fn())
    const host = mount({ text: '立即全文', speed: 0 })

    expect(host.textContent).toBe('立即全文')
    expect(host.querySelector('.typewriter-text__cursor')).toBeNull()
  })

  it('types character by character with a cursor, then settles on the full text', async () => {
    const frames: FrameRequestCallback[] = []
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      frames.push(callback)
      return frames.length
    })
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
    vi.stubGlobal('navigator', { userAgent: 'Mozilla/5.0 (TestBrowser)' })
    const host = mount({ text: 'ABCDEF', speed: 30 })

    expect(host.textContent).toContain('▍')
    expect(host.textContent).not.toContain('A')

    frames.shift()!(0)
    await nextTick()
    expect(host.querySelector('.typewriter-text')?.textContent).toBe('▍')

    frames.shift()!(90)
    await nextTick()
    expect(host.querySelector('.typewriter-text')?.textContent).toBe('ABC▍')

    frames.shift()!(1000)
    await nextTick()
    expect(host.querySelector('.typewriter-text')?.textContent).toBe('ABCDEF')
    expect(host.querySelector('.typewriter-text__cursor')).toBeNull()
  })

  it('replays once when a new text arrives', async () => {
    const text = ref('第一段')
    const Harness = defineComponent({
      components: { TypewriterText },
      setup: () => ({ text }),
      template: '<TypewriterText :text="text" />',
    })
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(Harness)
    app.mount(host)
    apps.push(app)

    expect(host.textContent).toBe('第一段')
    text.value = '第二段全文'
    await nextTick()
    expect(host.textContent).toBe('第二段全文')
  })
})
