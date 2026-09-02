import { beforeEach, describe, expect, it, vi } from 'vitest'

import { teachingPrepCatalogApi, type MaterialVersion } from './catalog'

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

const materialPayload: MaterialVersion = {
  id: '8'.repeat(32),
  source_id: '9'.repeat(32),
  display_name: '合成教材',
  material_type: 'pdf',
  content_sha256: '3'.repeat(64),
  safe_filename: '合成教材.pdf',
  size_bytes: 18,
  modified_ns: '1760000000000000000',
  unit_count: null,
  parse_expected_unit_count: null,
  preview_completed_count: 0,
  ocr_completed_count: 0,
  ocr_total_count: 0,
  inspection_status: 'uninspected',
  availability: 'available',
  source_revision: 1,
  source_archived_at: null,
  created_at: '2026-07-30T00:00:00Z',
}

const preferencesPayload = {
  schema_version: 1,
  label_textbook_pages: true,
  page_label_font_size: 28,
  trim_excess_practice: true,
  practice_trim_level: 'moderate',
  preserve_teaching_examples: true,
  prefer_short_practice: true,
  supplement_from_references: true,
  supplement_question_limit: 2,
  supplement_as_source_image: true,
  prioritize_homework_workbook: true,
  avoid_direct_homework_copy: true,
  avoid_ppt_duplicates: true,
} as const

beforeEach(() => vi.restoreAllMocks())

describe('teaching preparation delivery API', () => {
  it('saves structured teaching preferences with revision protection', async () => {
    const current = {
      revision: 1,
      payload: preferencesPayload,
      updated_at: '2026-07-30T00:00:00Z',
    }
    const saved = {
      ...current,
      revision: 2,
      updated_at: '2026-07-30T00:01:00Z',
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValue(response(saved))

    await expect(
      teachingPrepCatalogApi.updateTeachingPreferences(
        current,
        preferencesPayload,
      ),
    ).resolves.toEqual(saved)

    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      expected_revision: 1,
      payload: preferencesPayload,
    })
  })

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

  it('previews, confirms, and checks one material deletion with the same operation number', async () => {
    const operationId = 'delete-material-1234567890abcdef1234567890abcdef'
    const impactCounts = {
      material_sources: 1,
      material_versions: 2,
      material_units: 36,
      lesson_material_links: 3,
      semester_material_records: 1,
      semester_mapping_proposals: 1,
      reference_ppt_collections: 0,
      exercise_regions: 2,
      exercise_candidates: 1,
    }
    const preview = {
      source_id: materialPayload.source_id,
      display_name: materialPayload.display_name,
      source_revision: 1,
      impact_counts: impactCounts,
      affected_semesters: [{
        semester_id: 's'.repeat(32), title: '八年级上册',
        school_year: '2026-2027', term: 'first',
      }],
      generation_history_count: 0,
      preserved_snapshot_count: 0,
      blocking_generation_count: 0,
      can_delete: true,
      blocker_code: null,
      preserved_history_note: null,
      confirmation_phrase: '确认彻底删除资料',
      preview_version: 'preview-v1',
      owned_file_count: 4,
    }
    const operation = {
      operation_id: operationId,
      status: 'succeeded',
      preview_version: preview.preview_version,
      deleted_source_id: materialPayload.source_id,
      deleted_file_count: 4,
      counts: impactCounts,
      error_code: null,
      impact: null,
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response(preview))
      .mockResolvedValueOnce(response(operation))
      .mockResolvedValueOnce(response(operation))

    await expect(teachingPrepCatalogApi.getMaterialDeletionPreview(materialPayload))
      .resolves.toEqual(preview)
    await expect(teachingPrepCatalogApi.deleteMaterialSource(materialPayload, {
      operation_id: operationId,
      preview_version: preview.preview_version,
      confirmation_phrase: preview.confirmation_phrase,
    })).resolves.toEqual(operation)
    await expect(teachingPrepCatalogApi.getMaterialDeletionStatus(operationId))
      .resolves.toEqual(operation)

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      `/api/teaching-prep/material-sources/${materialPayload.source_id}/deletion-preview?expected_revision=1`,
      expect.objectContaining({ method: 'GET' }),
    )
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      expected_revision: 1,
      operation_id: operationId,
      preview_version: preview.preview_version,
      confirmation_phrase: preview.confirmation_phrase,
    })
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      `/api/teaching-prep/material-deletions/${operationId}`,
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it.each([
    ['negative preserved snapshot count', { preserved_snapshot_count: -1 }],
    ['fractional blocking generation count', { blocking_generation_count: 0.5 }],
    ['path field', { path: 'C:\\private\\material.pdf' }],
    ['root field', { root: 'C:\\private' }],
    ['local file path field', { local_file_path: 'C:\\private\\material.pdf' }],
  ])('rejects an unsafe material deletion preview with %s', async (_case, mutation) => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({
      source_id: materialPayload.source_id,
      display_name: materialPayload.display_name,
      source_revision: 1,
      impact_counts: {
        material_sources: 1,
        material_versions: 1,
        material_units: 8,
        lesson_material_links: 1,
        semester_material_records: 1,
        semester_mapping_proposals: 0,
        reference_ppt_collections: 0,
        exercise_regions: 0,
        exercise_candidates: 0,
      },
      affected_semesters: [],
      generation_history_count: 1,
      preserved_snapshot_count: 1,
      blocking_generation_count: 0,
      can_delete: true,
      blocker_code: null,
      preserved_history_note: '仅保留生成事实快照。',
      confirmation_phrase: '确认彻底删除资料',
      preview_version: 'preview-v2',
      owned_file_count: 9,
      ...mutation,
    }))

    await expect(teachingPrepCatalogApi.getMaterialDeletionPreview(materialPayload))
      .rejects.toMatchObject({
        kind: 'contract',
        code: 'invalid_success_contract',
      })
  })

  it('starts page parsing as a background job', async () => {
    const job = {
      id: 17,
      job_type: 'teaching_prep.material_parse',
      payload: { material_version_id: materialPayload.id },
      result: {},
      status: 'queued',
      progress: 0,
      stage: 'queued',
      detail: '等待后台处理',
      error: null,
      cancel_requested: false,
      created_at: '2026-08-02T00:00:00Z',
      started_at: null,
      updated_at: '2026-08-02T00:00:00Z',
      finished_at: null,
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValue(response(job, 202))

    await expect(
      teachingPrepCatalogApi.startMaterialParse(materialPayload.id),
    ).resolves.toEqual(job)

    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      `/api/teaching-prep/materials/${materialPayload.id}/parse-job`,
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('restores persisted material parse jobs after a page refresh', async () => {
    const job = {
      id: 18,
      job_type: 'teaching_prep.material_parse',
      payload: { material_version_id: materialPayload.id },
      result: {},
      status: 'running',
      progress: 0.42,
      stage: 'ocr',
      detail: '正在本机识别扫描文字：24/67 页',
      error: null,
      cancel_requested: false,
      created_at: '2026-08-02T00:00:00Z',
      started_at: '2026-08-02T00:00:01Z',
      updated_at: '2026-08-02T00:01:00Z',
      finished_at: null,
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValue(response({ items: [job] }))

    await expect(teachingPrepCatalogApi.listMaterialParseJobs())
      .resolves.toEqual([job])

    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      '/api/teaching-prep/material-parse-jobs',
      expect.objectContaining({ method: 'GET' }),
    )
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

  it('accepts a safe semester material filename without weakening path checks', async () => {
    const semesterId = 's'.repeat(32)
    const payload = {
      id: 'r'.repeat(32),
      semester_id: semesterId,
      material_source_id: 'm'.repeat(32),
      display_name: '合成日常作业教辅',
      material_role: 'homework_workbook',
      is_daily_workbook: true,
      workbook_series: '合成日常作业教辅',
      workbook_volume: 'A',
      parse_status: 'parsed',
      mapping_status: 'unmapped',
      current_material_version_id: 'v'.repeat(32),
      safe_filename: 'synthetic-homework.pdf',
      current_inspection_status: 'uninspected',
      current_unit_count: 3,
      last_parsed_version_id: 'v'.repeat(32),
      has_unparsed_update: false,
      parsed_at: '2026-07-31T00:00:00Z',
      is_active: true,
      revision: 2,
      created_at: '2026-07-31T00:00:00Z',
      updated_at: '2026-07-31T00:00:00Z',
    }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({ items: [payload] }))

    await expect(
      teachingPrepCatalogApi.listSemesterMaterials(semesterId),
    ).resolves.toEqual([payload])
  })

  it('freezes the resource pack with the confirmed question selection', async () => {
    const lessonId = 'l'.repeat(32)
    const pack = {
      id: 'p'.repeat(32),
      lesson_node_id: lessonId,
      version_number: 1,
      source_state_sha256: '4'.repeat(64),
      pack_sha256: '5'.repeat(64),
      payload: {},
      created_at: '2026-08-03T00:00:00Z',
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(pack, 201))

    await expect(teachingPrepCatalogApi.freezeResourcePack(lessonId, {
      request_token: 'resource-pack-0001',
      class_name: null,
      lesson_type: 'new_lesson',
      teacher_context: null,
      reference_ppt_intents: { ['m'.repeat(32)]: 'keep' },
      question_ids: [101, 102],
      assessment_ids: [],
      knowledge_scope: [],
      preparation_preferences: preferencesPayload,
      selected_material_link_ids: ['m'.repeat(32)],
      selected_exercise_candidate_ids: [],
      question_selection: {
        volume_id: 'bnu24-math-g8-upper',
        section_ids: ['bnu24-math-g8-upper-c04-s03'],
        difficulty_max: 5,
        stem_max_chars: 220,
        limit: 12,
        max_per_method: 2,
        items: [
          { question_id: 101, method: '函数建模', difficulty: 4, frequency_score: 0.873 },
          { question_id: 102, method: null, difficulty: 2, frequency_score: null },
        ],
      },
    })).resolves.toEqual(pack)

    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      `/api/teaching-prep/lessons/${lessonId}/resource-packs`,
      expect.objectContaining({ method: 'POST' }),
    )
    expect(body.question_ids).toEqual([101, 102])
    expect(body.question_selection.items).toEqual([
      { question_id: 101, method: '函数建模', difficulty: 4, frequency_score: 0.873 },
      { question_id: 102, method: null, difficulty: 2, frequency_score: null },
    ])
  })

})
