import { expect, test as base, type Route } from '@playwright/test'
import type { GradingWorkspace, ScanPreflight } from '../src/api/scan-grading'
import { curriculumCatalog } from './mock-curriculum'
export { curriculumCatalog } from './mock-curriculum'

export async function fulfillEmptyScanRead(route: Route): Promise<boolean> {
  const request = route.request()
  const match = new URL(request.url()).pathname.match(
    /^\/api\/sessions\/([1-9]\d*)\/(grading-workspace|scan\/student-options|scan\/preflight|review\/questions)$/,
  )
  if (request.method() !== 'GET' || !match) return false
  const sessionId = Number(match[1])
  let response: unknown
  switch (match[2]) {
    case 'grading-workspace':
      response = {
        session_id: sessionId,
        upload_batch: { batch_id: `TEST-empty-${sessionId}`, revision: 0, state: 'draft',
          files: [], file_count: 0, total_bytes: 0, frozen_at: null },
        replacement_batch: null, grading_run: null, grading_job: null, scan_analysis_job: null,
      } satisfies GradingWorkspace
      break
    case 'scan/student-options':
      response = { items: [] }
      break
    case 'scan/preflight':
      response = { revision: 0, summary: { auto_matched: 0, ready_to_grade: 0,
        issues: 0, absent_candidates: 0, total_pages: 0 }, groups: [], issues: [],
        absent_students: [], warnings: [], decisions: [], pending_issue_count: 0,
      } satisfies ScanPreflight
      break
    case 'review/questions':
      response = { items: [], total: 0 }
      break
    default:
      return false
  }
  await route.fulfill({ json: response })
  return true
}

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
    await page.route(/\/api\/sessions\/\d+\/(?:grading-workspace|scan\/student-options|scan\/preflight|review\/questions)(?:\?.*)?$/, async route => {
      if (!await fulfillEmptyScanRead(route)) await route.fallback()
    })
    await use()
    expect(unmatched, 'All mock browser API requests must have synthetic responses').toEqual([])
  }, { auto: true }],
})
