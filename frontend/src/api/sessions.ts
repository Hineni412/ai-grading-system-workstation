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

export class SessionReadError extends Error {
  constructor() {
    super('无法读取考试列表')
    this.name = 'SessionReadError'
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isNullableString(value: unknown): value is string | null {
  return typeof value === 'string' || value === null
}

export function isSessionSummary(value: unknown): value is SessionSummary {
  if (!isRecord(value)) return false

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
    const response = await fetch('/api/sessions', {
      headers: { accept: 'application/json' },
    })
    if (!response.ok) throw new SessionReadError()

    const payload: unknown = await response.json()
    if (!isSessionListResponse(payload)) throw new SessionReadError()

    return payload.items.filter((session) => session.is_deleted === false)
  } catch {
    throw new SessionReadError()
  }
}
