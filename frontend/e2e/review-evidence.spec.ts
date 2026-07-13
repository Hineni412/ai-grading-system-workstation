import { once } from 'node:events'
import { createDeflate, inflateSync } from 'node:zlib'

import { expect, test, type Page, type Route } from '@playwright/test'

const STORAGE_KEY = 'ai-grading:selected-session:v1'
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
const viewports = [
  { name: 'large desktop', width: 1920, height: 1080 },
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'standard workstation', width: 1366, height: 768 },
  { name: 'compact desktop', width: 1280, height: 800 },
  { name: 'minimum desktop', width: 1024, height: 768 },
]

const session = {
  id: 7,
  name: '匿名阅卷测试',
  status: 'grading',
  is_deleted: false,
  deleted_at: null,
  created_at: '2026-07-13T00:00:00Z',
  updated_at: '2026-07-13T00:00:00Z',
}
const questions = [
  { question_id: 'Q1', total_count: 4, needs_review_count: 0, max_score: 5 },
]
const items = Array.from({ length: 4 }, (_, offset) => {
  const detailId = offset + 1
  return {
    session_id: 7,
    result_id: detailId,
    detail_id: detailId,
    question_id: 'Q1',
    student_code: `ANON-${detailId}`,
    student_name: `匿名学生${detailId}`,
    class_name: '匿名班级',
    score_awarded: 3,
    max_score: 5,
    deduction_reason: null,
    error_category: null,
    error_summary: null,
    confidence_score: 90,
    needs_review: false,
    candidate_scores: [],
    metadata: {},
    media: {
      crop_url: `/api/media/crop/${detailId}`,
      original_front_url: `/api/media/original/front/${detailId}`,
      original_back_url: `/api/media/original/back/${detailId}`,
      annotated_front_url: `/api/media/annotated/front/${detailId}`,
      annotated_back_url: `/api/media/annotated/back/${detailId}`,
    },
  }
})

let crcTable: number[] | null = null

function crc32(buffer: Buffer): number {
  if (!crcTable) {
    crcTable = Array.from({ length: 256 }, (_, value) => {
      let crc = value
      for (let bit = 0; bit < 8; bit += 1) {
        crc = (crc & 1) === 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1
      }
      return crc >>> 0
    })
  }
  let crc = 0xffffffff
  for (const byte of buffer) crc = crcTable[(crc ^ byte) & 0xff]! ^ (crc >>> 8)
  return (crc ^ 0xffffffff) >>> 0
}

function pngChunk(type: string, data = Buffer.alloc(0)): Buffer {
  const typeBuffer = Buffer.from(type, 'ascii')
  const body = Buffer.concat([typeBuffer, data])
  const length = Buffer.alloc(4)
  const checksum = Buffer.alloc(4)
  length.writeUInt32BE(data.length)
  checksum.writeUInt32BE(crc32(body))
  return Buffer.concat([length, body, checksum])
}

async function generatedPng(
  width: number,
  height: number,
  pixel: (x: number, y: number) => readonly [number, number, number, number],
): Promise<Buffer> {
  const deflate = createDeflate({ level: 6 })
  const compressed: Buffer[] = []
  deflate.on('data', (chunk: Buffer) => compressed.push(chunk))
  const ended = once(deflate, 'end')

  for (let y = 0; y < height; y += 1) {
    const row = Buffer.allocUnsafe(1 + width * 4)
    row[0] = 0
    for (let x = 0; x < width; x += 1) {
      const [red, green, blue, alpha] = pixel(x, y)
      const index = 1 + x * 4
      row[index] = red
      row[index + 1] = green
      row[index + 2] = blue
      row[index + 3] = alpha
    }
    if (!deflate.write(row)) await once(deflate, 'drain')
  }
  deflate.end()
  await ended

  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(width, 0)
  ihdr.writeUInt32BE(height, 4)
  ihdr[8] = 8
  ihdr[9] = 6
  return Buffer.concat([
    PNG_SIGNATURE,
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', Buffer.concat(compressed)),
    pngChunk('IEND'),
  ])
}

const RED_PIXEL = [210, 35, 45, 255] as const
const DARK_PIXEL = [45, 50, 58, 255] as const
const PAPER_PIXEL = [245, 245, 240, 255] as const
const ORIGINAL_LINE_PIXEL = [70, 78, 90, 255] as const
const ORIGINAL_LIGHT_PIXEL = [232, 232, 224, 255] as const
const ORIGINAL_SHADE_PIXEL = [205, 205, 197, 255] as const

function cropPixel(x: number, y: number): readonly [number, number, number, number] {
  const border = x < 10 || y < 10 || x >= 1190 || y >= 790
  if (border) return RED_PIXEL
  const band = y > 150 && y < 175 || y > 330 && y < 355 || y > 510 && y < 535
  return band ? DARK_PIXEL : PAPER_PIXEL
}

function originalPixel(x: number, y: number): readonly [number, number, number, number] {
  const line = y % 512 > 460 && y % 512 < 480
  if (line) return ORIGINAL_LINE_PIXEL
  return (Math.floor(y / 256) + Math.floor(x / 512)) % 2 === 0
    ? ORIGINAL_LIGHT_PIXEL
    : ORIGINAL_SHADE_PIXEL
}

let cropPng: Buffer
let originalPng: Buffer

test.beforeAll(async () => {
  cropPng = await generatedPng(1200, 800, cropPixel)
  originalPng = await generatedPng(4096, 4096, originalPixel)
}, 60_000)

function fulfillJson(route: Route, body: unknown): Promise<void> {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

interface MediaState {
  failedPaths: Set<string>
  requestedPaths: string[]
}

async function installApi(page: Page, mediaState: MediaState): Promise<void> {
  await page.route(/\/api\/sessions$/, (route) =>
    fulfillJson(route, { items: [session], total: 1 }),
  )
  await page.route(/\/api\/sessions\/7\/review\/questions$/, (route) =>
    fulfillJson(route, { items: questions, total: questions.length }),
  )
  await page.route(
    /\/api\/sessions\/7\/review\/questions\/Q1\/items\?needs_review_only=false$/,
    (route) => fulfillJson(route, { items, total: items.length }),
  )
  await page.route(/\/api\/media\/(?:crop|original\/front|original\/back)\/\d+$/, (route) => {
    const path = new URL(route.request().url()).pathname
    mediaState.requestedPaths.push(path)
    if (mediaState.failedPaths.has(path)) {
      return route.fulfill({
        status: 503,
        headers: { 'Cache-Control': 'no-store' },
        body: 'anonymous media unavailable',
      })
    }
    return route.fulfill({
      status: 200,
      contentType: 'image/png',
      headers: { 'Cache-Control': 'no-store' },
      body: path.includes('/crop/') ? cropPng : originalPng,
    })
  })
}

async function openViewer(page: Page, detailId = 1): Promise<void> {
  await page.addInitScript(([key]) => localStorage.setItem(key, '7'), [STORAGE_KEY])
  await page.goto(`/grading?question=Q1&detail=${detailId}`)
  await expect(page.getByRole('heading', { name: '复核队列', exact: true })).toBeVisible()
  await expect(page.locator('.review-evidence-viewer img')).toHaveCount(1)
}

async function waitForImage(page: Page, width?: number): Promise<void> {
  await expect.poll(() => page.locator('.review-evidence-viewer img').evaluate((image) => {
    const element = image as HTMLImageElement
    return element.complete ? element.naturalWidth : 0
  })).toBe(width ?? 1200)
}

function decodePng(buffer: Buffer): {
  width: number
  height: number
  pixels: Buffer
  channels: number
} {
  expect(buffer.subarray(0, 8)).toEqual(PNG_SIGNATURE)
  let offset = 8
  let width = 0
  let height = 0
  let colorType = 6
  const idat: Buffer[] = []
  while (offset < buffer.length) {
    const length = buffer.readUInt32BE(offset)
    const type = buffer.toString('ascii', offset + 4, offset + 8)
    const data = buffer.subarray(offset + 8, offset + 8 + length)
    if (type === 'IHDR') {
      width = data.readUInt32BE(0)
      height = data.readUInt32BE(4)
      colorType = data[9] ?? 6
    }
    if (type === 'IDAT') idat.push(data)
    offset += 12 + length
    if (type === 'IEND') break
  }
  const channels = colorType === 6 ? 4 : colorType === 2 ? 3 : 0
  if (channels === 0) throw new Error(`unsupported screenshot color type ${colorType}`)
  const packed = inflateSync(Buffer.concat(idat))
  const stride = width * channels
  const pixels = Buffer.alloc(stride * height)
  let packedOffset = 0
  for (let y = 0; y < height; y += 1) {
    const filter = packed[packedOffset] ?? 0
    packedOffset += 1
    for (let x = 0; x < stride; x += 1) {
      const raw = packed[packedOffset + x] ?? 0
      const left = x >= channels ? pixels[y * stride + x - channels]! : 0
      const above = y > 0 ? pixels[(y - 1) * stride + x]! : 0
      const upperLeft = y > 0 && x >= channels
        ? pixels[(y - 1) * stride + x - channels]!
        : 0
      let value = raw
      if (filter === 1) value = raw + left
      else if (filter === 2) value = raw + above
      else if (filter === 3) value = raw + Math.floor((left + above) / 2)
      else if (filter === 4) {
        const estimate = left + above - upperLeft
        const leftDistance = Math.abs(estimate - left)
        const aboveDistance = Math.abs(estimate - above)
        const diagonalDistance = Math.abs(estimate - upperLeft)
        const predictor = leftDistance <= aboveDistance && leftDistance <= diagonalDistance
          ? left
          : aboveDistance <= diagonalDistance ? above : upperLeft
        value = raw + predictor
      }
      pixels[y * stride + x] = value & 0xff
    }
    packedOffset += stride
  }
  return { width, height, pixels, channels }
}

function screenshotHasContent(buffer: Buffer): boolean {
  const decoded = decodePng(buffer)
  const luminance = new Set<number>()
  let redBorder = false
  const total = decoded.width * decoded.height
  const step = Math.max(1, Math.floor(total / 50_000))
  for (let pixel = 0; pixel < total; pixel += step) {
    const index = pixel * decoded.channels
    const red = decoded.pixels[index] ?? 0
    const green = decoded.pixels[index + 1] ?? 0
    const blue = decoded.pixels[index + 2] ?? 0
    luminance.add(Math.round((red + green + blue) / 3))
    if (red > 150 && green < 120 && blue < 120) redBorder = true
  }
  return luminance.size >= 2 && redBorder
}

function freshMediaState(): MediaState {
  return { failedPaths: new Set(), requestedPaths: [] }
}

test('loads non-empty crop pixels and switches one active image across original pages', async ({
  page,
}) => {
  const state = freshMediaState()
  await installApi(page, state)
  await openViewer(page)
  await waitForImage(page)

  const canvasScreenshot = await page.getByLabel('答卷图片画布').screenshot()
  expect(screenshotHasContent(canvasScreenshot)).toBe(true)

  await page.getByRole('button', { name: '原卷正面', exact: true }).click()
  await waitForImage(page, 4096)
  await expect(page.locator('.review-evidence-viewer img')).toHaveCount(1)
  await expect(page.locator('.review-evidence-viewer img')).toHaveAttribute(
    'src',
    '/api/media/original/front/1',
  )

  await page.getByRole('button', { name: '原卷反面', exact: true }).click()
  await waitForImage(page, 4096)
  await expect(page.locator('.review-evidence-viewer img')).toHaveCount(1)
  await expect(page.locator('.review-evidence-viewer img')).toHaveAttribute(
    'src',
    '/api/media/original/back/1',
  )
})

test('fit, actual size, zoom, rotate, wheel center and pointer drag change the transform predictably', async ({
  page,
}) => {
  const state = freshMediaState()
  await installApi(page, state)
  await openViewer(page)
  await waitForImage(page)
  const image = page.locator('.review-evidence-viewer img')
  const canvas = page.getByLabel('答卷图片画布')

  await page.getByRole('button', { name: '原比例', exact: true }).click()
  await expect(image).toHaveAttribute('style', /scale\(1\)/)
  await page.getByRole('button', { name: '放大', exact: true }).click()
  await expect(image).toHaveAttribute('style', /scale\(1\.25\)/)
  await page.getByRole('button', { name: '向右旋转', exact: true }).click()
  await expect(image).toHaveAttribute('style', /rotate\(90deg\)/)

  const beforeWheel = await image.getAttribute('style')
  const box = await canvas.boundingBox()
  expect(box).not.toBeNull()
  await page.mouse.move(box!.x + box!.width * 0.25, box!.y + box!.height * 0.25)
  await page.mouse.wheel(0, -100)
  await expect.poll(() => image.getAttribute('style')).not.toBe(beforeWheel)

  const beforeDrag = await image.getAttribute('style')
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2)
  await page.mouse.down()
  await page.mouse.move(box!.x + box!.width / 2 + 80, box!.y + box!.height / 2 + 60)
  await page.mouse.up()
  await expect.poll(() => image.getAttribute('style')).not.toBe(beforeDrag)

  await page.getByRole('button', { name: '适应宽度', exact: true }).click()
  await expect(image).toHaveAttribute('style', /rotate\(90deg\)/)
})

test('failed media keeps review context and retries the same controlled URL', async ({ page }) => {
  const state = freshMediaState()
  state.failedPaths.add('/api/media/crop/2')
  await installApi(page, state)
  await openViewer(page, 2)

  await expect(page.getByText('裁剪图暂时无法读取。')).toBeVisible()
  await expect(page.locator('.review-queue-row[aria-current="true"]')).toContainText('匿名学生2')
  const failedImageKey = await page.locator('.review-evidence-viewer img')
    .getAttribute('data-load-key')
  state.failedPaths.delete('/api/media/crop/2')
  await page.getByLabel('答卷图片画布').getByRole('button', { name: '重新加载' }).click()
  await expect(page.locator('.review-evidence-viewer img')).not.toHaveAttribute(
    'data-load-key',
    failedImageKey ?? '',
  )
  await expect.poll(() =>
    state.requestedPaths.filter((path) => path === '/api/media/crop/2').length,
  ).toBeGreaterThanOrEqual(2)
  await waitForImage(page)

  const cropRequests = state.requestedPaths.filter((path) => path.includes('/crop/2'))
  expect(cropRequests.length).toBeGreaterThanOrEqual(2)
  expect(new Set(cropRequests)).toEqual(new Set(['/api/media/crop/2']))
  await expect(page.locator('.review-queue-row[aria-current="true"]')).toContainText('匿名学生2')
})

test('rapid J/K switching resets the viewer and never shows stale pixels', async ({ page }) => {
  const state = freshMediaState()
  await installApi(page, state)
  await openViewer(page, 1)
  await waitForImage(page)
  await page.getByRole('button', { name: '原卷正面', exact: true }).click()
  await waitForImage(page, 4096)
  await page.getByRole('button', { name: '向右旋转', exact: true }).click()
  await page.getByLabel('答卷图片画布').focus()

  await page.keyboard.press('j')
  await page.keyboard.press('j')
  await expect(page).toHaveURL(/detail=3$/)
  await waitForImage(page)
  const image = page.locator('.review-evidence-viewer img')
  await expect(image).toHaveCount(1)
  await expect(image).toHaveAttribute('src', '/api/media/crop/3')
  await expect(image).toHaveAttribute('style', /rotate\(0deg\)/)
  await expect(page.getByRole('button', { name: '裁剪证据', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
  expect(screenshotHasContent(await page.getByLabel('答卷图片画布').screenshot())).toBe(true)
})

test('large original image keeps one active DOM image and remains interactive', async ({ page }) => {
  const state = freshMediaState()
  await installApi(page, state)
  await openViewer(page, 3)
  await page.getByRole('button', { name: '原卷正面', exact: true }).click()
  await waitForImage(page, 4096)
  const image = page.locator('.review-evidence-viewer img')
  await expect(image).toHaveCount(1)
  const before = await image.getAttribute('style')
  await page.getByRole('button', { name: '放大', exact: true }).click()
  await expect.poll(() => image.getAttribute('style')).not.toBe(before)
  await expect(page.getByRole('button', { name: '向左旋转', exact: true })).toBeEnabled()
})

test('evidence viewer has no overflow, overlap, inaccessible tools, or console errors at five viewports', async ({
  page,
}) => {
  const state = freshMediaState()
  const pageErrors: Error[] = []
  const consoleErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error))
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  await installApi(page, state)

  for (const viewport of viewports) {
    await page.setViewportSize(viewport)
    await openViewer(page)
    await waitForImage(page)
    expect(await page.evaluate(() =>
      document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ), viewport.name).toBe(true)
    await expect(page.locator('.review-queue-row[aria-current="true"]'), viewport.name)
      .toBeInViewport()
    await expect(page.getByRole('toolbar', { name: '图片查看工具' }), viewport.name)
      .toBeInViewport()
    await expect(page.getByLabel('答卷图片画布'), viewport.name).toBeInViewport()
    await expect(page.locator('.review-evidence-viewer img'), viewport.name).toHaveCount(1)

    const stackedWithoutOverlap = await page.evaluate(() => {
      const source = document.querySelector<HTMLElement>('.review-evidence-source')
      const toolbar = document.querySelector<HTMLElement>('.review-evidence-toolbar')
      const canvas = document.querySelector<HTMLElement>('.review-evidence-canvas')
      if (!source || !toolbar || !canvas) return false
      const sourceBox = source.getBoundingClientRect()
      const toolbarBox = toolbar.getBoundingClientRect()
      const canvasBox = canvas.getBoundingClientRect()
      return sourceBox.bottom <= toolbarBox.top + 1 && toolbarBox.bottom <= canvasBox.top + 1
    })
    expect(stackedWithoutOverlap, viewport.name).toBe(true)
  }

  expect(pageErrors).toEqual([])
  expect(consoleErrors).toEqual([])
})
