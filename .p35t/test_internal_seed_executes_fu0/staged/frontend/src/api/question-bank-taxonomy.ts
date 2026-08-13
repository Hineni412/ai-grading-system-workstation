import { apiClient } from './client'
import { isRecord } from './validation'

export const TAXONOMY_DIMENSIONS = [
  'curriculum',
  'knowledge',
  'ability',
  'method',
  'model',
] as const

export type TaxonomyDimension = (typeof TAXONOMY_DIMENSIONS)[number]
export type TaxonomyReviewDecision = 'approve' | 'edit' | 'merge' | 'reject'

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
  expected_revision: number
  request_token: string
}

export interface TaxonomyReviewResponse {
  revision: number
  proposal: TaxonomyProposal
  approved_term: TaxonomyTerm | null
  application_status: 'not_requested' | 'pending' | 'applied' | 'failed' | null
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
  const applicationStatus = optionalString(value.application_status)
  if (
    applicationStatus !== null
    && !['not_requested', 'pending', 'applied', 'failed'].includes(applicationStatus)
  ) {
    throw new Error('Invalid taxonomy application status')
  }
  return {
    revision: revision(value.revision),
    proposal: decodeProposal(value.proposal),
    approved_term: approvedTerm,
    application_status: applicationStatus as TaxonomyReviewResponse['application_status'],
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
}
