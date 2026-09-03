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
}

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
    !value.gaps.every((gap) => (
      isRecord(gap) &&
      Number.isSafeInteger(gap.row_index) &&
      Number.isSafeInteger(gap.missing) &&
      Number(gap.missing) > 0 &&
      Number.isSafeInteger(gap.candidates) &&
      Number(gap.candidates) >= 0 &&
      Number.isSafeInteger(gap.excluded_by_dedupe) &&
      Number(gap.excluded_by_dedupe) >= 0 &&
      Array.isArray(gap.suggestions) &&
      gap.suggestions.every(isRelaxation)
    ))
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
      (entry.score === null || typeof entry.score === 'number')
    ))
  ) {
    throw new Error('Invalid AI assembly template structure')
  }
  return value as unknown as AiAssemblyTemplateStructure
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
}
