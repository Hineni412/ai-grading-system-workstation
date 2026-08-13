import { createApp } from 'vue'
import { describe, expect, it } from 'vitest'

import ReviewShortcutGuide from '../components/review/ReviewShortcutGuide.vue'

describe('ReviewShortcutGuide', () => {
  it('lists every supported workspace shortcut and no unimplemented action', () => {
    const host = document.createElement('div')
    const app = createApp(ReviewShortcutGuide)
    app.mount(host)
    const guide = host.querySelector<HTMLElement>('[aria-label="人工干预快捷键"]')

    expect(guide).not.toBeNull()
    expect(
      [...(guide?.querySelectorAll('dt') ?? [])].map((entry) => entry.textContent),
    ).toEqual(['Tab / Shift+Tab', 'Enter'])
    expect(guide?.textContent).toContain('Tab / Shift+Tab')
    expect(guide?.textContent).toContain('下一位 / 上一位')
    expect(guide?.textContent).toContain('Enter')
    expect(guide?.textContent).toContain('最后一位保存本题')

    app.unmount()
  })
})
