import { expect, test as base } from '@playwright/test'
import { curriculumCatalog } from './mock-curriculum'
export { curriculumCatalog } from './mock-curriculum'

// Existing page mocks register tests in their spec modules. The jsdom setup
// cannot intercept browser requests, so share only the browser isolation here.
export const test = base.extend<{ apiIsolation: void }>({
  apiIsolation: [async ({ page }, use) => {
    const unmatched: string[] = []
    await page.route(/^https?:\/\/[^/]+\/api\//, async route => {
      unmatched.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`)
      await route.fulfill({ status: 404, json: { error: {
        code: 'TEST-unmocked-api', message: 'Missing synthetic API response', request_id: 'TEST',
      } } })
    })
    await page.route(/\/api\/question-bank\/curriculum(?:\?.*)?$/, route => route.fulfill({ json: curriculumCatalog() }))
    await page.route(/\/api\/jobs(?:\?.*)?$/, route => route.fulfill({ json: { items: [], total: 0 } }))
    await use()
    expect(unmatched, 'All mock browser API requests must have synthetic responses').toEqual([])
  }, { auto: true }],
})
