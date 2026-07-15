export interface SessionSummary {
  id: number
  name: string
  status: string
  is_deleted: boolean
  deleted_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface SessionListResponse {
  items: SessionSummary[]
  total: number
}

export type SessionLoader = () => Promise<SessionSummary[]>
export type SessionDraftCreator = (name: string) => Promise<SessionSummary>

export class SessionReadError extends Error {
  constructor() {
    super('无法读取考试列表')
    this.name = 'SessionReadError'
  }
}

export function isSessionSummary(value: unknown): value is SessionSummary {
  if (!isRecord(value)) return false

  if (!hasExactKeys(value, [
    'id', 'name', 'status', 'is_deleted', 'deleted_at', 'created_at', 'updated_at',
  ])) return false

  return (
    Number.isSafeInteger(value.id) &&
    Number(value.id) > 0 &&
    typeof value.name === 'string' &&
    typeof value.status === 'string' &&
    typeof value.is_deleted === 'boolean' &&
    isNullableString(value.deleted_at) &&
    isNullableString(value.created_at) &&
    isNullableString(value.updated_at)
  )
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort()
  return actual.length === keys.length && actual.every((key, index) => key === [...keys].sort()[index])
}

function isSessionListResponse(value: unknown): value is SessionListResponse {
  if (!isRecord(value)) return false

  return (
    Array.isArray(value.items) &&
    value.items.every(isSessionSummary) &&
    Number.isSafeInteger(value.total) &&
    Number(value.total) >= 0
  )
}

export async function fetchSessions(): Promise<SessionSummary[]> {
  try {
    const payload = await apiClient.request('/api/sessions', {
      decode: (value) => {
        if (!isSessionListResponse(value)) throw new Error('invalid session response')
        return value
      },
    })
    return payload.items.filter((session) => session.is_deleted === false)
  } catch {
    throw new SessionReadError()
  }
}

function requireSessionId(id: number): number {
  if (!Number.isSafeInteger(id) || id <= 0) throw new Error('Invalid session id')
  return id
}

function requireSessionName(name: string): string {
  const normalized = name.trim()
  if (!normalized) throw new Error('考试名称不能为空')
  return normalized
}

export async function createSessionDraft(name: string): Promise<SessionSummary> {
  return apiClient.request('/api/sessions/drafts', {
    method: 'POST',
    body: { name: requireSessionName(name) },
    decode: (value) => {
      if (!isSessionSummary(value)) throw new Error('invalid session draft response')
      return value
    },
  })
}

export async function renameSession(id: number, name: string): Promise<SessionSummary> {
  const sessionId = requireSessionId(id)
  return apiClient.request(`/api/sessions/${sessionId}`, {
    method: 'PATCH',
    body: { name: requireSessionName(name) },
    decode: (value) => {
      if (!isSessionSummary(value)) throw new Error('invalid session rename response')
      return value
    },
  })
}
import { apiClient } from './client'
import { isNullableString, isRecord } from './validation'
