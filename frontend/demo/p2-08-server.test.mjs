import assert from 'node:assert/strict'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
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

test('rejects an invalid or duplicate batch before mutating any item', async () => {
  const origin = await startDemo()
  const itemsUrl = `${origin}/api/sessions/7/review/questions/Q1/items?needs_review_only=false`
  const before = await (await fetch(itemsUrl)).json()
  const confirmUrl = '/api/sessions/7/review/questions/Q1/confirm'

  const invalid = await postJson(origin, confirmUrl, { items: [
    { result_id: 101, detail_id: 1, score_awarded: 4.5 },
    { result_id: 103, detail_id: 3, score_awarded: 8 },
  ] })
  assert.equal(invalid.status, 422)
  assert.deepEqual(await (await fetch(itemsUrl)).json(), before)

  const duplicate = await postJson(origin, confirmUrl, { items: [
    { result_id: 101, detail_id: 1, score_awarded: 4 },
    { result_id: 101, detail_id: 1, score_awarded: 4.5 },
  ] })
  assert.equal(duplicate.status, 422)
  assert.deepEqual(await (await fetch(itemsUrl)).json(), before)
})
