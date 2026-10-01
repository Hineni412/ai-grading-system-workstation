import { apiClient } from './client'
import { assertNoPathLikeKeys, isNullableString, isRecord } from './validation'
import { decodeJobResponse, type JobResponse } from './jobs'

export const QUESTION_BANK_TAG_TYPES = [
  'ability',
  'canonical_knowledge_id',
  'curriculum_section',
  'error_type',
  'exam_scope',
  'knowledge_point',
  'measured_skill_name',
  'method',
  'model',
  'special_type',
  'prerequisite',
  'skill',
  'student_level',
  'sub_skill',
  'supporting_skill_name',
  'thought',
  'teaching_stage',
] as const

export type QuestionBankTagType = (typeof QUESTION_BANK_TAG_TYPES)[number]

export interface QuestionBankTag {
  tag_type: QuestionBankTagType
  tag_value: string
  confidence: number | null
}

// 知识点标签值是"册｜章｜小节｜细分点"全路径；界面只显示最末端节点名，
// 完整路径保留在标签值本身（筛选、跨模块契约都依赖全名）。
export function knowledgeLeafLabel(value: string): string {
  const parts = value.split(/[|｜/]/).map((part) => part.trim()).filter(Boolean)
  return parts[parts.length - 1] ?? value
}

export function skillLeafLabel(value: string): string {
  return knowledgeLeafLabel(value).replace(/^技能·/, '')
}

// 解答题的"画图/计算/证明"子类是 special_type 标签，不是题型枚举；
// 题型显示时把命中的子类标签并到题型后（如"解答题 · 证明"）。
export const ESSAY_SUBTYPE_TAGS = ['画图', '计算', '证明'] as const

export function questionTypeWithSubtype(
  questionType: string | null | undefined,
  tags: QuestionBankTag[],
): string {
  const base = (questionType ?? '').trim() || '未分类'
  const subtype = tags.find((tag) => (
    tag.tag_type === 'special_type'
    && (ESSAY_SUBTYPE_TAGS as readonly string[]).includes(tag.tag_value)
  ))?.tag_value
  return subtype ? `${base} · ${subtype}` : base
}

export interface QuestionBankListItem {
  id: number
  duplicate_of_question_id?: number | null
  duplicate_labels_reused?: boolean
  evidence_point_count?: number
  duplicate_members?: { id: number; paper_id: number | null; question_number: string; paper_title: string | null }[]
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
  criteria_needs_review: boolean
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
  /**
   * Older in-memory fixtures and cached caller objects may not include the
   * structured preview yet. Network responses remain strict at decode time.
   */
  rich_content?: QuestionBankRichContent
  skills?: { stable_key: string; display_name: string }[]
  skill_hits?: { point_id: string; point_label: string }[]
}

export interface QuestionBankListResponse {
  items: QuestionBankListItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface QuestionBankQuestionRef {
  id: number
  paper_id: number
  question_number: string
}

export interface QuestionBankQuestionRefListResponse {
  items: QuestionBankQuestionRef[]
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
  folder_name: string | null
  textbook_version: string | null
  curriculum_volume_id: string | null
  import_status: string | null
  created_at: string
  updated_at: string
  question_count: number
  tagged_question_count: number
  tagged_any_question_count: number
  evidence_question_count: number
  criteria_question_count: number
  criteria_needs_review_count: number
  complete_analysis_count: number
  skill_unlinked_question_count?: number
  source_type: 'docx' | 'pdf' | 'other'
}

export interface QuestionBankPaperListResponse {
  items: QuestionBankPaper[]
  total: number
}

export interface QuestionBankPaperMetadataInput {
  title: string
  year: string | null
  province: string | null
  city: string | null
  district: string | null
  exam_type: string | null
  grade: string | null
  semester: string | null
  folder_name: string | null
  textbook_version: string | null
}

export interface QuestionBankPaperMetadataResult
  extends QuestionBankPaperMetadataInput {
  id: number
  updated_at: string
}

export interface QuestionBankPaperPermanentDeleteSelection {
  id: number
  expected_updated_at: string
}

export interface QuestionBankPaperPermanentDeleteImpact {
  paper_count: number
  question_count: number
  tag_count: number
  analysis_record_count: number
  training_link_count: number
  knowledge_graph_link_count: number
  owned_file_count: number
  shared_file_count: number
  taxonomy_proposal_count: number
  permanent_delete_phrase: string
}

export interface QuestionBankPaperPermanentDeleteResult {
  deleted_paper_ids: number[]
  deleted_question_count: number
  deleted_tag_count: number
  deleted_analysis_record_count: number
  removed_training_link_count: number
  removed_knowledge_graph_link_count: number
  deleted_file_count: number
  skipped_shared_file_count: number
  storage_cleanup_pending: boolean
}

export type CurriculumSectionKind =
  | 'activity'
  | 'activity_group'
  | 'exercise'
  | 'lesson'
  | 'optional_lesson'
  | 'reflection'
  | 'review'

export interface CurriculumSourceRef {
  node_id: string
  relative_url: string
}

export interface CurriculumKnowledgePoint {
  id: string
  order: number
  label: string
  display_name: string
  parent_knowledge_id: string
  source_ref: CurriculumSourceRef
}

export interface CurriculumSection {
  id: string
  knowledge_id: string
  order: number
  number: string | null
  title: string
  label: string
  kind: CurriculumSectionKind
  display_name: string
  source_ref: CurriculumSourceRef
  knowledge_points: CurriculumKnowledgePoint[]
}

export interface CurriculumChapter {
  id: string
  knowledge_id: string
  order: number
  number: string | null
  title: string
  label: string
  kind: 'chapter' | 'activity'
  display_name: string
  source_ref: CurriculumSourceRef
  exam_scope_values: string[]
  sections: CurriculumSection[]
}

export interface CurriculumVolumeStatistics {
  raw_nodes: number
  excluded_nodes: number
  retained_nodes: number
}

export interface CurriculumCatalogStatistics extends CurriculumVolumeStatistics {
  chapters: number
  sections: number
  knowledge_points: number
}

export interface CurriculumVolume {
  id: string
  order: number
  label: string
  grade: string
  semester: string
  textbook_version: string
  source: Record<string, unknown>
  statistics: CurriculumVolumeStatistics
  chapters: CurriculumChapter[]
}

export interface CurriculumCatalog {
  schema_version: 2
  catalog_id: string
  knowledge_standard_id: string
  publisher: string
  subject: string
  edition: string
  statistics: CurriculumCatalogStatistics
  volumes: CurriculumVolume[]
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

export interface QuestionBankRichInlineSegment {
  text: string
  superscript: boolean
  subscript: boolean
  underline: boolean
  line_break: boolean
}

export interface QuestionBankRichTableCell {
  segments: QuestionBankRichInlineSegment[]
}

export interface QuestionBankRichTableRow {
  cells: QuestionBankRichTableCell[]
}

export interface QuestionBankRichBlock {
  /**
   * Optional only for legacy caller-owned objects. API decoders still require
   * the complete structured shape before accepting a server response.
   */
  kind?: 'paragraph' | 'table'
  text: string
  segments?: QuestionBankRichInlineSegment[]
  /**
   * Controlled HTML projected from the frozen Word XML: text runs keep their
   * styling, images stay inline in document order, formulas are
   * KaTeX-hydratable `.qm[data-latex]` spans. Empty for blocks whose XML is
   * unavailable or drifted from the stored text — renderers must then fall
   * back to `segments`/`rows`.
   */
  html?: string
  rows?: QuestionBankRichTableRow[]
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

export interface QuestionBankFacet {
  value: string
  count: number
}

export interface QuestionBankFacets {
  exam_scopes: QuestionBankFacet[]
  curriculum_sections: QuestionBankFacet[]
  knowledge_points: QuestionBankFacet[]
  curriculum_chapters: QuestionBankFacet[]
  abilities: QuestionBankFacet[]
  methods: QuestionBankFacet[]
  thoughts: QuestionBankFacet[]
  models: QuestionBankFacet[]
  special_types: QuestionBankFacet[]
  error_types: QuestionBankFacet[]
  student_levels: QuestionBankFacet[]
  teaching_stages: QuestionBankFacet[]
  sub_skills: QuestionBankFacet[]
  question_types: QuestionBankFacet[]
  years: QuestionBankFacet[]
  exam_types: QuestionBankFacet[]
  grades: QuestionBankFacet[]
}

// 相似题推荐理由按维度分组：标签类维度的 values 是原始标签值
// （知识点/技能为全路径，展示时取叶子名），信号类维度的 values 是展示短语。
export type SimilarityReasonKind =
  | 'knowledge_point'
  | 'skill'
  | 'method'
  | 'model'
  | 'difficulty'
  | 'wording'
  | 'question_type'
  | 'text_fragment'

export interface SimilarityReason {
  kind: SimilarityReasonKind
  values: string[]
}

export interface SimilarQuestionItem extends QuestionBankListItem {
  similarity_score: number
  similarity_reasons: SimilarityReason[]
}

export interface SimilarQuestionResponse {
  question_id: number
  items: SimilarQuestionItem[]
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
  error_patterns?: QuestionErrorPattern[]
  wrong_option_letters?: string[]
  selectable_skills?: QuestionBankSkillOption[]
}

export interface QuestionBankSkillOption {
  key: string
  label: string
}

export interface QuestionErrorPattern {
  id: number
  category: string | null
  pattern: string
  explanation: string
  trigger_kind: 'option' | 'wrong_answer' | 'step' | 'observation'
  trigger_value: string
  status: 'candidate' | 'confirmed'
  source: string
  has_evidence: boolean
  skill_key?: string | null
  skill_label?: string | null
  skill_keys?: string[]
  skill_labels?: string[]
  skill_source?: 'teacher' | 'criterion' | 'question' | null
}

export type CoreResolutionStatus = 'resolved' | 'ambiguous' | 'unmapped'
export type FineTermRole = 'direct' | 'supporting_prerequisite'

export interface SolutionEvidenceCoreResolution {
  status: CoreResolutionStatus
  stable_keys: string[]
  reason: string
}

export interface SolutionEvidenceFineTermLink {
  fine_term_id: string
  fine_term_name: string
  role: FineTermRole
  core_resolution: SolutionEvidenceCoreResolution
}

export interface SolutionEvidencePoint {
  evidence_point_id: string
  target: string
  observable_evidence: string
  fine_term_links: SolutionEvidenceFineTermLink[]
  equivalent_rules: string[]
  counterexamples: string[]
}

export interface SolutionEvidencePart {
  part_id: string
  label: string
  response_mode: 'exact_objective' | 'short_answer_points' | 'process_required' | 'visual_construction'
  canonical_answer: string
  accepted_forms: string[]
  full_answer: string
  proof_obligations: string[]
  visual_requirements: string[]
  deduction_policy: string[]
  allow_alternative_methods: boolean
  evidence_points: SolutionEvidencePoint[]
}

export interface WholeQuestionClassification {
  direct_fine_terms: Array<{ fine_term_id: string; fine_term_name: string }>
  supporting_prerequisite_fine_terms: Array<{ fine_term_id: string; fine_term_name: string }>
  direct_resolved_core_node_ids: string[]
  supporting_resolved_core_node_ids: string[]
  direct_ambiguous_core_node_ids: string[]
  supporting_ambiguous_core_node_ids: string[]
  direct_unmapped_fine_term_ids: string[]
  supporting_unmapped_fine_term_ids: string[]
  resolved_core_node_ids: string[]
  ambiguous_core_node_ids: string[]
  unmapped_fine_term_ids: string[]
}

export interface QuestionSolutionEvidence {
  schema_version: 'question-solution-evidence-v1' | 'question-solution-evidence-v2'
  question_id: number
  source_content_hash: string
  parts: SolutionEvidencePart[]
  auxiliary_rules: string[]
  rationale: string
  confidence: number
  content_hash: string
  version_id: string
  whole_question_classification: WholeQuestionClassification
}

export interface QuestionSolutionEvidenceResponse {
  question_id: number
  available: boolean
  evidence_version_id: string | null
  status: 'proposed' | 'approved' | 'rejected' | 'superseded' | 'stale' | null
  evidence: QuestionSolutionEvidence | null
  part_assessments?: Array<{ part_id: string; difficulty: number | null; source: string; rationale: string; formula_version?: string; review_note?: string }>
  assessment_revision?: string | null
}

export type QuestionBankTagStatus = 'all' | 'tagged' | 'untagged'
export type QuestionBankAnalysisStatus = 'all' | 'complete' | 'incomplete'
export type QuestionBankSort =
  | 'paper_order'
  | 'difficulty_desc'
  | 'difficulty_asc'
  | 'frequency_desc'
  | 'frequency_asc'

export interface QuestionBankFilters {
  skillKeys?: string[]
  skillUnlinked?: boolean
  includeSkills?: boolean
  page?: number
  pageSize?: number
  questionNumber?: string
  keyword?: string
  knowledgePoint?: string
  knowledgePoints?: string[]
  abilities?: string[]
  methods?: string[]
  thoughts?: string[]
  models?: string[]
  specialTypes?: string[]
  errorTypes?: string[]
  studentLevels?: string[]
  teachingStages?: string[]
  subSkills?: string[]
  difficultyMin?: number
  difficultyMax?: number
  questionTypes?: string[]
  paperIds?: number[]
  years?: string[]
  examTypes?: string[]
  grades?: string[]
  curriculumVolumeIds?: string[]
  examScopes?: string[]
  curriculumSections?: string[]
  tagStatus?: QuestionBankTagStatus
  analysisStatus?: QuestionBankAnalysisStatus
  sort?: QuestionBankSort
  criteriaNeedsReview?: boolean
  teachingProgressChapter?: string
  collapseDuplicates?: boolean
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
  'criteria_needs_review',
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
  'rich_content',
] as const

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value)
  return actual.length === keys.length && keys.every(
    (key) => Object.prototype.hasOwnProperty.call(value, key),
  )
}

function hasExactQuestionKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const base = Object.fromEntries(Object.entries(value).filter(([key]) =>
    !['duplicate_of_question_id', 'duplicate_labels_reused', 'skills', 'skill_hits', 'evidence_point_count', 'duplicate_members'].includes(key)))
  return hasExactKeys(base, keys)
}

function hasRequiredKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return keys.every((key) => Object.prototype.hasOwnProperty.call(value, key))
}

function isNonnegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
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
  if (!isRecord(value) || !hasExactQuestionKeys(value, QUESTION_LIST_KEYS)) return false
  return hasQuestionBankListFields(value)
}

function hasQuestionBankListFields(value: Record<string, unknown>): boolean {
  return (
    (value.evidence_point_count === undefined || isNonnegativeInteger(value.evidence_point_count)) &&
    (value.duplicate_members === undefined || (Array.isArray(value.duplicate_members) && value.duplicate_members.every(member => isRecord(member) && hasExactKeys(member, ['id', 'paper_id', 'question_number', 'paper_title']) && isPositiveInteger(member.id) && isNullableInteger(member.paper_id) && typeof member.question_number === 'string' && isNullableString(member.paper_title)))) &&
    (value.skills === undefined || (Array.isArray(value.skills) && value.skills.every((item) =>
      isRecord(item) && hasExactKeys(item, ['stable_key', 'display_name'])
      && typeof item.stable_key === 'string' && item.stable_key.startsWith('sk_') && typeof item.display_name === 'string'))) &&
    (value.skill_hits === undefined || (Array.isArray(value.skill_hits) && value.skill_hits.every((item) =>
      isRecord(item) && hasExactKeys(item, ['point_id', 'point_label'])
      && typeof item.point_id === 'string' && typeof item.point_label === 'string'))) &&
    isPositiveInteger(value.id) &&
    (value.duplicate_of_question_id === undefined || value.duplicate_of_question_id === null || isPositiveInteger(value.duplicate_of_question_id)) &&
    (value.duplicate_labels_reused === undefined || typeof value.duplicate_labels_reused === 'boolean') &&
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
    typeof value.criteria_needs_review === 'boolean' &&
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
    value.asset_urls.every((url) => url.startsWith('/api/question-bank/')) &&
    isQuestionBankRichContent(value.rich_content)
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

function isRichInlineSegment(value: unknown): value is QuestionBankRichInlineSegment {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'text',
      'superscript',
      'subscript',
      'underline',
      'line_break',
    ]) &&
    typeof value.text === 'string' &&
    typeof value.superscript === 'boolean' &&
    typeof value.subscript === 'boolean' &&
    typeof value.underline === 'boolean' &&
    typeof value.line_break === 'boolean'
  )
}

function isRichTableCell(value: unknown): value is QuestionBankRichTableCell {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['segments']) &&
    Array.isArray(value.segments) &&
    value.segments.every(isRichInlineSegment)
  )
}

function isRichTableRow(value: unknown): value is QuestionBankRichTableRow {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['cells']) &&
    Array.isArray(value.cells) &&
    value.cells.every(isRichTableCell)
  )
}

function isRichBlock(value: unknown): value is QuestionBankRichBlock {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'kind',
      'text',
      'segments',
      'rows',
      'html',
      'asset_indexes',
      'asset_urls',
    ]) &&
    (value.kind === 'paragraph' || value.kind === 'table') &&
    typeof value.text === 'string' &&
    Array.isArray(value.segments) &&
    value.segments.every(isRichInlineSegment) &&
    Array.isArray(value.rows) &&
    value.rows.every(isRichTableRow) &&
    typeof value.html === 'string' &&
    Array.isArray(value.asset_indexes) &&
    value.asset_indexes.every(isNonnegativeInteger) &&
    Array.isArray(value.asset_urls) &&
    value.asset_urls.every(isControlledQuestionUrl)
  )
}

export function isQuestionBankRichContent(value: unknown): value is QuestionBankRichContent {
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

const QUESTION_REF_KEYS = ['id', 'paper_id', 'question_number'] as const

function isQuestionBankQuestionRef(value: unknown): value is QuestionBankQuestionRef {
  return isRecord(value)
    && hasExactKeys(value, QUESTION_REF_KEYS)
    && isPositiveInteger(value.id)
    && isPositiveInteger(value.paper_id)
    && typeof value.question_number === 'string'
}

export function decodeQuestionRefListResponse(
  value: unknown,
): QuestionBankQuestionRefListResponse {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['items', 'total', 'page', 'page_size', 'total_pages']) ||
    !Array.isArray(value.items) ||
    !value.items.every(isQuestionBankQuestionRef) ||
    !isNonnegativeInteger(value.total) ||
    !isPositiveInteger(value.page) ||
    !isPositiveInteger(value.page_size) ||
    !isNonnegativeInteger(value.total_pages) ||
    value.items.length > Number(value.page_size)
  ) {
    throw new Error('Invalid question ref list')
  }
  return value as unknown as QuestionBankQuestionRefListResponse
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
    hasExactKeys(Object.fromEntries(Object.entries(value).filter(([key]) => key !== 'skill_unlinked_question_count')), [
      'id',
      'title',
      'year',
      'province',
      'city',
      'district',
      'exam_type',
      'grade',
      'semester',
      'folder_name',
      'textbook_version',
      'curriculum_volume_id',
      'import_status',
      'created_at',
      'updated_at',
      'question_count',
      'tagged_question_count',
      'tagged_any_question_count',
      'evidence_question_count',
      'criteria_question_count',
      'criteria_needs_review_count',
      'complete_analysis_count',
      'source_type',
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
    isNullableString(value.folder_name) &&
    isNullableString(value.textbook_version) &&
    isNullableString(value.curriculum_volume_id) &&
    isNullableString(value.import_status) &&
    typeof value.created_at === 'string' &&
    typeof value.updated_at === 'string' &&
    isNonnegativeInteger(value.question_count) &&
    isNonnegativeInteger(value.tagged_question_count) &&
    Number(value.tagged_question_count) <= Number(value.question_count) &&
    isNonnegativeInteger(value.tagged_any_question_count) &&
    Number(value.tagged_any_question_count) <= Number(value.question_count) &&
    Number(value.tagged_question_count) <= Number(value.tagged_any_question_count) &&
    isNonnegativeInteger(value.evidence_question_count) &&
    Number(value.evidence_question_count) <= Number(value.question_count) &&
    isNonnegativeInteger(value.criteria_question_count) &&
    Number(value.criteria_question_count) <= Number(value.question_count) &&
    isNonnegativeInteger(value.criteria_needs_review_count) &&
    Number(value.criteria_needs_review_count) <= Number(value.question_count) &&
    isNonnegativeInteger(value.complete_analysis_count) &&
    (value.skill_unlinked_question_count === undefined || (isNonnegativeInteger(value.skill_unlinked_question_count)
      && value.skill_unlinked_question_count <= Number(value.question_count))) &&
    Number(value.complete_analysis_count) <= Number(value.question_count) &&
    (value.source_type === 'docx' || value.source_type === 'pdf' || value.source_type === 'other')
  )
}

function isQuestionBankPaperMetadataResult(
  value: unknown,
): value is QuestionBankPaperMetadataResult {
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
      'folder_name',
      'textbook_version',
      'updated_at',
    ]) &&
    isPositiveInteger(value.id) &&
    typeof value.title === 'string' &&
    value.title.trim().length > 0 &&
    isNullableString(value.year) &&
    isNullableString(value.province) &&
    isNullableString(value.city) &&
    isNullableString(value.district) &&
    isNullableString(value.exam_type) &&
    isNullableString(value.grade) &&
    isNullableString(value.semester) &&
    isNullableString(value.folder_name) &&
    isNullableString(value.textbook_version) &&
    typeof value.updated_at === 'string' &&
    value.updated_at.trim().length > 0
  )
}

function normalizePaperMetadata(
  metadata: QuestionBankPaperMetadataInput,
): QuestionBankPaperMetadataInput {
  const title = String(metadata.title ?? '').trim()
  if (!title || title.length > 255) {
    throw new Error('Invalid paper metadata')
  }
  const optional = (value: string | null, maxLength: number): string | null => {
    const clean = String(value ?? '').trim()
    if (clean.length > maxLength) throw new Error('Invalid paper metadata')
    return clean || null
  }
  return {
    title,
    year: optional(metadata.year, 24),
    province: optional(metadata.province, 48),
    city: optional(metadata.city, 48),
    district: optional(metadata.district, 48),
    exam_type: optional(metadata.exam_type, 48),
    grade: optional(metadata.grade, 48),
    semester: optional(metadata.semester, 48),
    folder_name: optional(metadata.folder_name, 80),
    textbook_version: optional(metadata.textbook_version, 100),
  }
}

function isFacet(value: unknown): value is QuestionBankFacet {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['value', 'count']) &&
    typeof value.value === 'string' &&
    value.value.trim().length > 0 &&
    isNonnegativeInteger(value.count)
  )
}

const CURRICULUM_SECTION_KINDS = [
  'activity',
  'activity_group',
  'exercise',
  'lesson',
  'optional_lesson',
  'reflection',
  'review',
] as const satisfies readonly CurriculumSectionKind[]

function isCurriculumSourceRef(value: unknown): value is CurriculumSourceRef {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['node_id', 'relative_url']) &&
    typeof value.node_id === 'string' &&
    value.node_id.trim().length > 0 &&
    typeof value.relative_url === 'string' &&
    value.relative_url.trim().length > 0
  )
}

function isCurriculumKnowledgePoint(value: unknown): value is CurriculumKnowledgePoint {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'id',
      'order',
      'label',
      'display_name',
      'parent_knowledge_id',
      'source_ref',
    ]) &&
    typeof value.id === 'string' &&
    value.id.trim().length > 0 &&
    isPositiveInteger(value.order) &&
    typeof value.label === 'string' &&
    value.label.trim().length > 0 &&
    typeof value.display_name === 'string' &&
    value.display_name.trim().length > 0 &&
    typeof value.parent_knowledge_id === 'string' &&
    value.parent_knowledge_id.trim().length > 0 &&
    isCurriculumSourceRef(value.source_ref)
  )
}

function isCurriculumVolumeStatistics(
  value: unknown,
): value is CurriculumVolumeStatistics {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['raw_nodes', 'excluded_nodes', 'retained_nodes']) &&
    isNonnegativeInteger(value.raw_nodes) &&
    isNonnegativeInteger(value.excluded_nodes) &&
    isNonnegativeInteger(value.retained_nodes)
  )
}

function isCurriculumCatalogStatistics(
  value: unknown,
): value is CurriculumCatalogStatistics {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'raw_nodes',
      'excluded_nodes',
      'retained_nodes',
      'chapters',
      'sections',
      'knowledge_points',
    ]) &&
    isNonnegativeInteger(value.raw_nodes) &&
    isNonnegativeInteger(value.excluded_nodes) &&
    isNonnegativeInteger(value.retained_nodes) &&
    isNonnegativeInteger(value.chapters) &&
    isNonnegativeInteger(value.sections) &&
    isNonnegativeInteger(value.knowledge_points)
  )
}

function isCurriculumSection(value: unknown): value is CurriculumSection {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'id',
      'knowledge_id',
      'order',
      'number',
      'title',
      'label',
      'kind',
      'display_name',
      'source_ref',
      'knowledge_points',
    ]) &&
    typeof value.id === 'string' &&
    value.id.trim().length > 0 &&
    typeof value.knowledge_id === 'string' &&
    value.knowledge_id.trim().length > 0 &&
    isPositiveInteger(value.order) &&
    isNullableString(value.number) &&
    typeof value.title === 'string' &&
    value.title.trim().length > 0 &&
    typeof value.label === 'string' &&
    value.label.trim().length > 0 &&
    typeof value.kind === 'string' &&
    CURRICULUM_SECTION_KINDS.some((kind) => kind === value.kind) &&
    typeof value.display_name === 'string' &&
    value.display_name.trim().length > 0 &&
    isCurriculumSourceRef(value.source_ref) &&
    Array.isArray(value.knowledge_points) &&
    value.knowledge_points.every(isCurriculumKnowledgePoint)
  )
}

function isCurriculumChapter(value: unknown): value is CurriculumChapter {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'id',
      'knowledge_id',
      'order',
      'number',
      'title',
      'label',
      'kind',
      'display_name',
      'source_ref',
      'exam_scope_values',
      'sections',
    ]) &&
    typeof value.id === 'string' &&
    value.id.trim().length > 0 &&
    typeof value.knowledge_id === 'string' &&
    value.knowledge_id.trim().length > 0 &&
    isPositiveInteger(value.order) &&
    isNullableString(value.number) &&
    typeof value.title === 'string' &&
    value.title.trim().length > 0 &&
    typeof value.label === 'string' &&
    value.label.trim().length > 0 &&
    (value.kind === 'chapter' || value.kind === 'activity') &&
    typeof value.display_name === 'string' &&
    value.display_name.trim().length > 0 &&
    isCurriculumSourceRef(value.source_ref) &&
    isStringArray(value.exam_scope_values) &&
    value.exam_scope_values.length > 0 &&
    value.exam_scope_values.every((item) => item.trim().length > 0) &&
    Array.isArray(value.sections) &&
    value.sections.every(isCurriculumSection)
  )
}

function isCurriculumVolume(value: unknown): value is CurriculumVolume {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'id',
      'order',
      'label',
      'grade',
      'semester',
      'textbook_version',
      'source',
      'statistics',
      'chapters',
    ]) &&
    typeof value.id === 'string' &&
    value.id.trim().length > 0 &&
    isPositiveInteger(value.order) &&
    typeof value.label === 'string' &&
    value.label.trim().length > 0 &&
    typeof value.grade === 'string' &&
    value.grade.trim().length > 0 &&
    typeof value.semester === 'string' &&
    value.semester.trim().length > 0 &&
    typeof value.textbook_version === 'string' &&
    value.textbook_version.trim().length > 0 &&
    isRecord(value.source) &&
    isCurriculumVolumeStatistics(value.statistics) &&
    Array.isArray(value.chapters) &&
    value.chapters.length > 0 &&
    value.chapters.every(isCurriculumChapter)
  )
}

export function decodeCurriculumCatalog(value: unknown): CurriculumCatalog {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      'schema_version',
      'catalog_id',
      'knowledge_standard_id',
      'publisher',
      'subject',
      'edition',
      'statistics',
      'volumes',
    ]) ||
    value.schema_version !== 2 ||
    typeof value.catalog_id !== 'string' ||
    value.catalog_id.trim().length === 0 ||
    typeof value.knowledge_standard_id !== 'string' ||
    value.knowledge_standard_id.trim().length === 0 ||
    typeof value.publisher !== 'string' ||
    value.publisher.trim().length === 0 ||
    typeof value.subject !== 'string' ||
    value.subject.trim().length === 0 ||
    typeof value.edition !== 'string' ||
    value.edition.trim().length === 0 ||
    !isCurriculumCatalogStatistics(value.statistics) ||
    !Array.isArray(value.volumes) ||
    value.volumes.length !== 5 ||
    !value.volumes.every(isCurriculumVolume)
  ) {
    throw new Error('Invalid curriculum catalog')
  }
  return value as unknown as CurriculumCatalog
}

export function decodeQuestionBankFacets(value: unknown): QuestionBankFacets {
  const keys = [
    'exam_scopes',
    'curriculum_sections',
    'knowledge_points',
    'curriculum_chapters',
    'abilities',
    'methods',
    'thoughts',
    'models',
    'special_types',
    'error_types',
    'student_levels',
    'teaching_stages',
    'sub_skills',
    'question_types',
    'years',
    'exam_types',
    'grades',
  ] as const
  if (
    !isRecord(value) ||
    !hasExactKeys(value, keys) ||
    !keys.every((key) => Array.isArray(value[key]) && value[key].every(isFacet))
  ) {
    throw new Error('Invalid question bank facets')
  }
  return value as unknown as QuestionBankFacets
}

const SIMILARITY_REASON_KINDS: ReadonlySet<string> = new Set([
  'knowledge_point',
  'skill',
  'method',
  'model',
  'difficulty',
  'wording',
  'question_type',
  'text_fragment',
])

function isSimilarityReason(value: unknown): value is SimilarityReason {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['kind', 'values']) &&
    typeof value.kind === 'string' &&
    SIMILARITY_REASON_KINDS.has(value.kind) &&
    isStringArray(value.values)
  )
}

function isSimilarQuestion(value: unknown): value is SimilarQuestionItem {
  if (
    !isRecord(value) ||
    !hasExactQuestionKeys(value, [
      ...QUESTION_LIST_KEYS,
      'similarity_score',
      'similarity_reasons',
    ]) ||
    !hasQuestionBankListFields(value) ||
    typeof value.similarity_score !== 'number' ||
    !Number.isFinite(value.similarity_score) ||
    value.similarity_score < 0 ||
    value.similarity_score > 1 ||
    !Array.isArray(value.similarity_reasons) ||
    !value.similarity_reasons.every(isSimilarityReason)
  ) {
    return false
  }
  return true
}

export function decodeSimilarQuestionResponse(value: unknown): SimilarQuestionResponse {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['question_id', 'items']) ||
    !isPositiveInteger(value.question_id) ||
    !Array.isArray(value.items) ||
    !value.items.every(isSimilarQuestion)
  ) {
    throw new Error('Invalid similar question response')
  }
  return value as unknown as SimilarQuestionResponse
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

export function decodeQuestionBankPaperMetadataResult(
  value: unknown,
): QuestionBankPaperMetadataResult {
  if (!isQuestionBankPaperMetadataResult(value)) {
    throw new Error('Invalid paper metadata result')
  }
  return value
}

export function decodeQuestionBankPaperPermanentDeleteImpact(
  value: unknown,
): QuestionBankPaperPermanentDeleteImpact {
  const countKeys = [
    'question_count', 'tag_count', 'analysis_record_count', 'training_link_count',
    'knowledge_graph_link_count', 'owned_file_count', 'shared_file_count',
    'taxonomy_proposal_count',
  ]
  if (
    !isRecord(value)
    || !hasExactKeys(value, [
      'paper_count', ...countKeys, 'permanent_delete_phrase',
    ])
    || !isPositiveInteger(value.paper_count)
    || !countKeys.every((key) => isNonnegativeInteger(value[key]))
    || typeof value.permanent_delete_phrase !== 'string'
    || !value.permanent_delete_phrase.trim()
  ) {
    throw new Error('Invalid paper permanent deletion impact')
  }
  return value as unknown as QuestionBankPaperPermanentDeleteImpact
}

export function decodeQuestionBankPaperPermanentDeleteResult(
  value: unknown,
): QuestionBankPaperPermanentDeleteResult {
  const countKeys = [
    'deleted_question_count', 'deleted_tag_count', 'deleted_analysis_record_count',
    'removed_training_link_count', 'removed_knowledge_graph_link_count',
    'deleted_file_count', 'skipped_shared_file_count',
  ]
  if (
    !isRecord(value)
    || !hasExactKeys(value, [
      'deleted_paper_ids', ...countKeys, 'storage_cleanup_pending',
    ])
    || !Array.isArray(value.deleted_paper_ids)
    || !value.deleted_paper_ids.every(isPositiveInteger)
    || !countKeys.every((key) => isNonnegativeInteger(value[key]))
    || typeof value.storage_cleanup_pending !== 'boolean'
  ) {
    throw new Error('Invalid paper permanent deletion result')
  }
  return value as unknown as QuestionBankPaperPermanentDeleteResult
}

function isSolutionEvidenceCoreResolution(value: unknown): boolean {
  return isRecord(value)
    && hasExactKeys(value, ['status', 'stable_keys', 'reason'])
    && ['resolved', 'ambiguous', 'unmapped'].includes(String(value.status))
    && isStringArray(value.stable_keys)
    && typeof value.reason === 'string'
}

function isSolutionEvidenceFineTerm(value: unknown): boolean {
  return isRecord(value)
    && hasExactKeys(value, ['fine_term_id', 'fine_term_name', 'role', 'core_resolution'])
    && typeof value.fine_term_id === 'string' && value.fine_term_id.length > 0
    && typeof value.fine_term_name === 'string' && value.fine_term_name.length > 0
    && ['direct', 'supporting_prerequisite'].includes(String(value.role))
    && isSolutionEvidenceCoreResolution(value.core_resolution)
}

function isSolutionEvidencePoint(value: unknown): boolean {
  return isRecord(value)
    && hasRequiredKeys(value, [
      'evidence_point_id', 'target', 'observable_evidence', 'fine_term_links',
      'equivalent_rules', 'counterexamples',
    ])
    && typeof value.evidence_point_id === 'string' && value.evidence_point_id.length > 0
    && typeof value.target === 'string' && value.target.length > 0
    && typeof value.observable_evidence === 'string' && value.observable_evidence.length > 0
    && Array.isArray(value.fine_term_links)
    && value.fine_term_links.every(isSolutionEvidenceFineTerm)
    && isStringArray(value.equivalent_rules) && isStringArray(value.counterexamples)
}

function isSolutionEvidencePart(value: unknown): boolean {
  return isRecord(value)
    && hasExactKeys(value, [
      'part_id', 'label', 'response_mode', 'canonical_answer', 'accepted_forms',
      'full_answer', 'proof_obligations', 'visual_requirements', 'deduction_policy',
      'allow_alternative_methods', 'evidence_points',
    ])
    && typeof value.part_id === 'string' && value.part_id.length > 0
    && typeof value.label === 'string'
    && ['exact_objective', 'short_answer_points', 'process_required', 'visual_construction']
      .includes(String(value.response_mode))
    && typeof value.canonical_answer === 'string'
    && typeof value.full_answer === 'string'
    && isStringArray(value.accepted_forms)
    && isStringArray(value.proof_obligations)
    && isStringArray(value.visual_requirements)
    && isStringArray(value.deduction_policy)
    && typeof value.allow_alternative_methods === 'boolean'
    && Array.isArray(value.evidence_points) && value.evidence_points.length > 0
    && value.evidence_points.every(isSolutionEvidencePoint)
}

const WHOLE_CLASSIFICATION_KEYS = [
  'direct_fine_terms', 'supporting_prerequisite_fine_terms',
  'direct_resolved_core_node_ids', 'supporting_resolved_core_node_ids',
  'direct_ambiguous_core_node_ids', 'supporting_ambiguous_core_node_ids',
  'direct_unmapped_fine_term_ids', 'supporting_unmapped_fine_term_ids',
  'resolved_core_node_ids', 'ambiguous_core_node_ids', 'unmapped_fine_term_ids',
] as const

function isSummaryFineTerm(value: unknown): boolean {
  return isRecord(value)
    && hasExactKeys(value, ['fine_term_id', 'fine_term_name'])
    && typeof value.fine_term_id === 'string'
    && typeof value.fine_term_name === 'string'
}

function isWholeQuestionClassification(value: unknown): boolean {
  if (!isRecord(value) || !hasExactKeys(value, WHOLE_CLASSIFICATION_KEYS)) return false
  return Array.isArray(value.direct_fine_terms)
    && value.direct_fine_terms.every(isSummaryFineTerm)
    && Array.isArray(value.supporting_prerequisite_fine_terms)
    && value.supporting_prerequisite_fine_terms.every(isSummaryFineTerm)
    && WHOLE_CLASSIFICATION_KEYS.slice(2).every((key) => isStringArray(value[key]))
}

function isQuestionSolutionEvidence(value: unknown): boolean {
  return isRecord(value)
    && hasExactKeys(value, [
      'schema_version', 'question_id', 'source_content_hash', 'parts',
      'auxiliary_rules', 'rationale', 'confidence', 'content_hash', 'version_id',
      'whole_question_classification',
    ])
    && ['question-solution-evidence-v1', 'question-solution-evidence-v2']
      .includes(String(value.schema_version))
    && isPositiveInteger(value.question_id)
    && isHex(value.source_content_hash, 64)
    && Array.isArray(value.parts) && value.parts.length > 0
    && value.parts.every(isSolutionEvidencePart)
    && isStringArray(value.auxiliary_rules)
    && typeof value.rationale === 'string'
    && isFiniteNumber(value.confidence) && value.confidence >= 0 && value.confidence <= 1
    && isHex(value.content_hash, 64) && isHex(value.version_id, 64)
    && isWholeQuestionClassification(value.whole_question_classification)
}

export function decodeQuestionSolutionEvidenceResponse(
  value: unknown,
): QuestionSolutionEvidenceResponse {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || !hasExactKeys(value, [
      'question_id', 'available', 'evidence_version_id', 'status', 'evidence',
      ...('part_assessments' in value ? ['part_assessments'] : []),
      ...('assessment_revision' in value ? ['assessment_revision'] : []),
    ])
    || !(value.assessment_revision === undefined || value.assessment_revision === null || typeof value.assessment_revision === 'string')
    || !(value.part_assessments === undefined || (Array.isArray(value.part_assessments)
      && value.part_assessments.every(part => isRecord(part)
        && typeof part.part_id === 'string' && typeof part.source === 'string'
        && typeof part.rationale === 'string'
        && (part.review_note === undefined || typeof part.review_note === 'string')
        && (part.difficulty === null || (isFiniteNumber(part.difficulty) && part.difficulty >= 1 && part.difficulty <= 10)))))
    || !isPositiveInteger(value.question_id)
    || typeof value.available !== 'boolean'
    || !(value.evidence_version_id === null || isHex(value.evidence_version_id, 64))
    || !(value.status === null || ['proposed', 'approved', 'rejected', 'superseded', 'stale']
      .includes(String(value.status)))
    || !(value.evidence === null || isQuestionSolutionEvidence(value.evidence))
    || value.available !== (value.evidence !== null)
  ) {
    throw new Error('Invalid question solution evidence')
  }
  return value as unknown as QuestionSolutionEvidenceResponse
}

export function decodeQuestionDetailResponse(value: unknown): QuestionBankDetail {
  const detailKeys = [
    ...QUESTION_LIST_KEYS,
    'page_range',
    'assets',
    'previews',
  ]
  if (
    !isRecord(value) ||
    !(hasExactQuestionKeys(value, detailKeys)
      || hasExactQuestionKeys(value, [...detailKeys, 'error_patterns', 'wrong_option_letters', 'selectable_skills'])) ||
    !hasQuestionBankListFields(value) ||
    !isNullableString(value.page_range) ||
    !Array.isArray(value.assets) ||
    !value.assets.every(isAssetLink) ||
    !isQuestionBankRichContent(value.rich_content) ||
    !Array.isArray(value.previews) ||
    !value.previews.every(isPreview) ||
    (value.error_patterns !== undefined && (
      !Array.isArray(value.error_patterns) ||
      !value.error_patterns.every((item) => (
        isRecord(item) &&
        hasExactKeys(item, [
          'id', 'category', 'pattern', 'explanation', 'trigger_kind',
          'trigger_value', 'status', 'source', 'has_evidence',
          'skill_key', 'skill_label', 'skill_keys', 'skill_labels', 'skill_source',
        ]) &&
        isPositiveInteger(item.id) &&
        isNullableString(item.category) &&
        typeof item.pattern === 'string' &&
        typeof item.explanation === 'string' &&
        ['option', 'wrong_answer', 'step', 'observation'].includes(String(item.trigger_kind)) &&
        typeof item.trigger_value === 'string' &&
        (item.status === 'candidate' || item.status === 'confirmed') &&
        typeof item.source === 'string' &&
        typeof item.has_evidence === 'boolean' &&
        isNullableString(item.skill_key) &&
        isNullableString(item.skill_label) &&
        (item.skill_keys === undefined || isStringArray(item.skill_keys)) &&
        (item.skill_labels === undefined || isStringArray(item.skill_labels)) &&
        (item.skill_source === null || ['teacher', 'criterion', 'question'].includes(String(item.skill_source)))
      ))
    )) ||
    (value.wrong_option_letters !== undefined && (
      !Array.isArray(value.wrong_option_letters) ||
      !value.wrong_option_letters.every((letter) => typeof letter === 'string' && /^[A-F]$/.test(letter))
    )) ||
    (value.selectable_skills !== undefined && (
      !Array.isArray(value.selectable_skills) ||
      !value.selectable_skills.every((item) => (
        isRecord(item) &&
        hasExactKeys(item, ['key', 'label']) &&
        typeof item.key === 'string' &&
        typeof item.label === 'string'
      ))
    ))
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
    compact: 'true',
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
      !isFiniteNumber(filters.difficultyMin) ||
      !isFiniteNumber(filters.difficultyMax) ||
      Number(filters.difficultyMin) < 1 ||
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
  appendTexts(parameters, 'curriculum_volume_ids', filters.curriculumVolumeIds)
  appendTexts(parameters, 'exam_scopes', filters.examScopes)
  appendTexts(parameters, 'curriculum_sections', filters.curriculumSections)
  appendTexts(parameters, 'knowledge_points', filters.knowledgePoints)
  appendTexts(parameters, 'skill_keys', filters.skillKeys)
  if (filters.skillUnlinked) parameters.set('skill_unlinked', 'true')
  if (filters.includeSkills) parameters.set('include_skills', 'true')
  appendTexts(parameters, 'abilities', filters.abilities)
  appendTexts(parameters, 'methods', filters.methods)
  appendTexts(parameters, 'thoughts', filters.thoughts)
  appendTexts(parameters, 'models', filters.models)
  appendTexts(parameters, 'special_types', filters.specialTypes)
  appendTexts(parameters, 'error_types', filters.errorTypes)
  appendTexts(parameters, 'student_levels', filters.studentLevels)
  appendTexts(parameters, 'teaching_stages', filters.teachingStages)
  appendTexts(parameters, 'sub_skills', filters.subSkills)
  parameters.set('tag_status', filters.tagStatus ?? 'all')
  parameters.set('analysis_status', filters.analysisStatus ?? 'all')
  parameters.set('sort', filters.sort ?? 'newest')
  if (filters.criteriaNeedsReview === true) {
    parameters.set('criteria_needs_review', 'true')
  }
  if (filters.collapseDuplicates === true) {
    parameters.set('collapse_duplicates', 'true')
  }
  const teachingProgressChapter = filters.teachingProgressChapter?.trim()
  if (teachingProgressChapter) {
    parameters.set('teaching_progress_chapter', teachingProgressChapter)
  }
  return `/api/question-bank/questions?${parameters.toString()}`
}

function questionFacetPath(filters: QuestionBankFilters): string {
  const query = questionListPath({ ...filters, page: 1, pageSize: 20 }).split('?')[1] ?? ''
  const parameters = new URLSearchParams(query)
  parameters.delete('page')
  parameters.delete('page_size')
  parameters.delete('sort')
  parameters.delete('compact')
  parameters.delete('include_skills')
  return `/api/question-bank/facets?${parameters.toString()}`
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

function safeQuestionIds(value: unknown): number[] {
  if (!Array.isArray(value)) return []
  const result: number[] = []
  const seen = new Set<number>()
  for (const item of value) {
    if (isPositiveInteger(item) && !seen.has(item)) {
      seen.add(item)
      result.push(item)
    }
  }
  return result.slice(0, 500)
}

export function questionJobRetryIds(job: JobResponse): number[] {
  const explicit = safeQuestionIds(job.result.failed_question_ids)
  if (explicit.length > 0) return explicit
  const failures = questionJobFailures(job.result).map(({ question_id }) => question_id)
  if (failures.length > 0) return [...new Set(failures)]
  if (job.status === 'failed' || job.status === 'cancelled') {
    return safeQuestionIds(job.payload.question_ids)
  }
  return []
}

export interface QuestionStandardSummary {
  active_release_id: string | null
  taxonomy_revision: number | null
  versions: { release_id: string; taxonomy_revision: number; status: string; activated_at: string | null; created_at: string }[]
  question_count: number
  usable_question_count: number
  skill_question_count: number
  section_only_question_count: number
  missing_link_question_count: number
  older_link_question_count: number
  model_calls: number
}

export interface QuestionSkillEntry {
  stable_key: string
  display_name: string
  full_name: string
  question_count: number
  type_counts: Record<string, number>
  difficulty: { min: number; median: number; max: number } | null
  criteria_needs_review_count: number
  cross_section: boolean
  definition: { observable_evidence: string; include_scope: string; exclude_scope: string }
}

export interface QuestionTopicEntry extends Pick<QuestionSkillEntry, 'display_name' | 'question_count' | 'type_counts' | 'difficulty' | 'criteria_needs_review_count'> {
  stable_key: string
  filter_value: string
}

export interface QuestionSkillIndex {
  graph_release_id: string | null
  curriculum_volume_id: string
  model_calls: number
  question_count: number
  unlinked: { no_usable_evidence: number; no_skill_link: number }
  chapters: {
    id: string; label: string; question_count: number; cross_section_skills: QuestionSkillEntry[]
    sections: { id: string; label: string; question_count: number; skills: QuestionSkillEntry[]; topics: QuestionTopicEntry[] }[]
  }[]
}

export function decodeQuestionSkillIndex(value: unknown): QuestionSkillIndex {
  function stats(row: unknown): row is Record<string, unknown> {
    return isRecord(row) && typeof row.display_name === 'string' && isNonnegativeInteger(row.question_count)
      && isNonnegativeInteger(row.criteria_needs_review_count) && isRecord(row.type_counts)
      && ['选择题', '多选题', '填空题', '解答题'].every((key) => isNonnegativeInteger(row.type_counts && (row.type_counts as Record<string, unknown>)[key]))
      && (row.difficulty === null || (isRecord(row.difficulty)
        && ['min', 'median', 'max'].every((key) => isFiniteNumber((row.difficulty as Record<string, unknown>)[key]))))
  }
  function skill(row: unknown): boolean {
    return stats(row) && typeof row.stable_key === 'string' && row.stable_key.startsWith('sk_')
      && typeof row.full_name === 'string' && typeof row.cross_section === 'boolean' && isRecord(row.definition)
      && ['observable_evidence', 'include_scope', 'exclude_scope'].every((key) => typeof (row.definition as Record<string, unknown>)[key] === 'string')
  }
  if (!isRecord(value) || !isNullableString(value.graph_release_id) || typeof value.curriculum_volume_id !== 'string'
    || value.model_calls !== 0 || !isNonnegativeInteger(value.question_count) || !isRecord(value.unlinked)
    || !isNonnegativeInteger(value.unlinked.no_usable_evidence) || !isNonnegativeInteger(value.unlinked.no_skill_link)
    || !Array.isArray(value.chapters) || !value.chapters.every((chapter) => isRecord(chapter)
      && typeof chapter.id === 'string' && typeof chapter.label === 'string' && isNonnegativeInteger(chapter.question_count)
      && Array.isArray(chapter.cross_section_skills) && chapter.cross_section_skills.every(skill)
      && Array.isArray(chapter.sections) && chapter.sections.every((section) => isRecord(section)
        && typeof section.id === 'string' && typeof section.label === 'string' && isNonnegativeInteger(section.question_count)
        && Array.isArray(section.skills) && section.skills.every(skill) && Array.isArray(section.topics)
        && section.topics.every((topic) => stats(topic) && typeof topic.stable_key === 'string' && topic.stable_key.startsWith('kp_') && typeof topic.filter_value === 'string')))) {
    throw new Error('技能索引格式不正确')
  }
  return value as unknown as QuestionSkillIndex
}

function decodeQuestionStandardSummary(value: unknown): QuestionStandardSummary {
  if (!isRecord(value) || !isNullableString(value.active_release_id)
    || !(value.taxonomy_revision === null || Number.isInteger(value.taxonomy_revision))
    || !Array.isArray(value.versions)
    || !value.versions.every((row) => isRecord(row) && typeof row.release_id === 'string'
      && Number.isInteger(row.taxonomy_revision) && typeof row.status === 'string'
      && isNullableString(row.activated_at) && typeof row.created_at === 'string')
    || !['question_count', 'usable_question_count', 'skill_question_count', 'section_only_question_count',
      'missing_link_question_count', 'older_link_question_count', 'model_calls']
      .every((key) => typeof value[key] === 'number' && Number.isInteger(value[key]) && value[key] >= 0)) {
    throw new Error('标准版本信息格式不正确')
  }
  return value as unknown as QuestionStandardSummary
}

export const questionBankApi = {
  skillIndex(curriculumVolumeId: string, signal?: AbortSignal): Promise<QuestionSkillIndex> {
    const parameters = new URLSearchParams({ curriculum_volume_id: curriculumVolumeId })
    return apiClient.request(`/api/question-bank/skill-index?${parameters}`, { decode: decodeQuestionSkillIndex, signal })
  },
  standardSummary(signal?: AbortSignal): Promise<QuestionStandardSummary> {
    return apiClient.request('/api/question-bank/standard-summary', { decode: decodeQuestionStandardSummary, signal })
  },
  getCurriculum(
    signal?: AbortSignal,
    includeKnowledgePoints = true,
  ): Promise<CurriculumCatalog> {
    const query = includeKnowledgePoints ? '' : '?include_knowledge_points=false'
    return apiClient.request(`/api/question-bank/curriculum${query}`, {
      decode: decodeCurriculumCatalog,
      signal,
    })
  },

  listPapers(signal?: AbortSignal, deleted = false): Promise<QuestionBankPaperListResponse> {
    return apiClient.request(`/api/question-bank/papers${deleted ? '?deleted=true' : ''}`, {
      decode: decodeQuestionPaperListResponse,
      signal,
    })
  },

  changePaperState(paper: QuestionBankPaper, deleted: boolean): Promise<{ id: number; deleted: boolean; import_status: string; updated_at: string; affected_question_count: number }> {
    return apiClient.request(`/api/question-bank/papers/${paper.id}/${deleted ? 'trash' : 'restore'}`, {
      method: 'POST', body: { expected_updated_at: paper.updated_at },
      decode(value: unknown) {
        if (!isRecord(value) || !hasExactKeys(value, ['id', 'deleted', 'import_status', 'updated_at', 'affected_question_count'])
          || !isPositiveInteger(value.id) || typeof value.deleted !== 'boolean' || typeof value.import_status !== 'string'
          || typeof value.updated_at !== 'string' || !isNonnegativeInteger(value.affected_question_count)) throw new Error('试卷状态响应格式不正确')
        return value as { id: number; deleted: boolean; import_status: string; updated_at: string; affected_question_count: number }
      },
    })
  },

  updatePaperMetadata(
    paperId: number,
    expectedUpdatedAt: string,
    metadata: QuestionBankPaperMetadataInput,
    signal?: AbortSignal,
  ): Promise<QuestionBankPaperMetadataResult> {
    const expected = String(expectedUpdatedAt ?? '').trim()
    if (!isPositiveInteger(paperId) || !expected) {
      throw new Error('Invalid paper metadata write')
    }
    return apiClient.request(`/api/question-bank/papers/${paperId}`, {
      method: 'PATCH',
      body: {
        expected_updated_at: expected,
        metadata: normalizePaperMetadata(metadata),
      },
      decode: decodeQuestionBankPaperMetadataResult,
      signal,
    })
  },

  previewPaperPermanentDelete(
    selections: QuestionBankPaperPermanentDeleteSelection[],
    signal?: AbortSignal,
  ): Promise<QuestionBankPaperPermanentDeleteImpact> {
    return apiClient.request(
      '/api/question-bank/papers/permanent-deletion-impact',
      {
        method: 'POST',
        body: { selections },
        decode: decodeQuestionBankPaperPermanentDeleteImpact,
        signal,
      },
    )
  },

  permanentlyDeletePapers(
    selections: QuestionBankPaperPermanentDeleteSelection[],
    confirmationPhrase: string,
    requestToken: string,
    signal?: AbortSignal,
  ): Promise<QuestionBankPaperPermanentDeleteResult> {
    return apiClient.request('/api/question-bank/papers/permanent-delete', {
      method: 'POST',
      timeoutMs: 120_000,
      body: {
        selections,
        confirmation_phrase: confirmationPhrase,
        request_token: requestToken,
      },
      decode: decodeQuestionBankPaperPermanentDeleteResult,
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

  listQuestionRefs(
    filters: Pick<QuestionBankFilters, 'paperIds' | 'analysisStatus'> & {
      page?: number
      pageSize?: number
    } = {},
    signal?: AbortSignal,
  ): Promise<QuestionBankQuestionRefListResponse> {
    const page = filters.page ?? 1
    const pageSize = filters.pageSize ?? 500
    if (!isPositiveInteger(page) || !isPositiveInteger(pageSize) || pageSize > 500) {
      throw new Error('Invalid question ref filters')
    }
    const parameters = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
      analysis_status: filters.analysisStatus ?? 'all',
    })
    appendIds(parameters, 'paper_ids', filters.paperIds)
    return apiClient.request(
      `/api/question-bank/question-refs?${parameters.toString()}`,
      { decode: decodeQuestionRefListResponse, signal },
    )
  },

  listFacets(
    filters: QuestionBankFilters = {},
    signal?: AbortSignal,
  ): Promise<QuestionBankFacets> {
    return apiClient.request(questionFacetPath(filters), {
      decode: decodeQuestionBankFacets,
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

  editErrorPattern(
    questionId: number,
    pattern: QuestionErrorPattern,
    change:
      | { action: 'edit'; pattern: string; category: string; skill_key?: string | null }
      | { action: 'reject' },
  ): Promise<QuestionBankDetail> {
    if (!isPositiveInteger(questionId) || !isPositiveInteger(pattern.id)) {
      throw new Error('Invalid typical error target')
    }
    return apiClient.request(
      `/api/question-bank/questions/${questionId}/error-patterns/${pattern.id}`,
      {
        method: 'PATCH',
        body: { expected_pattern: pattern.pattern, ...change },
        decode: decodeQuestionDetailResponse,
      },
    )
  },

  getSolutionEvidence(
    questionId: number,
    signal?: AbortSignal,
  ): Promise<QuestionSolutionEvidenceResponse> {
    if (!isPositiveInteger(questionId)) throw new Error('Invalid question id')
    return apiClient.request(
      `/api/question-bank/questions/${questionId}/solution-evidence`,
      {
        decode: decodeQuestionSolutionEvidenceResponse,
        signal,
      },
    )
  },

  listSimilar(
    questionId: number,
    limit = 6,
    signal?: AbortSignal,
  ): Promise<SimilarQuestionResponse> {
    if (
      !isPositiveInteger(questionId) ||
      !isPositiveInteger(limit) ||
      limit > 20
    ) {
      throw new Error('Invalid similar question request')
    }
    return apiClient.request(
      `/api/question-bank/questions/${questionId}/similar?limit=${limit}`,
      {
        decode: decodeSimilarQuestionResponse,
        signal,
      },
    )
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
    curriculumVolumeId?: string,
    signal?: AbortSignal,
  ): Promise<JobResponse> {
    if (!isHex(requestId, 32)) throw new Error('Invalid question import request id')
    const volumeId = curriculumVolumeId?.trim()
    return apiClient.request(
      `/api/question-bank/import-requests/${requestId}/jobs`,
      {
        method: 'POST',
        body: volumeId ? { curriculum_volume_id: volumeId } : {},
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
    curriculumVolumeId: string,
    sourceJobId?: number,
    signal?: AbortSignal,
    forceRetag = false,
    clientRequestToken?: string,
  ): Promise<JobResponse> {
    const body: {
      question_ids: number[]
      curriculum_volume_id: string
      source_job_id?: number
      force_retag?: boolean
      client_request_token?: string
    } = {
      question_ids: normalizedQuestionIds(questionIds),
      curriculum_volume_id: String(curriculumVolumeId ?? '').trim(),
    }
    if (!body.curriculum_volume_id) throw new Error('Invalid curriculum volume')
    if (sourceJobId !== undefined) {
      if (!isPositiveInteger(sourceJobId)) throw new Error('Invalid source job id')
      body.source_job_id = sourceJobId
    }
    if (forceRetag) body.force_retag = true
    if (clientRequestToken !== undefined) {
      if (!/^[0-9a-f]{32}$/.test(clientRequestToken)) {
        throw new Error('Invalid tagging request token')
      }
      body.client_request_token = clientRequestToken
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

  submitAnswerDraft(
    questionIds: readonly number[],
    signal?: AbortSignal,
  ): Promise<JobResponse> {
    return apiClient.request('/api/question-bank/answer-draft-jobs', {
      method: 'POST',
      body: { question_ids: normalizedQuestionIds(questionIds) },
      decode: decodeJobResponse,
      signal,
    })
  },

  retryAnswerDraftJob(
    jobId: number,
    questionIds?: readonly number[],
    signal?: AbortSignal,
  ): Promise<JobResponse> {
    if (!isPositiveInteger(jobId)) throw new Error('Invalid answer draft job id')
    return apiClient.request(
      `/api/question-bank/answer-draft-jobs/${jobId}/retry`,
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
