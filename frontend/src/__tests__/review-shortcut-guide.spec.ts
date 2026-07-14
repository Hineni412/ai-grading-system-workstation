import { createApp } from 'vue'
import { describe, expect, it } from 'vitest'

import ReviewShortcutGuide from '../components/review/ReviewShortcutGuide.vue'

describe('ReviewShortcutGuide', () => {
  it('lists every supported workspace shortcut and no unimplemented action', () => {
    const host = document.createElement('div')
    const app = createApp(ReviewShortcutGuide)
    app.mount(host)
    const guide = host.querySelector<HTMLElement>('[aria-label="批量复核快捷键"]')

    expect(guide).not.toBeNull()
    expect(guide?.querySelectorAll('dt')).toHaveLength(2)
    expect(guide?.textContent).toContain('/')
    expect(guide?.textContent).toContain('搜索学生')
    expect(guide?.textContent).toContain('J / K')
    expect(guide?.textContent).toContain('下一份 / 上一份')
    expect(guide?.textContent).not.toContain('Enter')
    expect(guide?.textContent).not.toContain('确认')

    app.unmount()
  })
})
