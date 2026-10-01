import { apiClient } from './client'
import { decodeJobResponse, type JobResponse } from './jobs'
import { isNullableString, isRecord } from './validation'

export interface WrongQuestionMissingItem {
  student_id: number
  student_name: string
  session_id: number
  session_name: string
  question_id: string
}

export interface WrongQuestionBookPreview {
  students: StudentSummary[]
  sessions: Array<{ session_id: number; session_name: string; exam_created_at: string | null }>
  session_ids: number[]
  semester_label: string
  question_count: number
  missing_items: WrongQuestionMissingItem[]
}

function decodeWrongQuestionPreview(value: unknown): WrongQuestionBookPreview {
  if (!isRecord(value) || !Array.isArray(value.students) || !value.students.every(isStudentSummary)
    || !Array.isArray(value.sessions) || !value.sessions.every(item => isRecord(item)
      && isInteger(item.session_id, 1) && typeof item.session_name === 'string' && isNullableString(item.exam_created_at))
    || !Array.isArray(value.session_ids) || !value.session_ids.every(id => isInteger(id, 1))
    || typeof value.semester_label !== 'string' || !isInteger(value.question_count, 0)
    || !Array.isArray(value.missing_items) || !value.missing_items.every(item => isRecord(item)
      && isInteger(item.student_id, 1) && typeof item.student_name === 'string'
      && isInteger(item.session_id, 1) && typeof item.session_name === 'string' && typeof item.question_id === 'string')) {
    throw new Error('Invalid wrong question book preview')
  }
  return value as unknown as WrongQuestionBookPreview
}

export function previewWrongQuestionBook(studentId: number, body: {
  curriculum_volume_id: string; include_class: boolean; session_ids?: number[]
}, signal?: AbortSignal): Promise<WrongQuestionBookPreview> {
  return apiClient.request(`/api/students/${studentId}/wrong-question-book/preview`, {
    method: 'POST', body, decode: decodeWrongQuestionPreview, signal,
  })
}

export function submitWrongQuestionBooks(body: {
  curriculum_volume_id: string; student_ids: number[]; session_ids: number[]; client_request_token: string
}): Promise<JobResponse> {
  return apiClient.request('/api/students/wrong-question-books', { method: 'POST', body, decode: decodeJobResponse })
}

export function findWrongQuestionBookRequest(token: string): Promise<JobResponse> {
  return apiClient.request(`/api/students/wrong-question-books/by-request/${token}`, { decode: decodeJobResponse })
}

export interface StudentSummary {
  id: number
  student_code: string
  name: string
  class_name: string | null
  created_at: string | null
}

interface StudentListResponse {
  items: StudentSummary[]
  total: number
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value)
  return actual.length === keys.length && keys.every(
    (key) => Object.prototype.hasOwnProperty.call(value, key),
  )
}

function isStudentSummary(value: unknown): value is StudentSummary {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['id', 'student_code', 'name', 'class_name', 'created_at']) &&
    Number.isSafeInteger(value.id) &&
    Number(value.id) > 0 &&
    typeof value.student_code === 'string' &&
    typeof value.name === 'string' &&
    isNullableString(value.class_name) &&
    isNullableString(value.created_at)
  )
}

export function decodeStudentList(value: unknown): StudentSummary[] {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['items', 'total']) ||
    !Array.isArray(value.items) ||
    !value.items.every(isStudentSummary) ||
    !Number.isSafeInteger(value.total) ||
    Number(value.total) < 0 ||
    value.total !== value.items.length
  ) {
    throw new Error('Invalid student list')
  }
  return (value as unknown as StudentListResponse).items
}

export class StudentReadError extends Error {
  constructor() {
    super('无法读取学生列表')
    this.name = 'StudentReadError'
  }
}

export async function fetchStudents(signal?: AbortSignal): Promise<StudentSummary[]> {
  try {
    return await apiClient.request('/api/students', {
      decode: decodeStudentList,
      signal,
    })
  } catch {
    throw new StudentReadError()
  }
}

export interface StudentExamResultItem {
  detail_id: number
  question_id: string
  bank_question_id: number | null
  score_awarded: number
  max_score: number | null
  deduction_amount: number | null
  deduction_reason: string | null
  error_category: string | null
  error_summary: string | null
  evidence_url: string | null
}

export interface StudentExamResultSession {
  session_id: number
  session_name: string
  graded_at: string | null
  exam_created_at: string | null
  result_id: number
  student_score: number
  total_score: number
  items: StudentExamResultItem[]
}

export interface StudentExamResultsResponse {
  student: StudentSummary
  sessions: StudentExamResultSession[]
  total_sessions: number
  page: number
  page_size: number
  total_pages: number
}

export interface StudentExamResultsQuery {
  onlyDeducted?: boolean
  page?: number
  pageSize?: number
  curriculumVolumeId?: string
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || isFiniteNumber(value)
}

function isStudentExamResultItem(value: unknown): value is StudentExamResultItem {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'detail_id', 'question_id', 'bank_question_id', 'score_awarded', 'max_score',
      'deduction_amount', 'deduction_reason', 'error_category', 'error_summary', 'evidence_url',
    ]) &&
    isInteger(value.detail_id, 1) &&
    typeof value.question_id === 'string' &&
    value.question_id.length > 0 &&
    (value.bank_question_id === null || isInteger(value.bank_question_id, 1)) &&
    isFiniteNumber(value.score_awarded) &&
    isNullableNumber(value.max_score) &&
    (value.max_score === null || value.max_score >= 0) &&
    isNullableNumber(value.deduction_amount) &&
    (value.deduction_amount === null || value.deduction_amount >= 0) &&
    isNullableString(value.deduction_reason) &&
    isNullableString(value.error_category) &&
    isNullableString(value.error_summary) &&
    (value.evidence_url === null || (typeof value.evidence_url === 'string' && value.evidence_url.startsWith('/api/')))
  )
}

function isStudentExamResultSession(value: unknown): value is StudentExamResultSession {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'session_id', 'session_name', 'graded_at', 'exam_created_at', 'result_id',
      'student_score', 'total_score', 'items',
    ]) &&
    isInteger(value.session_id, 1) &&
    typeof value.session_name === 'string' &&
    isNullableString(value.graded_at) &&
    isNullableString(value.exam_created_at) &&
    isInteger(value.result_id, 1) &&
    isFiniteNumber(value.student_score) &&
    isFiniteNumber(value.total_score) &&
    value.total_score >= 0 &&
    Array.isArray(value.items) &&
    value.items.every(isStudentExamResultItem)
  )
}

export function decodeStudentExamResults(value: unknown): StudentExamResultsResponse {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['student', 'sessions', 'total_sessions', 'page', 'page_size', 'total_pages']) ||
    !isStudentSummary(value.student) ||
    !Array.isArray(value.sessions) ||
    !value.sessions.every(isStudentExamResultSession) ||
    !isInteger(value.total_sessions) ||
    !isInteger(value.page, 1) ||
    !isInteger(value.page_size, 1) ||
    !isInteger(value.total_pages) ||
    value.total_pages !== Math.ceil(value.total_sessions / value.page_size) ||
    value.sessions.length > value.page_size
  ) {
    throw new Error('Invalid student exam results')
  }
  return value as unknown as StudentExamResultsResponse
}

export function fetchStudentExamResults(
  studentId: string | number,
  query: StudentExamResultsQuery = {},
  signal?: AbortSignal,
): Promise<StudentExamResultsResponse> {
  const id = Number(studentId)
  if (!isInteger(id, 1)) throw new Error('Invalid student id')
  const page = isInteger(query.page ?? 1, 1) ? Number(query.page ?? 1) : 1
  const pageSize = isInteger(query.pageSize ?? 10, 1) ? Number(query.pageSize ?? 10) : 10
  const parameters = new URLSearchParams()
  parameters.set('only_deducted', String(query.onlyDeducted !== false))
  parameters.set('page', String(page))
  parameters.set('page_size', String(pageSize))
  if (query.curriculumVolumeId !== undefined) parameters.set('curriculum_volume_id', query.curriculumVolumeId)
  return apiClient.request(`/api/students/${id}/exam-results?${parameters.toString()}`, {
    decode: (value) => {
      const response = decodeStudentExamResults(value)
      if (response.student.id !== id || response.page !== page) {
        throw new Error('Invalid student exam results')
      }
      return response
    },
    signal,
  })
}

export interface StudentWorkspace {
  items: StudentSummary[]
  total: number
  page: number
  page_size: number
  total_pages: number
  class_names: string[]
  roster_revision: string
}

export interface StudentUpsertInput {
  student_code: string
  name: string
  class_name: string | null
}

export interface StudentImportMapping {
  student_code: string | null
  name: string | null
  class_name: string | null
}

export type StudentImportOperation =
  | 'insert'
  | 'update'
  | 'unchanged'
  | 'invalid'
  | 'duplicate'

export interface StudentImportCounts {
  insert: number
  update: number
  unchanged: number
  invalid: number
  duplicate: number
}

export interface StudentImportRow extends StudentUpsertInput {
  source_row: number
  operation: StudentImportOperation
  selectable: boolean
  issues: string[]
  existing: StudentSummary | null
}

export interface StudentImportPreview {
  filename: string
  columns: string[]
  mapping: StudentImportMapping
  counts: StudentImportCounts
  rows: StudentImportRow[]
  roster_revision: string
  issues: string[]
}

export interface StudentImportResult {
  inserted: number
  updated: number
  unchanged: number
  total: number
  roster_revision: string
}

export interface StudentMutationResult {
  student: StudentSummary
  roster_revision: string
}

export interface StudentDeletionCounts {
  deleted_students: number
  deleted_results: number
  deleted_details: number
  deleted_annotations: number
  deleted_attendance: number
  unlinked_papers: number
}

export interface StudentDeletionImpact {
  student: StudentSummary
  counts: StudentDeletionCounts
  roster_revision: string
}

export interface StudentDeleteResult extends StudentDeletionCounts {
  backup_created: boolean
  roster_revision: string
}

export interface StudentWorkspaceQuery {
  search?: string
  class_name?: string
  page?: number
  page_size?: number
}

function isInteger(value: unknown, minimum = 0): value is number {
  return Number.isSafeInteger(value) && Number(value) >= minimum
}

function isRevision(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function isStudentImportMapping(value: unknown): value is StudentImportMapping {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['student_code', 'name', 'class_name']) &&
    isNullableString(value.student_code) &&
    isNullableString(value.name) &&
    isNullableString(value.class_name)
  )
}

function isStudentImportCounts(value: unknown): value is StudentImportCounts {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['insert', 'update', 'unchanged', 'invalid', 'duplicate']) &&
    Object.values(value).every((count) => isInteger(count))
  )
}

function isStudentImportRow(value: unknown): value is StudentImportRow {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'source_row',
      'student_code',
      'name',
      'class_name',
      'operation',
      'selectable',
      'issues',
      'existing',
    ]) &&
    isInteger(value.source_row, 1) &&
    typeof value.student_code === 'string' &&
    typeof value.name === 'string' &&
    isNullableString(value.class_name) &&
    ['insert', 'update', 'unchanged', 'invalid', 'duplicate'].includes(
      String(value.operation),
    ) &&
    typeof value.selectable === 'boolean' &&
    isStringArray(value.issues) &&
    (value.existing === null || isStudentSummary(value.existing))
  )
}

function isDeletionCounts(value: unknown): value is StudentDeletionCounts {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'deleted_students',
      'deleted_results',
      'deleted_details',
      'deleted_annotations',
      'deleted_attendance',
      'unlinked_papers',
    ]) &&
    Object.values(value).every((count) => isInteger(count))
  )
}

export function decodeStudentWorkspace(value: unknown): StudentWorkspace {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      'items',
      'total',
      'page',
      'page_size',
      'total_pages',
      'class_names',
      'roster_revision',
    ]) ||
    !Array.isArray(value.items) ||
    !value.items.every(isStudentSummary) ||
    !isInteger(value.total) ||
    !isInteger(value.page, 1) ||
    !isInteger(value.page_size, 1) ||
    !isInteger(value.total_pages) ||
    !isStringArray(value.class_names) ||
    !isRevision(value.roster_revision)
  ) {
    throw new Error('Invalid student workspace')
  }
  return value as unknown as StudentWorkspace
}

export function decodeStudentImportPreview(value: unknown): StudentImportPreview {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      'filename',
      'columns',
      'mapping',
      'counts',
      'rows',
      'roster_revision',
      'issues',
    ]) ||
    typeof value.filename !== 'string' ||
    !isStringArray(value.columns) ||
    !isStudentImportMapping(value.mapping) ||
    !isStudentImportCounts(value.counts) ||
    !Array.isArray(value.rows) ||
    !value.rows.every(isStudentImportRow) ||
    !isRevision(value.roster_revision) ||
    !isStringArray(value.issues)
  ) {
    throw new Error('Invalid student import preview')
  }
  return value as unknown as StudentImportPreview
}

function decodeImportResult(value: unknown): StudentImportResult {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      'inserted',
      'updated',
      'unchanged',
      'total',
      'roster_revision',
    ]) ||
    !isInteger(value.inserted) ||
    !isInteger(value.updated) ||
    !isInteger(value.unchanged) ||
    !isInteger(value.total) ||
    !isRevision(value.roster_revision)
  ) {
    throw new Error('Invalid student import result')
  }
  return value as unknown as StudentImportResult
}

function decodeMutationResult(value: unknown): StudentMutationResult {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['student', 'roster_revision']) ||
    !isStudentSummary(value.student) ||
    !isRevision(value.roster_revision)
  ) {
    throw new Error('Invalid student mutation result')
  }
  return value as unknown as StudentMutationResult
}

function decodeDeletionImpact(value: unknown): StudentDeletionImpact {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['student', 'counts', 'roster_revision']) ||
    !isStudentSummary(value.student) ||
    !isDeletionCounts(value.counts) ||
    !isRevision(value.roster_revision)
  ) {
    throw new Error('Invalid student deletion impact')
  }
  return value as unknown as StudentDeletionImpact
}

function decodeDeleteResult(value: unknown): StudentDeleteResult {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      'deleted_students',
      'deleted_results',
      'deleted_details',
      'deleted_annotations',
      'deleted_attendance',
      'unlinked_papers',
      'backup_created',
      'roster_revision',
    ]) ||
    typeof value.backup_created !== 'boolean' ||
    !isRevision(value.roster_revision) ||
    !isDeletionCounts({
      deleted_students: value.deleted_students,
      deleted_results: value.deleted_results,
      deleted_details: value.deleted_details,
      deleted_annotations: value.deleted_annotations,
      deleted_attendance: value.deleted_attendance,
      unlinked_papers: value.unlinked_papers,
    })
  ) {
    throw new Error('Invalid student delete result')
  }
  return value as unknown as StudentDeleteResult
}

function workspacePath(query: StudentWorkspaceQuery): string {
  const parameters = new URLSearchParams()
  if (query.search?.trim()) parameters.set('search', query.search.trim())
  if (query.class_name?.trim()) parameters.set('class_name', query.class_name.trim())
  parameters.set('page', String(query.page ?? 1))
  parameters.set('page_size', String(query.page_size ?? 100))
  return `/api/students/workspace?${parameters.toString()}`
}

export const studentRosterApi = {
  getWorkspace(
    query: StudentWorkspaceQuery = {},
    signal?: AbortSignal,
  ): Promise<StudentWorkspace> {
    return apiClient.request(workspacePath(query), {
      decode: decodeStudentWorkspace,
      signal,
    })
  },

  previewImport(
    file: File,
    mapping: StudentImportMapping,
    signal?: AbortSignal,
  ): Promise<StudentImportPreview> {
    const parameters = new URLSearchParams({ filename: file.name })
    if (mapping.student_code) {
      parameters.set('student_code_column', mapping.student_code)
    }
    if (mapping.name) parameters.set('name_column', mapping.name)
    if (mapping.class_name) {
      parameters.set('class_name_column', mapping.class_name)
    }
    return apiClient.request(
      `/api/students/import/preview?${parameters.toString()}`,
      {
        method: 'POST',
        rawBody: file,
        headers: file.type ? { 'content-type': file.type } : undefined,
        decode: decodeStudentImportPreview,
        signal,
        timeoutMs: 30_000,
      },
    )
  },

  commitImport(
    expectedRevision: string,
    items: StudentUpsertInput[],
    signal?: AbortSignal,
  ): Promise<StudentImportResult> {
    return apiClient.request('/api/students/import/commit', {
      method: 'POST',
      body: { expected_revision: expectedRevision, items },
      decode: decodeImportResult,
      signal,
      timeoutMs: 30_000,
    })
  },

  updateStudent(
    studentId: number,
    expectedRevision: string,
    values: StudentUpsertInput,
    signal?: AbortSignal,
  ): Promise<StudentMutationResult> {
    return apiClient.request(`/api/students/${studentId}`, {
      method: 'PATCH',
      body: { expected_revision: expectedRevision, ...values },
      decode: decodeMutationResult,
      signal,
    })
  },

  getDeletionImpact(
    studentId: number,
    signal?: AbortSignal,
  ): Promise<StudentDeletionImpact> {
    return apiClient.request(`/api/students/${studentId}/deletion-impact`, {
      decode: decodeDeletionImpact,
      signal,
    })
  },

  deleteStudent(
    studentId: number,
    expectedRevision: string,
    signal?: AbortSignal,
  ): Promise<StudentDeleteResult> {
    const revision = encodeURIComponent(expectedRevision)
    return apiClient.request(
      `/api/students/${studentId}?expected_revision=${revision}&confirmed=true`,
      {
        method: 'DELETE',
        decode: decodeDeleteResult,
        signal,
        timeoutMs: 30_000,
      },
    )
  },
}
