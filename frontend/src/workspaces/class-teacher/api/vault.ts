import { apiClient } from '../../../api/client'

const CLIENT_HEADER = 'class-teacher-browser-v1'

export interface VaultStatus {
  initialized: boolean
  locked: boolean
  idle_timeout_seconds: number
  retry_after_seconds: number
  format_version: number
  protection_mode?: 'uninitialized' | 'legacy_password_v1' | 'pin_dpapi_current_user_v2'
  protection_state?: 'pending' | 'active' | null
  legacy_upgrade_available?: boolean
}

export interface VaultSession {
  session_token: string
  idle_timeout_seconds: number
  recovery_key: string | null
}

export interface VaultInitialization extends VaultSession {
  recovery_key: string
  recovery_key_shown_once: boolean
}

export interface VaultBackup {
  backup_id: string
  file_name: string
  created_at: string
  size_bytes: number
  status?: string
  source_instance_id?: string
}

export interface VaultRestorePreview {
  backup_id: string
  source_instance_id: string
  created_at: string
  format_version: number
  scope: string
  preview_token: string
  expires_in_seconds: number
  requires_complete_replacement: boolean
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('contract')
  return value as Record<string, unknown>
}

function text(value: unknown): string {
  if (typeof value !== 'string' || !value) throw new Error('contract')
  return value
}

function number(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error('contract')
  return value
}

function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('contract')
  return value
}

function status(payload: unknown): VaultStatus {
  const value = object(payload)
  const initialized = boolean(value.initialized)
  const fallbackMode = initialized ? 'legacy_password_v1' : 'uninitialized'
  const protectionMode = value.protection_mode === undefined
    ? fallbackMode
    : text(value.protection_mode)
  if (!['uninitialized', 'legacy_password_v1', 'pin_dpapi_current_user_v2'].includes(
    protectionMode,
  )) throw new Error('contract')
  const protectionState = value.protection_state === undefined || value.protection_state === null
    ? null
    : text(value.protection_state)
  if (protectionState !== null && !['pending', 'active'].includes(protectionState)) {
    throw new Error('contract')
  }
  return {
    initialized,
    locked: boolean(value.locked),
    idle_timeout_seconds: number(value.idle_timeout_seconds),
    retry_after_seconds: number(value.retry_after_seconds),
    format_version: number(value.format_version),
    protection_mode: protectionMode as VaultStatus['protection_mode'],
    protection_state: protectionState as VaultStatus['protection_state'],
    legacy_upgrade_available: value.legacy_upgrade_available === undefined
      ? protectionMode === 'legacy_password_v1'
      : boolean(value.legacy_upgrade_available),
  }
}

function session(payload: unknown): VaultSession {
  const value = object(payload)
  return {
    session_token: text(value.session_token),
    idle_timeout_seconds: number(value.idle_timeout_seconds),
    recovery_key: value.recovery_key === null || value.recovery_key === undefined
      ? null
      : text(value.recovery_key),
  }
}

function initialization(payload: unknown): VaultInitialization {
  const value = object(payload)
  return {
    ...session(payload),
    recovery_key: text(value.recovery_key),
    recovery_key_shown_once: boolean(value.recovery_key_shown_once),
  }
}

function backup(payload: unknown): VaultBackup {
  const value = object(payload)
  return {
    backup_id: text(value.backup_id),
    file_name: text(value.file_name),
    created_at: text(value.created_at),
    size_bytes: number(value.size_bytes),
    source_instance_id: text(value.source_instance_id),
  }
}

function backupList(payload: unknown): VaultBackup[] {
  const value = object(payload)
  if (!Array.isArray(value.items)) throw new Error('contract')
  return value.items.map((item) => {
    const entry = object(item)
    return {
      backup_id: text(entry.backup_id),
      file_name: text(entry.file_name),
      created_at: text(entry.created_at),
      size_bytes: number(entry.size_bytes),
      status: text(entry.status),
    }
  })
}

function restorePreview(payload: unknown): VaultRestorePreview {
  const value = object(payload)
  return {
    backup_id: text(value.backup_id),
    source_instance_id: text(value.source_instance_id),
    created_at: text(value.created_at),
    format_version: number(value.format_version),
    scope: text(value.scope),
    preview_token: text(value.preview_token),
    expires_in_seconds: number(value.expires_in_seconds),
    requires_complete_replacement: boolean(value.requires_complete_replacement),
  }
}

function headers(token?: string): Record<string, string> {
  return {
    'x-class-teacher-client': CLIENT_HEADER,
    ...(token ? { 'x-class-teacher-session': token } : {}),
  }
}

function operationId(): string {
  return globalThis.crypto.randomUUID()
}

export const vaultApi = {
  status(token?: string) {
    return apiClient.request('/api/class-teacher/vault/status', {
      headers: token ? { 'x-class-teacher-session': token } : {},
      decode: status,
    })
  },
  initialize(password: string) {
    return apiClient.request('/api/class-teacher/vault/initialize', {
      method: 'POST',
      headers: headers(),
      body: { password, operation_id: operationId() },
      decode: initialization,
    })
  },
  initializePin(pin: string) {
    return apiClient.request('/api/class-teacher/vault/pin/initialize', {
      method: 'POST',
      headers: headers(),
      body: { pin, operation_id: operationId() },
      decode: initialization,
    })
  },
  unlock(password: string) {
    return apiClient.request('/api/class-teacher/vault/unlock', {
      method: 'POST',
      headers: headers(),
      body: { password },
      decode: session,
    })
  },
  unlockPin(pin: string) {
    return apiClient.request('/api/class-teacher/vault/pin/unlock', {
      method: 'POST',
      headers: headers(),
      body: { pin },
      decode: session,
    })
  },
  recover(recoveryKey: string, newPassword: string) {
    return apiClient.request('/api/class-teacher/vault/recover', {
      method: 'POST',
      headers: headers(),
      body: {
        recovery_key: recoveryKey,
        new_password: newPassword,
        operation_id: operationId(),
      },
      decode: session,
    })
  },
  recoverPin(recoveryKey: string, newPin: string) {
    return apiClient.request('/api/class-teacher/vault/pin/recover', {
      method: 'POST',
      headers: headers(),
      body: {
        recovery_key: recoveryKey,
        new_pin: newPin,
        operation_id: operationId(),
      },
      decode: session,
    })
  },
  upgradeLegacyToPin(token: string, currentPassword: string, newPin: string) {
    return apiClient.request('/api/class-teacher/vault/pin/upgrade', {
      method: 'POST',
      headers: headers(token),
      body: {
        current_password: currentPassword,
        new_pin: newPin,
        operation_id: operationId(),
      },
      decode: object,
    })
  },
  lock(token: string) {
    return apiClient.request('/api/class-teacher/vault/lock', {
      method: 'POST',
      headers: headers(token),
      body: {},
      decode: object,
    })
  },
  touch(token: string) {
    return apiClient.request('/api/class-teacher/vault/touch', {
      method: 'POST',
      headers: headers(token),
      body: {},
      decode: object,
    })
  },
  acknowledgeRecoveryKey(token: string) {
    return apiClient.request('/api/class-teacher/vault/recovery-key/acknowledge', {
      method: 'POST',
      headers: headers(token),
      body: {},
      decode: object,
    })
  },
  changePassword(token: string, currentPassword: string, newPassword: string) {
    return apiClient.request('/api/class-teacher/vault/change-password', {
      method: 'POST',
      headers: headers(token),
      body: {
        current_password: currentPassword,
        new_password: newPassword,
        operation_id: operationId(),
      },
      decode: object,
    })
  },
  listBackups(token: string) {
    return apiClient.request('/api/class-teacher/vault/backups', {
      headers: { 'x-class-teacher-session': token },
      decode: backupList,
    })
  },
  createBackup(token: string, backupPassword: string) {
    return apiClient.request('/api/class-teacher/vault/backups', {
      method: 'POST',
      headers: headers(token),
      body: {
        backup_password: backupPassword,
        operation_id: operationId(),
      },
      decode: backup,
      timeoutMs: 60_000,
    })
  },
  previewRestore(
    token: string,
    fileName: string,
    secret: string,
    secretKind: 'password' | 'recovery_key',
  ) {
    return apiClient.request('/api/class-teacher/vault/restore/preview', {
      method: 'POST',
      headers: headers(token),
      body: { file_name: fileName, secret, secret_kind: secretKind },
      decode: restorePreview,
      timeoutMs: 60_000,
    })
  },
  confirmRestore(token: string, previewToken: string) {
    return apiClient.request('/api/class-teacher/vault/restore/confirm', {
      method: 'POST',
      headers: headers(token),
      body: {
        preview_token: previewToken,
        operation_id: operationId(),
        confirmation_phrase: '确认恢复班主任工作台',
      },
      decode: object,
      timeoutMs: 60_000,
    })
  },
}
