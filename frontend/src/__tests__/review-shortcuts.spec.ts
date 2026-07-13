import { describe, expect, it, vi } from 'vitest'

import {
  ReviewShortcutBus,
  type ReviewShortcutCommand,
} from '../composables/review-shortcuts'

describe('review workspace shortcut bus', () => {
  it('dispatches synchronously in subscription order and stops after unsubscribe', () => {
    const bus = new ReviewShortcutBus()
    const received: string[] = []
    const first = vi.fn((command: ReviewShortcutCommand) => received.push(`first:${command}`))
    const second = vi.fn((command: ReviewShortcutCommand) => received.push(`second:${command}`))
    const stopFirst = bus.subscribe(first)
    const stopSecond = bus.subscribe(second)

    bus.dispatch('focus-search')
    stopFirst()
    bus.dispatch('fit-width')
    stopSecond()
    bus.dispatch('zoom-in')

    expect(received).toEqual([
      'first:focus-search',
      'second:focus-search',
      'second:fit-width',
    ])
    expect(first).toHaveBeenCalledTimes(1)
    expect(second).toHaveBeenCalledTimes(2)
  })

  it('uses a snapshot so handlers may unsubscribe safely during dispatch', () => {
    const bus = new ReviewShortcutBus()
    const received: string[] = []
    let stopFirst: () => void = () => undefined
    stopFirst = bus.subscribe(() => {
      received.push('first')
      stopFirst()
    })
    bus.subscribe(() => received.push('second'))

    bus.dispatch('zoom-out')
    bus.dispatch('zoom-out')

    expect(received).toEqual(['first', 'second', 'second'])
  })
})
