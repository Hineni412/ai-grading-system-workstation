import { apiClient } from '../../../api/client'

const CLIENT_HEADER = 'class-teacher-browser-v1'

export interface SupportSubject {
  subject_id: string
  student_ref?: string
  revision: number
  source_student_id: string
  display_name: string
  class_label: string | null
}

export interface SupportRecord {
  record_id: string
  subject_id: string
  record_kind: string
  state: string
  current_revision: number
  plan_id: string | null
  content: string
  scene: string
  source: string
  counterexample: string | null
  observed_at: string
  review_at: string | null
  expires_at: string | null
}

export interface SupportSummary {
  subject_id: string
  as_of: string
  items: Array<{
    record_id: string
    revision: number
    record_kind: string
    content: string
    scene: string
    source: string
    basis: string | null
    counterexample: string | null
    review_at: string | null
    expires_at: string | null
  }>
  source_record_count: number
}

export type SupportPlanOutcome = 'effective' | 'ineffective' | 'continue'

export interface SupportPlan {
  support_plan_id: string
  subject_id: string
  revision: number
  action_id: string | null
  state: 'active' | 'completed'
  review_at: string
  goal: string
  support_actions: string[]
  result: string | null
  outcome: SupportPlanOutcome | null
  completed_at: string | null
  created_at: string
  updated_at: string
}

export interface SupportPlanAiDraft {
  goal: string
  support_actions: string[]
  review_at: string
}

export interface SubjectDeletionPreview {
  subject_id: string
  shared_object_count: number
  shared_objects: Array<{ object_id: string; object_type: string }>
  delete_confirmation_phrase: string
  impact_counts?: Record<string, number>
  projection_count?: number
  preview_version?: string
}

export interface AssessmentEvidence {
  evidence_version_id: string
  subject_id: string
  state: string
  title: string
  subject_name: string
  occurred_on: string
  max_score: number | null
  score: number | null
  result_state: string
  assessment_nature: string | null
  rank_context: {
    rank: number | null
    class_rank: number | null
    rank_scope: string | null
    participant_count: number | null
  } | null
}

export interface AttentionCard {
  attention_card_id: string
  revision: number
  subject_id: string
  evidence_version_id: string
  state: string
  decision: string | null
  action_id: string | null
  observed_fact: string
  evidence_source: Record<string, unknown>
  comparability: string
  limitations: string[]
  verification_question: string
  low_risk_next_step: string
  evidence_sufficiency: string
  review_suggestion: string
  risk_score: null
}

export interface SpreadsheetPreview {
  file_name: string
  sheet_names: string[]
  selected_sheet: string
  headers: string[]
  rows: Array<Record<string, string>>
  preview_row_count: number
  truncated: boolean
  raw_file_retained: false
  temporary_file_created: false
}

export interface EvidenceSessionUpdateFields {
  title?: string
  grade?: string | null
  term?: string | null
  exam_type?: string | null
  occurred_on?: string
  academic_year?: string | null
}

export interface EvidenceSessionDeletePreview {
  session_id: string
  title: string
  counts: { results: number; assessments: number; imports: number; attention_cards: number }
  preview_version: string
  confirmation_phrase: string
}

export interface EvidenceGlobalSettingsResult {
  sessions_updated: number
  subjects: Record<string, number>
  participant_count: number | null
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('contract')
  return value as Record<string, unknown>
}

function list<T>(payload: unknown, decode: (value: unknown) => T): T[] {
  const value = record(payload)
  if (!Array.isArray(value.items)) throw new Error('contract')
  return value.items.map(decode)
}

function subject(payload: unknown): SupportSubject {
  const value = record(payload)
  return value as unknown as SupportSubject
}

function supportRecord(payload: unknown): SupportRecord {
  const value = record(payload)
  return value as unknown as SupportRecord
}

function supportPlan(payload: unknown): SupportPlan {
  return record(payload) as unknown as SupportPlan
}

function evidence(payload: unknown): AssessmentEvidence {
  return record(payload) as unknown as AssessmentEvidence
}

function attention(payload: unknown): AttentionCard {
  return record(payload) as unknown as AttentionCard
}

function headers(): Record<string, string> {
  return { 'x-class-teacher-client': CLIENT_HEADER }
}

function operationId(): string {
  return globalThis.crypto.randomUUID()
}

export const followUpApi = {
  postpone(projectionId: string, dueDate: string) {
    return apiClient.request(
      `/api/class-teacher/support/follow-ups/${projectionId}/postpone`,
      {
        method: 'POST',
        headers: headers(),
        body: { due_date: dueDate },
        decode: record,
      },
    )
  },
  dismiss(projectionId: string) {
    return apiClient.request(
      `/api/class-teacher/support/follow-ups/${projectionId}/dismiss`,
      {
        method: 'POST',
        headers: headers(),
        decode: record,
      },
    )
  },
}

export const supportApi = {
  listSubjects() {
    return apiClient.request('/api/class-teacher/support/subjects', {
      decode: (payload) => list(payload, subject),
    })
  },
  createSubject(
    input: { source_student_id: string; display_name: string; class_label: string | null },
  ) {
    return apiClient.request('/api/class-teacher/support/subjects', {
      method: 'POST',
      headers: headers(),
      body: { ...input, operation_id: operationId() },
      decode: subject,
    })
  },
  previewSubjectDeletion(studentRef: string) {
    return apiClient.request(
      `/api/class-teacher/support/subjects/${encodeURIComponent(studentRef)}/deletion-preview`,
      {
        decode: (payload) => record(payload) as unknown as SubjectDeletionPreview,
      },
    )
  },
  deleteSubject(
    studentRef: string,
    preview: SubjectDeletionPreview,
    operationIdValue: string,
  ) {
    return apiClient.request(`/api/class-teacher/support/subjects/${encodeURIComponent(studentRef)}`, {
      method: 'DELETE',
      headers: headers(),
      body: {
        operation_id: operationIdValue,
        confirmation_phrase: preview.delete_confirmation_phrase,
        preview_version: preview.preview_version ?? null,
      },
      decode: record,
    })
  },
  listRecords(studentRef: string) {
    return apiClient.request(`/api/class-teacher/support/subjects/${encodeURIComponent(studentRef)}/records`, {
      decode: (payload) => list(payload, supportRecord),
    })
  },
  setRecordState(
    value: SupportRecord,
    state: 'active' | 'withdrawn' | 'archived',
    reason: string,
  ) {
    return apiClient.request(`/api/class-teacher/support/records/${value.record_id}/state`, {
      method: 'POST',
      headers: headers(),
      body: {
        operation_id: operationId(),
        expected_revision: value.current_revision,
        state,
        reason,
      },
      decode: supportRecord,
    })
  },
  getSummary(studentRef: string) {
    return apiClient.request(`/api/class-teacher/support/subjects/${encodeURIComponent(studentRef)}/summary`, {
      decode: (payload) => record(payload) as unknown as SupportSummary,
    })
  },
  createRecord(
    studentRef: string,
    input: {
      record_kind: string
      content: string
      scene: string
      source: string
      basis: string | null
      counterexample: string | null
      category: string | null
      observed_at: string
      review_at: string | null
      expires_at: string | null
      plan_id?: string | null
    },
    operationIdValue = operationId(),
  ) {
    return apiClient.request(`/api/class-teacher/support/subjects/${encodeURIComponent(studentRef)}/records`, {
      method: 'POST',
      headers: headers(),
      body: { ...input, operation_id: operationIdValue },
      decode: supportRecord,
    })
  },
  reviseRecord(
    value: SupportRecord,
    content: string,
    revisionReason: string,
  ) {
    return apiClient.request(`/api/class-teacher/support/records/${value.record_id}`, {
      method: 'PUT',
      headers: headers(),
      body: {
        operation_id: operationId(),
        expected_revision: value.current_revision,
        content,
        scene: value.scene,
        source: value.source,
        basis: null,
        counterexample: value.counterexample,
        category: 'general',
        observed_at: value.observed_at,
        review_at: value.review_at,
        expires_at: value.expires_at,
        revision_reason: revisionReason,
      },
      decode: supportRecord,
    })
  },
  createSupportPlan(
    studentRef: string,
    input: { goal: string; support_actions: string[]; review_at: string },
  ) {
    return apiClient.request(`/api/class-teacher/support/subjects/${encodeURIComponent(studentRef)}/plans`, {
      method: 'POST',
      headers: headers(),
      body: { ...input, action_id: null, operation_id: operationId() },
      decode: supportPlan,
    })
  },
  draftSupportPlan(studentRef: string, operationIdValue: string) {
    return apiClient.request(`/api/class-teacher/support/subjects/${encodeURIComponent(studentRef)}/plans/ai-draft`, {
      method: 'POST',
      headers: headers(),
      body: { operation_id: operationIdValue },
      timeoutMs: 120_000,
      decode: (payload) => record(payload) as unknown as SupportPlanAiDraft,
    })
  },
  listSupportPlans(studentRef: string) {
    return apiClient.request(`/api/class-teacher/support/subjects/${encodeURIComponent(studentRef)}/plans`, {
      decode: (payload) => list(
        payload,
        supportPlan,
      ),
    })
  },
  completeSupportPlan(
    planId: string,
    revision: number,
    result: string,
    outcome: SupportPlanOutcome | null = null,
  ) {
    return apiClient.request(`/api/class-teacher/support/plans/${planId}/complete`, {
      method: 'POST',
      headers: headers(),
      body: {
        operation_id: operationId(),
        expected_revision: revision,
        result,
        outcome,
      },
      decode: supportPlan,
    })
  },
  projectAffair(studentRef: string, affairId: string) {
    return apiClient.request(`/api/class-teacher/support/subjects/${encodeURIComponent(studentRef)}/project-affair`, {
      method: 'POST',
      headers: headers(),
      body: { operation_id: operationId(), affair_id: affairId },
      decode: supportRecord,
    })
  },
  listEvidence(studentRef: string) {
    return apiClient.request(`/api/class-teacher/evidence/subjects/${encodeURIComponent(studentRef)}`, {
      decode: (payload) => list(payload, evidence),
    })
  },
  confirmEvidence(
    batch: Record<string, unknown>,
  ) {
    return apiClient.request('/api/class-teacher/evidence/batches', {
      method: 'POST',
      headers: headers(),
      body: { operation_id: operationId(), batch },
      decode: record,
    })
  },
  updateEvidenceSession(sessionId: string, fields: EvidenceSessionUpdateFields) {
    return apiClient.request(`/api/class-teacher/evidence/sessions/${encodeURIComponent(sessionId)}`, {
      method: 'PATCH',
      headers: headers(),
      body: { operation_id: operationId(), ...fields },
      decode: record,
    })
  },
  updateEvidenceSessionMaxScores(sessionId: string, maxScores: Record<string, number>, participantCount?: number) {
    return apiClient.request(`/api/class-teacher/evidence/sessions/${encodeURIComponent(sessionId)}/max-scores`, {
      method: 'PATCH',
      headers: headers(),
      body: { operation_id: operationId(), max_scores: maxScores, ...(participantCount != null ? { participant_count: participantCount } : {}) },
      decode: record,
    })
  },
  updateGlobalEvidenceSettings(maxScores: Record<string, number>, participantCount?: number) {
    return apiClient.request('/api/class-teacher/evidence/max-scores/global', {
      method: 'PATCH',
      headers: headers(),
      body: { operation_id: operationId(), max_scores: maxScores, ...(participantCount != null ? { participant_count: participantCount } : {}) },
      decode: (payload) => record(payload) as unknown as EvidenceGlobalSettingsResult,
    })
  },
  previewDeleteEvidenceSession(sessionId: string) {
    return apiClient.request(
      `/api/class-teacher/evidence/sessions/${encodeURIComponent(sessionId)}/delete-preview`,
      {
        decode: (payload) => record(payload) as unknown as EvidenceSessionDeletePreview,
      },
    )
  },
  deleteEvidenceSession(sessionId: string, previewVersion: string, confirmationPhrase: string) {
    return apiClient.request(`/api/class-teacher/evidence/sessions/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
      headers: headers(),
      body: {
        operation_id: operationId(),
        preview_version: previewVersion,
        confirmation_phrase: confirmationPhrase,
      },
      decode: record,
    })
  },
  previewSpreadsheet(
    fileName: string,
    contentBase64: string,
    sheetName: string | null = null,
  ) {
    return apiClient.request('/api/class-teacher/evidence/spreadsheet-preview', {
      method: 'POST',
      headers: headers(),
      body: {
        file_name: fileName,
        content_base64: contentBase64,
        sheet_name: sheetName,
      },
      decode: (payload) => record(payload) as unknown as SpreadsheetPreview,
    })
  },
  listAttention(studentRef: string) {
    return apiClient.request(`/api/class-teacher/attention-cards/subjects/${encodeURIComponent(studentRef)}`, {
      decode: (payload) => list(payload, attention),
    })
  },
  createAttention(
    input: Omit<AttentionCard,
      'attention_card_id' | 'revision' | 'subject_id' | 'state' | 'decision'
      | 'action_id' | 'evidence_source' | 'risk_score'>,
  ) {
    return apiClient.request('/api/class-teacher/attention-cards', {
      method: 'POST',
      headers: headers(),
      body: { ...input, operation_id: operationId() },
      decode: attention,
    })
  },
  resolveAttention(
    card: AttentionCard,
    input: {
      decision: 'follow_up' | 'observe' | 'no_action'
      reason: string | null
      plan_id: string | null
      review_at: string | null
    },
  ) {
    return apiClient.request(
      `/api/class-teacher/attention-cards/${card.attention_card_id}/resolve`,
      {
        method: 'POST',
        headers: headers(),
        body: {
          ...input,
          operation_id: operationId(),
          revision: card.revision,
        },
        decode: record,
      },
    )
  },
}
