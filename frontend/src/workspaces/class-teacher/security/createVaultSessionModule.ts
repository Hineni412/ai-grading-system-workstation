import { computed, readonly, ref, type Ref } from 'vue'

import { ApiError } from '../../../api/errors'
import { vaultApi, type VaultStatus } from '../api/vault'

type ClearHandler = () => void

export interface VaultSessionModule {
  token: Readonly<Ref<string>>
  status: Readonly<Ref<VaultStatus | null>>
  epoch: Readonly<Ref<number>>
  unlocked: Readonly<Ref<boolean>>
  setSession: (token: string, status?: VaultStatus | null) => void
  setStatus: (status: VaultStatus | null) => void
  refreshStatus: () => Promise<VaultStatus>
  touch: () => Promise<void>
  lock: (reason?: string) => Promise<void>
  clearImmediately: () => string
  registerSensitiveState: (handler: ClearHandler) => () => void
  commitIfCurrent: <T>(epoch: number, value: T, commit: (value: T) => void) => boolean
  lastLockReason: Readonly<Ref<string>>
}

export function createVaultSessionModule(): VaultSessionModule {
  const token = ref('')
  const status = ref<VaultStatus | null>(null)
  const epoch = ref(0)
  const lastLockReason = ref('')
  const clearHandlers = new Set<ClearHandler>()
  const unlocked = computed(() => Boolean(token.value) && status.value?.locked === false)

  function clearImmediately(): string {
    const previous = token.value
    token.value = ''
    epoch.value += 1
    for (const clear of clearHandlers) clear()
    return previous
  }

  async function refreshStatus(): Promise<VaultStatus> {
    const observedEpoch = epoch.value
    const next = await vaultApi.status(token.value || undefined)
    if (observedEpoch !== epoch.value) return next
    status.value = next
    if (next.locked && token.value) clearImmediately()
    return next
  }

  function setSession(nextToken: string, nextStatus?: VaultStatus | null): void {
    epoch.value += 1
    token.value = nextToken
    if (nextStatus !== undefined) status.value = nextStatus
    lastLockReason.value = ''
  }

  function setStatus(nextStatus: VaultStatus | null): void {
    status.value = nextStatus
  }

  async function touch(): Promise<void> {
    if (!token.value) return
    const observedEpoch = epoch.value
    try {
      await vaultApi.touch(token.value)
    } catch (error) {
      if (error instanceof ApiError && error.code === 'vault_locked' && observedEpoch === epoch.value) {
        await lock('会话已过期，请重新解锁。')
        return
      }
      throw error
    }
  }

  async function lock(reason = '敏感区已锁定。'): Promise<void> {
    const previous = clearImmediately()
    lastLockReason.value = reason
    status.value = status.value ? {
      ...status.value,
      locked: true,
      session_expires_in_seconds: 0,
      lock_reason: 'locked',
    } : null
    try {
      if (previous) await vaultApi.lock(previous)
    } catch {
      // Clearing the browser state is authoritative for the page. The server
      // independently expires the same short-lived token.
    }
  }

  function registerSensitiveState(handler: ClearHandler): () => void {
    clearHandlers.add(handler)
    return () => clearHandlers.delete(handler)
  }

  function commitIfCurrent<T>(observedEpoch: number, value: T, commit: (value: T) => void): boolean {
    if (observedEpoch !== epoch.value || !token.value) return false
    commit(value)
    return true
  }

  return {
    token: readonly(token),
    status: readonly(status),
    epoch: readonly(epoch),
    unlocked: readonly(unlocked),
    setSession,
    setStatus,
    refreshStatus,
    touch,
    lock,
    clearImmediately,
    registerSensitiveState,
    commitIfCurrent,
    lastLockReason: readonly(lastLockReason),
  }
}
