import { describe, expect, it, vi } from 'vitest'

import { createMemoryNotificationSink } from '../notifications'

describe('notification port', () => {
  it('publishes only the user-safe notification shape', () => {
    const sink = createMemoryNotificationSink()
    const listener = vi.fn()
    sink.subscribe(listener)

    sink.publish({
      message: '任务状态暂时无法更新',
      impact: '保留上次任务状态',
      retryable: true,
      requestId: 'req-41',
    })

    expect(listener).toHaveBeenCalledExactlyOnceWith({
      message: '任务状态暂时无法更新',
      impact: '保留上次任务状态',
      retryable: true,
      requestId: 'req-41',
    })
    expect(JSON.stringify(listener.mock.calls)).not.toContain('details')
    expect(JSON.stringify(listener.mock.calls)).not.toContain('payload')
    expect(JSON.stringify(listener.mock.calls)).not.toContain('result')
  })

  it('stops publishing after unsubscribe', () => {
    const sink = createMemoryNotificationSink()
    const listener = vi.fn()
    const unsubscribe = sink.subscribe(listener)
    unsubscribe()

    sink.publish({ message: 'ignored', impact: 'none', retryable: false, requestId: 'req' })

    expect(listener).not.toHaveBeenCalled()
  })
})
