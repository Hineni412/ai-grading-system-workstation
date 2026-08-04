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

  it('preflights exactly the selected semester material before model mapping', async () => {
    const semesterId = 's'.repeat(32)
    const materialRecordId = 'r'.repeat(32)
    const payload = {
      semester_id: semesterId,
      source_state_sha256: '4'.repeat(64),
      will_call_model: true,
      model_available: true,
      model_label: '合成模型',
      material_count: 1,
      unit_count: 128,
      existing_lesson_count: 36,
      creates_initial_tree: false,
      automatic_retry: false,
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValue(response(payload))

    await expect(teachingPrepCatalogApi.semesterMappingPreflight(
      semesterId,
      [materialRecordId],
    )).resolves.toEqual(payload)

    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      `/api/teaching-prep/semesters/${semesterId}/mapping-preflight`,
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          material_record_ids: [materialRecordId],
        }),
      }),
    )
  })

  it('starts semester mapping as a durable background job', async () => {
    const semesterId = 's'.repeat(32)
    const materialRecordId = 'r'.repeat(32)
    const job = {
      id: 27,
      job_type: 'teaching_prep.semester_mapping',
      payload: {
        semester_id: semesterId,
        material_record_id: materialRecordId,
        operation_id: 'semester-mapping-1234567890abcdef1234567890abcdef',
        source_state_sha256: '4'.repeat(64),
      },
      result: {},
      status: 'queued',
      progress: 0,
      stage: 'queued',
      detail: '等待生成课时目录建议',
      error: null,
      cancel_requested: false,
      created_at: '2026-08-03T00:00:00Z',
      started_at: null,
      updated_at: '2026-08-03T00:00:00Z',
      finished_at: null,
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValue(response(job, 202))

    await expect(teachingPrepCatalogApi.startSemesterMappingProposalJob(
      semesterId,
      {
        operation_id: String(job.payload.operation_id),
        material_record_id: materialRecordId,
        expected_source_state_sha256: String(job.payload.source_state_sha256),
      },
    )).resolves.toEqual(job)

    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      `/api/teaching-prep/semesters/${semesterId}/mapping-proposal-jobs`,
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          operation_id: job.payload.operation_id,
          material_record_id: materialRecordId,
          expected_source_state_sha256: job.payload.source_state_sha256,
        }),
      }),
    )
  })

  it('decodes the recoverable semester mapping job list', async () => {
    const semesterId = 's'.repeat(32)
    const job = {
      id: 28,
      job_type: 'teaching_prep.semester_mapping',
      payload: {
        semester_id: semesterId,
        material_record_id: 'r'.repeat(32),
        operation_id: 'semester-mapping-1234567890abcdef1234567890abcdef',
        source_state_sha256: '4'.repeat(64),
      },
      result: {},
      status: 'running',
      progress: 0.5,
      stage: 'calling_model',
      detail: '模型正在生成建议',
      error: null,
      cancel_requested: false,
      created_at: '2026-08-03T00:00:00Z',
      started_at: '2026-08-03T00:00:01Z',
      updated_at: '2026-08-03T00:00:02Z',
      finished_at: null,
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValue(response({ items: [job] }))

    await expect(
      teachingPrepCatalogApi.listSemesterMappingProposalJobs(semesterId),
    ).resolves.toEqual([job])
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      `/api/teaching-prep/semesters/${semesterId}/mapping-proposal-jobs`,
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it.each([
    ['wrong job type', (job: Record<string, unknown>) => {
      job.job_type = 'report_export'
    }],
    ['missing material identity', (job: Record<string, unknown>) => {
      delete (job.payload as Record<string, unknown>).material_record_id
    }],
  ])('rejects a semester mapping Job with %s', async (_label, mutate) => {
    const semesterId = 's'.repeat(32)
    const job: Record<string, unknown> = {
      id: 29,
      job_type: 'teaching_prep.semester_mapping',
      payload: {
        semester_id: semesterId,
        material_record_id: 'r'.repeat(32),
        operation_id: 'semester-mapping-1234567890abcdef1234567890abcdef',
        source_state_sha256: '4'.repeat(64),
      },
      result: {},
      status: 'queued',
      progress: 0,
      stage: 'queued',
      detail: '等待生成课时目录建议',
      error: null,
      cancel_requested: false,
      created_at: '2026-08-03T00:00:00Z',
      started_at: null,
      updated_at: '2026-08-03T00:00:00Z',
      finished_at: null,
    }
    mutate(job)
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(job, 202))

    await expect(teachingPrepCatalogApi.startSemesterMappingProposalJob(
      semesterId,
      {
        operation_id: 'semester-mapping-1234567890abcdef1234567890abcdef',
        material_record_id: 'r'.repeat(32),
        expected_source_state_sha256: '4'.repeat(64),
      },
    )).rejects.toMatchObject({
      kind: 'contract',
      code: 'invalid_success_contract',
    })
  })

  it('rejects mapping proposals with missing review fields', async () => {
    const semesterId = 's'.repeat(32)
    const proposal = {
      id: 'p'.repeat(32),
      semester_id: semesterId,
      operation_id: 'stored-operation',
      source_state_sha256: '4'.repeat(64),
      status: 'proposed',
      payload: {
        tree: [],
        mappings: [{
          mapping_id: 'mapping-1',
          material_record_id: 'r'.repeat(32),
          lesson_ref: 'lesson-1',
          start_unit: 1,
          end_unit: 3,
          purpose: 'textbook',
          decision: 'pending',
          teacher_revision: null,
          decision_reason: null,
        }],
        uncertainties: [],
        source_material_record_ids: ['r'.repeat(32)],
      },
      revision: 1,
      created_at: '2026-08-03T00:00:00Z',
      updated_at: '2026-08-03T00:00:00Z',
      applied_at: null,
    }
    for (const field of [
      'mapping_id',
      'decision',
      'teacher_revision',
      'decision_reason',
    ]) {
      const invalid = structuredClone(proposal)
      delete (invalid.payload.mappings[0] as Record<string, unknown>)[field]
      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
        response({ items: [invalid] }),
      )
      await expect(
        teachingPrepCatalogApi.listSemesterMappingProposals(semesterId),
      ).rejects.toMatchObject({
        kind: 'contract',
        code: 'invalid_success_contract',
      })
    }
  })

  it('accepts directory evidence metadata without treating it as a local path', async () => {
    const semesterId = 's'.repeat(32)
    const proposal = {
      id: 'p'.repeat(32),
      semester_id: semesterId,
      operation_id: 'stored-operation',
      source_state_sha256: '4'.repeat(64),
      status: 'proposed',
      payload: {
        tree: [],
        mappings: [{
          mapping_id: 'mapping-1',
          material_record_id: 'r'.repeat(32),
          lesson_ref: 'lesson-1',
          start_unit: 1,
          end_unit: 3,
          purpose: 'textbook',
          decision: 'pending',
          teacher_revision: null,
          decision_reason: null,
        }],
        uncertainties: [],
        source_material_record_ids: ['r'.repeat(32)],
        directory_evidence: {
          strategy: 'sparse_outline',
          confidence: 'medium',
          scanned_unit_indices: [1, 2, 3],
          toc_entries: [],
          anchors: [],
          resolved_ranges: [],
          printed_to_pdf_offset: null,
          total_unit_count: 67,
          full_page_text_sent: false,
          issues: [],
        },
      },
      revision: 1,
      created_at: '2026-08-03T00:00:00Z',
      updated_at: '2026-08-03T00:00:00Z',
      applied_at: null,
    }
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      response({ items: [proposal] }),
    )

    await expect(
      teachingPrepCatalogApi.listSemesterMappingProposals(semesterId),
    ).resolves.toHaveLength(1)
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

  it('reads the five-minute performance result without inventing a WPS call', async () => {
    const runId = 'e'.repeat(32)
    const payload = {
      execution_run_id: runId,
      slide_plan_id: 'p'.repeat(32),
      status: 'failed',
      budget_ms: 300_000,
      total_machine_elapsed_ms: 301_000,
      draft_elapsed_ms: 301_000,
      wps_elapsed_ms: 0,
      model_call_count: 1,
      wps_execution_count: 0,
      technical_retry_count: 0,
      budget_status: 'exceeded',
      within_budget: false,
      human_review_wait_excluded: true,
    }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(payload))

    await expect(
      teachingPrepCatalogApi.getLessonGenerationPerformance(runId),
    ).resolves.toEqual(payload)
  })
})
