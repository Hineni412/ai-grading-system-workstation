import { beforeEach, describe, expect, it, vi } from 'vitest'

import { teachingPrepCatalogApi } from './catalog'

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

const packagePayload = {
  id: 'a'.repeat(32),
  pptx_version_id: 'b'.repeat(32),
  slide_plan_id: 'c'.repeat(32),
  lesson_draft_id: 'd'.repeat(32),
  resource_pack_id: 'e'.repeat(32),
  lesson_node_id: 'f'.repeat(32),
  class_name: '合成七年级一班',
  version_number: 1,
  status: 'complete',
  output_filename: 'lesson-package.zip',
  package_sha256: '1'.repeat(64),
  manifest: {
    schema_version: 1,
    files: [{ name: 'lesson-slides.pptx', sha256: '2'.repeat(64) }],
  },
  error_code: null,
  is_current: true,
  staging_retained: false,
  recovery_actions: [],
  created_at: '2026-07-30T00:00:00Z',
  updated_at: '2026-07-30T00:00:00Z',
  completed_at: '2026-07-30T00:00:00Z',
  download_url: `/api/teaching-prep/up-class-packages/${'a'.repeat(32)}/download`,
}

const materialPayload = {
  id: '8'.repeat(32),
  source_id: '9'.repeat(32),
  display_name: '合成教材',
  material_type: 'pdf',
  content_sha256: '3'.repeat(64),
  safe_filename: '合成教材.pdf',
  size_bytes: 18,
  modified_ns: '1760000000000000000',
  unit_count: null,
  inspection_status: 'uninspected',
  availability: 'available',
  created_at: '2026-07-30T00:00:00Z',
}

beforeEach(() => vi.restoreAllMocks())

describe('teaching preparation delivery API', () => {
  it('imports an explicitly selected file as a binary copy without a local path', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValue(response(materialPayload, 201))
    const file = new File(
      ['%PDF-1.7 synthetic'],
      '合成教材.pdf',
      { type: 'application/pdf', lastModified: 1_760_000_000_000 },
    )

    await expect(teachingPrepCatalogApi.importMaterialCopy(
      file,
      'material-import-0001',
    )).resolves.toEqual(materialPayload)

    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      '/api/teaching-prep/materials/import-copy',
      expect.objectContaining({
        method: 'POST',
        body: file,
        headers: expect.objectContaining({
          'content-type': 'application/pdf',
          'x-upload-filename': encodeURIComponent('合成教材.pdf'),
          'x-display-name': encodeURIComponent('合成教材'),
          'x-request-token': 'material-import-0001',
        }),
      }),
    )
  })

  it('creates a final package once with explicit confirmation', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValue(response(packagePayload, 201))

    await expect(teachingPrepCatalogApi.createUpClassPackage(
      'b'.repeat(32),
      'package-request-0001',
    )).resolves.toMatchObject({
      id: 'a'.repeat(32),
      status: 'complete',
      is_current: true,
    })

    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      `/api/teaching-prep/pptx-versions/${'b'.repeat(32)}/up-class-package`,
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          request_token: 'package-request-0001',
          confirmed: true,
        }),
      }),
    )
  })

  it('rejects a package response that discloses a server path', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({
      ...packagePayload,
      manifest: {
        ...packagePayload.manifest,
        source_path: 'C:\\private\\lesson.pptx',
      },
    }))

    await expect(teachingPrepCatalogApi.createUpClassPackage(
      'b'.repeat(32),
      'package-request-0002',
    )).rejects.toMatchObject({
      kind: 'contract',
      code: 'invalid_success_contract',
    })
  })

  it('keeps class evidence and selected review IDs in the target variant request', async () => {
    const resourcePack = {
      id: '3'.repeat(32),
      lesson_node_id: 'f'.repeat(32),
      version_number: 2,
      source_state_sha256: '4'.repeat(64),
      pack_sha256: '5'.repeat(64),
      payload: {},
      created_at: '2026-07-30T00:00:00Z',
    }
    const variant = {
      id: '6'.repeat(32),
      base_resource_pack_id: 'e'.repeat(32),
      resource_pack_id: resourcePack.id,
      lesson_node_id: resourcePack.lesson_node_id,
      class_name: '合成七年级二班',
      prior_review_ids: ['7'.repeat(32)],
      created_at: '2026-07-30T00:00:00Z',
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValue(response({ variant, resource_pack: resourcePack }, 201))

    await expect(teachingPrepCatalogApi.deriveClassVariant(
      'e'.repeat(32),
      {
        request_token: 'class-variant-request-0001',
        class_name: '合成七年级二班',
        teacher_context: '匿名班级整体情况',
        assessment_ids: [7],
        knowledge_scope: ['一元一次方程'],
        prior_review_ids: ['7'.repeat(32)],
      },
    )).resolves.toEqual({ variant, resource_pack: resourcePack })

    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      request_token: 'class-variant-request-0001',
      class_name: '合成七年级二班',
      teacher_context: '匿名班级整体情况',
      assessment_ids: [7],
      knowledge_scope: ['一元一次方程'],
      prior_review_ids: ['7'.repeat(32)],
    })
  })

  it('cancels an in-flight model draft without retrying the write', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({
      operation_id: 'draft-operation-0001',
      status: 'cancelled',
      newly_cancelled: true,
    }))

    await expect(teachingPrepCatalogApi.cancelLessonDraftGeneration(
      'draft-operation-0001',
    )).resolves.toEqual({
      operation_id: 'draft-operation-0001',
      status: 'cancelled',
      newly_cancelled: true,
    })
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      '/api/teaching-prep/lesson-draft-generations/draft-operation-0001/cancel',
      expect.objectContaining({ method: 'POST' }),
    )
  })
})
