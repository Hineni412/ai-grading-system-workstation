import { apiClient } from './client'
import { decodeJobResponse, type JobResponse } from './jobs'
import { assertNoPathLikeKeys, isNullableString, isRecord } from './validation'

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
  score: number | null
  deduction_reason: string | null
  error_category: string | null
  error_summary: string | null
}

export interface ClassAnalysisQuestion {
  question_id: string
  max_score: number
  class_rate: number
  stem_summary: string | null
  canonical_answer: string | null
  records: ClassAnalysisRecord[]
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

export interface ClassAnalysisData {
  exam: ClassAnalysisExam
  present: number
  roster_absent: string[]
  score_distribution: ClassScoreDistribution
  questions: ClassAnalysisQuestion[]
  students: ClassAnalysisStudent[]
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
}

export interface ClassAnalysisSettings {
  auto_generate: boolean
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isNonNegativeCount(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0
}

function decodeRecord(value: unknown): ClassAnalysisRecord {
  if (
    !isRecord(value)
    || typeof value.student_name !== 'string'
    || !(isFiniteNumber(value.score) || value.score === null)
    || !isNullableString(value.deduction_reason)
    || !isNullableString(value.error_category)
    || !isNullableString(value.error_summary)
  ) {
    throw new Error('Invalid class analysis record')
  }
  return {
    student_name: value.student_name,
    score: value.score,
    deduction_reason: value.deduction_reason,
    error_category: value.error_category,
    error_summary: value.error_summary,
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
    || !(isNonNegativeCount(value.rank) || value.rank === null)
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
    || !Object.values(value.bands).every(isNonNegativeCount)
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
    || !isNonNegativeCount(value.present)
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
  ): Promise<ClassAnalysisResponse> {
    const id = requireSessionId(sessionId)
    return apiClient.request(`/api/sessions/${id}/class-analysis`, {
      decode: decodeClassAnalysisResponse,
      signal,
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

  async regenerate(
    sessionId: number,
    signal?: AbortSignal,
  ): Promise<JobResponse> {
    const id = requireSessionId(sessionId)
    return apiClient.request(`/api/sessions/${id}/class-analysis/regenerate`, {
      method: 'POST',
      decode: decodeJobResponse,
      signal,
    })
  },
}
