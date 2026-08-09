import { readdir, stat } from 'node:fs/promises'
import { resolve } from 'node:path'

const MAX_CHUNK_BYTES = 500_000
const assetsDirectory = resolve(process.cwd(), 'dist/assets')

const entries = await readdir(assetsDirectory)
const chunks = await Promise.all(
  entries
    .filter(name => name.endsWith('.js'))
    .map(async name => ({ name, bytes: (await stat(resolve(assetsDirectory, name))).size })),
)
const oversized = chunks
  .filter(chunk => chunk.bytes > MAX_CHUNK_BYTES)
  .sort((left, right) => right.bytes - left.bytes)

if (oversized.length > 0) {
  const details = oversized
    .map(chunk => `${chunk.name}: ${chunk.bytes} bytes`)
    .join('\n')
  throw new Error(
    `JavaScript bundle budget exceeded (${MAX_CHUNK_BYTES} bytes per chunk):\n${details}`,
  )
}

const largest = [...chunks].sort((left, right) => right.bytes - left.bytes)[0]
console.log(
  largest
    ? `JavaScript bundle budget passed; largest chunk ${largest.name} is ${largest.bytes} bytes.`
    : 'JavaScript bundle budget passed; no JavaScript chunks were emitted.',
)
