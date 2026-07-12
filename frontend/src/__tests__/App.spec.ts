import { createApp, nextTick } from 'vue'
import { describe, expect, it } from 'vitest'

import App from '../App.vue'

describe('App', () => {
  it('renders every P2-02 showcase section and extreme content state', async () => {
    const host = document.createElement('div')
    createApp(App).mount(host)
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
  })
})
