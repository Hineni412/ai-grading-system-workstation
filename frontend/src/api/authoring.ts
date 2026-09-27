import { apiClient } from './client'
import { decodeQuestionDetailResponse, type QuestionBankDetail } from './question-bank'
import { assertNoPathLikeKeys, isRecord } from './validation'

export const AUTHORING_SOLO_LEVELS = [
  'prestructural',
  'unistructural',
  'multistructural',
  'relational',
  'extended_abstract',
] as const

export type AuthoringSoloLevel = (typeof AUTHORING_SOLO_LEVELS)[number]

export const AUTHORING_SOLO_LABELS: Record<AuthoringSoloLevel, string> = {
  prestructural: '前结构',
  unistructural: '单点结构',
  multistructural: '多点结构',
  relational: '关联结构',
  extended_abstract: '拓展抽象结构',
}

export const AUTHORING_QUESTION_TYPES = ['选择题', '多选题', '填空题', '解答题'] as const

export type AuthoringKind = 'decompose' | 'adapt'

export const AUTHORING_KIND_LABELS: Record<AuthoringKind, string> = {
  decompose: '拆解练习',
  adapt: '改编练习',
}

export interface AuthoringTaskCard {
  id: string
  label: string
  method: string
  description: string
}

export interface AuthoringTaskCardListResponse {
  items: AuthoringTaskCard[]
}

export interface AuthoringJudgmentPoints {
  points: Array<Record<string, unknown>>
  auxiliary_rules: string[]
  rationale: string
}

export interface AuthoringSourceSnapshot {
  question_id: number
  question: QuestionBankDetail
  judgment_points: AuthoringJudgmentPoints | null
  part_assessments: Array<Record<string, unknown>>
  error_patterns: Array<Record<string, unknown>>
  captured_at: string
}

export interface AuthoringWorkSummary {
  work_id: string
  kind: AuthoringKind
  title: string
  source_question_id: number | null
  source_question_number: string
  source_question_snippet: string
  current_version: number
  created_at: string
  updated_at: string
}

export interface AuthoringWorkListResponse {
  items: AuthoringWorkSummary[]
  total: number
}

export interface AuthoringTaskCardTargets {
  keep_knowledge: boolean
  target_difficulty: number | null
  target_solo: AuthoringSoloLevel | null
  note: string
}

export interface AuthoringTaskCardSelection {
  method_id: string
  targets: AuthoringTaskCardTargets
}

export interface AuthoringVersionSummary {
  version_no: number
  created_at: string
}

export type AuthoringContent = Record<string, unknown>

export interface AuthoringWorkDetail extends AuthoringWorkSummary {
  source_snapshot: AuthoringSourceSnapshot
  /** 拆解练习没有任务卡，服务端返回 {}，解码时归一为 null。 */
  task_card: AuthoringTaskCardSelection | null
  versions: AuthoringVersionSummary[]
  latest_content: AuthoringContent | null
}

export interface AuthoringVersionResponse {
  work_id: string
  version_no: number
  content: AuthoringContent
  created_at: string
  current_version: number
}

export interface AuthoringDeleteResponse {
  work_id: string
  deleted: boolean
}

export interface AuthoringPartEntry {
  part_label: string
  predicted_difficulty: number | null
  predicted_solo: AuthoringSoloLevel | null
}

export interface AuthoringCreateWorkInput {
  kind: AuthoringKind
  source_question_id: number
  title?: string
  task_card?: {
    method_id: string
    targets?: Partial<AuthoringTaskCardTargets>
  }
  operation_token: string
}

export interface AuthoringSaveVersionInput {
  content: AuthoringContent
  base_version: number
  operation_token: string
}

function isHexToken(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{32}$/.test(value)
}

function isWorkId(value: unknown): value is string {
  return isHexToken(value)
}

function isNonnegativeInt(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0
}

function isPositiveInt(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0
}

function isSoloLevel(value: unknown): value is AuthoringSoloLevel {
  return typeof value === 'string'
    && (AUTHORING_SOLO_LEVELS as readonly string[]).includes(value)
}

function isNullableDifficulty(value: unknown): value is number | null {
  return value === null || (
    Number.isSafeInteger(value) && Number(value) >= 1 && Number(value) <= 10
  )
}

function isAuthoringKind(value: unknown): value is AuthoringKind {
  return value === 'decompose' || value === 'adapt'
}

function isTaskCard(value: unknown): value is AuthoringTaskCard {
  return (
    isRecord(value)
    && typeof value.id === 'string'
    && typeof value.label === 'string'
    && typeof value.method === 'string'
    && typeof value.description === 'string'
  )
}

function isJudgmentPoints(value: unknown): value is AuthoringJudgmentPoints {
  return (
    isRecord(value)
    && Array.isArray(value.points)
    && value.points.every(isRecord)
    && Array.isArray(value.auxiliary_rules)
    && value.auxiliary_rules.every((item) => typeof item === 'string')
    && typeof value.rationale === 'string'
  )
}

function isSourceSnapshot(value: unknown): value is AuthoringSourceSnapshot {
  if (!isRecord(value)) return false
  try {
    decodeQuestionDetailResponse(value.question)
  } catch {
    return false
  }
  return (
    isPositiveInt(value.question_id)
    && (value.judgment_points === null || isJudgmentPoints(value.judgment_points))
    && Array.isArray(value.part_assessments)
    && value.part_assessments.every(isRecord)
    && Array.isArray(value.error_patterns)
    && value.error_patterns.every(isRecord)
    && typeof value.captured_at === 'string'
  )
}

function isWorkSummary(value: unknown): value is AuthoringWorkSummary {
  return (
    isRecord(value)
    && isWorkId(value.work_id)
    && isAuthoringKind(value.kind)
    && typeof value.title === 'string'
    && (value.source_question_id === null || isPositiveInt(value.source_question_id))
    && typeof value.source_question_number === 'string'
    && typeof value.source_question_snippet === 'string'
    && isNonnegativeInt(value.current_version)
    && typeof value.created_at === 'string'
    && typeof value.updated_at === 'string'
  )
}

function isTaskCardSelection(value: unknown): value is AuthoringTaskCardSelection {
  if (!isRecord(value)) return false
  const targets = value.targets
  return (
    typeof value.method_id === 'string'
    && isRecord(targets)
    && typeof targets.keep_knowledge === 'boolean'
    && isNullableDifficulty(targets.target_difficulty)
    && (targets.target_solo === null || isSoloLevel(targets.target_solo))
    && typeof targets.note === 'string'
  )
}

function isVersionSummary(value: unknown): value is AuthoringVersionSummary {
  return (
    isRecord(value)
    && isPositiveInt(value.version_no)
    && typeof value.created_at === 'string'
  )
}

function decodeTaskCardList(value: unknown): AuthoringTaskCardListResponse {
  if (
    !isRecord(value)
    || !Array.isArray(value.items)
    || !value.items.every(isTaskCard)
  ) {
    throw new Error('Invalid task card list')
  }
  assertNoPathLikeKeys(value)
  return value as unknown as AuthoringTaskCardListResponse
}

function decodeWorkList(value: unknown): AuthoringWorkListResponse {
  if (
    !isRecord(value)
    || !Array.isArray(value.items)
    || !value.items.every(isWorkSummary)
    || !isNonnegativeInt(value.total)
  ) {
    throw new Error('Invalid authoring work list')
  }
  return value as unknown as AuthoringWorkListResponse
}

function decodeWorkDetail(value: unknown): AuthoringWorkDetail {
  if (!isRecord(value) || !isWorkSummary(value)) {
    throw new Error('Invalid authoring work detail')
  }
  const taskCard = value.task_card
  if (
    !isSourceSnapshot(value.source_snapshot)
    || !isRecord(taskCard)
    || (Object.keys(taskCard).length > 0 && !isTaskCardSelection(taskCard))
    || !Array.isArray(value.versions)
    || !value.versions.every(isVersionSummary)
    || !(value.latest_content === null || isRecord(value.latest_content))
  ) {
    throw new Error('Invalid authoring work detail')
  }
  assertNoPathLikeKeys(value)
  const detail = value as unknown as AuthoringWorkDetail
  if (Object.keys(taskCard).length === 0) {
    detail.task_card = null
  }
  return detail
}

function decodeVersionResponse(value: unknown): AuthoringVersionResponse {
  if (
    !isRecord(value)
    || !isWorkId(value.work_id)
    || !isPositiveInt(value.version_no)
    || !isRecord(value.content)
    || typeof value.created_at !== 'string'
    || !isNonnegativeInt(value.current_version)
  ) {
    throw new Error('Invalid authoring version')
  }
  return value as unknown as AuthoringVersionResponse
}

function decodeDeleteResponse(value: unknown): AuthoringDeleteResponse {
  if (
    !isRecord(value)
    || !isWorkId(value.work_id)
    || typeof value.deleted !== 'boolean'
  ) {
    throw new Error('Invalid authoring delete response')
  }
  return value as unknown as AuthoringDeleteResponse
}

export function authoringOperationToken(): string {
  return globalThis.crypto.randomUUID().replace(/-/g, '')
}

export const authoringApi = {
  listTaskCards(signal?: AbortSignal): Promise<AuthoringTaskCardListResponse> {
    return apiClient.request('/api/authoring/task-cards', {
      decode: decodeTaskCardList,
      signal,
    })
  },

  listWorks(
    kind?: AuthoringKind,
    signal?: AbortSignal,
  ): Promise<AuthoringWorkListResponse> {
    const query = kind ? `?kind=${kind}` : ''
    return apiClient.request(`/api/authoring/works${query}`, {
      decode: decodeWorkList,
      signal,
    })
  },

  createWork(
    input: AuthoringCreateWorkInput,
    signal?: AbortSignal,
  ): Promise<AuthoringWorkDetail> {
    if (
      !isAuthoringKind(input.kind)
      || !isPositiveInt(input.source_question_id)
      || !isHexToken(input.operation_token)
    ) {
      throw new Error('Invalid authoring work create')
    }
    return apiClient.request('/api/authoring/works', {
      method: 'POST',
      body: {
        kind: input.kind,
        source_question_id: input.source_question_id,
        title: input.title ?? '',
        task_card: input.task_card,
        operation_token: input.operation_token,
      },
      decode: decodeWorkDetail,
      signal,
    })
  },

  getWork(
    workId: string,
    signal?: AbortSignal,
  ): Promise<AuthoringWorkDetail> {
    if (!isWorkId(workId)) throw new Error('Invalid authoring work id')
    return apiClient.request(`/api/authoring/works/${workId}`, {
      decode: decodeWorkDetail,
      signal,
    })
  },

  saveVersion(
    workId: string,
    input: AuthoringSaveVersionInput,
    signal?: AbortSignal,
  ): Promise<AuthoringVersionResponse> {
    if (
      !isWorkId(workId)
      || !isRecord(input.content)
      || !isNonnegativeInt(input.base_version)
      || !isHexToken(input.operation_token)
    ) {
      throw new Error('Invalid authoring version save')
    }
    return apiClient.request(`/api/authoring/works/${workId}/versions`, {
      method: 'POST',
      body: input,
      decode: decodeVersionResponse,
      signal,
    })
  },

  getVersion(
    workId: string,
    versionNo: number,
    signal?: AbortSignal,
  ): Promise<AuthoringVersionResponse> {
    if (!isWorkId(workId) || !isPositiveInt(versionNo)) {
      throw new Error('Invalid authoring version request')
    }
    return apiClient.request(`/api/authoring/works/${workId}/versions/${versionNo}`, {
      decode: decodeVersionResponse,
      signal,
    })
  },

  deleteWork(
    workId: string,
    signal?: AbortSignal,
  ): Promise<AuthoringDeleteResponse> {
    if (!isWorkId(workId)) throw new Error('Invalid authoring work id')
    return apiClient.request(`/api/authoring/works/${workId}`, {
      method: 'DELETE',
      decode: decodeDeleteResponse,
      signal,
    })
  },
}
