import { apiClient } from './client'
import { createClientRequestToken } from './config-workspace'
import { assertNoPathLikeKeys, isRecord } from './validation'

export type PageRole = 'front' | 'back'
export interface TemplatePage { url: string; width: number; height: number }
export interface TemplateSummary {
  session_id: number; template_id: number; template_fingerprint: string
  first_page_role: PageRole; pages: Record<PageRole, TemplatePage>
  is_confirmed: boolean; regions_snapshot_pending: boolean
}
export interface Region {
  region_uuid: string; page: PageRole; region_order: number
  x: number; y: number; w: number; h: number
  mapped_question_id: string | null; mapping_status: string
  is_confirmed: boolean; multi_region_confirmed: boolean
  detected_question_id?: string | null; confidence?: number | null
}
export interface RegionAutoProposal {
  regions: Region[]; missing_question_ids: string[]; template_fingerprint: string
}
export interface RegionIssue {
  code: string; message: string; region_uuid: string | null; question_id: string | null
}
export interface RegionWorkspace {
  session_id: number; template: TemplateSummary; formal_regions: Region[]
  draft: { status: 'missing' | 'compatible' | 'incompatible' | 'corrupt'; revision: number; regions: Region[] }
  automatic_candidates: string[]
  manual_question_options: Array<{ value: string; label: string }>
  issues: RegionIssue[]; template_ready: boolean
}
export interface RegionDraftResponse {
  status: string; session_id: number; template_id: number; template_fingerprint: string
  draft: { revision: number; regions: Region[] } | null
}
export interface RegionCommitResponse {
  committed: boolean; snapshot_pending: boolean; error: string | null
  issues: RegionIssue[]; region_count: number
}
export interface TemplateSubmission {
  status: 'processing' | 'succeeded' | 'failed' | 'replaced' | 'abandoned'; template: TemplateSummary | null
}
export interface TemplatePageAssignment {
  changed: boolean
  draft_sync_pending: boolean
  template: TemplateSummary
}
export interface RegionReadiness {
  session_id: number; scoring_configured: boolean; template_present: boolean; template_ready: boolean
}

function exact(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).sort().join('|') === [...keys].sort().join('|')
}
function positive(value: unknown): value is number { return Number.isSafeInteger(value) && Number(value) > 0 }
function hash(value: unknown): value is string { return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value) }
function safeUrl(value: unknown): value is string {
  return typeof value === 'string' && /^\/api\/sessions\/\d+\/template\/pages\/(front|back)$/.test(value)
}
function page(value: unknown): value is TemplatePage {
  return isRecord(value) && exact(value, ['url', 'width', 'height'])
    && safeUrl(value.url) && positive(value.width) && positive(value.height)
}
function decodeTemplate(value: unknown): TemplateSummary {
  assertNoPathLikeKeys(value)
  if (!isRecord(value) || !exact(value, ['session_id', 'template_id', 'template_fingerprint',
    'first_page_role', 'pages', 'is_confirmed', 'regions_snapshot_pending'])
    || !positive(value.session_id) || !positive(value.template_id) || !hash(value.template_fingerprint)
    || (value.first_page_role !== 'front' && value.first_page_role !== 'back')
    || !isRecord(value.pages) || !exact(value.pages, ['front', 'back'])
    || !page(value.pages.front) || !page(value.pages.back)
    || typeof value.is_confirmed !== 'boolean' || typeof value.regions_snapshot_pending !== 'boolean') {
    throw new Error('Invalid template response')
  }
  const summary = value as unknown as TemplateSummary
  const version = encodeURIComponent(summary.template_fingerprint)
  return {
    ...summary,
    pages: {
      front: { ...summary.pages.front, url: `${summary.pages.front.url}?v=${version}` },
      back: { ...summary.pages.back, url: `${summary.pages.back.url}?v=${version}` },
    },
  }
}
function region(value: unknown): value is Region {
  const metadataKeys = ['detected_question_id', 'confidence'].filter((key) => isRecord(value) && key in value)
  return isRecord(value) && exact(value, ['region_uuid', 'page', 'region_order', 'x', 'y', 'w', 'h',
    'mapped_question_id', 'mapping_status', 'is_confirmed', 'multi_region_confirmed', ...metadataKeys])
    && typeof value.region_uuid === 'string' && (value.page === 'front' || value.page === 'back')
    && Number.isSafeInteger(value.region_order) && ['x', 'y', 'w', 'h'].every((key) => Number.isFinite(value[key]))
    && (value.mapped_question_id === null || typeof value.mapped_question_id === 'string')
    && typeof value.mapping_status === 'string' && typeof value.is_confirmed === 'boolean'
    && typeof value.multi_region_confirmed === 'boolean'
    && (value.detected_question_id == null || typeof value.detected_question_id === 'string')
    && (value.confidence == null || (typeof value.confidence === 'number' && Number.isFinite(value.confidence)))
}
function issue(value: unknown): value is RegionIssue {
  return isRecord(value) && exact(value, ['code', 'message', 'region_uuid', 'question_id'])
    && typeof value.code === 'string' && typeof value.message === 'string'
    && (value.region_uuid === null || typeof value.region_uuid === 'string')
    && (value.question_id === null || typeof value.question_id === 'string')
}
function decodeWorkspace(value: unknown): RegionWorkspace {
  assertNoPathLikeKeys(value)
  if (!isRecord(value) || !exact(value, ['session_id', 'template', 'formal_regions', 'draft',
    'automatic_candidates', 'manual_question_options', 'issues', 'template_ready'])
    || !positive(value.session_id) || !Array.isArray(value.formal_regions) || !value.formal_regions.every(region)
    || !isRecord(value.draft) || !exact(value.draft, ['status', 'revision', 'regions'])
    || !['missing', 'compatible', 'incompatible', 'corrupt'].includes(String(value.draft.status))
    || !Number.isSafeInteger(value.draft.revision) || Number(value.draft.revision) < 0
    || !Array.isArray(value.draft.regions) || !value.draft.regions.every(region)
    || !Array.isArray(value.automatic_candidates) || !value.automatic_candidates.every((item) => typeof item === 'string')
    || !Array.isArray(value.manual_question_options) || !value.manual_question_options.every((item) =>
      isRecord(item) && exact(item, ['value', 'label']) && typeof item.value === 'string' && typeof item.label === 'string')
    || !Array.isArray(value.issues) || !value.issues.every(issue) || typeof value.template_ready !== 'boolean') {
    throw new Error('Invalid region workspace response')
  }
  return { ...value, template: decodeTemplate(value.template) } as unknown as RegionWorkspace
}
function decodeDraft(value: unknown): RegionDraftResponse {
  assertNoPathLikeKeys(value)
  if (!isRecord(value) || !exact(value, ['status', 'session_id', 'template_id', 'template_fingerprint', 'draft'])
    || typeof value.status !== 'string' || !positive(value.session_id) || !positive(value.template_id)
    || !hash(value.template_fingerprint) || (value.draft !== null && (!isRecord(value.draft)
      || !exact(value.draft, ['revision', 'regions']) || !Number.isSafeInteger(value.draft.revision)
      || !Array.isArray(value.draft.regions) || !value.draft.regions.every(region)))) {
    throw new Error('Invalid region draft response')
  }
  return value as unknown as RegionDraftResponse
}
function decodeCommit(value: unknown): RegionCommitResponse {
  assertNoPathLikeKeys(value)
  if (!isRecord(value) || !exact(value, ['committed', 'snapshot_pending', 'error', 'issues', 'region_count'])
    || typeof value.committed !== 'boolean' || typeof value.snapshot_pending !== 'boolean'
    || (value.error !== null && typeof value.error !== 'string') || !Array.isArray(value.issues)
    || !value.issues.every(issue) || !Number.isSafeInteger(value.region_count) || Number(value.region_count) < 0) {
    throw new Error('Invalid region commit response')
  }
  return value as unknown as RegionCommitResponse
}
function decodePageAssignment(value: unknown): TemplatePageAssignment {
  assertNoPathLikeKeys(value)
  if (!isRecord(value) || !exact(value, ['changed', 'draft_sync_pending', 'template'])
    || typeof value.changed !== 'boolean' || typeof value.draft_sync_pending !== 'boolean') {
    throw new Error('Invalid template page assignment response')
  }
  return {
    changed: value.changed,
    draft_sync_pending: value.draft_sync_pending,
    template: decodeTemplate(value.template),
  }
}
function sessionId(value: number): number { if (!positive(value)) throw new Error('Invalid session id'); return value }
function token(value: string): string { if (!/^[0-9a-f]{32}$/.test(value)) throw new Error('Invalid request token'); return value }
async function fileSha256(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

export function fetchRegionWorkspace(id: number, signal?: AbortSignal): Promise<RegionWorkspace> {
  return apiClient.request(`/api/sessions/${sessionId(id)}/regions/workspace`, { decode: decodeWorkspace, signal })
}
export function fetchRegionAutoProposal(id: number, signal?: AbortSignal): Promise<RegionAutoProposal> {
  return apiClient.request(`/api/sessions/${sessionId(id)}/regions/auto-proposal`, {
    signal, timeoutMs: 5 * 60_000,
    decode(value) {
      assertNoPathLikeKeys(value)
      if (!isRecord(value) || !exact(value, ['regions', 'missing_question_ids', 'template_fingerprint'])
        || !Array.isArray(value.regions) || !value.regions.every(region)
        || !Array.isArray(value.missing_question_ids)
        || !value.missing_question_ids.every((item) => typeof item === 'string')
        || !hash(value.template_fingerprint)) throw new Error('Invalid region auto proposal')
      return value as unknown as RegionAutoProposal
    },
  })
}
export function fetchRegionReadiness(id: number): Promise<RegionReadiness> {
  return apiClient.request(`/api/sessions/${sessionId(id)}/regions/readiness`, {
    decode(value) {
      assertNoPathLikeKeys(value)
      if (!isRecord(value) || !exact(value, ['session_id', 'scoring_configured', 'template_present', 'template_ready'])
        || !positive(value.session_id) || typeof value.scoring_configured !== 'boolean'
        || typeof value.template_present !== 'boolean'
        || typeof value.template_ready !== 'boolean') throw new Error('Invalid region readiness')
      return value as unknown as RegionReadiness
    },
  })
}
export async function uploadTemplate(id: number, file: File, firstPageRole: PageRole,
  requestToken = createClientRequestToken()): Promise<TemplateSummary> {
  if (!(file instanceof File) || file.type !== 'application/pdf' || !file.name.toLowerCase().endsWith('.pdf')) throw new Error('Invalid PDF')
  const contentSha256 = await fileSha256(file)
  return apiClient.request(`/api/sessions/${sessionId(id)}/template?first_page_role=${firstPageRole}`, {
    method: 'POST', rawBody: file, timeoutMs: 10 * 60_000, decode: decodeTemplate,
    headers: { 'content-type': 'application/pdf', 'x-upload-filename': encodeURIComponent(file.name),
      'x-client-request-token': token(requestToken), 'x-content-sha256': contentSha256 },
  })
}
export function assignTemplatePageRole(
  id: number,
  firstPageRole: PageRole,
  expectedTemplateFingerprint: string,
): Promise<TemplatePageAssignment> {
  if (!hash(expectedTemplateFingerprint)) throw new Error('Invalid template fingerprint')
  return apiClient.request(`/api/sessions/${sessionId(id)}/template/page-assignment`, {
    method: 'PUT',
    body: {
      first_page_role: firstPageRole,
      expected_template_fingerprint: expectedTemplateFingerprint,
    },
    decode: decodePageAssignment,
  })
}
export function fetchTemplateSubmission(id: number, requestToken: string): Promise<TemplateSubmission> {
  return apiClient.request(`/api/sessions/${sessionId(id)}/template/submissions/${token(requestToken)}`, {
    decode(value) {
      assertNoPathLikeKeys(value)
      if (!isRecord(value) || !exact(value, ['status', 'template'])
        || !['processing', 'succeeded', 'failed', 'replaced', 'abandoned'].includes(String(value.status))) throw new Error('Invalid submission')
      return { status: value.status, template: value.template === null ? null : decodeTemplate(value.template) } as TemplateSubmission
    },
  })
}
export async function abandonTemplateSubmission(id: number, requestToken: string): Promise<void> {
  await apiClient.request(`/api/sessions/${sessionId(id)}/template/submissions/${token(requestToken)}/abandon`, {
    method: 'POST', decode(value) { if (!isRecord(value) || !exact(value, ['status']) || value.status !== 'abandoned') throw new Error('Invalid abandon response') },
  })
}
export function saveRegionDraft(id: number, request: { revision: number; regions: Region[];
  expected_template_fingerprint: string; expected_revision: number }): Promise<RegionDraftResponse> {
  return apiClient.request(`/api/sessions/${sessionId(id)}/regions/draft`, { method: 'PUT', body: request, decode: decodeDraft })
}
export async function discardRegionDraft(id: number): Promise<void> {
  await apiClient.request(`/api/sessions/${sessionId(id)}/regions/draft`, { method: 'DELETE',
    decode(value) { if (!isRecord(value) || !exact(value, ['status']) || value.status !== 'discarded') throw new Error('Invalid discard response') } })
}
export function commitRegions(id: number, request: { regions: Region[]; image_sizes: Record<PageRole, [number, number]>;
  template_matches: boolean; expected_template_fingerprint: string }): Promise<RegionCommitResponse> {
  return apiClient.request(`/api/sessions/${sessionId(id)}/regions/commit`, { method: 'POST', body: request, decode: decodeCommit })
}
export function retryRegionSnapshot(id: number, fingerprint: string): Promise<RegionCommitResponse> {
  if (!hash(fingerprint)) throw new Error('Invalid template fingerprint')
  return apiClient.request(`/api/sessions/${sessionId(id)}/regions/snapshot/retry`, { method: 'POST',
    body: { expected_template_fingerprint: fingerprint }, decode: decodeCommit })
}
