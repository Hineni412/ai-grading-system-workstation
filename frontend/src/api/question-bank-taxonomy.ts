import { apiClient } from './client'
import { decodeJobResponse, type JobResponse } from './jobs'
import { isRecord } from './validation'

export const TAXONOMY_DIMENSIONS = [
  'curriculum',
  'knowledge',
  'ability',
  'method',
  'model',
  'special_type',
] as const

export type TaxonomyDimension = (typeof TAXONOMY_DIMENSIONS)[number]
export type TaxonomyReviewDecision =
  | 'approve'
  | 'edit'
  | 'merge'
  | 'reject'

export interface TaxonomyTerm {
  id: string
  dimension: TaxonomyDimension
  name: string
  aliases: string[]
  status: string | null
}

export interface TaxonomyCatalogResponse {
  revision: number
  dimensions: Record<TaxonomyDimension, TaxonomyTerm[]>
}

export interface TaxonomyProposal {
  id: string
  dimension: TaxonomyDimension
  proposed_name: string
  edited_name: string | null
  reason: string | null
  nearest_id: string | null
  why_not_reuse: string | null
  question_refs: number[]
  resolved_term_ids: string[]
  status: string
  created_at: string | null
  updated_at: string | null
}

export interface TaxonomyProposalListResponse {
  revision: number
  items: TaxonomyProposal[]
  counts: {
    pending: number
  }
}

export interface TaxonomyReviewInput {
  decision: TaxonomyReviewDecision
  edited_name?: string
  target_term_id?: string
  target_term_ids?: string[]
  question_ids?: number[]
  expected_revision: number
  request_token: string
}

export interface TaxonomyReviewApplication {
  status: 'not_requested' | 'pending' | 'applied' | 'partial' | 'failed'
  selected_question_ids: number[]
  applied_question_ids: number[]
  failures: Array<{
    question_id: number
    category: string
    message: string
  }>
}

export interface TaxonomyReviewResponse {
  revision: number
  proposal: TaxonomyProposal
  approved_term: TaxonomyTerm | null
  approved_terms: TaxonomyTerm[]
  application_status: TaxonomyReviewApplication['status'] | null
  application: TaxonomyReviewApplication | null
}

export type TaxonomySuggestionDecision =
  | 'merge'
  | 'map_many'
  | 'approve'
  | 'reject'
  | 'uncertain'

export interface TaxonomySuggestion {
  decision: TaxonomySuggestionDecision
  target_term_ids: string[]
  reason: string
  confidence: number
  source: 'local_exact' | 'ai'
}

export type TaxonomySuggestionItemStatus =
  | 'pending'
  | 'running'
  | 'suggested'
  | 'failed'
  | 'cancelled'
  | 'stale'

export interface TaxonomySuggestionItem {
  proposal_id: string
  dimension: TaxonomyDimension
  proposed_name: string
  question_refs: number[]
  status: TaxonomySuggestionItemStatus
  attempts: number
  suggestion: TaxonomySuggestion | null
  error: {
    category: string
    message: string
  } | null
}

export type TaxonomySuggestionRunStatus =
  | 'queued'
  | 'running'
  | 'cancelling'
  | 'completed'
  | 'partial'
  | 'failed'
  | 'cancelled'
  | 'stale'

export interface TaxonomySuggestionRun {
  run_id: string
  status: TaxonomySuggestionRunStatus
  taxonomy_revision: number
  stale: boolean
  retryable: boolean
  created_at: string
  updated_at: string
  items: TaxonomySuggestionItem[]
  progress: {
    total: number
    completed: number
    failed: number
    pending: number
    cancelled: number
  }
}

export interface TaxonomySuggestionStartInput {
  proposal_ids: string[]
  expected_revision: number
  request_token: string
}

export interface TaxonomySuggestionRetryInput {
  request_token: string
}

export interface TaxonomySuggestionJobResponse {
  job: JobResponse
  run: TaxonomySuggestionRun
}

function requiredString(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error('Expected string')
  return value.trim()
}

function optionalString(value: unknown): string | null {
  if (value === undefined || value === null || value === '') return null
  return requiredString(value)
}

function revision(value: unknown): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0) {
    throw new Error('Expected revision')
  }
  return Number(value)
}

function safeCount(value: unknown): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0) {
    throw new Error('Expected count')
  }
  return Number(value)
}

function stableId(value: unknown): string {
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (Number.isSafeInteger(value) && Number(value) >= 0) return String(value)
  throw new Error('Expected stable id')
}

function dimension(value: unknown): TaxonomyDimension {
  if (
    typeof value !== 'string'
    || !TAXONOMY_DIMENSIONS.includes(value as TaxonomyDimension)
  ) {
    throw new Error('Unknown taxonomy dimension')
  }
  return value as TaxonomyDimension
}

function stringList(value: unknown): string[] {
  if (value === undefined || value === null) return []
  if (!Array.isArray(value)) throw new Error('Expected string list')
  return value.map(requiredString)
}

function questionRefs(value: unknown): number[] {
  if (!Array.isArray(value)) throw new Error('Expected question references')
  const result = value.map((item) => {
    if (!Number.isSafeInteger(item) || Number(item) <= 0) {
      throw new Error('Invalid question reference')
    }
    return Number(item)
  })
  return [...new Set(result)]
}

function optionalQuestionRefs(value: unknown): number[] {
  if (value === undefined || value === null) return []
  return questionRefs(value)
}

function decodeTerm(value: unknown, fallbackDimension?: TaxonomyDimension): TaxonomyTerm {
  if (!isRecord(value)) throw new Error('Expected taxonomy term')
  const termDimension = value.dimension === undefined
    ? fallbackDimension
    : dimension(value.dimension)
  if (!termDimension) throw new Error('Missing taxonomy dimension')
  return {
    id: stableId(value.id),
    dimension: termDimension,
    name: requiredString(value.name),
    aliases: stringList(value.aliases),
    status: optionalString(value.status),
  }
}

function decodeProposal(value: unknown): TaxonomyProposal {
  if (!isRecord(value)) throw new Error('Expected taxonomy proposal')
  const rawId = value.id ?? value.proposal_id
  const rawName = value.proposed_name ?? value.name
  return {
    id: stableId(rawId),
    dimension: dimension(value.dimension),
    proposed_name: requiredString(rawName),
    edited_name: optionalString(value.edited_name),
    reason: optionalString(value.reason),
    nearest_id: optionalString(value.nearest_id),
    why_not_reuse: optionalString(value.why_not_reuse),
    question_refs: questionRefs(value.question_refs),
    resolved_term_ids: stringList(value.resolved_term_ids),
    status: requiredString(value.status),
    created_at: optionalString(value.created_at),
    updated_at: optionalString(value.updated_at),
  }
}

export function decodeTaxonomyCatalog(value: unknown): TaxonomyCatalogResponse {
  if (!isRecord(value) || !isRecord(value.dimensions)) {
    throw new Error('Invalid taxonomy catalog')
  }
  const rawDimensions = value.dimensions
  const dimensions = Object.fromEntries(
    TAXONOMY_DIMENSIONS.map((key) => {
      const items = rawDimensions[key]
      if (!Array.isArray(items)) throw new Error(`Missing ${key} terms`)
      return [key, items.map((item) => decodeTerm(item, key))]
    }),
  ) as Record<TaxonomyDimension, TaxonomyTerm[]>
  return {
    revision: revision(value.revision),
    dimensions,
  }
}

export function decodeTaxonomyProposals(value: unknown): TaxonomyProposalListResponse {
  if (!isRecord(value) || !Array.isArray(value.items) || !isRecord(value.counts)) {
    throw new Error('Invalid taxonomy proposal list')
  }
  return {
    revision: revision(value.revision),
    items: value.items.map(decodeProposal),
    counts: {
      pending: safeCount(value.counts.pending),
    },
  }
}

export function decodeTaxonomyReview(value: unknown): TaxonomyReviewResponse {
  if (!isRecord(value)) throw new Error('Invalid taxonomy review result')
  let approvedTerm: TaxonomyTerm | null = null
  if (value.approved_term !== undefined && value.approved_term !== null) {
    approvedTerm = decodeTerm(value.approved_term)
  }
  const approvedTerms = Array.isArray(value.approved_terms)
    ? value.approved_terms.map((item) => decodeTerm(item))
    : (approvedTerm ? [approvedTerm] : [])
  let application: TaxonomyReviewApplication | null = null
  if (value.application !== undefined && value.application !== null) {
    if (!isRecord(value.application)) throw new Error('Invalid taxonomy application')
    const failures = value.application.failures
    if (!Array.isArray(failures)) throw new Error('Invalid taxonomy application failures')
    application = {
      status: requiredString(value.application.status) as TaxonomyReviewApplication['status'],
      selected_question_ids: optionalQuestionRefs(
        value.application.selected_question_ids,
      ),
      applied_question_ids: optionalQuestionRefs(
        value.application.applied_question_ids,
      ),
      failures: failures.map((item) => {
        if (!isRecord(item)) throw new Error('Invalid taxonomy application failure')
        const [questionId] = questionRefs([item.question_id])
        if (!questionId) throw new Error('Invalid taxonomy application question')
        return {
          question_id: questionId,
          category: requiredString(item.category),
          message: requiredString(item.message),
        }
      }),
    }
  }
  const applicationStatus = optionalString(
    value.application_status ?? application?.status,
  )
  if (
    applicationStatus !== null
    && !['not_requested', 'pending', 'applied', 'partial', 'failed'].includes(
      applicationStatus,
    )
  ) {
    throw new Error('Invalid taxonomy application status')
  }
  return {
    revision: revision(value.revision),
    proposal: decodeProposal(value.proposal),
    approved_term: approvedTerm,
    approved_terms: approvedTerms,
    application_status: applicationStatus as TaxonomyReviewResponse['application_status'],
    application,
  }
}

const SUGGESTION_RUN_STATUSES = [
  'queued',
  'running',
  'cancelling',
  'completed',
  'partial',
  'failed',
  'cancelled',
  'stale',
] as const
const SUGGESTION_ITEM_STATUSES = [
  'pending',
  'running',
  'suggested',
  'failed',
  'cancelled',
  'stale',
] as const
const SUGGESTION_DECISIONS = [
  'merge',
  'map_many',
  'approve',
  'reject',
  'uncertain',
] as const

function oneOf<const T extends readonly string[]>(
  value: unknown,
  allowed: T,
  label: string,
): T[number] {
  if (
    typeof value !== 'string'
    || !allowed.includes(value as T[number])
  ) throw new Error(`Invalid ${label}`)
  return value as T[number]
}

function decodeSuggestion(value: unknown): TaxonomySuggestion {
  if (!isRecord(value)) throw new Error('Invalid taxonomy suggestion')
  const confidence = Number(value.confidence)
  if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
    throw new Error('Invalid taxonomy suggestion confidence')
  }
  const source = oneOf(
    value.source,
    ['local_exact', 'ai'] as const,
    'taxonomy suggestion source',
  )
  return {
    decision: oneOf(
      value.decision,
      SUGGESTION_DECISIONS,
      'taxonomy suggestion decision',
    ),
    target_term_ids: stringList(value.target_term_ids),
    reason: requiredString(value.reason),
    confidence,
    source,
  }
}

function decodeSuggestionItem(value: unknown): TaxonomySuggestionItem {
  if (!isRecord(value)) throw new Error('Invalid taxonomy suggestion item')
  let error: TaxonomySuggestionItem['error'] = null
  if (value.error !== undefined && value.error !== null) {
    if (!isRecord(value.error)) throw new Error('Invalid taxonomy suggestion error')
    error = {
      category: requiredString(value.error.category),
      message: requiredString(value.error.message),
    }
  }
  return {
    proposal_id: stableId(value.proposal_id),
    dimension: dimension(value.dimension),
    proposed_name: requiredString(value.proposed_name),
    question_refs: optionalQuestionRefs(value.question_refs),
    status: oneOf(
      value.status,
      SUGGESTION_ITEM_STATUSES,
      'taxonomy suggestion item status',
    ),
    attempts: safeCount(value.attempts),
    suggestion: value.suggestion === undefined || value.suggestion === null
      ? null
      : decodeSuggestion(value.suggestion),
    error,
  }
}

export function decodeTaxonomySuggestionRun(
  value: unknown,
): TaxonomySuggestionRun {
  if (
    !isRecord(value)
    || !Array.isArray(value.items)
    || !isRecord(value.progress)
    || typeof value.stale !== 'boolean'
  ) throw new Error('Invalid taxonomy suggestion run')
  return {
    run_id: stableId(value.run_id),
    status: oneOf(
      value.status,
      SUGGESTION_RUN_STATUSES,
      'taxonomy suggestion run status',
    ),
    taxonomy_revision: revision(value.taxonomy_revision),
    stale: value.stale,
    retryable: value.retryable === true,
    created_at: requiredString(value.created_at),
    updated_at: requiredString(value.updated_at),
    items: value.items.map(decodeSuggestionItem),
    progress: {
      total: safeCount(value.progress.total),
      completed: safeCount(value.progress.completed),
      failed: safeCount(value.progress.failed),
      pending: safeCount(value.progress.pending),
      cancelled: safeCount(value.progress.cancelled),
    },
  }
}

export function decodeTaxonomySuggestionJob(
  value: unknown,
): TaxonomySuggestionJobResponse {
  if (!isRecord(value)) throw new Error('Invalid taxonomy suggestion job')
  return {
    job: decodeJobResponse(value.job),
    run: decodeTaxonomySuggestionRun(value.run),
  }
}

export const questionBankTaxonomyApi = {
  getCatalog(signal?: AbortSignal): Promise<TaxonomyCatalogResponse> {
    return apiClient.request('/api/question-bank/taxonomy/catalog', {
      decode: decodeTaxonomyCatalog,
      signal,
    })
  },

  listProposals(signal?: AbortSignal): Promise<TaxonomyProposalListResponse> {
    return apiClient.request('/api/question-bank/taxonomy/proposals?status=pending', {
      decode: decodeTaxonomyProposals,
      signal,
    })
  },

  reviewProposal(
    proposalId: string,
    input: TaxonomyReviewInput,
    signal?: AbortSignal,
  ): Promise<TaxonomyReviewResponse> {
    if (!proposalId.trim()) throw new Error('Invalid proposal id')
    return apiClient.request(
      `/api/question-bank/taxonomy/proposals/${encodeURIComponent(proposalId)}/review`,
      {
        method: 'POST',
        body: input,
        decode: decodeTaxonomyReview,
        signal,
      },
    )
  },

  startSuggestions(
    input: TaxonomySuggestionStartInput,
    signal?: AbortSignal,
  ): Promise<TaxonomySuggestionJobResponse> {
    return apiClient.request('/api/question-bank/taxonomy/suggestions', {
      method: 'POST',
      body: input,
      decode: decodeTaxonomySuggestionJob,
      signal,
    })
  },

  getSuggestionRun(
    runId: string,
    signal?: AbortSignal,
  ): Promise<TaxonomySuggestionRun> {
    if (!runId.trim()) throw new Error('Invalid suggestion run id')
    return apiClient.request(
      `/api/question-bank/taxonomy/suggestions/${encodeURIComponent(runId)}`,
      {
        decode: decodeTaxonomySuggestionRun,
        signal,
      },
    )
  },

  cancelSuggestionRun(
    runId: string,
    signal?: AbortSignal,
  ): Promise<TaxonomySuggestionRun> {
    if (!runId.trim()) throw new Error('Invalid suggestion run id')
    return apiClient.request(
      `/api/question-bank/taxonomy/suggestions/${encodeURIComponent(runId)}/cancel`,
      {
        method: 'POST',
        decode: decodeTaxonomySuggestionRun,
        signal,
      },
    )
  },

  retrySuggestionRun(
    runId: string,
    input: TaxonomySuggestionRetryInput,
    signal?: AbortSignal,
  ): Promise<TaxonomySuggestionJobResponse> {
    if (!runId.trim()) throw new Error('Invalid suggestion run id')
    return apiClient.request(
      `/api/question-bank/taxonomy/suggestions/${encodeURIComponent(runId)}/retry`,
      {
        method: 'POST',
        body: input,
        decode: decodeTaxonomySuggestionJob,
        signal,
      },
    )
  },
}
