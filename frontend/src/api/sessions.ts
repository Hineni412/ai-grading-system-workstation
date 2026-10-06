export interface SessionSummary {
  id: number
  name: string
  status: string
  curriculum_volume_id?: string | null
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
export type SessionDraftCreator = (
  name: string,
  curriculumVolumeId?: string | null,
) => Promise<SessionSummary>
export type SessionRenamer = (id: number, name: string) => Promise<SessionSummary>
export interface SessionMetadataUpdate {
  name?: string
  curriculum_volume_id?: string | null
}
export type SessionMetadataUpdater = (
  id: number,
  update: SessionMetadataUpdate,
) => Promise<SessionSummary>
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
  storage_cleanup_pending: boolean
  recovered_interrupted_delete: boolean
}

export interface SessionPendingCleanup {
  session_id: number
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
    'id', 'name', 'status', 'curriculum_volume_id', 'is_deleted', 'deleted_at',
    'created_at', 'updated_at',
  ])) return false

  return (
    Number.isSafeInteger(value.id) &&
    Number(value.id) > 0 &&
    typeof value.name === 'string' &&
    typeof value.status === 'string' &&
    isNullableString(value.curriculum_volume_id) &&
    typeof value.is_deleted === 'boolean' &&
    isNullableString(value.deleted_at) &&
    isNullableString(value.created_at) &&
    isNullableString(value.updated_at)
  )
}

const sessionSummaryKeys = [
  'id', 'name', 'status', 'curriculum_volume_id', 'is_deleted', 'deleted_at',
  'created_at', 'updated_at',
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
    curriculum_volume_id: value.curriculum_volume_id,
    is_deleted: value.is_deleted,
    deleted_at: value.deleted_at,
    created_at: value.created_at,
    updated_at: value.updated_at,
  }
  if (!isSessionSummary(summary)) throw new Error('invalid session rename response')
  return summary
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

export async function fetchPendingSessionCleanups(): Promise<SessionPendingCleanup[]> {
  return apiClient.request('/api/sessions/permanent-cleanups', {
    decode: (value) => {
      if (
        !isRecord(value)
        || !hasExactKeys(value, ['items', 'total'])
        || !Array.isArray(value.items)
        || !Number.isSafeInteger(value.total)
        || Number(value.total) < 0
      ) throw new Error('invalid pending cleanup response')
      const items = value.items.map((item) => {
        if (
          !isRecord(item)
          || !hasExactKeys(item, [
            'session_id', 'deleted_files', 'deleted_dirs', 'skipped_shared',
          ])
          || !Number.isSafeInteger(item.session_id)
          || Number(item.session_id) <= 0
          || !Number.isSafeInteger(item.deleted_files)
          || Number(item.deleted_files) < 0
          || !Number.isSafeInteger(item.deleted_dirs)
          || Number(item.deleted_dirs) < 0
          || !Number.isSafeInteger(item.skipped_shared)
          || Number(item.skipped_shared) < 0
        ) throw new Error('invalid pending cleanup response')
        return item as unknown as SessionPendingCleanup
      })
      if (items.length !== Number(value.total)) {
        throw new Error('invalid pending cleanup response')
      }
      return items
    },
  })
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

export async function createSessionDraft(
  name: string,
  curriculumVolumeId?: string | null,
): Promise<SessionSummary> {
  return apiClient.request('/api/sessions/drafts', {
    method: 'POST',
    body: {
      name: requireSessionName(name),
      ...(curriculumVolumeId ? { curriculum_volume_id: curriculumVolumeId } : {}),
    },
    decode: (value) => {
      if (!isSessionSummary(value)) throw new Error('invalid session draft response')
      return value
    },
  })
}

export async function renameSession(id: number, name: string): Promise<SessionSummary> {
  return updateSessionMetadata(id, { name: requireSessionName(name) })
}

export async function updateSessionMetadata(
  id: number,
  update: SessionMetadataUpdate,
): Promise<SessionSummary> {
  const sessionId = requireSessionId(id)
  const body: SessionMetadataUpdate = {}
  if (update.name !== undefined) body.name = requireSessionName(update.name)
  if (update.curriculum_volume_id !== undefined) {
    body.curriculum_volume_id = update.curriculum_volume_id?.trim() || null
  }
  if (!Object.keys(body).length) throw new Error('没有需要保存的考试信息')
  return apiClient.request(`/api/sessions/${sessionId}`, {
    method: 'PATCH',
    body,
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
          'storage_cleanup_pending',
          'recovered_interrupted_delete',
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
        || typeof value.storage_cleanup_pending !== 'boolean'
        || typeof value.recovered_interrupted_delete !== 'boolean'
      ) throw new Error('invalid permanent deletion response')
      return value as unknown as SessionPermanentDeletionResponse
    },
    timeoutMs: 60_000,
  })
}
import { apiClient } from './client'
import { hasExactKeys, isNullableString, isRecord, isRevision } from './validation'
