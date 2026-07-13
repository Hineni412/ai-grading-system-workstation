import { createApp } from 'vue'
import { describe, expect, it } from 'vitest'

import ReviewShortcutGuide from '../components/review/ReviewShortcutGuide.vue'

describe('ReviewShortcutGuide', () => {
  it('lists every supported workspace shortcut and no unimplemented action', () => {
    const host = document.createElement('div')
    const app = createApp(ReviewShortcutGuide)
    app.mount(host)
    const guide = host.querySelector<HTMLElement>('[aria-label="单题复核快捷键"]')

    expect(guide).not.toBeNull()
    expect(guide?.querySelectorAll('dt')).toHaveLength(6)
    expect(guide?.textContent).toContain('J / K')
    expect(guide?.textContent).toContain('上一份 / 下一份')
    expect(guide?.textContent).toContain('Enter')
    expect(guide?.textContent).toContain('确认并停留')
    expect(guide?.textContent).toContain('Shift + Enter')
    expect(guide?.textContent).toContain('确认并前进')
    expect(guide?.textContent).toContain('Z')
    expect(guide?.textContent).toContain('适应宽度')
    expect(guide?.textContent).toContain('+ / −')
    expect(guide?.textContent).toContain('缩放答卷')
    expect(guide?.textContent).toContain('/')
    expect(guide?.textContent).toContain('搜索学生')
    expect(guide?.textContent).not.toMatch(/(^|\s)R($|\s)/)

    app.unmount()
  })
})
