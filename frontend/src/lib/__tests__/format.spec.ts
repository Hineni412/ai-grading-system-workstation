import { describe, expect, it } from 'vitest'

import { formatBytes, formatPercent, formatScore, formatTime } from '../format'

describe('formatScore', () => {
  it('formats integers without decimals', () => {
    expect(formatScore(3)).toBe('3')
    expect(formatScore(0)).toBe('0')
  })
  it('keeps one decimal by default and strips trailing zeros', () => {
    expect(formatScore(2.5)).toBe('2.5')
    expect(formatScore(2.26)).toBe('2.3')
    expect(formatScore(2.04)).toBe('2')
  })
  it('supports extra digits for averages', () => {
    expect(formatScore(2.25, 2)).toBe('2.25')
    expect(formatScore(2.2, 2)).toBe('2.2')
  })
  it('renders missing or non-finite values as a dash', () => {
    expect(formatScore(null)).toBe('—')
    expect(formatScore(undefined)).toBe('—')
    expect(formatScore(Number.NaN)).toBe('—')
  })
})

describe('formatBytes', () => {
  it('uses B below one KiB', () => {
    expect(formatBytes(512)).toBe('512 B')
  })
  it('uses KB below one MiB', () => {
    expect(formatBytes(1536)).toBe('1.5 KB')
  })
  it('uses MB at and above one MiB', () => {
    expect(formatBytes(3 * 1048576)).toBe('3.0 MB')
  })
  it('uses GB at and above one GiB', () => {
    expect(formatBytes(2 * 1073741824)).toBe('2.0 GB')
  })
})

describe('formatPercent', () => {
  it('treats values up to 1 as ratios', () => {
    expect(formatPercent(0.876)).toBe('88%')
  })
  it('treats values above 1 as percentages', () => {
    expect(formatPercent(87.6)).toBe('88%')
  })
  it('renders missing values as a dash', () => {
    expect(formatPercent(null)).toBe('—')
  })
})

describe('formatTime', () => {
  it('normalizes ISO timestamps', () => {
    expect(formatTime('2026-10-06T13:30:00Z')).toBe('2026-10-06 13:30:00')
  })
  it('returns empty for missing values', () => {
    expect(formatTime(null)).toBe('')
    expect(formatTime(undefined)).toBe('')
  })
})
