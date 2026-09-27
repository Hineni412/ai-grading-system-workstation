import { expect, test, type Page, type Route, type TestInfo } from '@playwright/test';

const STORAGE_KEY = 'ai-grading:selected-session:v1'

const sessions = [
  {
    id: 7,
    name: '七年级数学期末质量监测（匿名合成数据）',
    status: 'grading',
    curriculum_volume_id: null,
    is_deleted: false,
    deleted_at: null,
    created_at: '2026-07-14T08:00:00Z',
    updated_at: '2026-07-15T09:30:00Z',
  },
  {
    id: 8,
    name: '八年级物理单元检测（匿名合成数据）',
    status: 'completed',
    curriculum_volume_id: null,
    is_deleted: false,
    deleted_at: null,
    created_at: '2026-07-12T08:00:00Z',
    updated_at: '2026-07-15T10:00:00Z',
  },
]

const viewports = [
  { width: 1024, height: 768 },
  { width: 1280, height: 720 },
  { width: 1366, height: 768 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
]

interface MockOptions {
  overviewFailure?: boolean
}

interface RequestLog {
  method: string
  pathname: string
}

function progress(sessionId: number) {
  const completed = sessionId === 8
  return {
    total_papers: completed ? 40 : 36,
    matched_papers: completed ? 40 : 35,
    unmatched_papers: completed ? 0 : 1,
    graded_papers: completed ? 40 : 12,
    failed_papers: completed ? 0 : 1,
    grading_papers: completed ? 0 : 2,
    needs_human_review: completed ? 0 : 3,
    absent_students: 0,
    scan_issue_students: completed ? 0 : 1,
    progress_percent: completed ? 100 : 33.33,
  }
}

function overview(sessionId: number) {
  const session = sessions.find((item) => item.id === sessionId) ?? sessions[0]!
  return {
    current_session: session,
    progress: progress(sessionId),
    review: sessionId === 8 ? { question_count: 0, item_count: 0 } : { question_count: 2, item_count: 3 },
    anomalies: sessionId === 8
      ? { unmatched_papers: 0, scan_issue_students: 0, failed_papers: 0 }
      : { unmatched_papers: 1, scan_issue_students: 1, failed_papers: 1 },
    recent_jobs: [],
    recent_sessions: sessions
      .filter((item) => item.id !== sessionId)
      .map((item) => ({ session: item, progress: progress(item.id) })),
    updated_at: sessionId === 8 ? '2026-07-15T10:05:00Z' : '2026-07-15T09:35:00Z',
  }
}

function fulfillJson(route: Route, body: unknown): Promise<void> {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

function configSource(sessionId: number) {
  return {
    session_id: sessionId,
    source_id: 'a'.repeat(32),
    source_revision: 'b'.repeat(64),
    safe_filename: 'synthetic-exam.pdf',
    suffix: '.pdf',
    size_bytes: 1,
    sha256_prefix: 'c'.repeat(12),
    parse_state: 'ready',
    questions: [],
  }
}

function configEditor(sessionId: number) {
  return {
    session_id: sessionId,
    configured: false,
    revision: 'd'.repeat(64),
    rows: [],
    total_score: 0,
    issues: [],
    source: null,
  }
}

function curriculumCatalog() {
  const volumes = [
    ['七年级', '上学期'],
    ['七年级', '下学期'],
    ['八年级', '上学期'],
    ['八年级', '下学期'],
    ['九年级', '上学期'],
  ] as const
  return {
    schema_version: 2,
    catalog_id: 'bnu-math-2024',
    knowledge_standard_id: 'bnu-math-2024-curriculum-knowledge-v2',
    publisher: '北京师范大学出版社',
    subject: '初中数学',
    edition: '2024',
    statistics: {
      raw_nodes: 5,
      excluded_nodes: 0,
      retained_nodes: 5,
      chapters: 5,
      sections: 0,
      knowledge_points: 0,
    },
    volumes: volumes.map(([grade, semester], index) => ({
      id: `volume-${index + 1}`,
      order: index + 1,
      label: `${grade}${semester === '上学期' ? '上册' : '下册'}`,
      grade,
      semester,
      textbook_version: '北师大版2024',
      source: { provider: '组卷网' },
      statistics: { raw_nodes: 1, excluded_nodes: 0, retained_nodes: 1 },
      chapters: [{
        id: `chapter-${index + 1}`,
        knowledge_id: `chapter-${index + 1}`,
        order: 1,
        number: '第一章',
        title: `测试章节${index + 1}`,
        label: `第一章 测试章节${index + 1}`,
        kind: 'chapter',
        display_name: `${grade}${semester === '上学期' ? '上册' : '下册'}｜第一章 测试章节${index + 1}`,
        source_ref: {
          node_id: `node-${index + 1}`,
          relative_url: `/czsx/zj${index + 1}`,
        },
        exam_scope_values: [
          `${grade}${semester === '上学期' ? '上册' : '下册'} 测试范围${index + 1}`,
        ],
        sections: [],
      }],
    })),
  }
}

async function installSyntheticApi(
  page: Page,
  options: MockOptions = {},
): Promise<RequestLog[]> {
  const requests: RequestLog[] = []

  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/')) requests.push({ method: request.method(), pathname: url.pathname })
  })

  await page.route(/^https?:\/\/[^/]+\/api\//, async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const { pathname } = url

    if (pathname === '/api/sessions') {
      await fulfillJson(route, { items: sessions, total: sessions.length })
      return
    }
    const activeSourceMatch = pathname.match(/^\/api\/sessions\/(\d+)\/config\/sources\/active$/)
    if (request.method() === 'GET' && activeSourceMatch) {
      await fulfillJson(route, configSource(Number(activeSourceMatch[1])))
      return
    }
    const editorMatch = pathname.match(/^\/api\/sessions\/(\d+)\/config\/editor$/)
    if (request.method() === 'GET' && editorMatch) {
      await fulfillJson(route, configEditor(Number(editorMatch[1])))
      return
    }
    if (pathname === '/api/question-bank/curriculum') {
      await fulfillJson(route, curriculumCatalog())
      return
    }
    if (pathname === '/api/workbench/overview') {
      if (options.overviewFailure) {
        await route.fulfill({ status: 503, body: 'private synthetic failure' })
        return
      }
      const sessionId = Number(url.searchParams.get('session_id') ?? 7)
      await fulfillJson(route, overview(sessionId))
      return
    }
    if (pathname === '/api/jobs') {
      await fulfillJson(route, { items: [], total: 0, page: 1, page_size: 20, total_pages: 0 })
      return
    }
    await route.fulfill({ status: 418, body: `unexpected synthetic API request: ${request.method()} ${pathname}` })
  })

  return requests
}

function trackBrowserErrors(page: Page) {
  const pageErrors: Error[] = []
  const consoleErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error))
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  return { pageErrors, consoleErrors }
}

async function openWorkbench(page: Page, sessionId = 7): Promise<void> {
  await page.addInitScript(([key, value]) => localStorage.setItem(key, value), [STORAGE_KEY, String(sessionId)])
  await page.goto('/workbench')
  await expect(page.getByRole('heading', { name: /今天先完成这三件事/ })).toBeVisible()
  await expect(page.locator('.workbench-workflow')).toBeVisible()
}

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  expect(await page.evaluate(() => ({
    document: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    body: document.body.scrollWidth <= document.body.clientWidth,
  }))).toEqual({ document: true, body: true })
}

function expectReadOnlyRequests(requests: RequestLog[]): void {
  expect(requests.length).toBeGreaterThan(0)
  for (const request of requests) {
    expect(request.method, `${request.method} ${request.pathname}`).toBe('GET')
    expect(request.pathname).not.toMatch(/(?:submit|confirm|cancel|score|grading\/jobs)/)
  }
}

async function captureVisualEvidence(page: Page, testInfo: TestInfo, width: number): Promise<void> {
  if (![1024, 1366, 1920].includes(width)) return
  await page.screenshot({
    path: testInfo.outputPath(`workbench-${width}.png`),
    fullPage: true,
    animations: 'disabled',
  })
}

for (const viewport of viewports) {
  test(`${viewport.width}x${viewport.height} shows the whole home loop within one screen`, async ({ page }, testInfo) => {
    const errors = trackBrowserErrors(page)
    const requests = await installSyntheticApi(page)
    await page.setViewportSize(viewport)
    await openWorkbench(page)

    await expect(page.locator('.workbench-focus-board')).toBeVisible()
    await expect(page.locator('.workbench-focus-list > li')).toHaveCount(3)
    await expect(page.locator('.workbench-pulse')).toContainText('七年级数学期末质量监测')
    await expect(page.locator('.workbench-pulse')).toContainText('33.33')
    await expect(page.locator('.workbench-workflow')).toContainText('从一次考试，走到下一堂课')
    await expect(page.locator('.workbench-workflow__steps > li')).toHaveCount(5)

    await expect(page.getByText('当前考试详情', { exact: true })).toHaveCount(0)
    await expect(page.getByText('班级题目分析', { exact: true })).toHaveCount(0)
    await expect(page.getByText('最近考试', { exact: true })).toHaveCount(0)

    const workflowBox = await page.locator('.workbench-workflow').boundingBox()
    expect(workflowBox).not.toBeNull()
    expect(workflowBox!.y + workflowBox!.height).toBeLessThanOrEqual(viewport.height)

    await expectNoHorizontalOverflow(page)
    await captureVisualEvidence(page, testInfo, viewport.width)
    expectReadOnlyRequests(requests)
    expect(errors.pageErrors).toEqual([])
    expect(errors.consoleErrors).toEqual([])
  })
}
