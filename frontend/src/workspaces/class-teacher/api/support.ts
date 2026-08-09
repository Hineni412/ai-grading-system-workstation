import { apiClient } from '../../../api/client'

const CLIENT_HEADER = 'class-teacher-browser-v1'

export interface SupportSubject {
  subject_id: string
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
  content: string
  scene: string
  source: string
  counterexample: string | null
  observed_at: string
  review_at: string | null
  expires_at: string | null
}

export interface QuickFragment {
  fragment_id: string
  text: string
  suggested_kind: string
}

export interface QuickInboxItem {
  inbox_item_id: string
  revision: number
  subject_id: string | null
  state: string
  original_text: string | null
  fragments: QuickFragment[]
  voice_inbox_available: boolean
  external_transcription_allowed: boolean
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
  created_at: string
  updated_at: string
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

function quick(payload: unknown): QuickInboxItem {
  const value = record(payload)
  if (!Array.isArray(value.fragments)) throw new Error('contract')
  return value as unknown as QuickInboxItem
}

function evidence(payload: unknown): AssessmentEvidence {
  return record(payload) as unknown as AssessmentEvidence
}

function attention(payload: unknown): AttentionCard {
  return record(payload) as unknown as AttentionCard
}

function headers(token: string): Record<string, string> {
  return {
    'x-class-teacher-session': token,
    'x-class-teacher-client': CLIENT_HEADER,
  }
}

function readHeaders(token: string): Record<string, string> {
  return { 'x-class-teacher-session': token }
}

function operationId(): string {
  return globalThis.crypto.randomUUID()
}

export const supportApi = {
  listSubjects(token: string) {
    return apiClient.request('/api/class-teacher/support/subjects', {
      headers: readHeaders(token),
      decode: (payload) => list(payload, subject),
    })
  },
  createSubject(
    token: string,
    input: { source_student_id: string; display_name: string; class_label: string | null },
  ) {
    return apiClient.request('/api/class-teacher/support/subjects', {
      method: 'POST',
      headers: headers(token),
      body: { ...input, operation_id: operationId() },
      decode: subject,
    })
  },
  previewSubjectDeletion(token: string, subjectId: string) {
    return apiClient.request(
      `/api/class-teacher/support/subjects/${subjectId}/deletion-preview`,
      {
        headers: readHeaders(token),
        decode: (payload) => record(payload) as unknown as SubjectDeletionPreview,
      },
    )
  },
  deleteSubject(
    token: string,
    subjectId: string,
    preview: SubjectDeletionPreview,
    operationIdValue: string,
  ) {
    return apiClient.request(`/api/class-teacher/support/subjects/${subjectId}`, {
      method: 'DELETE',
      headers: headers(token),
      body: {
        operation_id: operationIdValue,
        confirmation_phrase: preview.delete_confirmation_phrase,
        preview_version: preview.preview_version ?? null,
      },
      decode: record,
    })
  },
  listRecords(token: string, subjectId: string) {
    return apiClient.request(`/api/class-teacher/support/subjects/${subjectId}/records`, {
      headers: readHeaders(token),
      decode: (payload) => list(payload, supportRecord),
    })
  },
  getSummary(token: string, subjectId: string) {
    return apiClient.request(`/api/class-teacher/support/subjects/${subjectId}/summary`, {
      headers: readHeaders(token),
      decode: (payload) => record(payload) as unknown as SupportSummary,
    })
  },
  createRecord(
    token: string,
    subjectId: string,
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
    },
    operationIdValue = operationId(),
  ) {
    return apiClient.request(`/api/class-teacher/support/subjects/${subjectId}/records`, {
      method: 'POST',
      headers: headers(token),
      body: { ...input, operation_id: operationIdValue },
      decode: supportRecord,
    })
  },
  reviseRecord(
    token: string,
    value: SupportRecord,
    content: string,
    revisionReason: string,
  ) {
    return apiClient.request(`/api/class-teacher/support/records/${value.record_id}`, {
      method: 'PUT',
      headers: headers(token),
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
    token: string,
    subjectId: string,
    input: { goal: string; support_actions: string[]; review_at: string },
  ) {
    return apiClient.request(`/api/class-teacher/support/subjects/${subjectId}/plans`, {
      method: 'POST',
      headers: headers(token),
      body: { ...input, action_id: null, operation_id: operationId() },
      decode: supportPlan,
    })
  },
  listSupportPlans(token: string, subjectId: string) {
    return apiClient.request(`/api/class-teacher/support/subjects/${subjectId}/plans`, {
      headers: readHeaders(token),
      decode: (payload) => list(
        payload,
        supportPlan,
      ),
    })
  },
  completeSupportPlan(
    token: string,
    planId: string,
    revision: number,
    result: string,
  ) {
    return apiClient.request(`/api/class-teacher/support/plans/${planId}/complete`, {
      method: 'POST',
      headers: headers(token),
      body: {
        operation_id: operationId(),
        expected_revision: revision,
        result,
      },
      decode: supportPlan,
    })
  },
  projectAffair(token: string, subjectId: string, affairId: string) {
    return apiClient.request(`/api/class-teacher/support/subjects/${subjectId}/project-affair`, {
      method: 'POST',
      headers: headers(token),
      body: { operation_id: operationId(), affair_id: affairId },
      decode: supportRecord,
    })
  },
  listQuickInbox(token: string) {
    return apiClient.request('/api/class-teacher/quick-inbox', {
      headers: readHeaders(token),
      decode: (payload) => list(payload, quick),
    })
  },
  updateQuickFragments(
    token: string,
    item: QuickInboxItem,
    fragments: QuickFragment[],
  ) {
    return apiClient.request(`/api/class-teacher/quick-inbox/${item.inbox_item_id}`, {
      method: 'PUT',
      headers: headers(token),
      body: {
        operation_id: operationId(),
        revision: item.revision,
        fragments,
      },
      decode: quick,
    })
  },
  createQuickText(token: string, text: string, subjectId: string | null) {
    return apiClient.request('/api/class-teacher/quick-inbox', {
      method: 'POST',
      headers: headers(token),
      body: { operation_id: operationId(), text, subject_id: subjectId },
      decode: quick,
    })
  },
  confirmQuickRecord(
    token: string,
    item: QuickInboxItem,
    fragment: QuickFragment,
    targetKind: 'support_record' | 'action' | 'sop',
    options: Record<string, unknown>,
  ) {
    return apiClient.request(
      `/api/class-teacher/quick-inbox/${item.inbox_item_id}/confirm`,
      {
        method: 'POST',
        headers: headers(token),
        body: {
          operation_id: operationId(),
          fragment_id: fragment.fragment_id,
          target_kind: targetKind,
          target_options: options,
        },
        decode: record,
      },
    )
  },
  cancelQuick(token: string, itemId: string) {
    return apiClient.request(`/api/class-teacher/quick-inbox/${itemId}/cancel`, {
      method: 'POST',
      headers: headers(token),
      body: { operation_id: operationId() },
      decode: record,
    })
  },
  listEvidence(token: string, subjectId: string) {
    return apiClient.request(`/api/class-teacher/evidence/subjects/${subjectId}`, {
      headers: readHeaders(token),
      decode: (payload) => list(payload, evidence),
    })
  },
  confirmEvidence(
    token: string,
    batch: Record<string, unknown>,
  ) {
    return apiClient.request('/api/class-teacher/evidence/batches', {
      method: 'POST',
      headers: headers(token),
      body: { operation_id: operationId(), batch },
      decode: record,
    })
  },
  previewSpreadsheet(
    token: string,
    fileName: string,
    contentBase64: string,
    sheetName: string | null = null,
  ) {
    return apiClient.request('/api/class-teacher/evidence/spreadsheet-preview', {
      method: 'POST',
      headers: headers(token),
      body: {
        file_name: fileName,
        content_base64: contentBase64,
        sheet_name: sheetName,
      },
      decode: (payload) => record(payload) as unknown as SpreadsheetPreview,
    })
  },
  listAttention(token: string, subjectId: string) {
    return apiClient.request(`/api/class-teacher/attention-cards/subjects/${subjectId}`, {
      headers: readHeaders(token),
      decode: (payload) => list(payload, attention),
    })
  },
  createAttention(
    token: string,
    input: Omit<AttentionCard,
      'attention_card_id' | 'revision' | 'subject_id' | 'state' | 'decision'
      | 'action_id' | 'evidence_source' | 'risk_score'>,
  ) {
    return apiClient.request('/api/class-teacher/attention-cards', {
      method: 'POST',
      headers: headers(token),
      body: { ...input, operation_id: operationId() },
      decode: attention,
    })
  },
  resolveAttention(
    token: string,
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
        headers: headers(token),
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
