import { apiClient } from './client'
import { decodeJobResponse, type JobResponse } from './jobs'
import { assertNoPathLikeKeys, isNullableString, isRecord } from './validation'

export const QUESTION_TYPES = [
  'choice', 'fill_blank', 'calculation', 'proof', 'comprehensive',
] as const
export type QuestionType = (typeof QUESTION_TYPES)[number]
export type GenerationMode = 'per_question' | 'whole_document'

export interface QuestionDecision {
  question_id: string
  question_type: QuestionType
  excluded: boolean
}

export interface ConfigQuestionPreview {
  question_id: string
  question_type: string
  question_preview: string
  answer_preview: string
  answer_present: boolean
  needs_review: boolean
  local_answer_trusted: boolean
  has_question_asset: boolean
  has_answer_asset: boolean
}

export interface ConfigSource {
  session_id: number
  source_id: string
  source_revision: string
  safe_filename: string
  suffix: '.docx' | '.pdf'
  size_bytes: number
  sha256_prefix: string
  parse_state: 'ready'
  questions: ConfigQuestionPreview[]
}

export interface ConfigSourceSubmission {
  status: 'processing' | 'succeeded' | 'failed' | 'replaced'
  source: ConfigSource | null
}

export interface ConfigGenerationRequest {
  source_id: string
  source_revision: string
  generation_mode: GenerationMode
  decisions: QuestionDecision[]
  client_request_token?: string
}

export interface ConfigGenerationRetryRequest {
  source_job_id: number
  retry_question_ids: string[]
  client_request_token?: string
}

export interface ConfigEditorRow {
  row_id: string
  question_id: string
  part_id: string
  step_id: string
  part_label: string
  question_type: string
  core_goal: string
  score: number
  standard_answer: string
  accepted_answers: string[]
  match_rule: string
  knowledge: string
  answer_only_max_score: number | null
  require_final_answer: boolean | null
  required_elements: string[]
  deduction_rules: string[]
  final_answer_rule: string
}

export interface ConfigEditorIssue {
  code: string
  severity: string
  row_id: string | null
  field: string
  message: string
}

export interface ConfigEditorSource {
  safe_filename: string
  suffix: '.docx' | '.pdf'
  sha256_prefix: string
}

export interface ConfigEditorResponse {
  session_id: number
  configured: boolean
  revision: string
  rows: ConfigEditorRow[]
  total_score: number
  issues: ConfigEditorIssue[]
  source: ConfigEditorSource | null
}

export interface ConfigEditorEdit {
  row_id: string
  score?: number | null
  standard_answer?: string | null
  accepted_answers?: string[] | null
  answer_only_max_score?: number | null
  require_final_answer?: boolean | null
  required_elements?: string[] | null
  deduction_rules?: string[] | null
  final_answer_rule?: string | null
}

export interface ManualPartInput { part_id: string; score: number; core_goal: string }
export type ConfigEditorCommand =
  | { kind: 'split'; question_id: string; count: number; style: 'subquestion' | 'blank' }
  | { kind: 'replace_parts'; question_id: string; parts: ManualPartInput[] }

export interface ConfigEditorSaveRequest {
  revision: string
  edits: ConfigEditorEdit[]
  commands: ConfigEditorCommand[]
}

export interface ConfigEditorRefineRequest {
  revision: string
  commands: ConfigEditorCommand[]
  client_request_token?: string
}

export interface ConfigEditorSaveResponse extends ConfigEditorResponse {
  save_result: {
    config_saved: boolean
    mapping_status: 'not_present' | 'refreshed' | 'reconfirm_required'
    mapping_message: string
  }
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const expected = [...keys].sort()
  const actual = Object.keys(value).sort()
  return actual.length === expected.length && actual.every((key, index) => key === expected[index])
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isStringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function isQuestionPreview(value: unknown): value is ConfigQuestionPreview {
  if (!isRecord(value) || !hasExactKeys(value, [
    'question_id', 'question_type', 'question_preview', 'answer_preview', 'answer_present',
    'needs_review', 'local_answer_trusted', 'has_question_asset', 'has_answer_asset',
  ])) return false
  return typeof value.question_id === 'string' && typeof value.question_type === 'string'
    && typeof value.question_preview === 'string' && typeof value.answer_preview === 'string'
    && typeof value.answer_present === 'boolean' && typeof value.needs_review === 'boolean'
    && typeof value.local_answer_trusted === 'boolean'
    && typeof value.has_question_asset === 'boolean' && typeof value.has_answer_asset === 'boolean'
}

function isSafeBasename(value: unknown): value is string {
  if (typeof value !== 'string' || !value || value === '.' || value === '..') return false
  if (/[\\/:]/.test(value) || /(^|[\\/])\.\.?([\\/]|$)/.test(value)) return false
  return !/^[a-z]:/i.test(value) && !value.startsWith('\\\\')
}

function decodeConfigSource(value: unknown): ConfigSource {
  assertNoPathLikeKeys(value)
  if (!isRecord(value) || !hasExactKeys(value, [
    'session_id', 'source_id', 'source_revision', 'safe_filename', 'suffix', 'size_bytes',
    'sha256_prefix', 'parse_state', 'questions',
  ]) || !isPositiveInteger(value.session_id) || typeof value.source_id !== 'string'
    || !/^[0-9a-f]{32}$/.test(value.source_id) || typeof value.source_revision !== 'string'
    || !/^[0-9a-f]{64}$/.test(value.source_revision) || !isSafeBasename(value.safe_filename)
    || (value.suffix !== '.docx' && value.suffix !== '.pdf') || !isPositiveInteger(value.size_bytes)
    || typeof value.sha256_prefix !== 'string' || !/^[0-9a-f]{12}$/.test(value.sha256_prefix)
    || value.parse_state !== 'ready' || !Array.isArray(value.questions)
    || !value.questions.every(isQuestionPreview)) throw new Error('Invalid config source response')
  return value as unknown as ConfigSource
}

function isEditorRow(value: unknown): value is ConfigEditorRow {
  if (!isRecord(value) || !hasExactKeys(value, [
    'row_id', 'question_id', 'part_id', 'step_id', 'part_label', 'question_type', 'core_goal',
    'score', 'standard_answer', 'accepted_answers', 'match_rule', 'knowledge',
    'answer_only_max_score', 'require_final_answer', 'required_elements', 'deduction_rules',
    'final_answer_rule',
  ])) return false
  return ['row_id', 'question_id', 'part_id', 'step_id', 'part_label', 'question_type',
    'core_goal', 'standard_answer', 'match_rule', 'knowledge', 'final_answer_rule']
    .every((key) => typeof value[key] === 'string')
    && isFiniteNumber(value.score)
    && (value.answer_only_max_score === null || isFiniteNumber(value.answer_only_max_score))
    && (value.require_final_answer === null || typeof value.require_final_answer === 'boolean')
    && isStringList(value.accepted_answers) && isStringList(value.required_elements)
    && isStringList(value.deduction_rules)
}

function isEditorIssue(value: unknown): value is ConfigEditorIssue {
  return isRecord(value) && hasExactKeys(value, ['code', 'severity', 'row_id', 'field', 'message'])
    && typeof value.code === 'string' && typeof value.severity === 'string'
    && isNullableString(value.row_id) && typeof value.field === 'string'
    && typeof value.message === 'string'
}

function isEditorSource(value: unknown): value is ConfigEditorSource {
  return isRecord(value) && hasExactKeys(value, ['safe_filename', 'suffix', 'sha256_prefix'])
    && isSafeBasename(value.safe_filename) && (value.suffix === '.docx' || value.suffix === '.pdf')
    && typeof value.sha256_prefix === 'string' && /^[0-9a-f]{12}$/.test(value.sha256_prefix)
}

function decodeEditor(value: unknown): ConfigEditorResponse {
  assertNoPathLikeKeys(value)
  if (!isRecord(value) || !hasExactKeys(value, [
    'session_id', 'configured', 'revision', 'rows', 'total_score', 'issues', 'source',
  ]) || !isPositiveInteger(value.session_id) || typeof value.configured !== 'boolean'
    || typeof value.revision !== 'string' || !/^[0-9a-f]{64}$/.test(value.revision)
    || !Array.isArray(value.rows) || !value.rows.every(isEditorRow)
    || !isFiniteNumber(value.total_score) || !Array.isArray(value.issues)
    || !value.issues.every(isEditorIssue) || (value.source !== null && !isEditorSource(value.source))) {
    throw new Error('Invalid config editor response')
  }
  return value as unknown as ConfigEditorResponse
}

function decodeStrictJob(value: unknown): JobResponse {
  assertNoPathLikeKeys(value)
  if (!isRecord(value) || !hasExactKeys(value, [
    'id', 'job_type', 'payload', 'result', 'status', 'progress', 'stage', 'detail', 'error',
    'cancel_requested', 'created_at', 'started_at', 'updated_at', 'finished_at',
  ])) throw new Error('Invalid job response')
  return decodeJobResponse(value)
}

function requireSessionId(id: number): number {
  if (!isPositiveInteger(id)) throw new Error('Invalid session id')
  return id
}

function requireSourceId(id: string): string {
  if (!/^[0-9a-f]{32}$/.test(id)) throw new Error('Invalid source id')
  return id
}

function requireRequestToken(token: string): string {
  if (!/^[0-9a-f]{32}$/.test(token)) throw new Error('Invalid client request token')
  return token
}

export function createClientRequestToken(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('')
}

export async function uploadConfigSource(
  sessionId: number, file: File, requestToken = createClientRequestToken(),
): Promise<ConfigSource> {
  const id = requireSessionId(sessionId)
  if (!(file instanceof File) || !file.name.trim()) throw new Error('Invalid source file')
  return apiClient.request(`/api/sessions/${id}/config/sources`, {
    method: 'POST', rawBody: file,
    headers: {
      'content-type': 'application/octet-stream',
      'x-upload-filename': encodeURIComponent(file.name),
      'x-client-request-token': requireRequestToken(requestToken),
    },
    timeoutMs: 10 * 60 * 1_000,
    decode: decodeConfigSource,
  })
}

export async function fetchConfigSourceSubmission(
  sessionId: number, requestToken: string,
): Promise<ConfigSourceSubmission> {
  const id = requireSessionId(sessionId)
  return apiClient.request(
    `/api/sessions/${id}/config/sources/submissions/${requireRequestToken(requestToken)}`,
    {
      decode: (value) => {
        assertNoPathLikeKeys(value)
        if (!isRecord(value) || !hasExactKeys(value, ['status', 'source'])
          || !['processing', 'succeeded', 'failed', 'replaced'].includes(String(value.status))) {
          throw new Error('Invalid source submission response')
        }
        if (value.source === null) {
          return { status: value.status, source: null } as ConfigSourceSubmission
        }
        return { status: value.status, source: decodeConfigSource(value.source) } as ConfigSourceSubmission
      },
    },
  )
}

export async function fetchConfigSource(
  sessionId: number, sourceId: string, signal?: AbortSignal,
): Promise<ConfigSource> {
  const id = requireSessionId(sessionId)
  return apiClient.request(`/api/sessions/${id}/config/sources/${requireSourceId(sourceId)}`, {
    decode: decodeConfigSource, signal,
  })
}

export async function fetchActiveConfigSource(sessionId: number): Promise<ConfigSource> {
  const id = requireSessionId(sessionId)
  return apiClient.request(`/api/sessions/${id}/config/sources/active`, {
    decode: decodeConfigSource,
  })
}

export async function fetchLatestConfigGenerationJob(
  sessionId: number,
  request: ConfigGenerationRequest,
): Promise<JobResponse> {
  const id = requireSessionId(sessionId)
  const sourceId = requireSourceId(request.source_id)
  if (!/^[0-9a-f]{64}$/.test(request.source_revision)
    || !['per_question', 'whole_document'].includes(request.generation_mode)) {
    throw new Error('Invalid generation lookup')
  }
  const query = new URLSearchParams({
    source_id: sourceId,
    source_revision: request.source_revision,
    generation_mode: request.generation_mode,
  })
  return apiClient.request(`/api/sessions/${id}/config/generation-jobs/latest?${query}`, {
    decode: decodeStrictJob,
  })
}

export async function fetchConfigGenerationJobByToken(
  sessionId: number, requestToken: string,
): Promise<JobResponse> {
  const id = requireSessionId(sessionId)
  return apiClient.request(
    `/api/sessions/${id}/config/generation-jobs/requests/${requireRequestToken(requestToken)}`,
    { decode: decodeStrictJob },
  )
}

export async function submitConfigGeneration(
  sessionId: number, request: ConfigGenerationRequest,
): Promise<JobResponse> {
  const id = requireSessionId(sessionId)
  return apiClient.request(`/api/sessions/${id}/config/generate-from-source`, {
    method: 'POST', body: request, decode: decodeStrictJob,
  })
}

export async function retryConfigGeneration(
  sessionId: number,
  sourceJobId: number,
  retryQuestionIds: string[],
  requestToken?: string,
): Promise<JobResponse> {
  const id = requireSessionId(sessionId)
  if (!isPositiveInteger(sourceJobId)) throw new Error('Invalid source Job id')
  const questionIds = [...new Set(retryQuestionIds.map((item) => item.trim()))]
  if (questionIds.length === 0 || questionIds.some((item) => !item || item.length > 100)) {
    throw new Error('Invalid retry question ids')
  }
  const request: ConfigGenerationRetryRequest = {
    source_job_id: sourceJobId,
    retry_question_ids: questionIds,
  }
  if (requestToken) request.client_request_token = requireRequestToken(requestToken)
  return apiClient.request(`/api/sessions/${id}/config/generate/retry`, {
    method: 'POST', body: request, decode: decodeStrictJob,
  })
}

export async function fetchConfigEditor(sessionId: number, signal?: AbortSignal): Promise<ConfigEditorResponse> {
  const id = requireSessionId(sessionId)
  return apiClient.request(`/api/sessions/${id}/config/editor`, { decode: decodeEditor, signal })
}

export async function saveConfigEditor(
  sessionId: number, request: ConfigEditorSaveRequest,
): Promise<ConfigEditorSaveResponse> {
  const id = requireSessionId(sessionId)
  return apiClient.request(`/api/sessions/${id}/config/editor`, {
    method: 'PUT', body: request,
    decode: (value) => {
      if (!isRecord(value) || !hasExactKeys(value, [
        'session_id', 'configured', 'revision', 'rows', 'total_score', 'issues', 'source', 'save_result',
      ]) || !isRecord(value.save_result) || !hasExactKeys(value.save_result, [
        'config_saved', 'mapping_status', 'mapping_message',
      ])) throw new Error('Invalid config save response')
      const { save_result, ...editorValue } = value
      const decoded = decodeEditor(editorValue)
      if (typeof save_result.config_saved !== 'boolean'
        || !['not_present', 'refreshed', 'reconfirm_required'].includes(String(save_result.mapping_status))
        || typeof save_result.mapping_message !== 'string') throw new Error('Invalid config save result')
      return { ...decoded, save_result } as ConfigEditorSaveResponse
    },
  })
}

export async function refineConfigEditor(
  sessionId: number, request: ConfigEditorRefineRequest,
): Promise<JobResponse> {
  const id = requireSessionId(sessionId)
  return apiClient.request(`/api/sessions/${id}/config/editor/refine`, {
    method: 'POST', body: request, decode: decodeStrictJob,
  })
}
