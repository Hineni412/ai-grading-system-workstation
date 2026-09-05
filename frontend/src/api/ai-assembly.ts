import { apiClient } from './client'
import { decodeJobResponse, type JobResponse } from './jobs'
import { isRecord } from './validation'

export interface AiAssemblyPreflight {
  configured: boolean
  service_name: string | null
  model_name: string | null
  call_count: 1
  estimated_total_tokens: number
}

export interface AiAssemblySpecRow {
  question_type: string
  count: number
  knowledge_points: string[]
  difficulty: number | null
  score: number | null
  essay_subtype?: AiAssemblyEssaySubtype | null
}

// 解答题子类的封闭取值，与后端 EssaySubtype 一致。
export type AiAssemblyEssaySubtype = '画图' | '计算' | '证明'

export interface AiAssemblySpec {
  title: string
  rows: AiAssemblySpecRow[]
  scope_knowledge_points: string[]
}

export interface AiAssemblySpecRequest {
  template_paper_id?: number
  scope_keys: string[]
  difficulty_ratio: Record<string, number>
  type_counts: Record<string, number>
  exam_types: string[]
  years: number[]
  free_text: string
  essay_subtype?: AiAssemblyEssaySubtype
  current_spec?: AiAssemblySpec
  locked_question_ids: number[]
  new_instruction: string
}

export interface AiAssemblySpecJobResult {
  spec: AiAssemblySpec
  model_name: string
}

export interface AiAssemblyRelaxation {
  step: string
  title: string
  sacrifice: string
  knowledge_points: string[]
}

export interface AiAssemblyGap {
  row_index: number
  missing: number
  candidates: number
  excluded_by_dedupe: number
  suggestions: AiAssemblyRelaxation[]
}

export interface AiAssemblySelectResult {
  rows: Array<{ row_index: number; question_ids: number[] }>
  gaps: AiAssemblyGap[]
}

export interface AiAssemblyTemplateEntry {
  question_number: string
  question_type: string
  difficulty: number | null
  score: number | null
  // 同题型同难度的连续行合并数；模板摘要必须按它汇总题数，不能逐 entry 计数。
  count: number
}

export interface AiAssemblyTemplateStructure {
  paper_id: number
  paper_title: string
  entries: AiAssemblyTemplateEntry[]
}

export interface AiAssemblySelectOptions {
  dedupe_enabled?: boolean
  exclude_ids?: number[]
}

export interface AiAssemblySessionParams {
  template_paper_id: number | null
  scope_keys: string[]
  difficulty_ratio: { easy: number | null; medium: number | null; hard: number | null }
  type_counts: Record<string, number>
  exam_types: string[]
  years: number[]
  free_text: string
  essay_subtype: AiAssemblyEssaySubtype | null
}

export interface AiAssemblySession {
  params: AiAssemblySessionParams
  spec: AiAssemblySpec | null
  spec_model_name: string
  selections: Record<number, number[]>
  locked_question_ids: number[]
  locked_row_by_id: Record<number, number>
  gaps: AiAssemblyGap[]
  dedupe_enabled: boolean
  title: string
  spec_job_id: number | null
  updated_at: string
  revision: string
}

export type AiAssemblySessionWrite = Omit<AiAssemblySession, 'updated_at' | 'revision'>

function isNullableString(value: unknown): value is string | null {
  return typeof value === 'string' || value === null
}

function isStringList(value: unknown, max = 500): value is string[] {
  return (
    Array.isArray(value) &&
    value.length <= max &&
    value.every((item) => typeof item === 'string' && item.length <= 200)
  )
}

function isIdList(value: unknown): value is number[] {
  return (
    Array.isArray(value) &&
    value.length <= 500 &&
    value.every((item) => Number.isSafeInteger(item) && Number(item) > 0)
  )
}

function isSpecRow(value: unknown): value is AiAssemblySpecRow {
  return (
    isRecord(value) &&
    (value.essay_subtype === undefined || value.essay_subtype === null || (
      value.question_type === '解答题' && isEssaySubtype(value.essay_subtype)
    )) &&
    typeof value.question_type === 'string' &&
    value.question_type.length > 0 &&
    value.question_type.length <= 40 &&
    Number.isSafeInteger(value.count) &&
    Number(value.count) >= 1 &&
    Number(value.count) <= 200 &&
    isStringList(value.knowledge_points, 50) &&
    (value.difficulty === null || (
      Number.isSafeInteger(value.difficulty) &&
      Number(value.difficulty) >= 1 &&
      Number(value.difficulty) <= 9
    )) &&
    (value.score === null || (
      typeof value.score === 'number' &&
      Number.isFinite(value.score) &&
      value.score > 0 &&
      value.score <= 1000
    ))
  )
}

export function decodeAiAssemblySpec(value: unknown): AiAssemblySpec {
  if (
    !isRecord(value) ||
    typeof value.title !== 'string' ||
    value.title.length > 120 ||
    !Array.isArray(value.rows) ||
    value.rows.length < 1 ||
    value.rows.length > 100 ||
    !value.rows.every(isSpecRow) ||
    !isStringList(value.scope_knowledge_points)
  ) {
    throw new Error('Invalid AI assembly spec')
  }
  return value as unknown as AiAssemblySpec
}

export function decodeAiAssemblySpecJobResult(value: unknown): AiAssemblySpecJobResult {
  if (
    !isRecord(value) ||
    typeof value.model_name !== 'string' ||
    !value.model_name.trim()
  ) {
    throw new Error('Invalid AI assembly spec job result')
  }
  return {
    spec: decodeAiAssemblySpec(value.spec),
    model_name: value.model_name,
  }
}

function decodePreflight(value: unknown): AiAssemblyPreflight {
  if (
    !isRecord(value) ||
    typeof value.configured !== 'boolean' ||
    !isNullableString(value.service_name) ||
    !isNullableString(value.model_name) ||
    value.call_count !== 1 ||
    !Number.isSafeInteger(value.estimated_total_tokens) ||
    Number(value.estimated_total_tokens) < 0
  ) {
    throw new Error('Invalid AI assembly preflight')
  }
  return value as unknown as AiAssemblyPreflight
}

function isRelaxation(value: unknown): value is AiAssemblyRelaxation {
  return (
    isRecord(value) &&
    typeof value.step === 'string' &&
    typeof value.title === 'string' &&
    typeof value.sacrifice === 'string' &&
    isStringList(value.knowledge_points)
  )
}

function isGap(value: unknown): value is AiAssemblyGap {
  return (
    isRecord(value) &&
    Number.isSafeInteger(value.row_index) &&
    Number.isSafeInteger(value.missing) &&
    Number(value.missing) > 0 &&
    Number.isSafeInteger(value.candidates) &&
    Number(value.candidates) >= 0 &&
    Number.isSafeInteger(value.excluded_by_dedupe) &&
    Number(value.excluded_by_dedupe) >= 0 &&
    Array.isArray(value.suggestions) &&
    value.suggestions.every(isRelaxation)
  )
}

function decodeSelectResult(value: unknown): AiAssemblySelectResult {
  if (
    !isRecord(value) ||
    !Array.isArray(value.rows) ||
    !value.rows.every((row) => (
      isRecord(row) &&
      Number.isSafeInteger(row.row_index) &&
      Number(row.row_index) >= 0 &&
      isIdList(row.question_ids)
    )) ||
    !Array.isArray(value.gaps) ||
    !value.gaps.every(isGap)
  ) {
    throw new Error('Invalid AI assembly select result')
  }
  return value as unknown as AiAssemblySelectResult
}

function decodeTemplateStructure(value: unknown): AiAssemblyTemplateStructure {
  if (
    !isRecord(value) ||
    !Number.isSafeInteger(value.paper_id) ||
    Number(value.paper_id) <= 0 ||
    typeof value.paper_title !== 'string' ||
    !Array.isArray(value.entries) ||
    !value.entries.every((entry) => (
      isRecord(entry) &&
      typeof entry.question_number === 'string' &&
      typeof entry.question_type === 'string' &&
      (entry.difficulty === null || Number.isSafeInteger(entry.difficulty)) &&
      (entry.score === null || typeof entry.score === 'number') &&
      Number.isSafeInteger(entry.count) &&
      Number(entry.count) >= 1
    ))
  ) {
    throw new Error('Invalid AI assembly template structure')
  }
  return value as unknown as AiAssemblyTemplateStructure
}

const SESSION_REVISION = /^[0-9a-f]{64}$/
const ESSAY_SUBTYPES: readonly AiAssemblyEssaySubtype[] = ['画图', '计算', '证明']

function isEssaySubtype(value: unknown): value is AiAssemblyEssaySubtype {
  return typeof value === 'string' && (ESSAY_SUBTYPES as readonly string[]).includes(value)
}

function isDifficultyRatio(
  value: unknown,
): value is { easy: number | null; medium: number | null; hard: number | null } {
  if (!isRecord(value)) return false
  return (['easy', 'medium', 'hard'] as const).every((key) => {
    const entry = value[key]
    return entry === null || (
      Number.isSafeInteger(entry) && Number(entry) >= 0 && Number(entry) <= 100
    )
  })
}

function isTypeCounts(value: unknown): value is Record<string, number> {
  return (
    isRecord(value) &&
    Object.entries(value).length <= 40 &&
    Object.entries(value).every(([key, count]) => (
      key.trim().length > 0 &&
      key.length <= 40 &&
      Number.isSafeInteger(count) &&
      Number(count) > 0
    ))
  )
}

function isRowKey(key: string): boolean {
  return /^\d{1,4}$/.test(key)
}

function isSessionSelections(value: unknown): value is Record<number, number[]> {
  return (
    isRecord(value) &&
    Object.entries(value).every(([key, ids]) => isRowKey(key) && isIdList(ids))
  )
}

function isLockedRowById(value: unknown): value is Record<number, number> {
  return (
    isRecord(value) &&
    Object.entries(value).every(([key, row]) => (
      /^\d{1,10}$/.test(key) &&
      Number(key) > 0 &&
      Number.isSafeInteger(row) &&
      Number(row) >= 0
    ))
  )
}

function isSessionParams(value: unknown): value is AiAssemblySessionParams {
  return (
    isRecord(value) &&
    (value.template_paper_id === null || (
      Number.isSafeInteger(value.template_paper_id) &&
      Number(value.template_paper_id) > 0
    )) &&
    isStringList(value.scope_keys, 200) &&
    isDifficultyRatio(value.difficulty_ratio) &&
    isTypeCounts(value.type_counts) &&
    isStringList(value.exam_types, 20) &&
    Array.isArray(value.years) &&
    value.years.length <= 50 &&
    value.years.every((year) => (
      Number.isSafeInteger(year) && Number(year) >= 1990 && Number(year) <= 2100
    )) &&
    typeof value.free_text === 'string' &&
    value.free_text.length <= 2000 &&
    (value.essay_subtype === null || isEssaySubtype(value.essay_subtype))
  )
}

function decodeAiAssemblySession(value: unknown): AiAssemblySession {
  if (
    !isRecord(value) ||
    !isSessionParams(value.params) ||
    !(value.spec === null || isRecord(value.spec)) ||
    typeof value.spec_model_name !== 'string' ||
    value.spec_model_name.length > 200 ||
    !isSessionSelections(value.selections) ||
    !isIdList(value.locked_question_ids) ||
    !isLockedRowById(value.locked_row_by_id) ||
    !Array.isArray(value.gaps) ||
    !value.gaps.every(isGap) ||
    typeof value.dedupe_enabled !== 'boolean' ||
    typeof value.title !== 'string' ||
    value.title.length > 120 ||
    !(value.spec_job_id === null || (
      Number.isSafeInteger(value.spec_job_id) && Number(value.spec_job_id) > 0
    )) ||
    typeof value.updated_at !== 'string' ||
    typeof value.revision !== 'string' ||
    !SESSION_REVISION.test(value.revision)
  ) {
    throw new Error('Invalid AI assembly session')
  }
  return {
    ...(value as unknown as AiAssemblySession),
    spec: value.spec === null ? null : decodeAiAssemblySpec(value.spec),
  }
}

function cleanSpecRequest(request: AiAssemblySpecRequest): Record<string, unknown> {
  const body: Record<string, unknown> = {
    scope_keys: request.scope_keys.filter((key) => typeof key === 'string' && key.trim()),
    difficulty_ratio: Object.fromEntries(
      Object.entries(request.difficulty_ratio).filter(([, value]) => (
        Number.isSafeInteger(value) && value >= 0 && value <= 100
      )),
    ),
    type_counts: Object.fromEntries(
      Object.entries(request.type_counts)
        .filter(([key, value]) => key.trim() && Number.isSafeInteger(value) && value > 0),
    ),
    exam_types: request.exam_types.filter((item) => typeof item === 'string' && item.trim()),
    years: request.years.filter((year) => (
      Number.isSafeInteger(year) && year >= 1990 && year <= 2100
    )),
    free_text: request.free_text.slice(0, 2000),
    locked_question_ids: request.locked_question_ids.filter((id) => (
      Number.isSafeInteger(id) && id > 0
    )),
    new_instruction: request.new_instruction.slice(0, 2000),
  }
  if (request.template_paper_id !== undefined) {
    if (!Number.isSafeInteger(request.template_paper_id) || request.template_paper_id <= 0) {
      throw new Error('Invalid AI assembly template paper id')
    }
    body.template_paper_id = request.template_paper_id
  }
  if (request.essay_subtype !== undefined) {
    if (!isEssaySubtype(request.essay_subtype)) {
      throw new Error('Invalid AI assembly essay subtype')
    }
    body.essay_subtype = request.essay_subtype
  }
  if (request.current_spec !== undefined) body.current_spec = request.current_spec
  return body
}

export const aiAssemblyApi = {
  getPreflight(signal?: AbortSignal): Promise<AiAssemblyPreflight> {
    return apiClient.request('/api/question-assembly/ai/preflight', {
      decode: decodePreflight,
      signal,
    })
  },

  submitSpecJob(
    request: AiAssemblySpecRequest,
    signal?: AbortSignal,
  ): Promise<JobResponse> {
    return apiClient.request('/api/question-assembly/ai/spec-jobs', {
      method: 'POST',
      body: { request: cleanSpecRequest(request) },
      decode: decodeJobResponse,
      signal,
    })
  },

  select(
    spec: AiAssemblySpec,
    options: AiAssemblySelectOptions = {},
    signal?: AbortSignal,
  ): Promise<AiAssemblySelectResult> {
    decodeAiAssemblySpec(spec)
    const excludeIds = options.exclude_ids ?? []
    if (!isIdList(excludeIds)) throw new Error('Invalid AI assembly exclude ids')
    return apiClient.request('/api/question-assembly/ai/select', {
      method: 'POST',
      body: {
        spec,
        dedupe_enabled: options.dedupe_enabled ?? true,
        exclude_ids: excludeIds,
      },
      decode: decodeSelectResult,
      // 选题要在真实库上算全库频度，服务端可能需要几十秒，放宽等待上限。
      timeoutMs: 600_000,
      signal,
    })
  },

  getTemplateStructure(
    paperId: number,
    signal?: AbortSignal,
  ): Promise<AiAssemblyTemplateStructure> {
    if (!Number.isSafeInteger(paperId) || paperId <= 0) {
      throw new Error('Invalid AI assembly template paper id')
    }
    return apiClient.request(`/api/question-assembly/ai/template-structure/${paperId}`, {
      decode: decodeTemplateStructure,
      signal,
    })
  },

  getSession(signal?: AbortSignal): Promise<AiAssemblySession> {
    return apiClient.request('/api/question-assembly/ai/session', {
      decode: decodeAiAssemblySession,
      signal,
    })
  },

  saveSession(
    expectedRevision: string,
    session: AiAssemblySessionWrite,
    signal?: AbortSignal,
  ): Promise<AiAssemblySession> {
    if (!SESSION_REVISION.test(expectedRevision)) {
      throw new Error('Invalid AI assembly session revision')
    }
    return apiClient.request('/api/question-assembly/ai/session', {
      method: 'PUT',
      body: { expected_revision: expectedRevision, session },
      decode: decodeAiAssemblySession,
      signal,
    })
  },

  clearSession(signal?: AbortSignal): Promise<AiAssemblySession> {
    return apiClient.request('/api/question-assembly/ai/session', {
      method: 'DELETE',
      decode: decodeAiAssemblySession,
      signal,
    })
  },
}
