import { apiClient } from './client'
import {
  assertNoPathLikeKeys,
  isFiniteNumber,
  isNonnegativeInteger,
  isNullableString,
  isRecord,
} from './validation'
import type { QuestionBankRichContent } from './question-bank'
import { isConfigRichContent } from './config-workspace'

export const CLASS_ANALYSIS_STATUSES = ['no_data', 'ready', 'generating'] as const

export type ClassAnalysisStatus = (typeof CLASS_ANALYSIS_STATUSES)[number]

export interface ClassAnalysisExam {
  title: string
  subject: string | null
  full_score: number
  graded_at: string | null
}

export interface ClassScoreDistribution {
  avg: number
  median: number
  max: number
  min: number
  pass_rate: number
  bands: Record<string, number>
}

export interface ClassAnalysisRecord {
  student_name: string
  student_id?: number
  student_code?: string
  class_name?: string
  score: number | null
  deduction_reason: string | null
  error_category: string | null
  error_summary: string | null
}

export interface ClassCauseEvidence {
  text: string
  student_ids: number[]
  student_answer?: string
  evidence_steps?: string[]
  missing_steps?: string[]
  /** 按步骤整理时存在的独立扣分步骤单元；页面暂不展示。 */
  failed_steps?: Record<string, unknown>[]
  previous_answers?: { question_id: string; student_answer: string; text: string; evidence_steps: string[] }[]
}

export const CLASS_CAUSE_KINDS = ['error', 'process', 'response_state', 'carry_forward', 'review'] as const
export type ClassCauseKind = (typeof CLASS_CAUSE_KINDS)[number]

/** 7 个固定家长可读错因大类（错因体系改造方案 P1）。 */
export const CAUSE_CATEGORIES = [
  '概念理解', '计算与化简', '审题与条件', '方法与思路', '过程与依据', '书写与规范', '未作答',
] as const
export type CauseCategory = (typeof CAUSE_CATEGORIES)[number]

export interface ClassCause {
  reason: string
  count: number
  kind?: ClassCauseKind
  /** v3 起输出；carry_forward/review 与旧版结果没有大类。 */
  category?: CauseCategory
  step_id?: string | null
  /** v4 起输出：该错因关联的评分步骤 id（对应 rubric step_id）。 */
  step_ids?: string[]
  /** existing=复用已有错法名；candidate=本场新命名。 */
  pattern_status?: 'existing' | 'candidate'
  /** true=该错法被老师修改过名称或大类（P5 可选操作）。 */
  teacher_edited?: boolean
  evidence?: ClassCauseEvidence[]
  manifestations?: { description: string; source_question_id: string | null; evidence: ClassCauseEvidence[] }[]
}

export interface ClassCauseAnalysis {
  status: 'ready' | 'partial' | 'not_generated'
  pending_questions: number
  total_questions: number
  failed_questions: number
  legacy_questions?: number
  /** v2 旧版整理结果仍在展示的题目数（无错误大类，建议重新整理）。 */
  outdated_questions?: number
  /** 按步骤拆分前整理的题目数（兼容展示，手动整理后升级为按步骤结果）。 */
  pre_step_questions?: number
  stale: boolean
  generated_at: string | null
  origin: string | null
}

export interface ClassAnalysisQuestion {
  question_id: string
  max_score: number
  class_rate: number
  stem_summary: string | null
  canonical_answer: string | null
  records: ClassAnalysisRecord[]
  causes?: ClassCause[]
  causes_grouped?: boolean
  causes_legacy?: boolean
  /** v2 旧版结果仍在展示：无错误大类，重新整理后自动升级。 */
  causes_outdated?: boolean
  /** false=按步骤拆分前的兼容结果；手动「整理错因」后按扣分步骤逐条整理。 */
  causes_by_step?: boolean
  /** 已关联题库的题目 id；未关联为 null/缺省。 */
  bank_question_id?: number | null
  /** 该题各错误大类涉及的去重学生数，按人数降序。 */
  cause_category_counts?: { category: CauseCategory; count: number }[]
  cause_review?: { positive: ClassCauseEvidence[]; uncertain: ClassCauseEvidence[] }
}

export interface ClassQuestionPreview {
  question_id: string
  parent_question_id: string
  text: string
  rich_content: QuestionBankRichContent
  notice: string
}

export interface ClassAnalysisLostRecord {
  deduction_reason: string | null
  error_category: string | null
  error_summary: string | null
}

export interface ClassAnalysisLostItem {
  question_id: string
  lost_points: number
  record: ClassAnalysisLostRecord | null
}

export interface ClassAnalysisStudent {
  student_name: string
  student_code: string | null
  total_score: number
  rank: number | null
  needs_review: boolean
  lost: ClassAnalysisLostItem[]
}

export interface ClassAnalysisSkipped {
  class_name: string
  student_code: string
  student_name: string
  reason: string
}

export interface ClassAnalysisData {
  exam: ClassAnalysisExam
  present: number
  roster_absent: string[]
  score_distribution: ClassScoreDistribution
  questions: ClassAnalysisQuestion[]
  students: ClassAnalysisStudent[]
  skipped: ClassAnalysisSkipped[]
}

export interface ClassNarrativeKeyFinding {
  title: string
  detail: string
  severity: string
}

export interface ClassNarrativeCommonIssue {
  title: string
  evidence: string
  teaching_action: string
}

export interface ClassNarrativeStudentNote {
  alias: string
  note: string
  suggestion: string
  flags: string[]
}

export interface ClassNarrative {
  key_findings: ClassNarrativeKeyFinding[]
  common_issues: ClassNarrativeCommonIssue[]
  student_notes: ClassNarrativeStudentNote[]
  grouping_advice: string
}

export interface ClassAnalysisResponse {
  status: ClassAnalysisStatus
  auto_generate: boolean
  small_sample: boolean
  data: ClassAnalysisData | null
  narrative: ClassNarrative | null
  narrative_failed: boolean
  generated_at: string | null
  stale: boolean
  active_job_id: number | null
  class_names?: string[]
  selected_class?: string | null
  cause_analysis?: ClassCauseAnalysis | null
}

export interface ClassAnalysisSettings {
  auto_generate: boolean
}



function decodeRecord(value: unknown): ClassAnalysisRecord {
  if (
    !isRecord(value)
    || typeof value.student_name !== 'string'
    || !(isFiniteNumber(value.score) || value.score === null)
    || !(value.deduction_reason === undefined || isNullableString(value.deduction_reason))
    || !(value.error_category === undefined || isNullableString(value.error_category))
    || !(value.error_summary === undefined || isNullableString(value.error_summary))
    || !(value.student_id === undefined || isFiniteNumber(value.student_id))
    || !(value.student_code === undefined || typeof value.student_code === 'string')
    || !(value.class_name === undefined || typeof value.class_name === 'string')
  ) {
    throw new Error('Invalid class analysis record')
  }
  return {
    student_name: value.student_name,
    ...(typeof value.student_id === 'number' ? { student_id: value.student_id } : {}),
    ...(typeof value.student_code === 'string' ? { student_code: value.student_code } : {}),
    ...(typeof value.class_name === 'string' ? { class_name: value.class_name } : {}),
    score: value.score,
    deduction_reason: value.deduction_reason ?? null,
    error_category: value.error_category ?? null,
    error_summary: value.error_summary ?? null,
  }
}

function decodeLostRecord(value: unknown): ClassAnalysisLostRecord {
  if (
    !isRecord(value)
    || !isNullableString(value.deduction_reason)
    || !isNullableString(value.error_category)
    || !isNullableString(value.error_summary)
  ) {
    throw new Error('Invalid class analysis lost record')
  }
  return {
    deduction_reason: value.deduction_reason,
    error_category: value.error_category,
    error_summary: value.error_summary,
  }
}

function decodeCauseEvidence(value: unknown): ClassCauseEvidence[] {
  const strings = (items: unknown) => Array.isArray(items) && items.every((item) => typeof item === 'string')
  if (!Array.isArray(value) || !value.every((item) => (
    isRecord(item) && typeof item.text === 'string'
    && Array.isArray(item.student_ids) && item.student_ids.every(isNonnegativeInteger)
    && (item.student_answer === undefined || typeof item.student_answer === 'string')
    && (item.evidence_steps === undefined || strings(item.evidence_steps))
    && (item.missing_steps === undefined || strings(item.missing_steps))
    && (item.failed_steps === undefined || (Array.isArray(item.failed_steps) && item.failed_steps.every(isRecord)))
    && (item.previous_answers === undefined || (Array.isArray(item.previous_answers) && item.previous_answers.every((previous) => (
      isRecord(previous) && typeof previous.question_id === 'string' && typeof previous.student_answer === 'string'
      && typeof previous.text === 'string' && strings(previous.evidence_steps)
    ))))
  ))) throw new Error('Invalid class cause evidence')
  return value as ClassCauseEvidence[]
}

function decodeCause(value: unknown): ClassCause {
  if (!isRecord(value) || typeof value.reason !== 'string' || !isNonnegativeInteger(value.count)
    || !(value.kind === undefined || CLASS_CAUSE_KINDS.some((kind) => kind === value.kind))
    || !(value.category === undefined || CAUSE_CATEGORIES.some((category) => category === value.category))
    || !(value.step_id === undefined || isNullableString(value.step_id))
    || !(value.step_ids === undefined || (Array.isArray(value.step_ids) && value.step_ids.every((item) => typeof item === 'string')))
    || !(value.pattern_status === undefined || value.pattern_status === 'existing' || value.pattern_status === 'candidate')
    || !(value.teacher_edited === undefined || typeof value.teacher_edited === 'boolean')) {
    throw new Error('Invalid class cause')
  }
  let manifestations: ClassCause['manifestations']
  if (value.manifestations !== undefined) {
    if (!Array.isArray(value.manifestations)) throw new Error('Invalid class cause manifestations')
    manifestations = value.manifestations.map((item) => {
      if (!isRecord(item) || typeof item.description !== 'string' || !isNullableString(item.source_question_id)) {
        throw new Error('Invalid class cause manifestation')
      }
      return { description: item.description, source_question_id: item.source_question_id, evidence: decodeCauseEvidence(item.evidence) }
    })
  }
  return { reason: value.reason, count: value.count,
    ...(value.kind === undefined ? {} : { kind: value.kind as ClassCauseKind }),
    ...(value.category === undefined ? {} : { category: value.category as CauseCategory }),
    ...(value.step_id === undefined ? {} : { step_id: value.step_id }),
    ...(value.step_ids === undefined ? {} : { step_ids: value.step_ids as string[] }),
    ...(value.pattern_status === undefined ? {} : { pattern_status: value.pattern_status as ClassCause['pattern_status'] }),
    ...(value.teacher_edited === undefined ? {} : { teacher_edited: value.teacher_edited }),
    ...(manifestations === undefined ? {} : { manifestations }),
    ...(value.evidence === undefined ? {} : { evidence: decodeCauseEvidence(value.evidence) }) }
}

function decodeCauseAnalysis(value: unknown): ClassCauseAnalysis | null {
  if (value == null) return null
  if (!isRecord(value) || !['ready', 'partial', 'not_generated'].includes(String(value.status))
    || !isNonnegativeInteger(value.pending_questions) || !isNonnegativeInteger(value.total_questions)
    || !isNonnegativeInteger(value.failed_questions) || typeof value.stale !== 'boolean'
    || !(value.legacy_questions === undefined || isNonnegativeInteger(value.legacy_questions))
    || !(value.outdated_questions === undefined || isNonnegativeInteger(value.outdated_questions))
    || !(value.pre_step_questions === undefined || isNonnegativeInteger(value.pre_step_questions))
    || !isNullableString(value.generated_at) || !isNullableString(value.origin)) {
    throw new Error('Invalid class cause analysis')
  }
  return value as unknown as ClassCauseAnalysis
}

function decodeQuestion(value: unknown): ClassAnalysisQuestion {
  if (
    !isRecord(value)
    || typeof value.question_id !== 'string'
    || !value.question_id.trim()
    || !isFiniteNumber(value.max_score)
    || !isFiniteNumber(value.class_rate)
    || !isNullableString(value.stem_summary)
    || !isNullableString(value.canonical_answer)
    || !Array.isArray(value.records)
    || !(value.causes_grouped === undefined || typeof value.causes_grouped === 'boolean')
    || !(value.causes_legacy === undefined || typeof value.causes_legacy === 'boolean')
    || !(value.causes_outdated === undefined || typeof value.causes_outdated === 'boolean')
    || !(value.causes_by_step === undefined || typeof value.causes_by_step === 'boolean')
    || !(value.cause_review === undefined || isRecord(value.cause_review))
    || !(value.bank_question_id === undefined || value.bank_question_id === null || isNonnegativeInteger(value.bank_question_id))
    || !(value.cause_category_counts === undefined || (Array.isArray(value.cause_category_counts)
      && value.cause_category_counts.every((item) => (
        isRecord(item) && typeof item.category === 'string' && isNonnegativeInteger(item.count)
      ))))
    || !(value.causes === undefined || (Array.isArray(value.causes) && value.causes.every((cause) => (
      isRecord(cause) && typeof cause.reason === 'string' && isNonnegativeInteger(cause.count)
    ))))
  ) {
    throw new Error('Invalid class analysis question')
  }
  return {
    question_id: value.question_id,
    max_score: value.max_score,
    class_rate: value.class_rate,
    stem_summary: value.stem_summary,
    canonical_answer: value.canonical_answer,
    records: value.records.map(decodeRecord),
    ...(Array.isArray(value.causes) ? { causes: value.causes.map(decodeCause) } : {}),
    ...(typeof value.causes_grouped === 'boolean' ? { causes_grouped: value.causes_grouped } : {}),
    ...(typeof value.causes_legacy === 'boolean' ? { causes_legacy: value.causes_legacy } : {}),
    ...(typeof value.causes_outdated === 'boolean' ? { causes_outdated: value.causes_outdated } : {}),
    ...(typeof value.causes_by_step === 'boolean' ? { causes_by_step: value.causes_by_step } : {}),
    ...(value.bank_question_id === undefined ? {} : { bank_question_id: value.bank_question_id }),
    cause_category_counts: Array.isArray(value.cause_category_counts)
      ? (value.cause_category_counts as ClassAnalysisQuestion['cause_category_counts'])
      : [],
    ...(isRecord(value.cause_review) ? { cause_review: {
      positive: decodeCauseEvidence(value.cause_review.positive),
      uncertain: decodeCauseEvidence(value.cause_review.uncertain),
    } } : {}),
  }
}

function decodeLostItem(value: unknown): ClassAnalysisLostItem {
  if (
    !isRecord(value)
    || typeof value.question_id !== 'string'
    || !value.question_id.trim()
    || !isFiniteNumber(value.lost_points)
    || !(value.record === null || isRecord(value.record))
  ) {
    throw new Error('Invalid class analysis lost item')
  }
  return {
    question_id: value.question_id,
    lost_points: value.lost_points,
    record: value.record === null ? null : decodeLostRecord(value.record),
  }
}

function decodeStudent(value: unknown): ClassAnalysisStudent {
  if (
    !isRecord(value)
    || typeof value.student_name !== 'string'
    || !isNullableString(value.student_code)
    || !isFiniteNumber(value.total_score)
    || !(isNonnegativeInteger(value.rank) || value.rank === null)
    || typeof value.needs_review !== 'boolean'
    || !Array.isArray(value.lost)
  ) {
    throw new Error('Invalid class analysis student')
  }
  return {
    student_name: value.student_name,
    student_code: value.student_code,
    total_score: value.total_score,
    rank: value.rank,
    needs_review: value.needs_review,
    lost: value.lost.map(decodeLostItem),
  }
}

function decodeScoreDistribution(value: unknown): ClassScoreDistribution {
  if (
    !isRecord(value)
    || !isFiniteNumber(value.avg)
    || !isFiniteNumber(value.median)
    || !isFiniteNumber(value.max)
    || !isFiniteNumber(value.min)
    || !isFiniteNumber(value.pass_rate)
    || !isRecord(value.bands)
    || !Object.values(value.bands).every(isNonnegativeInteger)
  ) {
    throw new Error('Invalid class score distribution')
  }
  return {
    avg: value.avg,
    median: value.median,
    max: value.max,
    min: value.min,
    pass_rate: value.pass_rate,
    bands: { ...value.bands } as Record<string, number>,
  }
}

function decodeData(value: unknown): ClassAnalysisData {
  if (
    !isRecord(value)
    || !isRecord(value.exam)
    || typeof value.exam.title !== 'string'
    || !isNullableString(value.exam.subject)
    || !isFiniteNumber(value.exam.full_score)
    || !isNullableString(value.exam.graded_at)
    || !isNonnegativeInteger(value.present)
    || !Array.isArray(value.roster_absent)
    || !value.roster_absent.every((name) => typeof name === 'string')
    || !Array.isArray(value.questions)
    || !Array.isArray(value.students)
  ) {
    throw new Error('Invalid class analysis data')
  }
  return {
    exam: {
      title: value.exam.title,
      subject: value.exam.subject,
      full_score: value.exam.full_score,
      graded_at: value.exam.graded_at,
    },
    present: Number(value.present),
    roster_absent: [...value.roster_absent] as string[],
    score_distribution: decodeScoreDistribution(value.score_distribution),
    questions: value.questions.map(decodeQuestion),
    students: value.students.map(decodeStudent),
    skipped: Array.isArray(value.skipped)
      ? value.skipped.map((item) => {
        if (!isRecord(item)
          || typeof item.class_name !== 'string'
          || typeof item.student_code !== 'string'
          || typeof item.student_name !== 'string'
          || typeof item.reason !== 'string') {
          throw new Error('Invalid class analysis skipped entry')
        }
        return {
          class_name: item.class_name,
          student_code: item.student_code,
          student_name: item.student_name,
          reason: item.reason,
        }
      })
      : [],
  }
}

function decodeNarrative(value: unknown): ClassNarrative {
  if (
    !isRecord(value)
    || !Array.isArray(value.key_findings)
    || !value.key_findings.every((finding) => (
      isRecord(finding)
      && typeof finding.title === 'string'
      && typeof finding.detail === 'string'
      && typeof finding.severity === 'string'
    ))
    || !Array.isArray(value.common_issues)
    || !value.common_issues.every((issue) => (
      isRecord(issue)
      && typeof issue.title === 'string'
      && typeof issue.evidence === 'string'
      && typeof issue.teaching_action === 'string'
    ))
    || !Array.isArray(value.student_notes)
    || !value.student_notes.every((note) => (
      isRecord(note)
      && typeof note.alias === 'string'
      && typeof note.note === 'string'
      && typeof note.suggestion === 'string'
      && Array.isArray(note.flags)
      && note.flags.every((flag) => typeof flag === 'string')
    ))
    || typeof value.grouping_advice !== 'string'
  ) {
    throw new Error('Invalid class narrative')
  }
  return value as unknown as ClassNarrative
}

// 后端 job id 是整数（与 JobResponse.id 一致）；兼容数字字符串，非法值返回 undefined。
function decodeActiveJobId(value: unknown): number | null | undefined {
  if (value === null) return null
  if (typeof value === 'number' && Number.isSafeInteger(value) && value > 0) return value
  if (typeof value === 'string' && /^\d+$/.test(value)) {
    const parsed = Number(value)
    if (Number.isSafeInteger(parsed) && parsed > 0) return parsed
  }
  return undefined
}

export function decodeClassAnalysisResponse(value: unknown): ClassAnalysisResponse {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || !CLASS_ANALYSIS_STATUSES.some((status) => status === value.status)
    || typeof value.auto_generate !== 'boolean'
    || typeof value.small_sample !== 'boolean'
    || !(value.data === null || isRecord(value.data))
    || !(value.narrative === null || isRecord(value.narrative))
    || typeof value.narrative_failed !== 'boolean'
    || !isNullableString(value.generated_at)
    || typeof value.stale !== 'boolean'
    || decodeActiveJobId(value.active_job_id) === undefined
    || !(value.class_names === undefined || (Array.isArray(value.class_names) && value.class_names.every((item) => typeof item === 'string')))
    || !(value.selected_class === undefined || isNullableString(value.selected_class))
  ) {
    throw new Error('Invalid class analysis response')
  }
  return {
    status: value.status as ClassAnalysisStatus,
    auto_generate: value.auto_generate,
    small_sample: value.small_sample,
    data: value.data === null ? null : decodeData(value.data),
    narrative: value.narrative === null ? null : decodeNarrative(value.narrative),
    narrative_failed: value.narrative_failed,
    generated_at: value.generated_at,
    stale: value.stale,
    active_job_id: decodeActiveJobId(value.active_job_id) ?? null,
    ...(value.cause_analysis === undefined ? {} : { cause_analysis: decodeCauseAnalysis(value.cause_analysis) }),
    ...(Array.isArray(value.class_names) ? { class_names: value.class_names as string[] } : {}),
    ...(value.selected_class !== undefined ? { selected_class: value.selected_class as string | null } : {}),
  }
}

export function decodeClassAnalysisSettings(value: unknown): ClassAnalysisSettings {
  assertNoPathLikeKeys(value)
  if (!isRecord(value) || typeof value.auto_generate !== 'boolean') {
    throw new Error('Invalid class analysis settings')
  }
  return { auto_generate: value.auto_generate }
}

function requireSessionId(sessionId: number): number {
  if (!Number.isSafeInteger(sessionId) || sessionId <= 0) {
    throw new Error('Invalid session id')
  }
  return sessionId
}

export const classAnalysisApi = {
  async getClassAnalysis(
    sessionId: number,
    signal?: AbortSignal,
    className?: string,
    view?: 'full' | 'summary' | 'narrative',
  ): Promise<ClassAnalysisResponse> {
    const id = requireSessionId(sessionId)
    const query = new URLSearchParams()
    if (className !== undefined) query.set('class_name', className)
    if (view !== undefined) query.set('view', view)
    return apiClient.request(`/api/sessions/${id}/class-analysis${query.size ? `?${query}` : ''}`, {
      decode: decodeClassAnalysisResponse,
      signal,
    })
  },

  async getQuestionPreview(sessionId: number, questionId: string, signal?: AbortSignal): Promise<ClassQuestionPreview> {
    const id = requireSessionId(sessionId)
    return apiClient.request(`/api/sessions/${id}/class-analysis/questions/${encodeURIComponent(questionId)}/preview`, {
      signal,
      decode(value) {
        assertNoPathLikeKeys(value)
        if (!isRecord(value) || typeof value.question_id !== 'string'
          || typeof value.parent_question_id !== 'string' || typeof value.text !== 'string'
          || typeof value.notice !== 'string' || !isConfigRichContent(value.rich_content)) {
          throw new Error('Invalid class question preview')
        }
        return value as unknown as ClassQuestionPreview
      },
    })
  },

  async updateSettings(
    sessionId: number,
    autoGenerate: boolean,
    signal?: AbortSignal,
  ): Promise<ClassAnalysisSettings> {
    const id = requireSessionId(sessionId)
    return apiClient.request(`/api/sessions/${id}/class-analysis/settings`, {
      method: 'PUT',
      body: { auto_generate: autoGenerate },
      decode: decodeClassAnalysisSettings,
      signal,
    })
  },

  /** 教师可选修改一条错法的名称/大类；修改不是必经步骤。 */
  async editCausePattern(
    sessionId: number,
    payload: {
      question_id: string
      kind: string
      reason: string
      new_reason: string
      category?: string | null
      operation_token?: string
    },
    signal?: AbortSignal,
  ): Promise<{ ok: boolean }> {
    const id = requireSessionId(sessionId)
    return apiClient.request(`/api/sessions/${id}/class-analysis/causes/edit`, {
      method: 'POST',
      body: payload,
      signal,
      decode(value) {
        assertNoPathLikeKeys(value)
        if (!isRecord(value) || value.ok !== true) {
          throw new Error('Invalid cause pattern edit response')
        }
        return { ok: true }
      },
    })
  },
}
