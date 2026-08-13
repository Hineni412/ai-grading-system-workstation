import { createApp, type App } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import TimelineItem from './TimelineItem.vue'

const apps: App[] = []

function mount(props: Record<string, unknown>, slot = '<p>任务正文</p>') {
  const host = document.createElement('div')
  document.body.append(host)
  const wrapper = createApp({
    components: { TimelineItem },
    setup: () => ({ props }),
    template: `<TimelineItem v-bind="props">${slot}</TimelineItem>`,
  })
  wrapper.mount(host)
  apps.push(wrapper)
  return host
}

afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount())
  document.body.innerHTML = ''
})

describe('TimelineItem', () => {
  it('renders the slot content with a node and a neutral tone by default', () => {
    const host = mount({})

    const item = host.querySelector('.timeline-item')
    expect(item?.getAttribute('data-tone')).toBe('neutral')
    expect(item?.querySelector('.timeline-item__node')).not.toBeNull()
    expect(host.querySelector('.timeline-item__content')?.textContent).toContain('任务正文')
  })

  it.each(['info', 'success', 'warning', 'danger'] as const)(
    'exposes the %s tone as a data attribute for status-colored nodes',
    (tone) => {
      const host = mount({ tone })
      expect(host.querySelector('.timeline-item')?.getAttribute('data-tone')).toBe(tone)
    },
  )

  it('keeps the node hidden from assistive technology', () => {
    const host = mount({ tone: 'success' })
    expect(host.querySelector('.timeline-item__node')?.getAttribute('aria-hidden')).toBe('true')
  })
})
