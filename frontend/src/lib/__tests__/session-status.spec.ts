import { describe, expect, it } from 'vitest'

import { sessionStatusLabel, sessionStatusTone } from '../session-status'

describe('session-status helpers', () => {
  it('maps known statuses to Chinese labels', () => {
    expect(sessionStatusLabel('created')).toBe('待开始')
    expect(sessionStatusLabel('pending')).toBe('待开始')
    expect(sessionStatusLabel('grading')).toBe('批改中')
    expect(sessionStatusLabel('completed')).toBe('已完成')
    expect(sessionStatusLabel('failed')).toBe('有失败记录')
  })

  it('falls back to the raw status for unknown values', () => {
    expect(sessionStatusLabel('archived')).toBe('archived')
    expect(sessionStatusLabel('')).toBe('')
  })

  it('maps statuses to sidebar tones and null for unknown', () => {
    expect(sessionStatusTone('completed')).toBe('done')
    expect(sessionStatusTone('created')).toBe('run')
    expect(sessionStatusTone('pending')).toBe('run')
    expect(sessionStatusTone('grading')).toBe('run')
    expect(sessionStatusTone('failed')).toBe('danger')
    expect(sessionStatusTone('archived')).toBeNull()
  })
})
