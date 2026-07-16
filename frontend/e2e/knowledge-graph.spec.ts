import { expect, test, type Page, type Request, type Route, type TestInfo } from '@playwright/test'

const STORAGE_KEY = 'ai-grading:selected-session:v1'
const sessions = [
  { id: 7, name: '匿名七年级数学考试', status: 'completed', is_deleted: false, deleted_at: null, created_at: null, updated_at: null },
  { id: 8, name: '匿名八年级数学考试', status: 'completed', is_deleted: false, deleted_at: null, created_at: null, updated_at: null },
]
const students = [
  { id: 12, student_code: 'S012', name: '匿名学生甲', class_name: '七年级一班', created_at: null },
  { id: 15, student_code: 'S015', name: '匿名学生乙（长名称用于布局验证）', class_name: '七年级一班', created_at: null },
  { id: 21, student_code: 'S021', name: '匿名学生丙', class_name: '七年级二班', created_at: null },
]

const viewports = [
  { width: 1024, height: 768 },
  { width: 1280, height: 800 },
  { width: 1366, height: 768 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
]

interface RequestLog { method: string; pathname: string }

function trackBrowserErrors(page: Page) {
  const pageErrors: Error[] = []
  const consoleErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error))
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  return { pageErrors, consoleErrors }
}

function graphContext(body: {
  scope: { mode: 'class' | 'student' | 'selected'; class_id?: string; student_ids?: string[] }
  exam_scope: { mode: 'current' | 'manual' | 'cross_exam'; session_ids?: number[] }
}) {
  const sessionIds = body.exam_scope.mode === 'cross_exam' ? [7, 8] : body.exam_scope.session_ids ?? [7]
  const studentIds = body.scope.mode === 'class' ? ['12', '15'] : body.scope.student_ids ?? []
  return {
    scope: {
      mode: body.scope.mode,
      student_ids: studentIds,
      class_id: body.scope.mode === 'class' ? body.scope.class_id ?? '七年级一班' : null,
    },
    exam_scope: {
      mode: body.exam_scope.mode,
      session_ids: sessionIds,
      sessions: sessionIds.map((id) => ({
        session_id: id,
        session_name: sessions.find((session) => session.id === id)?.name ?? `匿名考试${id}`,
      })),
    },
  }
}

function graphRows(request: Request, nodeCount: number) {
  const body = request.postDataJSON() as Parameters<typeof graphContext>[0]
  const context = graphContext(body)
  const nodes = Array.from({ length: nodeCount }, (_, index) => {
    const mastery = [0.48, 0.66, 0.81, 0.94][index % 4]!
    return {
      knowledge_key: `knowledge_point:匿名知识点-${index}`,
      knowledge_label: `匿名知识点 ${index}${index % 17 === 0 ? '（较长名称用于验证标签布局）' : ''}`,
      student_count: 2,
      item_count: index + 1,
      deduction_count: index % 9,
      average_mastery: mastery,
      tag_context: index === 0 ? { prerequisite: ['题目支持标签甲'] } : {},
      error_counts: { primary: index === 0 ? { 步骤不完整: 2 } : {}, secondary: {} },
    }
  })
  const rows = nodes.slice(0, 30).flatMap((node, index) => [12, 15].map((studentId) => ({
    student_id: studentId,
    student_code: `S0${studentId}`,
    student_name: studentId === 12 ? '匿名学生甲' : '匿名学生乙',
    knowledge_key: node.knowledge_key,
    knowledge_label: node.knowledge_label,
    weighted_score_rate: Math.round(node.average_mastery * 1000) / 10,
    deduction_count: node.deduction_count,
    item_count: node.item_count,
    sample_reasons: index === 0 ? '步骤不完整' : '',
    source_question_refs: [],
    tag_context: {},
    error_counts: node.error_counts,
  })))
  return {
    ...context,
    rows,
    nodes,
    edges: [],
    coverage: { covered_items: nodeCount, total_items: nodeCount + 2, missing_items: { QX: '未标注' } },
    warnings: ['匿名数据中有 2 份作答未关联知识标签'],
    diagnosis_identity: 'question_tag',
  }
}

function graphEvidence(request: Request) {
  const body = request.postDataJSON() as Parameters<typeof graphContext>[0] & {
    knowledge_key: string
    page: number
  }
  const context = graphContext(body)
  const start = body.page === 1 ? 1 : 21
  const count = body.page === 1 ? 20 : 1
  const label = body.knowledge_key.replace('knowledge_point:', '')
  return {
    ...context,
    knowledge_key: body.knowledge_key,
    knowledge_label: label,
    items: Array.from({ length: count }, (_, index) => {
      const id = start + index
      return {
        student_id: id,
        student_code: `S${String(id).padStart(3, '0')}`,
        student_name: id === 21 ? '后续页匿名学生' : `证据学生 ${id}`,
        class_id: context.scope.class_id ?? '七年级一班',
        knowledge_key: body.knowledge_key,
        knowledge_label: label,
        session_id: context.exam_scope.session_ids[0]!,
        session_name: context.exam_scope.sessions[0]!.session_name,
        question_id: `Q${id}`,
        bank_question_id: 1000 + id,
        score_awarded: 3,
        full_score: 5,
        score_rate: 0.6,
        tag_context: {},
        actionable_reasons: id === 1 ? ['步骤不完整'] : [],
        error_counts: { primary: {}, secondary: {} },
      }
    }),
    total: 21,
    page: body.page,
    page_size: 20,
    total_pages: 2,
    coverage: { covered_items: 20, total_items: 22, missing_items: { QX: '未标注' } },
    warnings: [],
    diagnosis_identity: 'question_tag',
  }
}

async function fulfillJson(route: Route, body: unknown): Promise<void> {
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
}

async function installGraphApi(page: Page, nodeCount: number): Promise<RequestLog[]> {
  const requests: RequestLog[] = []
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/')) requests.push({ method: request.method(), pathname: url.pathname })
  })
  await page.route(/^https?:\/\/[^/]+\/api\//, async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname
    if (pathname === '/api/sessions') return fulfillJson(route, { items: sessions, total: sessions.length })
    if (pathname === '/api/students') return fulfillJson(route, { items: students, total: students.length })
    if (pathname === '/api/graph/rows') return fulfillJson(route, graphRows(request, nodeCount))
    if (pathname === '/api/graph/evidence') return fulfillJson(route, graphEvidence(request))
    await route.fulfill({ status: 418, body: `unexpected anonymous request: ${request.method()} ${pathname}` })
  })
  return requests
}

async function openGraph(page: Page, nodeCount = 120): Promise<RequestLog[]> {
  const requests = await installGraphApi(page, nodeCount)
  await page.addInitScript(([key]) => localStorage.setItem(key, '7'), [STORAGE_KEY])
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/knowledge-graph?session=7&class=七年级一班')
  await expect(page.getByRole('heading', { name: '知识图谱', exact: true })).toBeVisible()
  await expect(page.getByText(`已覆盖 ${nodeCount} / ${nodeCount + 2} 份作答`)).toBeVisible()
  await expect(page.locator('.knowledge-graph-canvas canvas').first()).toBeVisible()
  return requests
}

async function nonBackgroundPixelCount(page: Page): Promise<number> {
  return page.locator('.knowledge-graph-canvas canvas').first().evaluate((canvas: HTMLCanvasElement) => {
    const context = canvas.getContext('2d')
    if (!context) return 0
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
    let count = 0
    for (let index = 0; index < pixels.length; index += 16) {
      if (pixels[index + 3]! > 0 && (pixels[index]! < 248 || pixels[index + 1]! < 248 || pixels[index + 2]! < 248)) count += 1
    }
    return count
  })
}

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  expect(await page.evaluate(() => ({
    document: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    body: document.body.scrollWidth <= document.body.clientWidth,
  }))).toEqual({ document: true, body: true })
}

function expectReadOnly(requests: RequestLog[]): void {
  expect(requests.length).toBeGreaterThan(0)
  for (const request of requests) {
    const allowedPost = request.method === 'POST' && ['/api/graph/rows', '/api/graph/evidence'].includes(request.pathname)
    expect(request.method === 'GET' || allowedPost).toBe(true)
  }
}

async function captureViewportEvidence(page: Page, testInfo: TestInfo, width: number): Promise<void> {
  if (![1024, 1366, 1920].includes(width)) return
  await page.screenshot({
    path: testInfo.outputPath(`knowledge-graph-${width}.png`),
    fullPage: true,
  })
}

for (const viewport of viewports) {
  test(`${viewport.width}x${viewport.height} keeps filters, canvas, directory and inspector reachable`, async ({ page }, testInfo) => {
    const errors = trackBrowserErrors(page)
    await page.setViewportSize(viewport)
    const requests = await openGraph(page)
    await expect(page.getByRole('button', { name: '应用范围' })).toBeVisible()
    const canvas = page.locator('.knowledge-graph-canvas canvas').first()
    await canvas.scrollIntoViewIfNeeded()
    expect(await nonBackgroundPixelCount(page)).toBeGreaterThan(100)
    await expect(page.getByRole('heading', { name: '知识标签文字目录' })).toBeAttached()
    await expect(page.getByRole('heading', { name: '知识点详情' })).toBeAttached()
    await expectNoHorizontalOverflow(page)
    await captureViewportEvidence(page, testInfo, viewport.width)
    expectReadOnly(requests)
    expect(errors.pageErrors).toEqual([])
    expect(errors.consoleErrors).toEqual([])
  })
}

test('supports zoom, grouping explanation, keyboard selection and paged evidence', async ({ page }) => {
  const errors = trackBrowserErrors(page)
  const requests = await openGraph(page)
  const canvas = page.locator('.knowledge-graph-canvas canvas').first()
  await canvas.scrollIntoViewIfNeeded()
  const beforeZoom = await canvas.screenshot()
  await canvas.hover()
  await page.mouse.wheel(0, -500)
  await expect.poll(async () => (await canvas.screenshot()).equals(beforeZoom)).toBe(false)
  await page.getByRole('button', { name: '恢复视图' }).click()

  await page.getByRole('button', { name: '学生分组树' }).click()
  await expect(page.getByText(/虚线仅表示筛选范围、学生与知识标签的分组归属/)).toBeVisible()
  await expect(page.getByText(/不是知识点父子、先修或相关关系/)).toBeVisible()
  await page.getByRole('button', { name: '掌握度分区' }).click()

  const search = page.getByRole('searchbox', { name: '搜索知识标签' })
  await search.fill('匿名知识点 0')
  const directoryItem = page.getByTestId('graph-directory-item').first()
  await directoryItem.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: /匿名知识点 0/ })).toBeVisible()
  await expect(page.getByText('步骤不完整', { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: '加载更多证据' }).click()
  await expect(page.getByText('后续页匿名学生')).toBeVisible()
  expect(requests.filter((request) => request.pathname === '/api/graph/evidence')).toHaveLength(2)
  expectReadOnly(requests)
  expect(errors.pageErrors).toEqual([])
  expect(errors.consoleErrors).toEqual([])
})

test('renders and operates on a reproducible 1000-node anonymous graph', async ({ page }, testInfo: TestInfo) => {
  const errors = trackBrowserErrors(page)
  const startedAt = Date.now()
  const requests = await openGraph(page, 1000)
  const firstRenderMs = Date.now() - startedAt
  expect(Number.isFinite(firstRenderMs)).toBe(true)
  expect(await nonBackgroundPixelCount(page)).toBeGreaterThan(100)

  const modeStartedAt = Date.now()
  await page.getByRole('button', { name: '学生分组树' }).click()
  await expect(page.getByText(/不是知识点父子、先修或相关关系/)).toBeVisible()
  const modeSwitchMs = Date.now() - modeStartedAt
  await page.getByRole('button', { name: '掌握度分区' }).click()

  await page.getByRole('searchbox', { name: '搜索知识标签' }).fill('匿名知识点 999')
  await expect(page.getByTestId('graph-directory-item')).toHaveCount(1)
  await page.getByTestId('graph-directory-item').click()
  await expect(page.getByRole('heading', { name: /匿名知识点 999/ })).toBeVisible()

  await testInfo.attach('knowledge-graph-1000-node-baseline.json', {
    body: Buffer.from(JSON.stringify({
      nodeCount: 1000,
      firstRenderMs,
      modeSwitchMs,
      browser: testInfo.project.name,
      viewport: page.viewportSize(),
    }, null, 2)),
    contentType: 'application/json',
  })
  expectReadOnly(requests)
  expect(errors.pageErrors).toEqual([])
  expect(errors.consoleErrors).toEqual([])
})
