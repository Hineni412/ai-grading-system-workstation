import { apiClient } from './client'
import { isRecord, isNullableString } from './validation'

export type ScoreImportSheetKind = 'scores' | 'options' | 'unrecognized'

export interface ScoreImportSheetInfo {
  name: string
  kind: ScoreImportSheetKind
  class_hint: string | null
  row_count: number
}

export interface ScoreImportUploadMeta {
  upload_id: string
  filename: string
  uploaded_at: string
  sheets: ScoreImportSheetInfo[]
}

export interface ScoreImportReceipt {
  imported_at: string
  upload_ids: string[]
  parameters: {
    sheet_classes: Record<string, Record<string, string>>
    part_choices: Record<string, 'parts' | 'whole'>
    item_max: Record<string, number>
    student_overrides?: Record<string, number | null>
  }
  counts: { students: number; items: number; absent: number }
}

export interface ScoreImportState {
  kind: string
  exam_date: string | null
  uploads: ScoreImportUploadMeta[]
  receipt: ScoreImportReceipt | null
  has_results: boolean
}

export type ScoreImportIssueLevel = 'block' | 'warn'

export interface ScoreImportIssue {
  level: ScoreImportIssueLevel
  code: string
  message: string
  sheet?: string
  row?: number
  column?: string
  student_id?: number
}

export interface ScoreImportPlanItem {
  item_id: string
  max_score: number
  observed_max: number | null
}

export type ScoreImportChoice =
  | 'single'
  | 'parts'
  | 'whole'
  | 'derive_options'
  | 'combined'

export interface ScoreImportQuestionPlan {
  question_id: string
  question_type: string
  columns: string[]
  choice: ScoreImportChoice
  status: 'ok' | 'needs_confirm'
  group_id?: string
  group_label?: string
  items: ScoreImportPlanItem[]
}

export interface ScoreImportGroup {
  group_id: string
  label: string
  question_ids: string[]
  max_score: number
  counts: { full: number; zero: number; intermediate: number; derived: number }
}

export interface ScoreImportCandidate {
  student_id: number
  name: string
  class_name: string
}

export interface ScoreImportUnresolved {
  row_key: string
  sheet: string
  row: number
  name: string
  class: string
  candidates: ScoreImportCandidate[]
  reason: string
}

export interface ScoreImportAbsent {
  student_id: number
  name: string
  class_name: string
  reason: string
}

export interface ScoreImportStudents {
  matched: number
  absent: ScoreImportAbsent[]
  unresolved: ScoreImportUnresolved[]
  skipped_by_class: Record<string, number>
  skipped_rows: number
}

export interface ScoreImportDiffEntry {
  student_id: number
  item_id: string
  old_score: number | null
  new_score: number
  teacher_edited: boolean
}

export interface ScoreImportPreview {
  questions: ScoreImportQuestionPlan[]
  groups: ScoreImportGroup[]
  students: ScoreImportStudents
  issues: ScoreImportIssue[]
  diff: ScoreImportDiffEntry[] | null
  proposed_rubric_total: number
  preview_revision: string
  has_results: boolean
}

export interface ScoreImportPlanRequest {
  upload_ids: string[]
  sheet_classes: Record<string, Record<string, string>>
  part_choices: Record<string, 'parts' | 'whole'>
  item_max: Record<string, number>
  student_overrides: Record<string, number | null>
}

export interface ScoreImportConfirmRequest extends ScoreImportPlanRequest {
  preview_revision: string
  replace_existing: boolean
}

export interface ScoreImportConfirmResult {
  status: string
  batch_id: string
  imported_students: number
  teacher_final_items: number
  absent_students: number
  total_score: number
}

function isText(value: unknown): value is string {
  return typeof value === 'string'
}

function isNonnegativeNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

function isNullableNonnegativeNumber(value: unknown): value is number | null {
  return value === null || isNonnegativeNumber(value)
}

function isScoreImportIssueLevel(value: unknown): value is ScoreImportIssueLevel {
  return value === 'block' || value === 'warn'
}

function decodeSheet(value: unknown): ScoreImportSheetInfo {
  if (!isRecord(value)
    || !isText(value.name)
    || (value.kind !== 'scores' && value.kind !== 'options' && value.kind !== 'unrecognized')
    || !isNullableString(value.class_hint)
    || !isNonnegativeNumber(value.row_count)) {
    throw new Error('invalid score import sheet')
  }
  return value as unknown as ScoreImportSheetInfo
}

function decodeUploadMeta(value: unknown): ScoreImportUploadMeta {
  if (!isRecord(value)
    || !isText(value.upload_id)
    || !isText(value.filename)
    || !isText(value.uploaded_at)
    || !Array.isArray(value.sheets)) {
    throw new Error('invalid score import upload')
  }
  return {
    upload_id: value.upload_id,
    filename: value.filename,
    uploaded_at: value.uploaded_at,
    sheets: value.sheets.map(decodeSheet),
  }
}

function decodeReceipt(value: unknown): ScoreImportReceipt {
  if (!isRecord(value)
    || !isText(value.imported_at)
    || !Array.isArray(value.upload_ids)
    || !value.upload_ids.every(isText)
    || !isRecord(value.parameters)
    || !isRecord(value.counts)
    || !isNonnegativeNumber(value.counts.students)
    || !isNonnegativeNumber(value.counts.items)
    || !isNonnegativeNumber(value.counts.absent)) {
    throw new Error('invalid score import receipt')
  }
  return value as unknown as ScoreImportReceipt
}

function decodeIssue(value: unknown): ScoreImportIssue {
  if (!isRecord(value)
    || !isScoreImportIssueLevel(value.level)
    || !isText(value.code)
    || !isText(value.message)) {
    throw new Error('invalid score import issue')
  }
  return value as unknown as ScoreImportIssue
}

function decodePlanItem(value: unknown): ScoreImportPlanItem {
  if (!isRecord(value)
    || !isText(value.item_id)
    || !isNonnegativeNumber(value.max_score)
    || !isNullableNonnegativeNumber(value.observed_max)) {
    throw new Error('invalid score import plan item')
  }
  return value as unknown as ScoreImportPlanItem
}

function decodeQuestionPlan(value: unknown): ScoreImportQuestionPlan {
  if (!isRecord(value)
    || !isText(value.question_id)
    || !isText(value.question_type)
    || !Array.isArray(value.columns)
    || !value.columns.every(isText)
    || !isText(value.choice)
    || (value.status !== 'ok' && value.status !== 'needs_confirm')
    || !Array.isArray(value.items)) {
    throw new Error('invalid score import question')
  }
  return {
    ...value as unknown as ScoreImportQuestionPlan,
    items: value.items.map(decodePlanItem),
  }
}

function decodeGroup(value: unknown): ScoreImportGroup {
  if (!isRecord(value)
    || !isText(value.group_id)
    || !isText(value.label)
    || !Array.isArray(value.question_ids)
    || !value.question_ids.every(isText)
    || !isNonnegativeNumber(value.max_score)
    || !isRecord(value.counts)
    || !isNonnegativeNumber(value.counts.full)
    || !isNonnegativeNumber(value.counts.zero)
    || !isNonnegativeNumber(value.counts.intermediate)) {
    throw new Error('invalid score import group')
  }
  const counts = value.counts
  if (!isNonnegativeNumber(counts.full)
    || !isNonnegativeNumber(counts.zero)
    || !isNonnegativeNumber(counts.intermediate)) {
    throw new Error('invalid score import group')
  }
  return {
    group_id: value.group_id,
    label: value.label,
    question_ids: value.question_ids as string[],
    max_score: value.max_score,
    counts: {
      full: counts.full,
      zero: counts.zero,
      intermediate: counts.intermediate,
      derived: isNonnegativeNumber(counts.derived) ? counts.derived : 0,
    },
  }
}

function decodeUnresolved(value: unknown): ScoreImportUnresolved {
  if (!isRecord(value)
    || !isText(value.row_key)
    || !isText(value.sheet)
    || !isNonnegativeNumber(value.row)
    || !isText(value.name)
    || !isText(value.reason)
    || !Array.isArray(value.candidates)) {
    throw new Error('invalid score import unresolved row')
  }
  const candidates = value.candidates.map((entry: unknown) => {
    if (!isRecord(entry)
      || !isNonnegativeNumber(entry.student_id)
      || !isText(entry.name)
      || !isText(entry.class_name)) {
      throw new Error('invalid score import candidate')
    }
    return entry as unknown as ScoreImportCandidate
  })
  return {
    ...(value as unknown as ScoreImportUnresolved),
    class: isText(value.class) ? value.class : '',
    candidates,
  }
}

function decodeStudents(value: unknown): ScoreImportStudents {
  if (!isRecord(value)
    || !isNonnegativeNumber(value.matched)
    || !isNonnegativeNumber(value.skipped_rows)
    || !Array.isArray(value.absent)
    || !Array.isArray(value.unresolved)
    || !isRecord(value.skipped_by_class)) {
    throw new Error('invalid score import students')
  }
  const skipped_by_class = value.skipped_by_class as Record<string, unknown>
  return {
    matched: value.matched,
    absent: value.absent.map((entry: unknown) => {
      if (!isRecord(entry)) throw new Error('invalid score import absent row')
      return entry as unknown as ScoreImportAbsent
    }),
    unresolved: value.unresolved.map(decodeUnresolved),
    skipped_by_class: Object.fromEntries(
      Object.entries(skipped_by_class).filter(
        ([, count]) => isNonnegativeNumber(count),
      ),
    ) as Record<string, number>,
    skipped_rows: value.skipped_rows,
  }
}

function decodeDiffEntry(value: unknown): ScoreImportDiffEntry {
  if (!isRecord(value)
    || !isNonnegativeNumber(value.student_id)
    || !isText(value.item_id)
    || !isNullableNonnegativeNumber(value.old_score)
    || !isNonnegativeNumber(value.new_score)
    || typeof value.teacher_edited !== 'boolean') {
    throw new Error('invalid score import diff entry')
  }
  return value as unknown as ScoreImportDiffEntry
}

export function decodeScoreImportPreview(value: unknown): ScoreImportPreview {
  if (!isRecord(value)
    || !Array.isArray(value.questions)
    || !Array.isArray(value.groups)
    || !Array.isArray(value.issues)
    || !isNonnegativeNumber(value.proposed_rubric_total)
    || !isText(value.preview_revision)
    || typeof value.has_results !== 'boolean') {
    throw new Error('invalid score import preview')
  }
  return {
    questions: value.questions.map(decodeQuestionPlan),
    groups: value.groups.map(decodeGroup),
    students: decodeStudents(value.students),
    issues: value.issues.map(decodeIssue),
    diff: value.diff === null
      ? null
      : Array.isArray(value.diff)
        ? value.diff.map(decodeDiffEntry)
        : (() => { throw new Error('invalid score import diff') })(),
    proposed_rubric_total: value.proposed_rubric_total,
    preview_revision: value.preview_revision,
    has_results: value.has_results,
  }
}

function decodeState(value: unknown): ScoreImportState {
  if (!isRecord(value)
    || !isText(value.kind)
    || !isNullableString(value.exam_date)
    || !Array.isArray(value.uploads)
    || !(value.receipt === null || isRecord(value.receipt))
    || typeof value.has_results !== 'boolean') {
    throw new Error('invalid score import state')
  }
  return {
    kind: value.kind,
    exam_date: value.exam_date,
    uploads: value.uploads.map(decodeUploadMeta),
    receipt: value.receipt === null ? null : decodeReceipt(value.receipt),
    has_results: value.has_results,
  }
}

function requireSessionId(sessionId: number): number {
  if (!Number.isSafeInteger(sessionId) || sessionId <= 0) {
    throw new Error('Invalid session id')
  }
  return sessionId
}

export function fetchScoreImportState(
  sessionId: number,
  signal?: AbortSignal,
): Promise<ScoreImportState> {
  return apiClient.request(`/api/sessions/${requireSessionId(sessionId)}/score-import`, {
    decode: decodeState,
    signal,
  })
}

export function uploadScoreImportFile(
  sessionId: number,
  file: File,
  signal?: AbortSignal,
): Promise<ScoreImportUploadMeta> {
  const id = requireSessionId(sessionId)
  if (!(file instanceof File) || !file.name.trim()) throw new Error('Invalid upload file')
  return apiClient.request(`/api/sessions/${id}/score-import/uploads`, {
    method: 'POST',
    rawBody: file,
    headers: {
      'content-type': 'application/octet-stream',
      'x-upload-filename': encodeURIComponent(file.name),
    },
    timeoutMs: 120_000,
    decode: decodeUploadMeta,
    signal,
  })
}

export function previewScoreImport(
  sessionId: number,
  body: ScoreImportPlanRequest,
  signal?: AbortSignal,
): Promise<ScoreImportPreview> {
  return apiClient.request(
    `/api/sessions/${requireSessionId(sessionId)}/score-import/preview`,
    { method: 'POST', body, decode: decodeScoreImportPreview, signal },
  )
}

export function confirmScoreImport(
  sessionId: number,
  body: ScoreImportConfirmRequest,
): Promise<ScoreImportConfirmResult> {
  return apiClient.request(
    `/api/sessions/${requireSessionId(sessionId)}/score-import/confirm`,
    {
      method: 'POST',
      body,
      timeoutMs: 120_000,
      decode(value) {
        if (!isRecord(value) || value.status !== 'imported') {
          throw new Error('invalid score import confirm response')
        }
        return value as unknown as ScoreImportConfirmResult
      },
    },
  )
}
