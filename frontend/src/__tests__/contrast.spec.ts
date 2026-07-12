import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve(process.cwd(), 'src/styles/tokens.css'), 'utf-8')

function token(name: string) {
  const match = css.match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`))
  if (!match?.[1]) throw new Error(`Missing color token: ${name}`)
  return match[1]
}

function luminance(hex: string) {
  const channels = [1, 3, 5].map((index) => Number.parseInt(hex.slice(index, index + 2), 16) / 255)
  const [red, green, blue] = channels.map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  )
  return 0.2126 * (red ?? 0) + 0.7152 * (green ?? 0) + 0.0722 * (blue ?? 0)
}

function contrast(foreground: string, background: string) {
  const [lighter, darker] = [luminance(foreground), luminance(background)].sort((a, b) => b - a)
  return ((lighter ?? 0) + 0.05) / ((darker ?? 0) + 0.05)
}

describe('P2-02 color contrast', () => {
  it.each([
    ['--color-text-primary', '--color-bg-surface'],
    ['--color-text-secondary', '--color-bg-surface'],
    ['--color-accent', '--color-bg-surface'],
    ['--color-danger', '--color-danger-subtle'],
    ['--color-text-primary', '--color-warning-subtle'],
    ['--color-success', '--color-success-subtle'],
    ['--color-teacher', '--color-teacher-subtle'],
    ['--color-ai', '--color-ai-subtle'],
  ])('%s on %s meets WCAG AA for normal text', (foreground, background) => {
    expect(contrast(token(foreground), token(background))).toBeGreaterThanOrEqual(4.5)
  })
})
