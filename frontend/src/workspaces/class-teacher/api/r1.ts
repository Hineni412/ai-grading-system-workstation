import { apiClient } from '../../../api/client'

const CLIENT_HEADER = 'class-teacher-browser-v1'
const operationId = () => globalThis.crypto.randomUUID()
export type JsonRecord = Record<string, unknown>
const record = (value: unknown): JsonRecord => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('contract')
  return value as JsonRecord
}
const readHeaders = (token: string) => ({ 'x-class-teacher-session': token })
const writeHeaders = (token: string) => ({ ...readHeaders(token), 'x-class-teacher-client': CLIENT_HEADER })

export interface AffairSummary {
  affair_id: string
  title: string
  summary: string | null
  state: string
  revision: number
  current_step_count: number
  completed_step_count: number
  projection_state: string
  updated_at: string
}

export interface AffairStep {
  step_instance_id: string
  title: string
  details?: string | null
  state: string
  revision: number
  safety_required?: boolean
  due_at?: string | null
  decision_key?: string | null
  decision_prompt?: string | null
  decision_options?: Array<{ value: string; label: string }>
}

export interface AffairDraft { step_instance_id: string; draft_kind: string; revision: number }
export interface AffairDetail extends AffairSummary {
  template_key: string
  occurrence_sequence: number
  current_steps: AffairStep[]
  completed_steps: AffairStep[]
  preview_steps: AffairStep[]
  drafts: AffairDraft[]
  school_config_gaps?: string[]
  emergency_prompt?: string | null
}

export interface DirectorySubject {
  subject_id: string
  display_name: string
  source_student_id: string
  class_label: string | null
  support_record_count: number
  support_plan_count: number
  confirmed_entry_count: number
  projection_state: string
  attention_pending_count: number
  last_confirmed_at: string | null
}

export interface AcademicAnalysis {
  contract_version: string
  source_version: string
  ruleset_version: string
  sessions: AcademicSession[]
  series: AcademicSeries[]
  rank_change_pairs: RankChangePair[]
  relative_subject_signals: RelativeSubjectSignal[]
  recent_changes: JsonRecord[]
  insufficient_reasons: string[]
  attention_cards: AttentionCard[]
  filter_options?: { series: string[]; subjects: string[] }
  applied_filters?: { time_range: string; comparison_series: string | null; subject_name: string | null; comparable_only: boolean }
}

export interface AcademicPoint { evidence_version_id: string; session_id?: string; subject_name: string; result_state: string; score: number | null; occurred_on: string; relative_position?: number | null; is_comparable?: boolean; comparable_outputs?: string[] }
export interface AcademicSession { session_id: string; title: string; occurred_on: string; comparison_series?: string | null; metadata_complete: boolean; evidence: AcademicPoint[] }
export interface ComparisonSegment { overall_status?: string; dimensions: { rank: { status: string }; score: { status: string } } }
export interface AcademicSeries { subject_name: string; points: AcademicPoint[]; segments: ComparisonSegment[] }
export interface RankChangePair { subject_name: string; from: number; to: number }
export interface RelativeSubjectSignal { subject_name: string; signal: string; eligible_session_count: number }
export interface AttentionCard { attention_card_id: string; revision: number; state: string; observed_fact: string; evidence_sufficiency: string }
export interface SupportReviewPreview { review_id: string; preview_id: string; fingerprint: string; exact_payload: JsonRecord }
export interface SupportReviewTurn { model_result?: { operation_id?: string; proposal?: { summary?: string }; draft_text?: string } }
export interface SupportReview { review_id: string; base_revision_number: number; state: string; model_operation_id?: string | null; turns?: SupportReviewTurn[] }

export const affairR1Api = {
  list(token: string) {
    return apiClient.request('/api/class-teacher/sop/affairs', {
      headers: readHeaders(token),
      decode: (value) => record(value).items as AffairSummary[],
    })
  },
  read(token: string, affairId: string) {
    return apiClient.request(`/api/class-teacher/sop/affairs/${affairId}`, { headers: readHeaders(token), decode: (value) => record(value) as unknown as AffairDetail })
  },
  command(token: string, affair: AffairDetail, command: string, input: Record<string, unknown>) {
    return apiClient.request(`/api/class-teacher/sop/affairs/${affair.affair_id}/commands`, {
      method: 'POST', headers: writeHeaders(token),
      body: { command, expected_revision: affair.revision, operation_id: operationId(), ...input }, decode: (value) => record(value) as unknown as AffairDetail,
    })
  },
  saveDraft(token: string, affairId: string, stepId: string, kind: 'fact' | 'communication', text: string, revision: number | null) {
    return apiClient.request(`/api/class-teacher/sop/affairs/${affairId}/steps/${stepId}/draft`, {
      method: 'PUT', headers: writeHeaders(token), body: {
        draft_kind: kind, text, expected_revision: revision, operation_id: operationId(),
      }, decode: record,
    })
  },
}

export const projectionR1Api = {
  resolve(token: string, projectionId: string) {
    return apiClient.request(`/api/class-teacher/protected-work/${projectionId}`, {
      headers: readHeaders(token), decode: record,
    })
  },
}

export const studentR1Api = {
  directory(token: string, input: { q?: string; classLabel?: string; state?: string; sort?: string; cursor?: string; pageSize?: number } = {}) {
    const query = new URLSearchParams()
    if (input.q) query.set('q', input.q)
    if (input.classLabel) query.set('class_label', input.classLabel)
    if (input.state) query.set('state', input.state)
    if (input.sort) query.set('sort', input.sort)
    if (input.cursor) query.set('cursor', input.cursor)
    if (input.pageSize) query.set('page_size', String(input.pageSize))
    return apiClient.request(`/api/class-teacher/support/directory?${query}`, {
      headers: readHeaders(token), decode: (value) => record(value) as { items: DirectorySubject[]; cursor: string | null; total: number; page_size: number },
    })
  },
  header(token: string, subjectId: string) {
    return apiClient.request(`/api/class-teacher/support/subjects/${subjectId}/workspace-header`, { headers: readHeaders(token), decode: record })
  },
  records(token: string, subjectId: string) {
    return apiClient.request(`/api/class-teacher/support/subjects/${subjectId}/records`, { headers: readHeaders(token), decode: (value) => record(value).items as JsonRecord[] })
  },
  prepareReview(token: string, recordId: string, expectedRevision: number, supplement: string) {
    return apiClient.request(`/api/class-teacher/support/records/${recordId}/ai-reviews/previews`, {
      method: 'POST', headers: writeHeaders(token), body: { expected_revision: expectedRevision, teacher_supplement: supplement || null }, decode: (value) => record(value) as unknown as SupportReviewPreview,
    })
  },
  confirmReview(token: string, preview: SupportReviewPreview) {
    return apiClient.request(`/api/class-teacher/support/ai-reviews/${preview.review_id}/previews/${preview.preview_id}/confirm`, {
      method: 'POST', headers: writeHeaders(token), body: { fingerprint: preview.fingerprint, operation_id: operationId() }, decode: (value) => record(value) as unknown as SupportReview, timeoutMs: 125_000,
    })
  },
  reviewOperation(token: string, operationId: string) {
    return apiClient.request(`/api/class-teacher/support/ai-reviews/operations/${operationId}`, { headers: readHeaders(token), decode: (value) => record(value) as unknown as SupportReview })
  },
  applyReview(token: string, review: SupportReview, summary: string) {
    const turn = review.turns?.[review.turns.length - 1]
    return apiClient.request(`/api/class-teacher/support/ai-reviews/${review.review_id}/apply`, {
      method: 'POST', headers: writeHeaders(token), body: {
        model_operation_id: turn?.model_result?.operation_id,
        expected_revision: review.base_revision_number,
        teacher_result: { summary, strengths: [], needs: [], open_questions: [] },
        operation_id: operationId(),
      }, decode: record,
    })
  },
  rejectReview(token: string, reviewId: string) {
    return apiClient.request(`/api/class-teacher/support/ai-reviews/${reviewId}/reject`, { method: 'POST', headers: writeHeaders(token), body: { operation_id: operationId() }, decode: record })
  },
  academic(token: string, subjectId: string, input: { timeRange?: string; comparisonSeries?: string; subjectName?: string; comparableOnly?: boolean } = {}) {
    const query = new URLSearchParams()
    if (input.timeRange && input.timeRange !== 'all') query.set('time_range', input.timeRange)
    if (input.comparisonSeries) query.set('comparison_series', input.comparisonSeries)
    if (input.subjectName) query.set('subject_name', input.subjectName)
    if (input.comparableOnly) query.set('comparable_only', 'true')
    return apiClient.request(`/api/class-teacher/support/subjects/${subjectId}/academic-analysis?${query}`, { headers: readHeaders(token), decode: (value) => record(value) as unknown as AcademicAnalysis })
  },
  evidenceSnapshot(token: string, evidenceId: string) {
    return apiClient.request(`/api/class-teacher/evidence/${evidenceId}/snapshot`, { headers: readHeaders(token), decode: record })
  },
  decideAttention(token: string, card: AttentionCard, analysis: AcademicAnalysis, input: { decision: string; reason: string; reviewAt: string | null; planId: string | null }) {
    return apiClient.request(`/api/class-teacher/attention-cards/${card.attention_card_id}/decide`, {
      method: 'POST', headers: writeHeaders(token), body: {
        operation_id: operationId(), revision: card.revision, decision: input.decision,
        reason: input.reason, review_at: input.reviewAt, plan_id: input.planId,
        source_version: analysis.source_version,
      }, decode: record,
    })
  },
}
