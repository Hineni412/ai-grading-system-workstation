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
  roster_state?: 'active' | 'historical' | 'manual'
  revision?: number
}

export interface ExistingRosterStudent {
  source_key: string
  student_code: string
  display_name: string
  class_label: string
  subject_id: string | null
  roster_state: 'available' | 'active' | 'historical'
  opaque_ref: string
  student_revision: string
}

export interface StudentCardEntry {
  entry_id: string
  teacher_confirmed_at: string
  portrait: { summary: string; strengths: string[]; needs: string[]; open_questions: string[] }
  sop: { title: string; steps: string[]; review_date: string | null }
}

export interface StudentProfileDimension {
  key: string
  label: string
  items: string[]
}

export interface StudentSupportFocus {
  key: string
  title: string
  need: string
  effective_methods: string[]
  next_actions: string[]
}

export interface CurrentStudentProfile {
  entry_id: string | null
  revision: number
  summary: string
  dimensions: StudentProfileDimension[]
  open_questions: string[]
  support_focus: StudentSupportFocus[]
  updated_at: string | null
}

export interface StudentCard {
  subject: DirectorySubject
  entries: StudentCardEntry[]
  current_profile?: CurrentStudentProfile
  existing_records: JsonRecord[]
  support_plans: JsonRecord[]
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
  studentCard(token: string, subjectId: string) {
    return apiClient.request(`/api/class-teacher/support/subjects/${subjectId}/student-card`, {
      headers: readHeaders(token), decode: value => record(value) as unknown as StudentCard,
    })
  },
  rosterSource(token: string, input: { q?: string; classLabel?: string; cursor?: string; pageSize?: number } = {}) {
    const query = new URLSearchParams()
    if (input.q) query.set('q', input.q)
    if (input.classLabel) query.set('class_label', input.classLabel)
    if (input.cursor) query.set('cursor', input.cursor)
    if (input.pageSize) query.set('page_size', String(input.pageSize))
    return apiClient.request(`/api/class-teacher/support/roster-source?${query}`, { headers: readHeaders(token), decode: (value) => record(value) as unknown as { items: ExistingRosterStudent[]; classes: string[]; source_revision: string; total: number; cursor: string | null } })
  },
  directory(token: string, input: { q?: string; classLabel?: string; state?: string; rosterState?: string; sort?: string; cursor?: string; pageSize?: number } = {}) {
    const query = new URLSearchParams()
    if (input.q) query.set('q', input.q)
    if (input.classLabel) query.set('class_label', input.classLabel)
    if (input.state) query.set('state', input.state)
    if (input.rosterState) query.set('roster_state', input.rosterState)
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
