import { apiClient } from './client'
import { decodeJobResponse, type JobResponse } from './jobs'
import { decodeChapterTypePlan, type ChapterTypePlan, type ChapterTypeAuthorization, type QuestionBankRichContent } from './question-bank'
import {
  assertNoPathLikeKeys,
  hasExactKeys,
  isFiniteNumber,
  isNullableString,
  isPositiveInteger,
  isRecord,
  isStringArray,
} from './validation'

export const QUESTION_TYPES = [
  'choice', 'fill_blank', 'calculation', 'proof', 'comprehensive',
] as const
export type QuestionType = (typeof QUESTION_TYPES)[number]
export type GenerationMode = 'batched'

export interface QuestionDecision {
  question_id: string
  excluded: boolean
  /** Legacy fields are accepted on old persisted clients but are no longer emitted. */
  question_type?: QuestionType
  answer_confirmed?: boolean
  answer_override?: string | null
  /** Teacher's call on a duplicate match against the question bank. */
  bank_match?: 'same' | 'different' | 'reanalyze' | null
  bank_question_id?: number | null
}

export interface ConfigAmbiguousAsset {
  candidate_id: string
  previous_question_id: string
  next_question_id: string
  source_section: 'question' | 'answer'
  asset_url: string
}

export interface ConfigSourceAsset {
  asset_id: string
  asset_url: string
  assignment_state: 'automatic' | 'uncertain'
  question_id: string | null
  asset_kind: 'question' | 'answer'
  candidate_question_ids: string[]
}

export type ConfigAmbiguousAssetDecision = {
  candidate_id: string
  action: 'ignore'
} | {
  candidate_id: string
  action: 'bind'
  question_id: string
  asset_kind: 'question' | 'answer'
}

export interface ConfigQuestionPreview {
  question_id: string
  question_type: string
  question_preview: string
  answer_preview: string
  answer_present: boolean
  needs_review: boolean
  question_type_review_required?: boolean
  question_type_review_reason?: string
  question_type_basis?: string
  parse_warnings?: string[]
  local_answer_trusted: boolean
  has_question_asset: boolean
  has_answer_asset: boolean
  rich_content?: QuestionBankRichContent
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
  ambiguous_assets?: ConfigAmbiguousAsset[]
  assets?: ConfigSourceAsset[]
}

export interface ConfigSourceSubmission {
  status: 'processing' | 'succeeded' | 'failed' | 'replaced'
  source: ConfigSource | null
}

export interface ConfigQuestionGenerationState {
  question_id: string
  state: 'pending' | 'running' | 'passed' | 'blocked' | 'failed'
  reason: string
  retryable: boolean
  category?: string
}

export type ConfigSourceDuplicateKind =
  | 'exact_reusable'
  | 'exact_needs_analysis'
  | 'image_uncertain'
  | 'answer_conflict'
  | 'variant'
  | 'suspected'
  | 'same_session'

export const CONFIG_SOURCE_DUPLICATE_KINDS: readonly ConfigSourceDuplicateKind[] = [
  'exact_reusable',
  'exact_needs_analysis',
  'image_uncertain',
  'answer_conflict',
  'variant',
  'suspected',
  'same_session',
]

export interface ConfigSourceDuplicateItem {
  question_id: string
  kind: ConfigSourceDuplicateKind
  matched_question_id: number
  matched_paper_title: string
  matched_question_number: string
  similarity: number
  matched_question_excerpt: string
  bank_answer_text?: string
  suggested_answer_override?: string
  reason: string
}

export interface ConfigSourceDuplicates {
  source_id: string
  source_revision: string
  items: ConfigSourceDuplicateItem[]
}

export interface ConfigGenerationRequest {
  chapter_type_authorization?: ChapterTypeAuthorization & { analysis_request_estimate: number; source_id: string; source_revision: string }
  source_id: string
  source_revision: string
  generation_mode: GenerationMode
  decisions: QuestionDecision[]
  asset_decisions?: ConfigAmbiguousAssetDecision[]
  sync_to_question_bank: boolean
  curriculum_volume_id?: string
  regenerate_question_ids?: string[]
  base_revision?: string
  client_request_token?: string
}

export interface ConfigGenerationRetryRequest {
  source_job_id: number
  retry_question_ids?: string[]
  confirm_uncertain_retry?: boolean
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
  answer_only_max_score: number | null
  require_final_answer: boolean | null
  required_elements: string[]
  deduction_rules: string[]
  part_deduction_rules: string[]
  final_answer_rule: string
  response_mode?: string
  allow_alternative_methods?: boolean
  equivalent_rules?: string[]
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
  part_deduction_rules?: string[] | null
  final_answer_rule?: string | null
}

export interface ManualPartInput { part_id: string; score: number; core_goal: string }
export interface ManualScoringStepInput { step_id: string; score: number; core_goal: string }
export interface ManualQuestionPartInput { part_id: string; steps: ManualScoringStepInput[] }
export type ConfigEditorCommand =
  | { kind: 'split'; question_id: string; count: number; style: 'subquestion' | 'blank' }
  | { kind: 'replace_parts'; question_id: string; parts: ManualPartInput[] }
  | { kind: 'replace_question_structure'; question_id: string; parts: ManualQuestionPartInput[] }

export interface ConfigEditorSaveRequest {
  revision: string
  edits: ConfigEditorEdit[]
  commands: ConfigEditorCommand[]
}

export interface ConfigEditorSaveResponse extends ConfigEditorResponse {
  save_result: {
    config_saved: boolean
    mapping_status: 'not_present' | 'refreshed' | 'reconfirm_required'
    mapping_message: string
  }
}


function isControlledConfigAssetUrl(value: string): boolean {
  return /^\/api\/sessions\/[1-9]\d*\/config\/sources\/[0-9a-f]{32}\/questions\/[A-Za-z0-9_-]{1,100}\/assets\/(?:question|answer)(?:\/\d+)?$/.test(value)
}

function isRichSegment(value: unknown): boolean {
  return isRecord(value) && hasExactKeys(value, [
    'text', 'superscript', 'subscript', 'underline', 'line_break',
  ]) && typeof value.text === 'string'
    && typeof value.superscript === 'boolean' && typeof value.subscript === 'boolean'
    && typeof value.underline === 'boolean' && typeof value.line_break === 'boolean'
}

function isRichBlock(value: unknown): boolean {
  const baseKeys = ['kind', 'text', 'segments', 'rows', 'asset_indexes', 'asset_urls']
  if (!isRecord(value)
    || !(hasExactKeys(value, baseKeys) || hasExactKeys(value, [...baseKeys, 'html']))
    || !['paragraph', 'table'].includes(String(value.kind)) || typeof value.text !== 'string'
    || (value.html !== undefined && typeof value.html !== 'string')
    || !Array.isArray(value.segments) || !value.segments.every(isRichSegment)
    || !Array.isArray(value.rows) || !Array.isArray(value.asset_indexes)
    || !value.asset_indexes.every((item) => Number.isSafeInteger(item) && Number(item) >= 0)
    || !isStringArray(value.asset_urls)
    || !value.asset_urls.every(isControlledConfigAssetUrl)) return false
  return value.rows.every((row) => isRecord(row) && hasExactKeys(row, ['cells'])
    && Array.isArray(row.cells) && row.cells.every((cell) => isRecord(cell)
      && hasExactKeys(cell, ['segments']) && Array.isArray(cell.segments)
      && cell.segments.every(isRichSegment)))
}

export function isConfigRichContent(value: unknown): value is QuestionBankRichContent {
  return isRecord(value) && hasExactKeys(value, [
    'available', 'question_block_count', 'answer_block_count', 'question_blocks', 'answer_blocks',
  ]) && typeof value.available === 'boolean'
    && Number.isSafeInteger(value.question_block_count) && Number(value.question_block_count) >= 0
    && Number.isSafeInteger(value.answer_block_count) && Number(value.answer_block_count) >= 0
    && Array.isArray(value.question_blocks) && value.question_blocks.every(isRichBlock)
    && Array.isArray(value.answer_blocks) && value.answer_blocks.every(isRichBlock)
    && value.question_block_count === value.question_blocks.length
    && value.answer_block_count === value.answer_blocks.length
}

function isQuestionPreview(value: unknown): value is ConfigQuestionPreview {
  if (!isRecord(value)) return false
  const baseKeys = [
    'question_id', 'question_type', 'question_preview', 'answer_preview', 'answer_present',
    'needs_review', 'local_answer_trusted', 'has_question_asset', 'has_answer_asset',
  ]
  const allowedKeys = new Set([
    ...baseKeys,
    'rich_content',
    'question_type_review_required',
    'question_type_review_reason',
    'question_type_basis',
    'parse_warnings',
  ])
  if (baseKeys.some((key) => !(key in value))
    || Object.keys(value).some((key) => !allowedKeys.has(key))) return false
  return typeof value.question_id === 'string' && typeof value.question_type === 'string'
    && typeof value.question_preview === 'string' && typeof value.answer_preview === 'string'
    && typeof value.answer_present === 'boolean' && typeof value.needs_review === 'boolean'
    && typeof value.local_answer_trusted === 'boolean'
    && (value.question_type_review_required === undefined
      || typeof value.question_type_review_required === 'boolean')
    && (value.question_type_review_reason === undefined
      || typeof value.question_type_review_reason === 'string')
    && (value.question_type_basis === undefined
      || typeof value.question_type_basis === 'string')
    && (value.parse_warnings === undefined
      || (Array.isArray(value.parse_warnings) && value.parse_warnings.every((warning) => typeof warning === 'string')))
    && typeof value.has_question_asset === 'boolean' && typeof value.has_answer_asset === 'boolean'
    && (value.rich_content === undefined || isConfigRichContent(value.rich_content))
}

function isSafeBasename(value: unknown): value is string {
  if (typeof value !== 'string' || !value || value === '.' || value === '..') return false
  if (/[\\/:]/.test(value) || /(^|[\\/])\.\.?([\\/]|$)/.test(value)) return false
  return !/^[a-z]:/i.test(value) && !value.startsWith('\\\\')
}

function isAmbiguousAsset(value: unknown): value is ConfigAmbiguousAsset {
  return isRecord(value) && hasExactKeys(value, [
    'candidate_id', 'previous_question_id', 'next_question_id', 'source_section', 'asset_url',
  ]) && typeof value.candidate_id === 'string' && /^A[1-9]\d{0,3}$/.test(value.candidate_id)
    && typeof value.previous_question_id === 'string'
    && typeof value.next_question_id === 'string'
    && ['question', 'answer'].includes(String(value.source_section))
    && typeof value.asset_url === 'string'
    && /^\/api\/sessions\/[1-9]\d*\/config\/sources\/[0-9a-f]{32}\/ambiguous-assets\/A[1-9]\d{0,3}$/.test(value.asset_url)
}

function isSourceAsset(value: unknown): value is ConfigSourceAsset {
  if (!isRecord(value) || !hasExactKeys(value, [
    'asset_id', 'asset_url', 'assignment_state', 'question_id', 'asset_kind',
    'candidate_question_ids',
  ])) return false
  return typeof value.asset_id === 'string' && /^[AP][1-9]\d{0,3}$/.test(value.asset_id)
    && typeof value.asset_url === 'string'
    && /^\/api\/sessions\/[1-9]\d*\/config\/sources\/[0-9a-f]{32}\/(?:ambiguous-assets\/A[1-9]\d{0,3}|questions\/[A-Za-z0-9_-]{1,100}\/assets\/(?:question|answer)(?:\/\d+)?)$/.test(value.asset_url)
    && ['automatic', 'uncertain'].includes(String(value.assignment_state))
    && (value.question_id === null || typeof value.question_id === 'string')
    && ['question', 'answer'].includes(String(value.asset_kind))
    && isStringArray(value.candidate_question_ids)
}

function decodeConfigSource(value: unknown): ConfigSource {
  assertNoPathLikeKeys(value)
  const baseKeys = [
    'session_id', 'source_id', 'source_revision', 'safe_filename', 'suffix', 'size_bytes',
    'sha256_prefix', 'parse_state', 'questions',
  ]
  if (!isRecord(value)
    || Object.keys(value).some((key) => ![...baseKeys, 'ambiguous_assets', 'assets'].includes(key))
    || baseKeys.some((key) => !(key in value))
    || !isPositiveInteger(value.session_id) || typeof value.source_id !== 'string'
    || !/^[0-9a-f]{32}$/.test(value.source_id) || typeof value.source_revision !== 'string'
    || !/^[0-9a-f]{64}$/.test(value.source_revision) || !isSafeBasename(value.safe_filename)
    || (value.suffix !== '.docx' && value.suffix !== '.pdf') || !isPositiveInteger(value.size_bytes)
    || typeof value.sha256_prefix !== 'string' || !/^[0-9a-f]{12}$/.test(value.sha256_prefix)
    || value.parse_state !== 'ready' || !Array.isArray(value.questions)
    || !value.questions.every(isQuestionPreview)
    || (value.ambiguous_assets !== undefined && (
      !Array.isArray(value.ambiguous_assets) || !value.ambiguous_assets.every(isAmbiguousAsset)
    )) || (value.assets !== undefined && (
      !Array.isArray(value.assets) || !value.assets.every(isSourceAsset)
    ))) throw new Error('Invalid config source response')
  return value as unknown as ConfigSource
}

function isEditorRow(value: unknown): value is ConfigEditorRow {
  if (!isRecord(value) || !hasExactKeys(value, [
    'row_id', 'question_id', 'part_id', 'step_id', 'part_label', 'question_type', 'core_goal',
    'score', 'standard_answer', 'accepted_answers', 'match_rule',
    'answer_only_max_score', 'require_final_answer', 'required_elements', 'deduction_rules',
    'part_deduction_rules', 'final_answer_rule',
    ...['response_mode', 'allow_alternative_methods', 'equivalent_rules'].filter((key) => key in value),
  ])) return false
  return ['row_id', 'question_id', 'part_id', 'step_id', 'part_label', 'question_type',
    'core_goal', 'standard_answer', 'match_rule', 'final_answer_rule']
    .every((key) => typeof value[key] === 'string')
    && isFiniteNumber(value.score)
    && (value.answer_only_max_score === null || isFiniteNumber(value.answer_only_max_score))
    && (value.require_final_answer === null || typeof value.require_final_answer === 'boolean')
    && isStringArray(value.accepted_answers) && isStringArray(value.required_elements)
    && isStringArray(value.deduction_rules)
    && isStringArray(value.part_deduction_rules)
    && (value.response_mode === undefined || typeof value.response_mode === 'string')
    && (value.allow_alternative_methods === undefined || typeof value.allow_alternative_methods === 'boolean')
    && (value.equivalent_rules === undefined || isStringArray(value.equivalent_rules))
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

function decodeAbandoned(value: unknown): void {
  assertNoPathLikeKeys(value)
  if (!isRecord(value) || !hasExactKeys(value, ['status']) || value.status !== 'abandoned') {
    throw new Error('Invalid abandoned submission response')
  }
}

export async function abandonConfigSourceSubmission(
  sessionId: number, requestToken: string,
): Promise<void> {
  const id = requireSessionId(sessionId)
  await apiClient.request(
    `/api/sessions/${id}/config/sources/submissions/${requireRequestToken(requestToken)}/abandon`,
    { method: 'POST', decode: decodeAbandoned },
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

function isDuplicateItem(value: unknown): value is ConfigSourceDuplicateItem {
  const baseKeys = [
    'question_id', 'kind', 'matched_question_id', 'matched_paper_title',
    'matched_question_number', 'similarity', 'matched_question_excerpt', 'reason',
  ]
  if (!isRecord(value)) return false
  const allowedKeys = new Set([...baseKeys, 'bank_answer_text', 'suggested_answer_override'])
  if (baseKeys.some((key) => !(key in value))
    || Object.keys(value).some((key) => !allowedKeys.has(key))) return false
  return typeof value.question_id === 'string'
    && /^[A-Za-z0-9_-]{1,100}$/.test(value.question_id)
    && CONFIG_SOURCE_DUPLICATE_KINDS.includes(
      value.kind as ConfigSourceDuplicateKind,
    )
    && isPositiveInteger(value.matched_question_id)
    && typeof value.matched_paper_title === 'string'
    && typeof value.matched_question_number === 'string'
    && isFiniteNumber(value.similarity)
    && typeof value.matched_question_excerpt === 'string'
    && (value.bank_answer_text === undefined
      || value.bank_answer_text === null
      || typeof value.bank_answer_text === 'string')
    && (value.suggested_answer_override === undefined
      || value.suggested_answer_override === null
      || typeof value.suggested_answer_override === 'string')
    && typeof value.reason === 'string'
}

function decodeConfigSourceDuplicates(value: unknown): ConfigSourceDuplicates {
  assertNoPathLikeKeys(value)
  if (!isRecord(value)
    || !hasExactKeys(value, ['source_id', 'source_revision', 'items'])
    || !validDuplicateSourceId(value.source_id)
    || typeof value.source_revision !== 'string'
    || !/^[0-9a-f]{64}$/.test(value.source_revision)
    || !Array.isArray(value.items)
    || !value.items.every(isDuplicateItem)) {
    throw new Error('Invalid config source duplicates response')
  }
  return value as unknown as ConfigSourceDuplicates
}

function validDuplicateSourceId(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{32}$/.test(value)
}

export async function fetchActiveSourceDuplicates(
  sessionId: number,
  signal?: AbortSignal,
): Promise<ConfigSourceDuplicates> {
  const id = requireSessionId(sessionId)
  return apiClient.request(`/api/sessions/${id}/config/sources/active/duplicates`, {
    decode: decodeConfigSourceDuplicates,
    signal,
  })
}

export async function fetchLatestConfigGenerationJob(
  sessionId: number,
  request: ConfigGenerationRequest,
): Promise<JobResponse> {
  const id = requireSessionId(sessionId)
  const sourceId = requireSourceId(request.source_id)
  if (!/^[0-9a-f]{64}$/.test(request.source_revision)
    || request.generation_mode !== 'batched') {
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

export async function fetchConfigGenerationQuestionStates(
  sessionId: number,
  jobId: number,
): Promise<ConfigQuestionGenerationState[]> {
  const id = requireSessionId(sessionId)
  if (!isPositiveInteger(jobId)) throw new Error('Invalid job id')
  return apiClient.request(
    `/api/sessions/${id}/config/generation-jobs/${jobId}/question-states`,
    {
      decode: (value) => {
        assertNoPathLikeKeys(value)
        if (!isRecord(value) || !hasExactKeys(value, ['job_id', 'questions'])
          || value.job_id !== jobId || !Array.isArray(value.questions)) {
          throw new Error('Invalid config question states response')
        }
        const questions = value.questions
        if (!questions.every((item) => isRecord(item)
          && hasExactKeys(item, [
            'question_id', 'state', 'reason', 'retryable',
            ...('category' in item ? ['category'] : []),
          ])
          && typeof item.question_id === 'string'
          && ['pending', 'running', 'passed', 'blocked', 'failed'].includes(String(item.state))
          && typeof item.reason === 'string' && typeof item.retryable === 'boolean'
          && (item.category === undefined || typeof item.category === 'string'))) {
          throw new Error('Invalid config question states response')
        }
        return questions as ConfigQuestionGenerationState[]
      },
    },
  )
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

export async function abandonConfigGenerationRequest(
  sessionId: number, requestToken: string,
): Promise<void> {
  const id = requireSessionId(sessionId)
  await apiClient.request(
    `/api/sessions/${id}/config/generation-jobs/requests/${requireRequestToken(requestToken)}/abandon`,
    { method: 'POST', decode: decodeAbandoned },
  )
}

export async function fetchConfigGenerationPreview(sessionId: number, request: ConfigGenerationRequest): Promise<ChapterTypePlan & { analysis_request_estimate: number; source_id: string; source_revision: string }> {
  return apiClient.request(`/api/sessions/${requireSessionId(sessionId)}/config/generation-preview`, {
    method: 'POST', body: request, decode: (value) => {
      const plan = decodeChapterTypePlan(value)
      if (!isRecord(value) || !Number.isSafeInteger(value.analysis_request_estimate) || Number(value.analysis_request_estimate) < 0
        || typeof value.source_id !== 'string' || typeof value.source_revision !== 'string') throw new Error('分析费用预估格式不正确')
      return { ...plan, analysis_request_estimate: Number(value.analysis_request_estimate), source_id: value.source_id, source_revision: value.source_revision }
    },
  })
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
  confirmUncertainRetry = false,
): Promise<JobResponse> {
  const id = requireSessionId(sessionId)
  if (!isPositiveInteger(sourceJobId)) throw new Error('Invalid source Job id')
  const questionIds = [...new Set(retryQuestionIds.map((item) => item.trim()))]
  if ((retryQuestionIds.length > 0 && questionIds.length === 0)
    || questionIds.some((item) => !item || item.length > 100)) {
    throw new Error('Invalid retry question ids')
  }
  const request: ConfigGenerationRetryRequest = {
    source_job_id: sourceJobId,
  }
  if (questionIds.length > 0) request.retry_question_ids = questionIds
  if (confirmUncertainRetry) request.confirm_uncertain_retry = true
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


