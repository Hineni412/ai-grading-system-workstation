import { createServer } from 'node:http'
import { readFile, stat } from 'node:fs/promises'
import { extname, resolve, sep } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const LOOPBACK = '127.0.0.1'
const DEFAULT_DATASET_URL = new URL('./p2-08-dataset.json', import.meta.url)
const JSON_HEADERS = { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' }
const MIME_TYPES = new Map([
  ['.css', 'text/css; charset=utf-8'],
  ['.html', 'text/html; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.map', 'application/json; charset=utf-8'],
  ['.svg', 'image/svg+xml; charset=utf-8'],
])

function clone(value) {
  return structuredClone(value)
}

function sendJson(response, status, value) {
  response.writeHead(status, JSON_HEADERS)
  response.end(JSON.stringify(value))
}

function sendNotFound(response) {
  sendJson(response, 404, { message: '未找到匿名验收资源' })
}

async function readJsonBody(request) {
  const chunks = []
  let length = 0
  for await (const chunk of request) {
    length += chunk.length
    if (length > 16_384) throw new Error('request body too large')
    chunks.push(chunk)
  }
  if (chunks.length === 0) return {}
  return JSON.parse(Buffer.concat(chunks).toString('utf8'))
}

function generatedAnswerSvg(detailId, variant) {
  const safeDetail = String(detailId).replace(/[^0-9]/g, '').slice(0, 6) || '0'
  const safeVariant = String(variant).replace(/[^a-z]/gi, '').slice(0, 18) || 'answer'
  const lines = Array.from({ length: 9 }, (_, index) => {
    const y = 155 + index * 58
    const width = 680 - (index % 3) * 92
    return `<path d="M120 ${y} Q 280 ${y - 22} ${120 + width} ${y}" />`
  }).join('')
  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="920" height="720" viewBox="0 0 920 720">
  <rect width="920" height="720" fill="#f4f1e8"/>
  <rect x="52" y="42" width="816" height="636" rx="8" fill="#fffef9" stroke="#b9b5aa" stroke-width="2"/>
  <text x="92" y="94" font-family="sans-serif" font-size="24" fill="#343a40">匿名答卷 ${safeDetail}</text>
  <text x="690" y="94" font-family="sans-serif" font-size="16" fill="#687078">${safeVariant}</text>
  <g fill="none" stroke="#244f86" stroke-width="4" stroke-linecap="round">${lines}</g>
  <g fill="none" stroke="#b64b45" stroke-width="4"><path d="M720 260 l18 18 l34 -42"/><ellipse cx="728" cy="430" rx="78" ry="38"/></g>
</svg>`
}

function questionSummaries(state) {
  return state.rubric.questions.map((question) => {
    const items = state.items.filter((item) => item.question_id === question.question_id)
    return {
      question_id: question.question_id,
      total_count: items.length,
      needs_review_count: items.filter((item) => item.needs_review).length,
      max_score: question.max_score,
    }
  })
}

function validateSubmittedItems(submitted, sessionId, questionId, items) {
  if (!Array.isArray(submitted) || submitted.length === 0) return null
  const detailIds = new Set()
  const validated = []
  for (const entry of submitted) {
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) return null
    if (!Number.isSafeInteger(entry.detail_id) || detailIds.has(entry.detail_id)) return null
    detailIds.add(entry.detail_id)
    const target = items.find((item) =>
      item.session_id === sessionId &&
      item.question_id === questionId &&
      item.result_id === entry.result_id &&
      item.detail_id === entry.detail_id,
    )
    if (
      !target ||
      !Number.isFinite(entry.score_awarded) ||
      entry.score_awarded < 0 ||
      entry.score_awarded > target.max_score ||
      (entry.deduction_reason !== undefined && typeof entry.deduction_reason !== 'string')
    ) return null
    validated.push({ entry, target })
  }
  return validated
}

function safeStaticPath(staticRoot, pathname) {
  let decoded
  try {
    decoded = decodeURIComponent(pathname)
  } catch {
    return null
  }
  if (decoded.includes('\0') || decoded.includes('\\') || decoded.split('/').includes('..')) return null
  const relative = decoded.replace(/^\/+/, '')
  const candidate = resolve(staticRoot, relative || 'index.html')
  return candidate === staticRoot || candidate.startsWith(`${staticRoot}${sep}`) ? candidate : null
}

async function serveStatic(response, staticRoot, pathname) {
  let target = safeStaticPath(staticRoot, pathname)
  if (target === null) return false
  try {
    const info = await stat(target)
    if (info.isDirectory()) target = resolve(target, 'index.html')
    const body = await readFile(target)
    response.writeHead(200, {
      'content-type': MIME_TYPES.get(extname(target).toLowerCase()) ?? 'application/octet-stream',
      'cache-control': extname(target) === '.html' ? 'no-store' : 'public, max-age=300',
    })
    response.end(body)
    return true
  } catch {
    return false
  }
}

async function loadDataset(datasetUrl = DEFAULT_DATASET_URL) {
  return JSON.parse(await readFile(datasetUrl, 'utf8'))
}

export async function startP208Server({ port = 4188, staticRoot = resolve('dist'), datasetUrl } = {}) {
  const fixedDataset = await loadDataset(datasetUrl)
  let state = clone(fixedDataset)
  let modes = { confirm: 'success', items: 'ready' }

  const server = createServer(async (request, response) => {
    try {
      if (/%(?:2e|2f|5c)/i.test(request.url ?? '')) return sendNotFound(response)
      const requestUrl = new URL(request.url ?? '/', `http://${LOOPBACK}`)
      const { pathname } = requestUrl

      if (request.method === 'GET' && pathname === '/healthz') {
        return sendJson(response, 200, { status: 'ok', dataset: 'P2-08-anonymous' })
      }

      if (request.method === 'POST' && pathname === '/__p2_08__/reset') {
        state = clone(fixedDataset)
        modes = { confirm: 'success', items: 'ready' }
        return sendJson(response, 200, { status: 'reset' })
      }

      if (request.method === 'POST' && pathname === '/__p2_08__/mode') {
        const body = await readJsonBody(request)
        const confirmModes = new Set(['success', '422', '500', 'retry'])
        const itemModes = new Set(['ready', 'empty', 'error', 'slow'])
        if (
          typeof body !== 'object' || body === null || Array.isArray(body) ||
          Object.keys(body).some((key) => key !== 'confirm' && key !== 'items')
        ) {
          return sendJson(response, 422, { message: '匿名验收模式字段无效' })
        }
        if (body.confirm !== undefined && !confirmModes.has(body.confirm)) {
          return sendJson(response, 422, { message: '无效的确认模式' })
        }
        if (body.items !== undefined && !itemModes.has(body.items)) {
          return sendJson(response, 422, { message: '无效的队列模式' })
        }
        modes = { ...modes, ...body }
        return sendJson(response, 200, { ...modes })
      }

      if (request.method === 'GET' && pathname === '/api/sessions') {
        return sendJson(response, 200, { items: [state.session], total: 1 })
      }

      if (request.method === 'GET' && pathname === `/api/sessions/${state.session.id}/config`) {
        return sendJson(response, 200, { rubric: state.rubric })
      }

      if (request.method === 'GET' && pathname === `/api/sessions/${state.session.id}/review/questions`) {
        const items = questionSummaries(state)
        return sendJson(response, 200, { items, total: items.length })
      }

      const itemMatch = pathname.match(/^\/api\/sessions\/(\d+)\/review\/questions\/([^/]+)\/items$/)
      if (request.method === 'GET' && itemMatch) {
        if (Number(itemMatch[1]) !== state.session.id) return sendNotFound(response)
        if (modes.items === 'error') return sendJson(response, 500, { message: '匿名验收队列加载失败' })
        if (modes.items === 'slow') await new Promise((resolveDelay) => setTimeout(resolveDelay, 220))
        const questionId = decodeURIComponent(itemMatch[2])
        const needsReviewOnly = requestUrl.searchParams.get('needs_review_only') === 'true'
        const questionItems = state.items.filter((item) => item.question_id === questionId)
        const items = modes.items === 'empty'
          ? []
          : needsReviewOnly
            ? questionItems.filter((item) => item.needs_review)
            : questionItems
        return sendJson(response, 200, { items, total: items.length })
      }

      const confirmMatch = pathname.match(/^\/api\/sessions\/(\d+)\/review\/questions\/([^/]+)\/confirm$/)
      if (request.method === 'POST' && confirmMatch) {
        if (Number(confirmMatch[1]) !== state.session.id) return sendNotFound(response)
        if (modes.confirm === '422') return sendJson(response, 422, { message: '匿名验收校验失败' })
        if (modes.confirm === '500') return sendJson(response, 500, { message: '匿名验收服务失败' })
        const body = await readJsonBody(request)
        const questionId = decodeURIComponent(confirmMatch[2])
        const validated = validateSubmittedItems(
          body.items,
          state.session.id,
          questionId,
          state.items,
        )
        if (!validated) {
          return sendJson(response, 422, { message: '匿名验收提交内容无效' })
        }
        for (const { entry, target } of validated) {
          target.score_awarded = entry.score_awarded
          target.deduction_reason = typeof entry.deduction_reason === 'string'
            ? entry.deduction_reason
            : target.deduction_reason
          target.error_category = '已由教师确认'
          target.error_summary = 'anonymous_review_confirmed'
          target.needs_review = false
        }
        const resultIds = [...new Set(validated.map(({ target }) => target.result_id))]
        return sendJson(response, 200, {
          updated_details: validated.length,
          updated_results: resultIds.length,
          annotation_outcomes: resultIds.map((resultId) => modes.confirm === 'retry'
            ? { result_id: resultId, status: 'retry_required', message: '批注图片稍后重试' }
            : { result_id: resultId, status: 'succeeded' }),
        })
      }

      const mediaMatch = pathname.match(
        /^\/api\/media\/(crop|(?:original|annotated)\/(?:front|back))\/(\d+)$/,
      )
      if (request.method === 'GET' && mediaMatch) {
        const svg = generatedAnswerSvg(mediaMatch[2], mediaMatch[1])
        response.writeHead(200, {
          'content-type': 'image/svg+xml; charset=utf-8',
          'cache-control': 'no-store',
          'content-length': Buffer.byteLength(svg),
        })
        response.end(svg)
        return
      }

      if (pathname.startsWith('/api/') || pathname.startsWith('/__p2_08__/')) {
        return sendNotFound(response)
      }

      if (request.method !== 'GET' && request.method !== 'HEAD') return sendNotFound(response)
      if (await serveStatic(response, staticRoot, pathname)) return
      if (extname(pathname) === '' && await serveStatic(response, staticRoot, '/index.html')) return
      return sendNotFound(response)
    } catch {
      return sendJson(response, 500, { message: '匿名验收服务处理失败' })
    }
  })

  await new Promise((resolveListen, reject) => {
    server.once('error', reject)
    server.listen(port, LOOPBACK, resolveListen)
  })
  const address = server.address()
  if (address === null || typeof address === 'string') throw new Error('P2-08 server failed to bind')
  const origin = `http://${LOOPBACK}:${address.port}`

  return {
    origin,
    close: () => new Promise((resolveClose, reject) => {
      server.close((error) => error ? reject(error) : resolveClose())
    }),
  }
}

const isCli = process.argv[1]
  ? pathToFileURL(resolve(process.argv[1])).href === import.meta.url
  : false

if (isCli) {
  const staticRoot = resolve(fileURLToPath(new URL('../dist', import.meta.url)))
  const port = Number(process.env.P2_08_PORT ?? 4188)
  const runtime = await startP208Server({ port, staticRoot })
  process.stdout.write(`Phase 2 anonymous recalibration: ${runtime.origin}/grading?question=Q1\n`)
}
