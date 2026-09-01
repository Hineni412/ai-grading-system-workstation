import { apiClient } from './client'
import { decodeJobResponse, type JobResponse } from './jobs'
import { assertNoPathLikeKeys, isNullableString, isRecord } from './validation'

export type CriterionVersionStatus =
  | 'proposed'
  | 'approved'
  | 'rejected'
  | 'superseded'
  | 'stale'

export type CriterionSchemaVersion =
  | 'training-criteria-draft-v1'
  | 'judgment-points-v1'

export interface TrainingCriterionPoint {
  point_id: string
  target: string
  observable_evidence: string
  equivalent_rules: string[]
  counterexamples: string[]
  depends_on?: string[]
}

export interface TrainingCriterionDraft {
  schema_version: CriterionSchemaVersion
  question_id: number
  source_content_hash: string
  question_type: string
  points: TrainingCriterionPoint[]
  auxiliary_rules: string[]
  rationale: string
  confidence: number
  source_kind: 'combined_model' | 'confirmed_rubric_adapter'
  solution_evidence?: Record<string, unknown>
}

export interface TrainingCriterionVersion {
  version_id: string
  question_id: number
  version_number: number
  parent_version_id: string | null
  source_content_hash: string
  schema_version: string
  status: CriterionVersionStatus
  source_kind: 'combined_model' | 'confirmed_rubric_adapter' | 'teacher_manual' | 'backfill'
  source_reference: string
  criteria: TrainingCriterionDraft
  criteria_hash: string
  quality_status: 'passed' | 'failed'
  quality_codes: string[]
  created_by: string
  decision_by: string | null
  decision_note: string | null
  decided_at: string | null
  created_at: string
  updated_at: string
}

export interface TrainingCriterionWorkspace {
  question_id: number
  state: 'missing' | CriterionVersionStatus | 'available'
  available: boolean
  revision: number
  current_source_hash: string
  current_version: TrainingCriterionVersion | null
  approved_version: TrainingCriterionVersion | null
  versions: TrainingCriterionVersion[]
}

export interface TrainingCriterionDraftInput {
  expected_revision: number
  parent_version_id: string | null
  request_token: string
  reason: string
  points: TrainingCriterionPoint[]
  auxiliary_rules: string[]
  rationale: string
  confidence: number
}

export interface TrainingCriterionReviewInput {
  version_id: string
  expected_revision: number
  action: 'approve' | 'reject'
  reason: string
}

export interface TrainingCriterionBackfillItem {
  question_id: number
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'skipped'
  version_id: string | null
  error_category: string
  attempt_count: number
}

export interface TrainingCriterionBackfillRun {
  run_id: string
  mode: 'missing_only' | 'regenerate'
  status: 'pending' | 'running' | 'partial' | 'succeeded' | 'failed' | 'cancelled'
  question_ids: number[]
  created_at: string
  updated_at: string
  finished_at: string | null
  items: TrainingCriterionBackfillItem[]
}

export interface TrainingCriterionBackfillStart {
  run: TrainingCriterionBackfillRun
  job: JobResponse | null
}

const VERSION_STATUSES = new Set([
  'proposed',
  'approved',
  'rejected',
  'superseded',
  'stale',
])
const CRITERION_SCHEMAS = new Set([
  'training-criteria-draft-v1',
  'judgment-points-v1',
])

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0
}

function decodePoint(value: unknown): TrainingCriterionPoint {
  if (
    !isRecord(value)
    || typeof value.point_id !== 'string'
    || typeof value.target !== 'string'
    || typeof value.observable_evidence !== 'string'
    || !isStringArray(value.equivalent_rules)
    || !isStringArray(value.counterexamples)
    || (value.depends_on !== undefined && !isStringArray(value.depends_on))
  ) throw new Error('Invalid training criterion point')
  return {
    point_id: value.point_id,
    target: value.target,
    observable_evidence: value.observable_evidence,
    equivalent_rules: value.equivalent_rules,
    counterexamples: value.counterexamples,
    ...(isStringArray(value.depends_on) ? { depends_on: value.depends_on } : {}),
  }
}

function decodeDraft(value: unknown): TrainingCriterionDraft {
  if (
    !isRecord(value)
    || !CRITERION_SCHEMAS.has(String(value.schema_version))
    || !isPositiveInteger(value.question_id)
    || typeof value.source_content_hash !== 'string'
    || typeof value.question_type !== 'string'
    || !Array.isArray(value.points)
    || value.points.length === 0
    || !isStringArray(value.auxiliary_rules)
    || typeof value.rationale !== 'string'
    || typeof value.confidence !== 'number'
    || !['combined_model', 'confirmed_rubric_adapter'].includes(String(value.source_kind))
    || (value.solution_evidence != null && !isRecord(value.solution_evidence))
  ) throw new Error('Invalid training criterion draft')
  return {
    schema_version: value.schema_version as CriterionSchemaVersion,
    question_id: value.question_id,
    source_content_hash: value.source_content_hash,
    question_type: value.question_type,
    points: value.points.map(decodePoint),
    auxiliary_rules: value.auxiliary_rules,
    rationale: value.rationale,
    confidence: value.confidence,
    source_kind: value.source_kind as TrainingCriterionDraft['source_kind'],
    ...(isRecord(value.solution_evidence)
      ? { solution_evidence: value.solution_evidence }
      : {}),
  }
}

function decodeVersion(value: unknown): TrainingCriterionVersion {
  if (
    !isRecord(value)
    || typeof value.version_id !== 'string'
    || !isPositiveInteger(value.question_id)
    || !isPositiveInteger(value.version_number)
    || !isNullableString(value.parent_version_id)
    || typeof value.source_content_hash !== 'string'
    || typeof value.schema_version !== 'string'
    || !VERSION_STATUSES.has(String(value.status))
    || !['combined_model', 'confirmed_rubric_adapter', 'teacher_manual', 'backfill']
      .includes(String(value.source_kind))
    || typeof value.source_reference !== 'string'
    || typeof value.criteria_hash !== 'string'
    || !['passed', 'failed'].includes(String(value.quality_status))
    || !isStringArray(value.quality_codes)
    || typeof value.created_by !== 'string'
    || !isNullableString(value.decision_by)
    || !isNullableString(value.decision_note)
    || !isNullableString(value.decided_at)
    || typeof value.created_at !== 'string'
    || typeof value.updated_at !== 'string'
  ) throw new Error('Invalid training criterion version')
  return {
    ...value,
    criteria: decodeDraft(value.criteria),
  } as unknown as TrainingCriterionVersion
}

export function decodeTrainingCriterionWorkspace(value: unknown): TrainingCriterionWorkspace {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || !isPositiveInteger(value.question_id)
    || !['missing', 'proposed', 'approved', 'rejected', 'superseded', 'stale', 'available']
      .includes(String(value.state))
    || typeof value.available !== 'boolean'
    || !Number.isSafeInteger(value.revision)
    || Number(value.revision) < 0
    || typeof value.current_source_hash !== 'string'
    || !Array.isArray(value.versions)
  ) throw new Error('Invalid training criterion workspace')
  return {
    ...value,
    current_version: value.current_version === null ? null : decodeVersion(value.current_version),
    approved_version: value.approved_version === null ? null : decodeVersion(value.approved_version),
    versions: value.versions.map(decodeVersion),
  } as unknown as TrainingCriterionWorkspace
}

function decodeBackfillRun(value: unknown): TrainingCriterionBackfillRun {
  if (
    !isRecord(value)
    || typeof value.run_id !== 'string'
    || !['missing_only', 'regenerate'].includes(String(value.mode))
    || !['pending', 'running', 'partial', 'succeeded', 'failed', 'cancelled']
      .includes(String(value.status))
    || !Array.isArray(value.question_ids)
    || !value.question_ids.every(isPositiveInteger)
    || typeof value.created_at !== 'string'
    || typeof value.updated_at !== 'string'
    || !isNullableString(value.finished_at)
    || !Array.isArray(value.items)
  ) throw new Error('Invalid training criterion backfill')
  const items = value.items.map((item) => {
    if (
      !isRecord(item)
      || !isPositiveInteger(item.question_id)
      || !['pending', 'running', 'succeeded', 'failed', 'cancelled', 'skipped']
        .includes(String(item.status))
      || !isNullableString(item.version_id)
      || typeof item.error_category !== 'string'
      || !Number.isSafeInteger(item.attempt_count)
    ) throw new Error('Invalid training criterion backfill item')
    return item as unknown as TrainingCriterionBackfillItem
  })
  return { ...value, items } as unknown as TrainingCriterionBackfillRun
}

function decodeBackfillStart(value: unknown): TrainingCriterionBackfillStart {
  assertNoPathLikeKeys(value)
  if (!isRecord(value)) throw new Error('Invalid training criterion backfill start')
  return {
    run: decodeBackfillRun(value.run),
    job: value.job === null ? null : decodeJobResponse(value.job),
  }
}

function questionId(value: number): number {
  if (!isPositiveInteger(value)) throw new Error('Invalid question id')
  return value
}

export const questionBankCriteriaApi = {
  getWorkspace(id: number, signal?: AbortSignal) {
    return apiClient.request(
      `/api/question-bank/criteria/questions/${questionId(id)}`,
      { decode: decodeTrainingCriterionWorkspace, signal },
    )
  },
  saveDraft(id: number, input: TrainingCriterionDraftInput, signal?: AbortSignal) {
    return apiClient.request(
      `/api/question-bank/criteria/questions/${questionId(id)}/drafts`,
      {
        method: 'POST',
        body: input,
        decode: decodeTrainingCriterionWorkspace,
        signal,
      },
    )
  },
  review(id: number, input: TrainingCriterionReviewInput, signal?: AbortSignal) {
    return apiClient.request(
      `/api/question-bank/criteria/questions/${questionId(id)}/review`,
      {
        method: 'POST',
        body: input,
        decode: decodeTrainingCriterionWorkspace,
        signal,
      },
    )
  },
  startBackfill(
    ids: number[],
    requestToken: string,
    mode: 'missing_only' | 'regenerate',
    signal?: AbortSignal,
  ) {
    return apiClient.request('/api/question-bank/criteria/backfill-runs', {
      method: 'POST',
      body: { question_ids: ids, request_token: requestToken, mode },
      decode: decodeBackfillStart,
      signal,
    })
  },
}
