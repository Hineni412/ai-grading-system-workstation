export interface AppNotification {
  message: string
  impact: string
  retryable: boolean
  requestId: string
}

export interface NotificationSink {
  publish(notification: AppNotification): void
  subscribe(listener: (notification: AppNotification) => void): () => void
}

export function createMemoryNotificationSink(): NotificationSink {
  const listeners = new Set<(notification: AppNotification) => void>()
  return {
    publish(notification) {
      for (const listener of listeners) listener({ ...notification })
    },
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
}
