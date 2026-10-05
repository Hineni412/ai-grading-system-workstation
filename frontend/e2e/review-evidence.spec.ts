import { once } from 'node:events';
import { createDeflate } from 'node:zlib';

import { expect, type Page, type Route } from '@playwright/test';
import { test } from './mock-fixtures'

const STORAGE_KEY = 'ai-grading:selected-session:v1'
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])

const session = {
  id: 7,
  name: '匿名阅卷测试',
  status: 'grading',
  curriculum_volume_id: null,
  is_deleted: false,
  deleted_at: null,
  created_at: '2026-07-13T00:00:00Z',
  updated_at: '2026-07-13T00:00:00Z',
}
const questions = [
  { question_id: 'Q1', question_type: null, total_count: 4, needs_review_count: 0, max_score: 5 },
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
  delayedPaths: Map<string, Promise<void>>
  fulfilledPaths: string[]
  requestedPaths: string[]
}

async function installApi(page: Page, mediaState: MediaState): Promise<void> {
  await page.route(/\/api\/sessions\/7\/review\/questions\/Q1\/rubric$/, route => route.fulfill({ json: null }))
  await page.route(/\/api\/sessions$/, (route) =>
    fulfillJson(route, { items: [session], total: 1 }),
  )
  await page.route(/\/api\/sessions\/7\/config$/, (route) =>
    fulfillJson(route, {
      rubric: {
        questions: [{ question_id: 'Q1', max_score: 5, core_goal: '核对解题步骤' }],
      },
    }),
  )
  await page.route(/\/api\/sessions\/7\/review\/questions(?:\?.*)?$/, (route) =>
    fulfillJson(route, { items: questions, total: questions.length }),
  )
  await page.route(
    /\/api\/sessions\/7\/review\/questions\/Q1\/items\?(?:needs_review_only=false|scope=all)$/,
    (route) => fulfillJson(route, { items, total: items.length }),
  )
  await page.route(/\/api\/media\/(?:crop|original\/front|original\/back)\/\d+$/, async (route) => {
    const path = new URL(route.request().url()).pathname
    mediaState.requestedPaths.push(path)
    const delayed = mediaState.delayedPaths.get(path)
    if (delayed) await delayed
    if (mediaState.failedPaths.has(path)) {
      await route.fulfill({
        status: 503,
        headers: { 'Cache-Control': 'no-store' },
        body: 'anonymous media unavailable',
      })
      mediaState.fulfilledPaths.push(path)
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'image/png',
      headers: { 'Cache-Control': 'no-store' },
      body: path.includes('/crop/') ? cropPng : originalPng,
    })
    mediaState.fulfilledPaths.push(path)
  })
}

async function openViewer(
  page: Page,
  detailId = 1,
  waitUntil: 'load' | 'domcontentloaded' = 'load',
): Promise<void> {
  await page.addInitScript(([key]) => localStorage.setItem(key, '7'), [STORAGE_KEY])
  await page.goto(`/grading?question=Q1&detail=${detailId}`, { waitUntil })
  await expect(page.getByRole('heading', { name: '人工干预工作台', exact: true })).toBeVisible()
  await expect(page.locator('.review-evidence-viewer img')).toHaveCount(1)
}

async function waitForImage(page: Page, width?: number): Promise<void> {
  await expect.poll(() => page.locator('.review-evidence-viewer img').evaluate((image) => {
    const element = image as HTMLImageElement
    return element.complete ? element.naturalWidth : 0
  })).toBe(width ?? 1200)
}

function freshMediaState(): MediaState {
  return {
    failedPaths: new Set(),
    delayedPaths: new Map(),
    fulfilledPaths: [],
    requestedPaths: [],
  }
}

test('fit, actual size, zoom, rotate and drag work while wheel keeps the image scale', async ({
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
  await expect(image).toHaveAttribute('style', beforeWheel ?? '')

  const beforeDrag = await image.getAttribute('style')
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2)
  await page.mouse.down()
  await page.mouse.move(box!.x + box!.width / 2 + 80, box!.y + box!.height / 2 + 60)
  await page.mouse.up()
  await expect.poll(() => image.getAttribute('style')).not.toBe(beforeDrag)

  await page.getByRole('button', { name: '适应宽度', exact: true }).click()
  await expect(image).toHaveAttribute('style', /rotate\(90deg\)/)
})

test('a delayed old original cannot alter the newly selected crop state', async ({ page }) => {
  const state = freshMediaState()
  let releaseOldOriginal!: () => void
  state.delayedPaths.set('/api/media/original/front/1', new Promise<void>((resolve) => {
    releaseOldOriginal = resolve
  }))
  await installApi(page, state)
  await openViewer(page, 1)
  await waitForImage(page)
  await page.getByRole('button', { name: '原卷正面', exact: true }).click()
  await expect.poll(() => state.requestedPaths).toContain('/api/media/original/front/1')

  await page.getByLabel('答卷图片画布').focus()
  await page.keyboard.press('j')
  await expect(page).toHaveURL(/item=7:Q1:2$/)
  await waitForImage(page)
  const image = page.locator('.review-evidence-viewer img')
  await expect(image).toHaveAttribute('src', '/api/media/crop/2')
  const cropTransform = await image.getAttribute('style')

  releaseOldOriginal()
  await expect.poll(() => state.fulfilledPaths).toContain('/api/media/original/front/1')
  await expect(image).toHaveAttribute('src', '/api/media/crop/2')
  await expect(image).toHaveAttribute('style', cropTransform ?? '')
  await expect.poll(() => image.evaluate((element) =>
    (element as HTMLImageElement).naturalWidth,
  )).toBe(1200)
})
