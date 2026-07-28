import { apiClient } from './client'
import { decodeJobResponse, type JobResponse } from './jobs'
import {
  isQuestionBankRichContent,
  type QuestionBankRichContent,
  type QuestionBankTag,
} from './question-bank'
import { isRecord } from './validation'

export type AssemblyLayoutMode = 'sequential' | 'grouped_by_type' | 'sections'
export type AssemblyPreviewMode = 'student' | 'teacher'
export type AssemblyExportFormat = 'docx' | 'markdown'
export type AssemblyExportSubmitFormat = 'docx'

export interface AssemblySection {
  id: string
  title: string
  question_ids: number[]
}

export interface AssemblyDraft {
  basket_ids: number[]
  order_ids: number[]
  sections: AssemblySection[]
  title: string
  header_text: string
  include_answer: boolean
  layout_mode: AssemblyLayoutMode
  preview_mode: AssemblyPreviewMode
  revision: string
}

export interface AssemblyQuestion {
  id: number
  revision: string
  question_number: string
  question_type: string | null
  question_text: string
  answer_text: string | null
  difficulty: string | null
  paper_title: string | null
  tags: QuestionBankTag[]
  asset_urls: string[]
  /**
   * Optional for legacy caller-owned drafts. The response decoder below still
   * rejects server payloads that omit the structured preview.
   */
  rich_content?: QuestionBankRichContent
  score_value: number | null
}

export interface AssemblyQuestionList {
  items: AssemblyQuestion[]
  missing_question_ids: number[]
}

export interface AssemblyRecord {
  id: string
  title: string
  question_ids: number[]
  order_ids: number[]
  sections: AssemblySection[]
  export_format: AssemblyExportFormat
  filename: string
  include_answer: boolean
  created_at: string
  question_count: number
  question_type_summary: Record<string, number>
  download_url: string
}

export interface AssemblyRecordList {
  items: AssemblyRecord[]
  total: number
}

export interface AssemblyRecordDelete {
  record_id: string
  deleted: boolean
}

const REVISION = /^[0-9a-f]{64}$/
const SECTION_ID = /^[A-Za-z0-9_.:-]{1,64}$/

function isPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0
}

function isRevision(value: unknown): value is string {
  return typeof value === 'string' && REVISION.test(value)
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value)
  return actual.length === keys.length && keys.every(
    (key) => Object.prototype.hasOwnProperty.call(value, key),
  )
}

function isQuestionIdList(value: unknown): value is number[] {
  return Array.isArray(value) && value.length <= 500 && value.every(isPositiveInteger)
}

function isSection(value: unknown): value is AssemblySection {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['id', 'title', 'question_ids']) &&
    typeof value.id === 'string' &&
    SECTION_ID.test(value.id) &&
    typeof value.title === 'string' &&
    value.title.length > 0 &&
    value.title.length <= 80 &&
    isQuestionIdList(value.question_ids)
  )
}

function isLayoutMode(value: unknown): value is AssemblyLayoutMode {
  return value === 'sequential' || value === 'grouped_by_type' || value === 'sections'
}

function isPreviewMode(value: unknown): value is AssemblyPreviewMode {
  return value === 'student' || value === 'teacher'
}

function isExportFormat(value: unknown): value is AssemblyExportFormat {
  return value === 'docx' || value === 'markdown'
}

export function decodeAssemblyDraft(value: unknown): AssemblyDraft {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      'basket_ids',
      'order_ids',
      'sections',
      'title',
      'header_text',
      'include_answer',
      'layout_mode',
      'preview_mode',
      'revision',
    ]) ||
    !isQuestionIdList(value.basket_ids) ||
    !isQuestionIdList(value.order_ids) ||
    !Array.isArray(value.sections) ||
    !value.sections.every(isSection) ||
    typeof value.title !== 'string' ||
    value.title.length > 120 ||
    typeof value.header_text !== 'string' ||
    value.header_text.length > 200 ||
    typeof value.include_answer !== 'boolean' ||
    !isLayoutMode(value.layout_mode) ||
    !isPreviewMode(value.preview_mode) ||
    !isRevision(value.revision)
  ) {
    throw new Error('Invalid assembly draft')
  }
  return value as unknown as AssemblyDraft
}

function isAssemblyQuestion(value: unknown): value is AssemblyQuestion {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'id',
      'revision',
      'question_number',
      'question_type',
      'question_text',
      'answer_text',
      'difficulty',
      'paper_title',
      'tags',
      'asset_urls',
      'rich_content',
      'score_value',
    ]) &&
    isPositiveInteger(value.id) &&
    isRevision(value.revision) &&
    typeof value.question_number === 'string' &&
    (value.question_type === null || typeof value.question_type === 'string') &&
    typeof value.question_text === 'string' &&
    (value.answer_text === null || typeof value.answer_text === 'string') &&
    (value.difficulty === null || typeof value.difficulty === 'string') &&
    (value.paper_title === null || typeof value.paper_title === 'string') &&
    Array.isArray(value.tags) &&
    Array.isArray(value.asset_urls) &&
    value.asset_urls.every((url) => typeof url === 'string' && url.startsWith('/api/question-bank/')) &&
    isQuestionBankRichContent(value.rich_content) &&
    (
      value.score_value === null ||
      (Number.isSafeInteger(value.score_value) && Number(value.score_value) >= 0)
    )
  )
}

export function decodeAssemblyQuestionList(value: unknown): AssemblyQuestionList {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['items', 'missing_question_ids']) ||
    !Array.isArray(value.items) ||
    !value.items.every(isAssemblyQuestion) ||
    !isQuestionIdList(value.missing_question_ids)
  ) {
    throw new Error('Invalid assembly questions')
  }
  return value as unknown as AssemblyQuestionList
}

function isRecordItem(value: unknown): value is AssemblyRecord {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'id',
      'title',
      'question_ids',
      'order_ids',
      'sections',
      'export_format',
      'filename',
      'include_answer',
      'created_at',
      'question_count',
      'question_type_summary',
      'download_url',
    ]) &&
    typeof value.id === 'string' &&
    typeof value.title === 'string' &&
    isQuestionIdList(value.question_ids) &&
    isQuestionIdList(value.order_ids) &&
    Array.isArray(value.sections) &&
    value.sections.every(isSection) &&
    isExportFormat(value.export_format) &&
    typeof value.filename === 'string' &&
    !/[\\/]/.test(value.filename) &&
    typeof value.include_answer === 'boolean' &&
    typeof value.created_at === 'string' &&
    Number.isSafeInteger(value.question_count) &&
    Number(value.question_count) >= 0 &&
    isRecord(value.question_type_summary) &&
    typeof value.download_url === 'string' &&
    value.download_url.startsWith('/api/question-assembly/records/')
  )
}

export function decodeAssemblyRecordList(value: unknown): AssemblyRecordList {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['items', 'total']) ||
    !Array.isArray(value.items) ||
    !value.items.every(isRecordItem) ||
    !Number.isSafeInteger(value.total) ||
    Number(value.total) < 0
  ) {
    throw new Error('Invalid assembly records')
  }
  return value as unknown as AssemblyRecordList
}

export function decodeAssemblyRecordDelete(value: unknown): AssemblyRecordDelete {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['record_id', 'deleted']) ||
    typeof value.record_id !== 'string' ||
    typeof value.deleted !== 'boolean'
  ) {
    throw new Error('Invalid assembly record delete')
  }
  return value as unknown as AssemblyRecordDelete
}

function publicDraftPayload(draft: AssemblyDraft) {
  const payload = { ...draft }
  delete (payload as Partial<AssemblyDraft>).revision
  return payload
}

function normalizedQuestionIds(values: readonly number[]): number[] {
  const result: number[] = []
  const seen = new Set<number>()
  for (const value of values) {
    if (!isPositiveInteger(value)) throw new Error('Invalid question ids')
    if (!seen.has(value)) {
      seen.add(value)
      result.push(value)
    }
  }
  if (result.length === 0 || result.length > 500) throw new Error('Invalid question ids')
  return result
}

export const assemblyApi = {
  getDraft(signal?: AbortSignal): Promise<AssemblyDraft> {
    return apiClient.request('/api/question-assembly/draft', {
      decode: decodeAssemblyDraft,
      signal,
    })
  },

  saveDraft(
    expectedRevision: string,
    draft: AssemblyDraft,
    signal?: AbortSignal,
  ): Promise<AssemblyDraft> {
    if (!isRevision(expectedRevision)) throw new Error('Invalid assembly revision')
    return apiClient.request('/api/question-assembly/draft', {
      method: 'PUT',
      body: {
        expected_revision: expectedRevision,
        draft: publicDraftPayload(draft),
      },
      decode: decodeAssemblyDraft,
      signal,
    })
  },

  resolveQuestions(ids: readonly number[], signal?: AbortSignal): Promise<AssemblyQuestionList> {
    const params = new URLSearchParams()
    for (const id of normalizedQuestionIds(ids)) params.append('question_ids', String(id))
    return apiClient.request(`/api/question-assembly/questions?${params.toString()}`, {
      decode: decodeAssemblyQuestionList,
      signal,
    })
  },

  listRecords(limit = 100, signal?: AbortSignal): Promise<AssemblyRecordList> {
    return apiClient.request(`/api/question-assembly/records?limit=${Math.max(1, Math.min(100, Math.floor(limit)))}`, {
      decode: decodeAssemblyRecordList,
      signal,
    })
  },

  deleteRecord(recordId: string, signal?: AbortSignal): Promise<AssemblyRecordDelete> {
    if (!recordId.trim()) throw new Error('Invalid assembly record id')
    return apiClient.request(`/api/question-assembly/records/${encodeURIComponent(recordId)}`, {
      method: 'DELETE',
      decode: decodeAssemblyRecordDelete,
      signal,
    })
  },

  restoreRecord(
    recordId: string,
    expectedRevision: string,
    signal?: AbortSignal,
  ): Promise<AssemblyDraft> {
    if (!recordId.trim() || !isRevision(expectedRevision)) {
      throw new Error('Invalid assembly record restore')
    }
    return apiClient.request(
      `/api/question-assembly/records/${encodeURIComponent(recordId)}/restore`,
      {
        method: 'POST',
        body: { expected_revision: expectedRevision },
        decode: decodeAssemblyDraft,
        signal,
      },
    )
  },

  submitExport(
    draftRevision: string,
    format: AssemblyExportSubmitFormat,
    signal?: AbortSignal,
  ): Promise<JobResponse> {
    if (!isRevision(draftRevision) || !isExportFormat(format)) {
      throw new Error('Invalid assembly export request')
    }
    return apiClient.request('/api/question-assembly/export', {
      method: 'POST',
      body: { draft_revision: draftRevision, format },
      decode: decodeJobResponse,
      signal,
    })
  },

  retryExport(jobId: number, signal?: AbortSignal): Promise<JobResponse> {
    if (!isPositiveInteger(jobId)) throw new Error('Invalid assembly export job id')
    return apiClient.request(`/api/question-assembly/exports/${jobId}/retry`, {
      method: 'POST',
      decode: decodeJobResponse,
      signal,
    })
  },
}
