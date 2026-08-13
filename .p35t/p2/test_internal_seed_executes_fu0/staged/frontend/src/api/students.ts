import { apiClient } from './client'
import { isNullableString, isRecord } from './validation'

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
