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
export type SessionRenamer = (id: number, name: string) => Promise<SessionSummary>
export type SessionDeleter = (
  id: number,
  expectedRevision: string,
  confirmationName: string,
) => Promise<SessionSummary>

export interface SessionDeletionImpact {
  session: SessionSummary
  revision: string
  active_jobs: number
  active_grading_runs: number
  can_archive: boolean
  can_permanently_delete: boolean
  permanent_delete_phrase: string
  permanent_counts: Record<string, number>
  storage_counts: Record<string, number>
  question_bank_counts: Record<string, number>
  blocking_training_tasks: string[]
  can_delete: boolean
}

export interface SessionPermanentDeletionResponse {
  session_id: number
  db_counts: Record<string, number>
  question_bank_counts: Record<string, number>
  deleted_files: number
  deleted_dirs: number
  skipped_shared: number
}

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

const sessionSummaryKeys = [
  'id', 'name', 'status', 'is_deleted', 'deleted_at', 'created_at', 'updated_at',
] as const

const legacySessionDetailKeys = [
  ...sessionSummaryKeys,
  'rubric_path',
  'answer_key_path',
  'template_config_path',
  'source_paper_path',
  'source_paper_sha256',
  'question_bank_sync_state',
  'question_bank_sync_details',
  'question_bank_sync_error',
  'question_bank_sync_updated_at',
] as const

function projectRenameResponse(value: unknown): SessionSummary {
  if (!isRecord(value)) throw new Error('invalid session rename response')

  const hasSupportedShape = hasExactKeys(value, sessionSummaryKeys)
    || hasExactKeys(value, legacySessionDetailKeys)
  if (!hasSupportedShape) throw new Error('invalid session rename response')

  const summary = {
    id: value.id,
    name: value.name,
    status: value.status,
    is_deleted: value.is_deleted,
    deleted_at: value.deleted_at,
    created_at: value.created_at,
    updated_at: value.updated_at,
  }
  if (!isSessionSummary(summary)) throw new Error('invalid session rename response')
  return summary
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

function isRevision(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
}

function isCountRecord(value: unknown): value is Record<string, number> {
  return isRecord(value) && Object.values(value).every((item) => (
    Number.isSafeInteger(item) && Number(item) >= 0
  ))
}

function decodeDeletionImpact(value: unknown): SessionDeletionImpact {
  if (
    !isRecord(value)
    || !hasExactKeys(value, [
      'session',
      'revision',
      'active_jobs',
      'active_grading_runs',
      'can_archive',
      'can_permanently_delete',
      'permanent_delete_phrase',
      'permanent_counts',
      'storage_counts',
      'question_bank_counts',
      'blocking_training_tasks',
      'can_delete',
    ])
    || !isSessionSummary(value.session)
    || !isRevision(value.revision)
    || !Number.isSafeInteger(value.active_jobs)
    || Number(value.active_jobs) < 0
    || !Number.isSafeInteger(value.active_grading_runs)
    || Number(value.active_grading_runs) < 0
    || typeof value.can_archive !== 'boolean'
    || typeof value.can_permanently_delete !== 'boolean'
    || typeof value.permanent_delete_phrase !== 'string'
    || !isCountRecord(value.permanent_counts)
    || !isCountRecord(value.storage_counts)
    || !isCountRecord(value.question_bank_counts)
    || !Array.isArray(value.blocking_training_tasks)
    || !value.blocking_training_tasks.every((item) => typeof item === 'string')
    || typeof value.can_delete !== 'boolean'
  ) {
    throw new Error('invalid session deletion impact')
  }
  return value as unknown as SessionDeletionImpact
}

async function fetchSessionList(includeArchived: boolean): Promise<SessionSummary[]> {
  try {
    const suffix = includeArchived ? '?include_deleted=true' : ''
    const payload = await apiClient.request(`/api/sessions${suffix}`, {
      decode: (value) => {
        if (!isSessionListResponse(value)) throw new Error('invalid session response')
        return value
      },
    })
    return payload.items
  } catch {
    throw new SessionReadError()
  }
}

export async function fetchSessions(): Promise<SessionSummary[]> {
  return (await fetchSessionList(false)).filter((session) => !session.is_deleted)
}

export async function fetchArchivedSessions(): Promise<SessionSummary[]> {
  return (await fetchSessionList(true)).filter((session) => session.is_deleted)
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
    decode: projectRenameResponse,
  })
}

export async function fetchSessionDeletionImpact(
  id: number,
): Promise<SessionDeletionImpact> {
  const sessionId = requireSessionId(id)
  return apiClient.request(`/api/sessions/${sessionId}/deletion-impact`, {
    decode: decodeDeletionImpact,
  })
}

export async function moveSessionToRecycleBin(
  id: number,
  expectedRevision: string,
  confirmationName: string,
): Promise<SessionSummary> {
  const sessionId = requireSessionId(id)
  if (!isRevision(expectedRevision)) throw new Error('Invalid session deletion revision')
  return apiClient.request(`/api/sessions/${sessionId}`, {
    method: 'DELETE',
    body: {
      expected_revision: expectedRevision,
      confirmation_name: requireSessionName(confirmationName),
    },
    decode: projectRenameResponse,
    timeoutMs: 30_000,
  })
}

export async function archiveSession(
  id: number,
  expectedRevision: string,
  confirmationName: string,
): Promise<SessionSummary> {
  return moveSessionToRecycleBin(id, expectedRevision, confirmationName)
}

export async function restoreArchivedSession(id: number): Promise<SessionSummary> {
  const sessionId = requireSessionId(id)
  return apiClient.request(`/api/sessions/${sessionId}/restore`, {
    method: 'POST',
    decode: projectRenameResponse,
    timeoutMs: 30_000,
  })
}

export async function permanentlyDeleteSession(
  id: number,
  expectedRevision: string,
  confirmationPhrase: string,
): Promise<SessionPermanentDeletionResponse> {
  const sessionId = requireSessionId(id)
  if (!isRevision(expectedRevision)) throw new Error('Invalid session deletion revision')
  return apiClient.request(`/api/sessions/${sessionId}/permanent`, {
    method: 'DELETE',
    body: {
      expected_revision: expectedRevision,
      confirmation_phrase: confirmationPhrase.trim(),
    },
    decode: (value) => {
      if (
        !isRecord(value)
        || !hasExactKeys(value, [
          'session_id',
          'db_counts',
          'question_bank_counts',
          'deleted_files',
          'deleted_dirs',
          'skipped_shared',
        ])
        || !Number.isSafeInteger(value.session_id)
        || Number(value.session_id) <= 0
        || !isCountRecord(value.db_counts)
        || !isCountRecord(value.question_bank_counts)
        || !Number.isSafeInteger(value.deleted_files)
        || Number(value.deleted_files) < 0
        || !Number.isSafeInteger(value.deleted_dirs)
        || Number(value.deleted_dirs) < 0
        || !Number.isSafeInteger(value.skipped_shared)
        || Number(value.skipped_shared) < 0
      ) throw new Error('invalid permanent deletion response')
      return value as unknown as SessionPermanentDeletionResponse
    },
    timeoutMs: 60_000,
  })
}
import { apiClient } from './client'
import { isNullableString, isRecord } from './validation'
