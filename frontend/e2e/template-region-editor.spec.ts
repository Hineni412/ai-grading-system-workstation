import { expect, test, type Page, type Route } from '@playwright/test';

const fingerprint = 'a'.repeat(64)
const session = { id: 7, name: '匿名数学考试', status: 'created', is_deleted: false,
  deleted_at: null, created_at: null, updated_at: null }
const template = {
  session_id: 7, template_id: 3, template_fingerprint: fingerprint, first_page_role: 'back',
  pages: {
    front: { url: '/api/sessions/7/template/pages/front', width: 1000, height: 1400 },
    back: { url: '/api/sessions/7/template/pages/back', width: 2480, height: 3508 },
  }, is_confirmed: false, regions_snapshot_pending: false,
}

function apiError(route: Route, code: string, message: string, status = 404) {
  const requestId = route.request().headers()['x-request-id'] ?? 'browser-request'
  return { status, headers: { 'x-request-id': requestId },
    contentType: 'application/json', body: JSON.stringify({
      error: { code, message, details: {}, request_id: requestId },
    }) }
}

async function installApi(page: Page, initiallyUploaded = false,
  scenario: 'normal' | 'conflict' | 'snapshot' = 'normal') {
  let uploaded = initiallyUploaded
  let committed = false
  let snapshotPending = false
  let revision = 0
  let regions: Array<Record<string, unknown>> = []
  await page.route(/^https?:\/\/[^/]+\/api\//, async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()
    if (path === '/api/sessions' && method === 'GET') {
      await route.fulfill({ json: { items: [session], total: 1 } })
    } else if (path === '/api/sessions/7/regions/readiness' && method === 'GET') {
      await route.fulfill({ json: {
        session_id: 7, scoring_configured: true, template_present: uploaded,
        template_ready: committed,
      } })
    } else if (path === '/api/sessions/7/regions/workspace' && method === 'GET') {
      if (!uploaded) await route.fulfill(apiError(route, 'template_not_found', 'missing'))
      else await route.fulfill({ json: {
        session_id: 7,
        template: { ...template, is_confirmed: committed,
          regions_snapshot_pending: snapshotPending },
        formal_regions: committed ? regions.map((item) => ({ ...item, is_confirmed: true })) : [],
        draft: { status: committed || revision === 0 ? 'missing' : 'compatible',
          revision: committed ? 0 : revision, regions: committed ? [] : regions },
        automatic_candidates: ['Q1'],
        manual_question_options: [{ value: '', label: '' }, { value: 'Q1', label: 'Q1' }],
        issues: [], template_ready: committed,
      } })
    } else if (path === '/api/sessions/7/template' && method === 'POST') {
      uploaded = true
      await route.fulfill({ status: 201, json: template })
    } else if (/^\/api\/sessions\/7\/template\/pages\/(front|back)$/.test(path)) {
      await route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1400"><rect width="100%" height="100%" fill="white"/></svg>' })
    } else if (path === '/api/sessions/7/regions/draft' && method === 'PUT') {
      if (scenario === 'conflict') {
        await route.fulfill(apiError(route, 'region_draft_revision_conflict', 'conflict', 409))
        return
      }
      const body = request.postDataJSON() as { revision: number; regions: Array<Record<string, unknown>> }
      revision = body.revision
      regions = body.regions.map((item) => ({ ...item, is_confirmed: false,
        multi_region_confirmed: Boolean(item.multi_region_confirmed) }))
      await route.fulfill({ json: { status: 'compatible', session_id: 7, template_id: 3,
        template_fingerprint: fingerprint, draft: { revision, regions } } })
    } else if (path === '/api/sessions/7/regions/commit' && method === 'POST') {
      const body = request.postDataJSON() as { regions: Array<Record<string, unknown>> }
      regions = body.regions.map((item) => ({ ...item, is_confirmed: true,
        multi_region_confirmed: Boolean(item.multi_region_confirmed) }))
      committed = true
      snapshotPending = scenario === 'snapshot'
      await route.fulfill({ json: { committed: true, snapshot_pending: snapshotPending,
        error: null, issues: [], region_count: regions.length } })
    } else if (path === '/api/sessions/7/regions/snapshot/retry' && method === 'POST') {
      snapshotPending = false
      await route.fulfill({ json: { committed: true, snapshot_pending: false,
        error: null, issues: [], region_count: regions.length } })
    } else if (path === '/api/sessions/7/config/sources/active'
      || path === '/api/sessions/7/config/editor') {
      await route.fulfill(apiError(route, 'config_not_found', 'missing'))
    } else {
      await route.fulfill(apiError(route, 'not_found', 'missing'))
    }
  })
}

test('stops autosave on a browser-visible revision conflict and offers reload', async ({ page }) => {
  await installApi(page, true, 'conflict')
  await page.goto('/sessions/7/regions')
  await page.getByRole('button', { name: '新增框' }).click()
  const canvas = page.locator('[data-role="canvas"]')
  const box = await canvas.boundingBox()
  expect(box).not.toBeNull()
  await page.mouse.move(box!.x + 80, box!.y + 100)
  await page.mouse.down()
  await page.mouse.move(box!.x + 240, box!.y + 220)
  await page.mouse.up()

  await expect(page.getByRole('button', { name: '重新加载服务器草稿' })).toBeVisible()
})

test('keeps confirmed regions available when the snapshot needs a retry', async ({ page }) => {
  await installApi(page, true, 'snapshot')
  await page.goto('/sessions/7/regions')
  await page.getByRole('button', { name: '新增框' }).click()
  const canvas = page.locator('[data-role="canvas"]')
  const box = await canvas.boundingBox()
  expect(box).not.toBeNull()
  await page.mouse.move(box!.x + 80, box!.y + 100)
  await page.mouse.down()
  await page.mouse.move(box!.x + 240, box!.y + 220)
  await page.mouse.up()
  await page.getByRole('button', { name: '题框列表' }).click()
  await page.locator('.mapping-select').selectOption('Q1')
  await expect(page.getByText('草稿已保存', { exact: true })).toBeVisible()

  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '完成标定' }).click()
  await expect(page.getByRole('button', { name: '重试生成确认快照' })).toBeVisible()
  await expect(page.locator('[data-region-uuid]')).toHaveCount(1)

  await page.getByRole('button', { name: '重试生成确认快照' }).click()
  await expect(page.getByText(/P2-11/)).toBeVisible()
})
