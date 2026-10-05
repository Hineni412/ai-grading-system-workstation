import { shallowRef } from 'vue'

export interface ConfirmDialogOptions {
  title: string
  message?: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
}

export interface AlertDialogOptions {
  title: string
  message?: string
  okLabel?: string
}

export interface ConfirmRequest {
  id: number
  kind: 'confirm' | 'alert'
  title: string
  message?: string
  confirmLabel: string
  cancelLabel: string
  danger: boolean
  resolve: (value: boolean) => void
}

export const confirmRequest = shallowRef<ConfirmRequest | null>(null)
const pendingQueue: ConfirmRequest[] = []
let nextRequestId = 1

function enqueue(request: ConfirmRequest) {
  if (confirmRequest.value) pendingQueue.push(request)
  else confirmRequest.value = request
}

export function settleConfirm(value: boolean) {
  const current = confirmRequest.value
  confirmRequest.value = null
  current?.resolve(value)
  const next = pendingQueue.shift()
  if (next) confirmRequest.value = next
}

export function useConfirm() {
  function confirm(options: ConfirmDialogOptions): Promise<boolean> {
    return new Promise((resolve) => {
      enqueue({
        id: nextRequestId++,
        kind: 'confirm',
        title: options.title,
        message: options.message,
        confirmLabel: options.confirmLabel ?? '确认',
        cancelLabel: options.cancelLabel ?? '取消',
        danger: options.danger ?? false,
        resolve,
      })
    })
  }

  function alert(options: AlertDialogOptions): Promise<void> {
    return new Promise((resolve) => {
      enqueue({
        id: nextRequestId++,
        kind: 'alert',
        title: options.title,
        message: options.message,
        confirmLabel: options.okLabel ?? '知道了',
        cancelLabel: '',
        danger: false,
        resolve: () => resolve(),
      })
    })
  }

  return { confirm, alert }
}
