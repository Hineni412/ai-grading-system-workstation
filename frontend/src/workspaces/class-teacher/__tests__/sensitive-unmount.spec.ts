import { describe, expect, it, vi } from 'vitest'

import { vaultApi } from '../api/vault'
import { createVaultSessionModule } from '../security/createVaultSessionModule'

describe('vault session module', () => {
  it('clears sensitive state synchronously before a best-effort server lock', async () => {
    let finishLock: (() => void) | undefined
    vi.spyOn(vaultApi, 'lock').mockImplementation(() => new Promise((resolve) => {
      finishLock = () => resolve({})
    }))
    const session = createVaultSessionModule()
    session.setSession('synthetic-token', {
      initialized: true,
      locked: false,
      idle_timeout_seconds: 300,
      retry_after_seconds: 0,
      format_version: 1,
      protection_mode: 'pin_dpapi_current_user_v2',
      protection_state: 'active',
      legacy_upgrade_available: false,
      session_expires_in_seconds: 300,
      status_observed_at: '2026-08-01T00:00:00Z',
      lock_reason: null,
    })
    const clear = vi.fn()
    session.registerSensitiveState(clear)

    const locking = session.lock()

    expect(session.token.value).toBe('')
    expect(session.epoch.value).toBe(2)
    expect(clear).toHaveBeenCalledOnce()
    finishLock?.()
    await locking
  })

  it('rejects stale responses from a previous epoch', () => {
    const session = createVaultSessionModule()
    session.setSession('first')
    const observed = session.epoch.value
    session.clearImmediately()
    session.setSession('second')
    const commit = vi.fn()

    expect(session.commitIfCurrent(observed, 'secret', commit)).toBe(false)
    expect(commit).not.toHaveBeenCalled()
  })
})
