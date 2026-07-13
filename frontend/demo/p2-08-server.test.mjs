import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { request } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, test } from 'node:test'

import { startP208Server } from './p2-08-server.mjs'

const cleanups = []

afterEach(async () => {
  await Promise.all(cleanups.splice(0).map((cleanup) => cleanup()))
})

async function startDemo() {
  const staticRoot = await mkdtemp(join(tmpdir(), 'p2-08-demo-'))
  await writeFile(join(staticRoot, 'index.html'), '<!doctype html><title>P2-08</title>')
  const runtime = await startP208Server({ port: 0, staticRoot })
  cleanups.push(async () => {
    await runtime.close()
    await rm(staticRoot, { recursive: true, force: true })
  })
  return runtime.origin
}

async function postJson(origin, path, body) {
  return fetch(`${origin}${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
}

function rawStatus(origin, path) {
  return new Promise((resolve, reject) => {
    const target = new URL(origin)
    const outgoing = request({
      hostname: target.hostname,
      port: target.port,
      path,
    }, (response) => {
      response.resume()
      response.once('end', () => resolve(response.statusCode))
    })
    outgoing.once('error', reject)
    outgoing.end()
  })
}

test('serves the anonymous SPA, API and generated no-store media on loopback', async () => {
  const origin = await startDemo()

  assert.equal((await fetch(`${origin}/healthz`)).status, 200)
  assert.equal((await fetch(`${origin}/api/sessions`)).status, 200)
  const spa = await fetch(`${origin}/grading?question=Q1&detail=1`)
  assert.equal(spa.status, 200)
  assert.match(await spa.text(), /P2-08/)
  const media = await fetch(`${origin}/api/media/crop/1`)
  assert.equal(media.status, 200)
  assert.equal(media.headers.get('cache-control'), 'no-store')
  assert.match(media.headers.get('content-type') ?? '', /^image\//)
})

test('confirmation changes only runtime state and reset restores the fixed dataset', async () => {
  const origin = await startDemo()
  const itemsUrl = `${origin}/api/sessions/7/review/questions/Q1/items?needs_review_only=false`
  const before = await (await fetch(itemsUrl)).json()

  const confirmed = await postJson(
    origin,
    '/api/sessions/7/review/questions/Q1/confirm',
    { items: [{ result_id: 101, detail_id: 1, score_awarded: 4.5, deduction_reason: '匿名验收调整' }] },
  )
  assert.equal(confirmed.status, 200)
  const after = await (await fetch(itemsUrl)).json()
  assert.equal(after.items.find((item) => item.detail_id === 1).score_awarded, 4.5)
  assert.equal(after.items.find((item) => item.detail_id === 1).needs_review, false)

  assert.equal((await postJson(origin, '/__p2_08__/reset', {})).status, 200)
  const reset = await (await fetch(itemsUrl)).json()
  assert.deepEqual(reset, before)
})

test('exposes explicit failure, retry, empty, error and slow acceptance modes', async () => {
  const origin = await startDemo()
  const confirmUrl = '/api/sessions/7/review/questions/Q1/confirm'
  const payload = { items: [{ result_id: 101, detail_id: 1, score_awarded: 4 }] }

  for (const [mode, status] of [['422', 422], ['500', 500]]) {
    assert.equal((await postJson(origin, '/__p2_08__/mode', { confirm: mode })).status, 200)
    assert.equal((await postJson(origin, confirmUrl, payload)).status, status)
  }

  await postJson(origin, '/__p2_08__/mode', { confirm: 'retry', items: 'ready' })
  const retry = await (await postJson(origin, confirmUrl, payload)).json()
  assert.equal(retry.annotation_outcomes[0].status, 'retry_required')

  const itemsUrl = `${origin}/api/sessions/7/review/questions/Q1/items?needs_review_only=false`
  await postJson(origin, '/__p2_08__/mode', { confirm: 'success', items: 'empty' })
  assert.deepEqual((await (await fetch(itemsUrl)).json()).items, [])
  await postJson(origin, '/__p2_08__/mode', { items: 'error' })
  assert.equal((await fetch(itemsUrl)).status, 500)
  await postJson(origin, '/__p2_08__/mode', { items: 'slow' })
  const started = Date.now()
  assert.equal((await fetch(itemsUrl)).status, 200)
  assert.ok(Date.now() - started >= 150)
})

test('rejects unknown controls, media shapes and invalid confirmation context', async () => {
  const origin = await startDemo()
  assert.equal(
    (await postJson(origin, '/__p2_08__/mode', { items: 'ready', extra: 'not-allowed' })).status,
    422,
  )
  assert.equal((await fetch(`${origin}/api/media/private/1`)).status, 404)

  const wrongQuestion = await postJson(
    origin,
    '/api/sessions/7/review/questions/Q2/confirm',
    { items: [{ result_id: 101, detail_id: 1, score_awarded: 4 }] },
  )
  assert.equal(wrongQuestion.status, 422)
  const outOfRange = await postJson(
    origin,
    '/api/sessions/7/review/questions/Q1/confirm',
    { items: [{ result_id: 101, detail_id: 1, score_awarded: 6 }] },
  )
  assert.equal(outOfRange.status, 422)
  const multiple = await postJson(
    origin,
    '/api/sessions/7/review/questions/Q1/confirm',
    { items: [
      { result_id: 101, detail_id: 1, score_awarded: 4 },
      { result_id: 102, detail_id: 2, score_awarded: 4 },
    ] },
  )
  assert.equal(multiple.status, 422)
})

test('rejects traversal and the server source contains no production data-tree reference', async () => {
  const origin = await startDemo()
  assert.equal((await fetch(`${origin}/..%2f..%2fVERSION`)).status, 404)
  assert.equal(await rawStatus(origin, '/%2e%2e/%2e%2e/VERSION'), 404)

  const source = await readFile(new URL('./p2-08-server.mjs', import.meta.url), 'utf8')
  assert.equal(source.includes(['user', 'data'].join('_')), false)
})
