import { apiClient } from './client'
import { isNullableString, isRecord } from './validation'
import { decodeJobResponse, type JobResponse } from './jobs'

export const QUESTION_BANK_TAG_TYPES = [
  'ability',
  'canonical_knowledge_id',
  'error_type',
  'exam_scope',
  'knowledge_point',
  'measured_skill_name',
  'method',
  'model',
  'prerequisite',
  'student_level',
  'sub_skill',
  'supporting_skill_name',
  'teaching_stage',
] as const

export type QuestionBankTagType = (typeof QUESTION_BANK_TAG_TYPES)[number]

export interface QuestionBankTag {
  tag_type: QuestionBankTagType
  tag_value: string
  confidence: number | null
}

export interface QuestionBankListItem {
  id: number
  revision: string
  paper_id: number | null
  question_number: string
  question_type: string | null
  question_text: string
  answer_text: string | null
  difficulty: string | null
  typicality: string | null
  reason: string | null
  needs_review: boolean
  has_images: boolean
  needs_image_review: boolean
  created_at: string
  updated_at: string
  paper_title: string | null
  year: string | null
  province: string | null
  city: string | null
  district: string | null
  exam_type: string | null
  grade: string | null
  semester: string | null
  textbook_version: string | null
  tags: QuestionBankTag[]
  asset_urls: string[]
}

export interface QuestionBankListResponse {
  items: QuestionBankListItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface QuestionBankPaper {
  id: number
  title: string | null
  year: string | null
  province: string | null
  city: string | null
  district: string | null
  exam_type: string | null
  grade: string | null
  semester: string | null
  textbook_version: string | null
  import_status: string | null
  created_at: string
  updated_at: string
  question_count: number
  tagged_question_count: number
}

export interface QuestionBankPaperListResponse {
  items: QuestionBankPaper[]
  total: number
}

export interface QuestionBankWriteResult {
  question_id: number
  revision: string
  deleted: boolean
  tags: QuestionBankTag[]
}

export interface QuestionImportUpload {
  upload_id: string
  filename: string
  suffix: '.docx' | '.pdf'
  size: number
  sha256: string
}

export interface QuestionImportRequest {
  request_id: string
  upload_id: string
  filename: string
  size: number
  sha256: string
  status: 'pending'
}

export interface QuestionBankAssetLink {
  index: number
  url: string
}

export interface QuestionBankRichBlock {
  text: string
  asset_indexes: number[]
  asset_urls: string[]
}

export interface QuestionBankRichContent {
  available: boolean
  question_block_count: number
  answer_block_count: number
  question_blocks: QuestionBankRichBlock[]
  answer_blocks: QuestionBankRichBlock[]
}

export interface QuestionBankPreview {
  preview_type: 'question' | 'answer'
  status: string
  page_number: number | null
  bbox: {
    x0: number | null
    y0: number | null
    x1: number | null
    y1: number | null
  } | null
  updated_at: string
  url: string | null
}

export interface QuestionJobFailure {
  question_id: number
  category: string
  message: string
}

export interface QuestionBankDetail extends QuestionBankListItem {
  page_range: string | null
  assets: QuestionBankAssetLink[]
  rich_content: QuestionBankRichContent
  previews: QuestionBankPreview[]
}

export type QuestionBankTagStatus = 'all' | 'tagged' | 'untagged'
export type QuestionBankSort =
  | 'newest'
  | 'difficulty'
  | 'frequency_midterm'
  | 'frequency_final'
  | 'frequency_zhongkao'
  | 'frequency_contextual'

export interface QuestionBankFilters {
  page?: number
  pageSize?: number
  questionNumber?: string
  keyword?: string
  knowledgePoint?: string
  difficultyMin?: number
  difficultyMax?: number
  questionTypes?: string[]
  paperIds?: number[]
  years?: string[]
  examTypes?: string[]
  grades?: string[]
  examScopes?: string[]
  tagStatus?: QuestionBankTagStatus
  sort?: QuestionBankSort
}

const QUESTION_LIST_KEYS = [
  'id',
  'revision',
  'paper_id',
  'question_number',
  'question_type',
  'question_text',
  'answer_text',
  'difficulty',
  'typicality',
  'reason',
  'needs_review',
  'has_images',
  'needs_image_review',
  'created_at',
  'updated_at',
  'paper_title',
  'year',
  'province',
  'city',
  'district',
  'exam_type',
  'grade',
  'semester',
  'textbook_version',
  'tags',
  'asset_urls',
] as const

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value)
  return actual.length === keys.length && keys.every(
    (key) => Object.prototype.hasOwnProperty.call(value, key),
  )
}

function isNonnegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0
}

function isRevision(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
}

function isHex(value: unknown, length: number): value is string {
  return typeof value === 'string' && new RegExp(`^[0-9a-f]{${length}}$`).test(value)
}

function isNullableInteger(value: unknown): value is number | null {
  return value === null || isPositiveInteger(value)
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function isQuestionBankTag(value: unknown): value is QuestionBankTag {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['tag_type', 'tag_value', 'confidence']) &&
    typeof value.tag_type === 'string' &&
    QUESTION_BANK_TAG_TYPES.some((item) => item === value.tag_type) &&
    typeof value.tag_value === 'string' &&
    value.tag_value.length > 0 &&
    (
      value.confidence === null ||
      (
        typeof value.confidence === 'number' &&
        Number.isFinite(value.confidence) &&
        value.confidence >= 0 &&
        value.confidence <= 1
      )
    )
  )
}

function isQuestionBankListItem(value: unknown): value is QuestionBankListItem {
  if (!isRecord(value) || !hasExactKeys(value, QUESTION_LIST_KEYS)) return false
  return hasQuestionBankListFields(value)
}

function hasQuestionBankListFields(value: Record<string, unknown>): boolean {
  return (
    isPositiveInteger(value.id) &&
    isRevision(value.revision) &&
    isNullableInteger(value.paper_id) &&
    typeof value.question_number === 'string' &&
    isNullableString(value.question_type) &&
    typeof value.question_text === 'string' &&
    isNullableString(value.answer_text) &&
    isNullableString(value.difficulty) &&
    isNullableString(value.typicality) &&
    isNullableString(value.reason) &&
    typeof value.needs_review === 'boolean' &&
    typeof value.has_images === 'boolean' &&
    typeof value.needs_image_review === 'boolean' &&
    typeof value.created_at === 'string' &&
    typeof value.updated_at === 'string' &&
    isNullableString(value.paper_title) &&
    isNullableString(value.year) &&
    isNullableString(value.province) &&
    isNullableString(value.city) &&
    isNullableString(value.district) &&
    isNullableString(value.exam_type) &&
    isNullableString(value.grade) &&
    isNullableString(value.semester) &&
    isNullableString(value.textbook_version) &&
    Array.isArray(value.tags) &&
    value.tags.every(isQuestionBankTag) &&
    isStringArray(value.asset_urls) &&
    value.asset_urls.every((url) => url.startsWith('/api/question-bank/'))
  )
}

function isControlledQuestionUrl(value: unknown): value is string {
  return typeof value === 'string' && value.startsWith('/api/question-bank/questions/')
}

function isNullableFiniteNumber(value: unknown): value is number | null {
  return value === null || (typeof value === 'number' && Number.isFinite(value))
}

function isAssetLink(value: unknown): value is QuestionBankAssetLink {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['index', 'url']) &&
    isNonnegativeInteger(value.index) &&
    isControlledQuestionUrl(value.url)
  )
}

function isRichBlock(value: unknown): value is QuestionBankRichBlock {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['text', 'asset_indexes', 'asset_urls']) &&
    typeof value.text === 'string' &&
    Array.isArray(value.asset_indexes) &&
    value.asset_indexes.every(isNonnegativeInteger) &&
    Array.isArray(value.asset_urls) &&
    value.asset_urls.every(isControlledQuestionUrl)
  )
}

function isRichContent(value: unknown): value is QuestionBankRichContent {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'available',
      'question_block_count',
      'answer_block_count',
      'question_blocks',
      'answer_blocks',
    ]) &&
    typeof value.available === 'boolean' &&
    isNonnegativeInteger(value.question_block_count) &&
    isNonnegativeInteger(value.answer_block_count) &&
    Array.isArray(value.question_blocks) &&
    value.question_blocks.every(isRichBlock) &&
    Array.isArray(value.answer_blocks) &&
    value.answer_blocks.every(isRichBlock) &&
    value.question_blocks.length === Number(value.question_block_count) &&
    value.answer_blocks.length === Number(value.answer_block_count)
  )
}

function isPreviewBox(value: unknown): boolean {
  return (
    value === null ||
    (
      isRecord(value) &&
      hasExactKeys(value, ['x0', 'y0', 'x1', 'y1']) &&
      isNullableFiniteNumber(value.x0) &&
      isNullableFiniteNumber(value.y0) &&
      isNullableFiniteNumber(value.x1) &&
      isNullableFiniteNumber(value.y1)
    )
  )
}

function isPreview(value: unknown): value is QuestionBankPreview {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'preview_type',
      'status',
      'page_number',
      'bbox',
      'updated_at',
      'url',
    ]) &&
    (value.preview_type === 'question' || value.preview_type === 'answer') &&
    typeof value.status === 'string' &&
    (value.page_number === null || isPositiveInteger(value.page_number)) &&
    isPreviewBox(value.bbox) &&
    typeof value.updated_at === 'string' &&
    (value.url === null || isControlledQuestionUrl(value.url))
  )
}

export function decodeQuestionListResponse(value: unknown): QuestionBankListResponse {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['items', 'total', 'page', 'page_size', 'total_pages']) ||
    !Array.isArray(value.items) ||
    !value.items.every(isQuestionBankListItem) ||
    !isNonnegativeInteger(value.total) ||
    !isPositiveInteger(value.page) ||
    !isPositiveInteger(value.page_size) ||
    !isNonnegativeInteger(value.total_pages) ||
    value.items.length > Number(value.page_size)
  ) {
    throw new Error('Invalid question bank list')
  }
  return value as unknown as QuestionBankListResponse
}

function isQuestionBankPaper(value: unknown): value is QuestionBankPaper {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'id',
      'title',
      'year',
      'province',
      'city',
      'district',
      'exam_type',
      'grade',
      'semester',
      'textbook_version',
      'import_status',
      'created_at',
      'updated_at',
      'question_count',
      'tagged_question_count',
    ]) &&
    isPositiveInteger(value.id) &&
    isNullableString(value.title) &&
    isNullableString(value.year) &&
    isNullableString(value.province) &&
    isNullableString(value.city) &&
    isNullableString(value.district) &&
    isNullableString(value.exam_type) &&
    isNullableString(value.grade) &&
    isNullableString(value.semester) &&
    isNullableString(value.textbook_version) &&
    isNullableString(value.import_status) &&
    typeof value.created_at === 'string' &&
    typeof value.updated_at === 'string' &&
    isNonnegativeInteger(value.question_count) &&
    isNonnegativeInteger(value.tagged_question_count) &&
    Number(value.tagged_question_count) <= Number(value.question_count)
  )
}

export function decodeQuestionPaperListResponse(
  value: unknown,
): QuestionBankPaperListResponse {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['items', 'total']) ||
    !Array.isArray(value.items) ||
    !value.items.every(isQuestionBankPaper) ||
    !isNonnegativeInteger(value.total) ||
    Number(value.total) !== value.items.length
  ) {
    throw new Error('Invalid question bank papers')
  }
  return value as unknown as QuestionBankPaperListResponse
}

export function decodeQuestionDetailResponse(value: unknown): QuestionBankDetail {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      ...QUESTION_LIST_KEYS,
      'page_range',
      'assets',
      'rich_content',
      'previews',
    ]) ||
    !hasQuestionBankListFields(value) ||
    !isNullableString(value.page_range) ||
    !Array.isArray(value.assets) ||
    !value.assets.every(isAssetLink) ||
    !isRichContent(value.rich_content) ||
    !Array.isArray(value.previews) ||
    !value.previews.every(isPreview)
  ) {
    throw new Error('Invalid question bank detail')
  }
  return value as unknown as QuestionBankDetail
}

export function decodeQuestionWriteResult(value: unknown): QuestionBankWriteResult {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['question_id', 'revision', 'deleted', 'tags']) ||
    !isPositiveInteger(value.question_id) ||
    !isRevision(value.revision) ||
    typeof value.deleted !== 'boolean' ||
    !Array.isArray(value.tags) ||
    !value.tags.every(isQuestionBankTag)
  ) {
    throw new Error('Invalid question bank write result')
  }
  return value as unknown as QuestionBankWriteResult
}

export function decodeQuestionImportUpload(value: unknown): QuestionImportUpload {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['upload_id', 'filename', 'suffix', 'size', 'sha256']) ||
    !isHex(value.upload_id, 32) ||
    typeof value.filename !== 'string' ||
    !value.filename ||
    (value.suffix !== '.docx' && value.suffix !== '.pdf') ||
    !isPositiveInteger(value.size) ||
    !isHex(value.sha256, 64)
  ) {
    throw new Error('Invalid question import upload')
  }
  return value as unknown as QuestionImportUpload
}

export function decodeQuestionImportRequest(value: unknown): QuestionImportRequest {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      'request_id',
      'upload_id',
      'filename',
      'size',
      'sha256',
      'status',
    ]) ||
    !isHex(value.request_id, 32) ||
    !isHex(value.upload_id, 32) ||
    typeof value.filename !== 'string' ||
    !value.filename ||
    !isPositiveInteger(value.size) ||
    !isHex(value.sha256, 64) ||
    value.status !== 'pending'
  ) {
    throw new Error('Invalid question import request')
  }
  return value as unknown as QuestionImportRequest
}

function appendTexts(
  parameters: URLSearchParams,
  key: string,
  values: readonly string[] | undefined,
): void {
  const seen = new Set<string>()
  for (const raw of values ?? []) {
    const value = raw.trim()
    if (value && !seen.has(value)) {
      seen.add(value)
      parameters.append(key, value)
    }
  }
}

function appendIds(
  parameters: URLSearchParams,
  key: string,
  values: readonly number[] | undefined,
): void {
  const seen = new Set<number>()
  for (const value of values ?? []) {
    if (isPositiveInteger(value) && !seen.has(value)) {
      seen.add(value)
      parameters.append(key, String(value))
    }
  }
}

function questionListPath(filters: QuestionBankFilters): string {
  const page = filters.page ?? 1
  const pageSize = filters.pageSize ?? 20
  if (!isPositiveInteger(page) || !isPositiveInteger(pageSize) || pageSize > 100) {
    throw new Error('Invalid question filters')
  }
  const parameters = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  })
  const textFilters: Array<[string, string | undefined]> = [
    ['question_number', filters.questionNumber],
    ['keyword', filters.keyword],
    ['knowledge_point', filters.knowledgePoint],
  ]
  for (const [key, raw] of textFilters) {
    const value = raw?.trim()
    if (value) parameters.set(key, value)
  }
  const hasDifficulty = filters.difficultyMin !== undefined || filters.difficultyMax !== undefined
  if (hasDifficulty) {
    if (
      !isPositiveInteger(filters.difficultyMin) ||
      !isPositiveInteger(filters.difficultyMax) ||
      Number(filters.difficultyMin) > Number(filters.difficultyMax) ||
      Number(filters.difficultyMax) > 10
    ) {
      throw new Error('Invalid question filters')
    }
    parameters.set('difficulty_min', String(filters.difficultyMin))
    parameters.set('difficulty_max', String(filters.difficultyMax))
  }
  appendTexts(parameters, 'question_types', filters.questionTypes)
  appendIds(parameters, 'paper_ids', filters.paperIds)
  appendTexts(parameters, 'years', filters.years)
  appendTexts(parameters, 'exam_types', filters.examTypes)
  appendTexts(parameters, 'grades', filters.grades)
  appendTexts(parameters, 'exam_scopes', filters.examScopes)
  parameters.set('tag_status', filters.tagStatus ?? 'all')
  parameters.set('sort', filters.sort ?? 'newest')
  return `/api/question-bank/questions?${parameters.toString()}`
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
  if (result.length === 0 || result.length > 500) {
    throw new Error('Invalid question ids')
  }
  return result
}

export function questionJobFailures(result: Record<string, unknown>): QuestionJobFailure[] {
  if (!Array.isArray(result.failures)) return []
  const failures: QuestionJobFailure[] = []
  for (const item of result.failures) {
    if (
      !isRecord(item)
      || !isPositiveInteger(item.question_id)
      || typeof item.category !== 'string'
      || !item.category.trim()
      || typeof item.message !== 'string'
    ) continue
    failures.push({
      question_id: item.question_id,
      category: item.category,
      message: item.message,
    })
  }
  return failures
}

function spreadsheetSafe(value: string): string {
  return /^[=+\-@]/.test(value) ? `\t${value}` : value
}

function csvCell(value: string | number): string {
  const text = spreadsheetSafe(String(value)).replace(/"/g, '""')
  return /[",\r\n\t]/.test(text) ? `"${text}"` : text
}

export function questionJobFailuresCsv(result: Record<string, unknown>): string {
  const rows = questionJobFailures(result).map((failure) => [
    failure.question_id,
    failure.category,
    failure.message,
  ])
  return [
    ['题目ID', '失败分类', '说明'],
    ...rows,
  ].map((row) => row.map(csvCell).join(',')).join('\r\n')
}

export const questionBankApi = {
  listPapers(signal?: AbortSignal): Promise<QuestionBankPaperListResponse> {
    return apiClient.request('/api/question-bank/papers', {
      decode: decodeQuestionPaperListResponse,
      signal,
    })
  },

  listQuestions(
    filters: QuestionBankFilters = {},
    signal?: AbortSignal,
  ): Promise<QuestionBankListResponse> {
    return apiClient.request(questionListPath(filters), {
      decode: decodeQuestionListResponse,
      signal,
    })
  },

  getQuestion(
    questionId: number,
    signal?: AbortSignal,
  ): Promise<QuestionBankDetail> {
    if (!isPositiveInteger(questionId)) throw new Error('Invalid question id')
    return apiClient.request(`/api/question-bank/questions/${questionId}`, {
      decode: decodeQuestionDetailResponse,
      signal,
    })
  },

  replaceTags(
    questionId: number,
    expectedRevision: string,
    tags: QuestionBankTag[],
    signal?: AbortSignal,
  ): Promise<QuestionBankWriteResult> {
    if (
      !isPositiveInteger(questionId) ||
      !isRevision(expectedRevision) ||
      tags.length > 100 ||
      !tags.every(isQuestionBankTag)
    ) {
      throw new Error('Invalid question tag write')
    }
    return apiClient.request(`/api/question-bank/questions/${questionId}/tags`, {
      method: 'PUT',
      body: {
        expected_revision: expectedRevision,
        tags,
      },
      decode: decodeQuestionWriteResult,
      signal,
    })
  },

  softDelete(
    questionId: number,
    expectedRevision: string,
    signal?: AbortSignal,
  ): Promise<QuestionBankWriteResult> {
    if (!isPositiveInteger(questionId) || !isRevision(expectedRevision)) {
      throw new Error('Invalid question state write')
    }
    return apiClient.request(`/api/question-bank/questions/${questionId}`, {
      method: 'DELETE',
      body: { expected_revision: expectedRevision },
      decode: decodeQuestionWriteResult,
      signal,
    })
  },

  restore(
    questionId: number,
    expectedRevision: string,
    signal?: AbortSignal,
  ): Promise<QuestionBankWriteResult> {
    if (!isPositiveInteger(questionId) || !isRevision(expectedRevision)) {
      throw new Error('Invalid question state write')
    }
    return apiClient.request(`/api/question-bank/questions/${questionId}/restore`, {
      method: 'POST',
      body: { expected_revision: expectedRevision },
      decode: decodeQuestionWriteResult,
      signal,
    })
  },

  stageImport(
    file: File,
    signal?: AbortSignal,
  ): Promise<QuestionImportUpload> {
    const suffix = file.name.slice(file.name.lastIndexOf('.')).toLowerCase()
    if (
      !file.name.trim() ||
      !['.docx', '.pdf'].includes(suffix) ||
      file.size <= 0 ||
      file.size > 200 * 1024 * 1024
    ) {
      throw new Error('Invalid question import file')
    }
    const parameters = new URLSearchParams({ filename: file.name })
    return apiClient.request(
      `/api/question-bank/import-uploads?${parameters.toString()}`,
      {
        method: 'POST',
        rawBody: file,
        headers: file.type ? { 'content-type': file.type } : undefined,
        decode: decodeQuestionImportUpload,
        signal,
        timeoutMs: 120_000,
      },
    )
  },

  createImportRequest(
    uploadId: string,
    signal?: AbortSignal,
  ): Promise<QuestionImportRequest> {
    if (!isHex(uploadId, 32)) throw new Error('Invalid question import upload id')
    return apiClient.request('/api/question-bank/import-requests', {
      method: 'POST',
      body: { upload_id: uploadId },
      decode: decodeQuestionImportRequest,
      signal,
    })
  },

  submitImportJob(
    requestId: string,
    signal?: AbortSignal,
  ): Promise<JobResponse> {
    if (!isHex(requestId, 32)) throw new Error('Invalid question import request id')
    return apiClient.request(
      `/api/question-bank/import-requests/${requestId}/jobs`,
      {
        method: 'POST',
        decode: decodeJobResponse,
        signal,
      },
    )
  },

  retryImport(
    jobId: number,
    signal?: AbortSignal,
  ): Promise<JobResponse> {
    if (!isPositiveInteger(jobId)) throw new Error('Invalid import job id')
    return apiClient.request(
      `/api/question-bank/question-import-jobs/${jobId}/retry`,
      {
        method: 'POST',
        decode: decodeJobResponse,
        signal,
      },
    )
  },

  submitTagging(
    questionIds: readonly number[],
    sourceJobId?: number,
    signal?: AbortSignal,
  ): Promise<JobResponse> {
    const body: { question_ids: number[]; source_job_id?: number } = {
      question_ids: normalizedQuestionIds(questionIds),
    }
    if (sourceJobId !== undefined) {
      if (!isPositiveInteger(sourceJobId)) throw new Error('Invalid source job id')
      body.source_job_id = sourceJobId
    }
    return apiClient.request('/api/question-bank/tagging-jobs', {
      method: 'POST',
      body,
      decode: decodeJobResponse,
      signal,
    })
  },

  retryTagging(
    jobId: number,
    questionIds?: readonly number[],
    signal?: AbortSignal,
  ): Promise<JobResponse> {
    if (!isPositiveInteger(jobId)) throw new Error('Invalid tagging job id')
    return apiClient.request(
      `/api/question-bank/tagging-jobs/${jobId}/retry`,
      {
        method: 'POST',
        body: questionIds === undefined
          ? {}
          : { question_ids: normalizedQuestionIds(questionIds) },
        decode: decodeJobResponse,
        signal,
      },
    )
  },
}
