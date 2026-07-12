import { createApp } from 'vue'
import { describe, expect, it } from 'vitest'

import App from '../App.vue'

describe('App', () => {
  it('renders the frontend readiness marker', () => {
    const host = document.createElement('div')

    createApp(App).mount(host)

    expect(host.querySelector('[data-testid="frontend-ready"]')?.textContent).toBe(
      '前端工程已就绪',
    )
  })
})
