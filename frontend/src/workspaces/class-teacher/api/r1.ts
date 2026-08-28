import { apiClient } from '../../../api/client'

const CLIENT_HEADER = 'class-teacher-browser-v1'
const operationId = () => globalThis.crypto.randomUUID()
export type JsonRecord = Record<string, unknown>
const record = (value: unknown): JsonRecord => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('contract')
  return value as JsonRecord
}
const writeHeaders = () => ({ 'x-class-teacher-client': CLIENT_HEADER })

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
  key?: string
  title: string
  details?: string | null
  state: string
  revision: number
  safety_required?: boolean
  due_at?: string | null
  decision_key?: string | null
  decision_prompt?: string | null
  decision_options?: Array<{ value: string; label: string }>
  depends_on?: string[]
  activation?: { decision_key: string; allowed_values: string[] } | null
  communication_templates?: Array<Record<string, unknown>>
  origin?: string | null
  result?: string | null
}

export interface AffairParticipant {
  participant_id: string
  reference: string
  subject_id?: string | null
  student_ref?: string | null
}

export interface AffairProfileDraft {
  draft_id: string
  affair_id: string
  subject_id: string
  student_ref?: string | null
  display_name?: string
  state: 'pending' | 'confirmed' | 'discarded'
  revision: number
  record_kind?: string
  source?: string
  basis?: string | null
  record_summary?: string
  scene?: string
  observed_at?: string
  review_at?: string | null
  expires_at?: string | null
  profile_update?: Record<string, unknown>
  confirmed_at?: string | null
}

export interface AffairDraft { step_instance_id: string; draft_kind: string; revision: number; text?: string }
export interface AffairFlowRevisionItem {
  item_id: string
  kind: 'add_step' | 'revise_step' | 'note'
  step_key?: string | null
  target_step_key?: string | null
  title?: string
  details?: string
  depends_on?: string[]
  reason?: string
  text?: string
  state: string
}
export interface AffairFlowRevision {
  revision_id: string
  sync_id: string
  source_text: string
  assistant_message: string
  items: AffairFlowRevisionItem[]
  dropped_items: Array<{ item_id: string; reason: string }>
  state: 'pending_review' | 'applied' | 'discarded' | string
  created_at: string
  decided_at?: string | null
  accepted_item_ids?: string[]
}
export interface AffairSyncRequest {
  sync_id: string
  text: string
  state: 'queued' | 'answered' | 'failed' | 'invalid_result' | string
  created_at: string
}
export interface AffairDetail extends AffairSummary {
  template_key: string
  occurrence_sequence: number
  current_steps: AffairStep[]
  completed_steps: AffairStep[]
  preview_steps: AffairStep[]
  drafts: AffairDraft[]
  participants?: AffairParticipant[]
  decisions?: Array<{ decision_id: string; step_instance_id: string | null; summary: string; decision_key?: string | null; selected_option?: string | null }>
  to_verify?: string[]
  discard_reason?: string | null
  profile_update_drafts?: AffairProfileDraft[]
  school_config_gaps?: string[]
  emergency_prompt?: string | null
  flow_revisions?: AffairFlowRevision[]
  sync_requests?: AffairSyncRequest[]
}

export interface DirectorySubject {
  subject_id: string
  // 对外学生编号：稳定学籍标识「班级|学号」；subject_id 为内部档案编号。
  student_ref: string
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
  profile_state?: 'created' | 'not_created'
}

// 调学生相关接口统一使用对外编号 student_ref；旧数据缺少该字段时退回内部编号。
export function studentRefOf(subject: { student_ref?: string | null; subject_id: string }): string {
  return subject.student_ref || subject.subject_id
}

export interface ExistingRosterStudent {
  source_key: string
  student_code: string
  display_name: string
  class_label: string
  subject_id: string | null
  roster_state: 'available' | 'active' | 'historical'
  roster_ref: string
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

export interface ProfileRoundChanges {
  summary_changed: boolean
  dimensions: Record<string, string[]>
  open_questions: string[]
  support_focus: string[]
}

export interface ProfileLatestRound {
  record_id: string
  adopted_at: string
  changed: ProfileRoundChanges
}

export interface CurrentStudentProfile {
  entry_id: string | null
  revision: number
  summary: string
  dimensions: StudentProfileDimension[]
  open_questions: string[]
  support_focus: StudentSupportFocus[]
  updated_at: string | null
  latest_round?: ProfileLatestRound | null
}

export interface StudentCard {
  subject: DirectorySubject
  entries: StudentCardEntry[]
  current_profile?: CurrentStudentProfile | null
  existing_records: JsonRecord[]
  support_plans: JsonRecord[]
  profile_state?: 'created' | 'not_created'
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
  profile?: AcademicProfile
  filter_options?: { series: string[]; subjects: string[] }
  applied_filters?: { time_range: string; comparison_series: string | null; subject_name: string | null; comparable_only: boolean }
}

export interface AcademicProfilePoint { occurred_on: string | null; term_label: string; short_label?: string; grade_level?: string | null; session_title?: string | null; rank: number | null; participant_count?: number | null; relative_position?: number | null; result_state?: string | null }
export interface AcademicProfileSubject { subject_name: string; latest: (AcademicProfilePoint & { score?: number | null; class_rank?: number | null }) | null; rank_delta: number | null; points: AcademicProfilePoint[]; attention: boolean }
export interface AcademicProfile {
  current: {
    session_title: string | null; occurred_on: string | null; term_label: string
    short_label?: string
    grade?: string | null; term?: string | null
    score: number | null; rank: number | null; class_rank: number | null
    participant_count: number | null; top_ratio: number | null
    previous: { session_title: string | null; occurred_on: string | null; term_label: string; short_label?: string; rank: number | null; participant_count: number | null } | null
    rank_delta: number | null
  } | null
  trend: { label: string; step_deltas: number[]; session_count: number }
  stability: { label: string; swing_ratio: number | null; session_count: number }
  skew: { label: string; strongest: { subject_name: string; rank: number | null; relative_position: number | null }[]; weakest: { subject_name: string; rank: number | null; relative_position: number | null }[]; gap_ratio: number | null }
  subjects: AcademicProfileSubject[]
  total_trend?: AcademicProfilePoint[]
  basis: { total_session_count: number; grade: string | null }
}

export interface AcademicPoint { evidence_version_id: string; session_id?: string; subject_name: string; result_state: string; score: number | null; occurred_on: string; grade_level?: string | null; measure_role?: string; rank?: number | null; class_rank?: number | null; participant_count?: number | null; relative_position?: number | null; max_score?: number | null; is_comparable?: boolean; comparable_outputs?: string[] }
export interface AcademicSession { session_id: string; title: string; occurred_on: string; short_label?: string; comparison_series?: string | null; metadata_complete: boolean; grade?: string | null; term?: string | null; exam_type?: string | null; academic_year?: string | null; evidence: AcademicPoint[] }
export interface ComparisonSegment { overall_status?: string; dimensions: { rank: { status: string }; score: { status: string } } }
export interface AcademicSeries { subject_name: string; points: AcademicPoint[]; segments: ComparisonSegment[] }
export interface RankChangePair { subject_name: string; from: number; to: number; delta?: number; rank_scope?: string | null; from_rank?: number | null; to_rank?: number | null; rank_delta?: number | null }
export interface RelativeSubjectSignal { subject_name: string; signal: string; eligible_session_count: number }
export interface AttentionCard { attention_card_id: string; revision: number; state: string; observed_fact: string; evidence_sufficiency: string }

export interface SupportOverviewFollowUp {
  subject_id: string
  student_ref?: string
  display_name: string
  class_label: string | null
  next_review_at: string | null
  last_record_at: string | null
  active_record_count: number
  due_soon: boolean
}

export interface SupportOverviewRecord {
  record_id: string
  subject_id: string
  student_ref?: string
  display_name: string
  class_label: string | null
  record_kind: string
  observed_at: string
  excerpt: string
}

export interface SupportOverview {
  follow_ups: SupportOverviewFollowUp[]
  recent_records: SupportOverviewRecord[]
  has_records: boolean
  review_soon_days: number
}

export interface AcademicSessionSummary {
  session_id: string
  title: string
  occurred_on: string
  short_label?: string
  comparison_series: string | null
  subject_names: string[]
  member_count: number
  metadata_complete: boolean
  grade?: string | null
  term?: string | null
  exam_type?: string | null
  academic_year?: string | null
}

export interface AcademicSubjectStats {
  subject_name: string
  count: number
  average: number
  // 总体标准差（标准分雷达用）；少于 2 个有效分数时为 null
  stddev?: number | null
  maximum: number
  minimum: number
  average_rank: number | null
  top50_count: number
  top100_count: number
  front30pct_count: number | null
  bands: Array<{ label: string; count: number }>
  // 等级人数（固定段序 A+…C+其他）；该科无人带等级时为 null/缺省，前端回退得分率分段
  grade_counts?: Array<{ label: string; count: number }> | null
}

export interface AcademicOverview {
  sessions: AcademicSessionSummary[]
  latest_session: { session_id: string; title: string; occurred_on: string; short_label?: string; subjects: AcademicSubjectStats[] } | null
  attention_students: Array<{ subject_id: string; student_ref?: string; display_name: string; class_label: string | null; pending_count: number }>
  attention_pending_count: number
}

export interface AcademicSessionClassResults {
  session_id: string
  title: string
  occurred_on: string
  short_label?: string
  participant_count: number | null
  subjects: Array<{
    subject_name: string
    max_score: number | null
    stats: AcademicSubjectStats
  }>
  students: Array<{
    subject_id: string
    student_ref?: string
    display_name: string
    class_label: string | null
    total_rank: number | null
    results: Record<string, {
      score: number | null
      rank: number | null
      class_rank: number | null
      relative_position: number | null
      max_score: number | null
      grade_level?: string | null
      result_state: string
    }>
  }>
}

export interface AcademicClassTrend {
  sessions: Array<{
    session_id: string
    occurred_on: string
    title: string
    short_label?: string
    term: string | null
    grade: string | null
    subjects: Array<{
      subject_name: string
      average: number
      count: number
      max_score: number | null
      average_rank: number | null
      top50_count: number
      top100_count: number
      front30pct_count: number | null
    }>
  }>
}

export const affairR1Api = {
  list() {
    return apiClient.request('/api/class-teacher/sop/affairs', {
      decode: (value) => record(value).items as AffairSummary[],
    })
  },
  read(affairId: string) {
    return apiClient.request(`/api/class-teacher/sop/affairs/${affairId}`, { decode: (value) => record(value) as unknown as AffairDetail })
  },
  command(affair: AffairDetail, command: string, input: Record<string, unknown>) {
    return apiClient.request(`/api/class-teacher/sop/affairs/${affair.affair_id}/commands`, {
      method: 'POST', headers: writeHeaders(),
      body: { command, expected_revision: affair.revision, operation_id: operationId(), ...input }, decode: (value) => record(value) as unknown as AffairDetail,
    })
  },
  saveDraft(affairId: string, stepId: string, kind: 'fact' | 'communication', text: string, revision: number | null) {
    return apiClient.request(`/api/class-teacher/sop/affairs/${affairId}/steps/${stepId}/draft`, {
      method: 'PUT', headers: writeHeaders(), body: {
        draft_kind: kind, text, expected_revision: revision, operation_id: operationId(),
      }, decode: record,
    })
  },
  syncUpdate(affair: AffairDetail, text: string) {
    return apiClient.request(`/api/class-teacher/sop/affairs/${affair.affair_id}/sync-updates`, {
      method: 'POST', headers: writeHeaders(),
      body: { text, expected_revision: affair.revision, operation_id: operationId() },
      decode: (value) => record(value) as unknown as { sync_id: string; task_id: string; task_state: string },
    })
  },
  decideFlowRevision(affair: AffairDetail, revisionId: string, acceptedItemIds: string[]) {
    return apiClient.request(`/api/class-teacher/sop/affairs/${affair.affair_id}/flow-revisions/${revisionId}/decide`, {
      method: 'POST', headers: writeHeaders(),
      body: { accepted_item_ids: acceptedItemIds, expected_revision: affair.revision, operation_id: operationId() },
      decode: (value) => record(value) as unknown as AffairDetail,
    })
  },
  discard(affair: AffairDetail, reason: string) {
    return apiClient.request(`/api/class-teacher/sop/affairs/${affair.affair_id}/commands`, {
      method: 'POST', headers: writeHeaders(),
      body: { command: 'discard', reason, expected_revision: affair.revision, operation_id: operationId() },
      decode: (value) => record(value) as unknown as AffairDetail,
    })
  },
  confirmProfileDraft(affairId: string, draftId: string) {
    return apiClient.request(`/api/class-teacher/sop/affairs/${affairId}/profile-drafts/${draftId}/confirm`, {
      method: 'POST', headers: writeHeaders(),
      body: { operation_id: operationId() }, decode: (value) => record(value) as unknown as AffairProfileDraft,
    })
  },
  discardProfileDraft(affairId: string, draftId: string) {
    return apiClient.request(`/api/class-teacher/sop/affairs/${affairId}/profile-drafts/${draftId}/discard`, {
      method: 'POST', headers: writeHeaders(),
      body: { operation_id: operationId() }, decode: (value) => record(value) as unknown as AffairProfileDraft,
    })
  },
}

export const projectionR1Api = {
  resolve(projectionId: string) {
    return apiClient.request(`/api/class-teacher/protected-work/${projectionId}`, {
      decode: record,
    })
  },
}

export const studentR1Api = {
  studentCard(subjectId: string) {
    return apiClient.request(`/api/class-teacher/support/subjects/${encodeURIComponent(subjectId)}/student-card`, {
      decode: value => record(value) as unknown as StudentCard,
    })
  },
  rosterSource(input: { q?: string; classLabel?: string; cursor?: string; pageSize?: number } = {}) {
    const query = new URLSearchParams()
    if (input.q) query.set('q', input.q)
    if (input.classLabel) query.set('class_label', input.classLabel)
    if (input.cursor) query.set('cursor', input.cursor)
    if (input.pageSize) query.set('page_size', String(input.pageSize))
    return apiClient.request(`/api/class-teacher/support/roster-source?${query}`, { decode: (value) => record(value) as unknown as { items: ExistingRosterStudent[]; classes: string[]; source_revision: string; total: number; cursor: string | null } })
  },
  directory(input: { q?: string; classLabel?: string; state?: string; rosterState?: string; sort?: string; cursor?: string; pageSize?: number } = {}) {
    const query = new URLSearchParams()
    if (input.q) query.set('q', input.q)
    if (input.classLabel) query.set('class_label', input.classLabel)
    if (input.state) query.set('state', input.state)
    if (input.rosterState) query.set('roster_state', input.rosterState)
    if (input.sort) query.set('sort', input.sort)
    if (input.cursor) query.set('cursor', input.cursor)
    if (input.pageSize) query.set('page_size', String(input.pageSize))
    return apiClient.request(`/api/class-teacher/support/directory?${query}`, {
      decode: (value) => record(value) as { items: DirectorySubject[]; cursor: string | null; total: number; page_size: number },
    })
  },
  header(subjectId: string) {
    return apiClient.request(`/api/class-teacher/support/subjects/${encodeURIComponent(subjectId)}/workspace-header`, { decode: record })
  },
  supportOverview(limit = 50) {
    return apiClient.request(`/api/class-teacher/support/overview?limit=${limit}`, {
      decode: (value) => record(value) as unknown as SupportOverview,
    })
  },
  academicOverview() {
    return apiClient.request('/api/class-teacher/evidence/overview', {
      decode: (value) => record(value) as unknown as AcademicOverview,
    })
  },
  sessionClassResults(sessionId: string) {
    return apiClient.request(`/api/class-teacher/evidence/sessions/${encodeURIComponent(sessionId)}/class-results`, {
      decode: (value) => record(value) as unknown as AcademicSessionClassResults,
    })
  },
  classTrend() {
    return apiClient.request('/api/class-teacher/evidence/class-trend', {
      decode: (value) => record(value) as unknown as AcademicClassTrend,
    })
  },
  records(subjectId: string) {
    return apiClient.request(`/api/class-teacher/support/subjects/${encodeURIComponent(subjectId)}/records`, { decode: (value) => record(value).items as JsonRecord[] })
  },
  academic(subjectId: string, input: { timeRange?: string; comparisonSeries?: string; subjectName?: string; comparableOnly?: boolean } = {}) {
    const query = new URLSearchParams()
    if (input.timeRange && input.timeRange !== 'all') query.set('time_range', input.timeRange)
    if (input.comparisonSeries) query.set('comparison_series', input.comparisonSeries)
    if (input.subjectName) query.set('subject_name', input.subjectName)
    if (input.comparableOnly) query.set('comparable_only', 'true')
    return apiClient.request(`/api/class-teacher/support/subjects/${encodeURIComponent(subjectId)}/academic-analysis?${query}`, { decode: (value) => record(value) as unknown as AcademicAnalysis })
  },
  evidenceSnapshot(evidenceId: string) {
    return apiClient.request(`/api/class-teacher/evidence/${evidenceId}/snapshot`, { decode: record })
  },
  decideAttention(card: AttentionCard, analysis: AcademicAnalysis, input: { decision: string; reason: string; reviewAt: string | null; planId: string | null }) {
    return apiClient.request(`/api/class-teacher/attention-cards/${card.attention_card_id}/decide`, {
      method: 'POST', headers: writeHeaders(), body: {
        operation_id: operationId(), revision: card.revision, decision: input.decision,
        reason: input.reason, review_at: input.reviewAt, plan_id: input.planId,
        source_version: analysis.source_version,
      }, decode: record,
    })
  },
}
