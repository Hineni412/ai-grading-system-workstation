import { beforeEach, describe, expect, it, vi } from 'vitest'

import { teachingPrepWorkbenchApi } from './workbench'

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function fullSemesterMappingProposal() {
  return {
    id: 'p'.repeat(32),
    semester_id: 's'.repeat(32),
    operation_id: 'stored-operation',
    source_state_sha256: '4'.repeat(64),
    status: 'proposed',
    payload: {
      tree: [{
        key: 'chapter-1',
        title: '第一章 勾股定理',
        sections: [{
          key: 'section-1',
          title: '1.1 探索勾股定理',
          lessons: [{
            key: 'lesson-1',
            title: '第1课时 探索勾股定理',
            duration_minutes: 45,
          }],
        }],
      }],
      mappings: [{
        mapping_id: 'mapping-1',
        material_record_id: 'r'.repeat(32),
        lesson_ref: 'proposal:lesson-1',
        start_unit: 9,
        end_unit: 14,
        purpose: 'textbook',
        basis: '正文标题与目录一致',
        evidence_refs: ['toc-001', 'range-001'],
        decision: 'accepted',
        teacher_revision: null,
        decision_reason: '教师确认',
      }],
      uncertainties: [],
      source_material_record_ids: ['r'.repeat(32)],
      directory_evidence: {
        strategy: 'toc_calibrated',
        total_unit_count: 67,
        directory_page_unit_indices: [2],
        scanned_unit_indices: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        toc_entries: [{
          evidence_id: 'toc-001',
          level: 'lesson',
          title: '探索勾股定理',
          printed_page: 3,
          printed_page_track: '页',
          page_refs: { 页: 3 },
          source_unit: 2,
          confidence: 0.9987,
          source: 'ocr_layout',
        }],
        resolved_ranges: [{
          evidence_id: 'range-001',
          toc_evidence_id: 'toc-001',
          title: '探索勾股定理',
          level: 'lesson',
          printed_page: 3,
          start_unit: 9,
          end_unit: 14,
        }],
        anchors: [{
          evidence_id: 'anchor-0009',
          unit_index: 9,
          title: '探索勾股定理',
          text_excerpt: '第一章 勾股定理',
        }],
        printed_to_pdf_offset: 6,
        confidence: 'high',
        issues: [],
        full_page_text_sent: false,
      },
    },
    revision: 2,
    created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:01:00Z',
    applied_at: null,
  }
}

beforeEach(() => vi.restoreAllMocks())

describe('teaching preparation workbench API', () => {
  it('returns the full saved proposal after reviewing one directory mapping', async () => {
    const proposal = fullSemesterMappingProposal()
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(proposal))

    await expect(teachingPrepWorkbenchApi.reviewMapping(
      proposal.id,
      'mapping-1',
      { expected_revision: 1, decision: 'accepted', reason: '教师确认' },
    )).resolves.toEqual(proposal)

    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      `/api/teaching-prep/semester-mapping-proposals/${proposal.id}/mappings/mapping-1`,
      expect.objectContaining({ method: 'PATCH' }),
    )
  })

  it.each([
    ['path at proposal root', (proposal: Record<string, unknown>) => {
      proposal.path = 'C:\\private\\mapping.json'
    }],
    ['root inside proposal payload', (proposal: Record<string, unknown>) => {
      const payload = proposal.payload as Record<string, unknown>
      payload.root = 'C:\\private'
    }],
    ['local_file_path inside directory evidence item', (proposal: Record<string, unknown>) => {
      const payload = proposal.payload as Record<string, unknown>
      const evidence = payload.directory_evidence as Record<string, unknown>
      const entries = evidence.toc_entries as Array<Record<string, unknown>>
      entries[0]!.local_file_path = 'C:\\private\\material.pdf'
    }],
  ])('rejects %s after reviewing a mapping', async (_case, mutate) => {
    const proposal = fullSemesterMappingProposal()
    mutate(proposal)
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(proposal))

    await expect(teachingPrepWorkbenchApi.reviewMapping(
      proposal.id,
      'mapping-1',
      { expected_revision: 1, decision: 'accepted' },
    )).rejects.toMatchObject({
      kind: 'contract',
      code: 'invalid_success_contract',
    })
  })

  it('loads all lesson readiness rows with one semester request', async () => {
    const semesterId = 's'.repeat(32)
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({
      items: [{
        lesson_node_id: 'l'.repeat(32),
        title: '一元一次方程',
        sort_order: 1,
        duration_minutes: 45,
        manual_progress: 'preparing',
        manual_progress_revision: 2,
        preparation_stage: 'plan',
        next_action: '确定课堂方案',
        blockers: [],
        latest: {
          resource_pack_id: 'p'.repeat(32),
          lesson_draft_id: null,
          slide_plan_id: null,
          pptx_version_id: null,
          pptx_revision: null,
          up_class_package_id: null,
        },
      }],
    }))

    await expect(teachingPrepWorkbenchApi.lessonStatuses(semesterId))
      .resolves.toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      `/api/teaching-prep/semesters/${semesterId}/lesson-preparation-statuses`,
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('freezes an exact reference draft revision before starting suggestions', async () => {
    const lessonId = 'l'.repeat(32)
    const snapshotId = 'n'.repeat(32)
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response({
        id: snapshotId,
        lesson_node_id: lessonId,
        source_state_sha256: 'a'.repeat(64),
        payload: {},
        created_at: '2026-08-01T00:00:00Z',
      }, 201))
      .mockResolvedValueOnce(response({
        id: 'r'.repeat(32),
        snapshot_id: snapshotId,
        operation_id: 'exercise-operation-0001',
        status: 'running',
        error_code: null,
        model_call_count: 0,
        created_at: '2026-08-01T00:00:00Z',
        updated_at: '2026-08-01T00:00:00Z',
        finished_at: null,
        suggestions: [],
      }, 202))

    await teachingPrepWorkbenchApi.freezeReferenceSnapshot(
      lessonId,
      'reference-token-0001',
      3,
    )
    await teachingPrepWorkbenchApi.startExerciseSuggestions(
      snapshotId,
      'exercise-operation-0001',
    )

    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      request_token: 'reference-token-0001',
      expected_draft_revision: 3,
    })
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      operation_id: 'exercise-operation-0001',
      confirmed: true,
    })
  })

  it('restores a trusted PPTX pointer with optimistic revision protection', async () => {
    const versionId = 'v'.repeat(32)
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({
      version: {
        id: versionId,
        slide_plan_id: 'p'.repeat(32),
        lesson_node_id: 'l'.repeat(32),
        execution_run_id: 'r'.repeat(32),
        version_number: 2,
        status: 'published',
        output_filename: 'lesson-v2.pptx',
        output_sha256: 'a'.repeat(64),
        slide_count: 12,
        verification_report: { verified: true },
        download_url: `/api/teaching-prep/pptx-versions/${versionId}/download`,
        created_at: '2026-08-01T00:00:00Z',
        published_at: '2026-08-01T00:00:00Z',
      },
      current_revision: 4,
      changed: true,
    }))

    await expect(teachingPrepWorkbenchApi.activatePptxVersion(versionId, 3))
      .resolves.toMatchObject({ current_revision: 4, changed: true })
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      expected_revision: 3,
    })
  })

  it('loads verified PPTX versions without weakening local path rejection', async () => {
    const lessonId = 'l'.repeat(32)
    const versionId = 'v'.repeat(32)
    const payload = {
      lesson_node_id: lessonId,
      items: [{
        id: versionId,
        slide_plan_id: 'p'.repeat(32),
        lesson_node_id: lessonId,
        execution_run_id: 'r'.repeat(32),
        version_number: 1,
        status: 'published',
        output_filename: 'lesson-v1.pptx',
        output_sha256: 'a'.repeat(64),
        slide_count: 23,
        verification_report: { verified: true },
        download_url: `/api/teaching-prep/pptx-versions/${versionId}/download`,
        created_at: '2026-08-01T00:00:00Z',
        published_at: '2026-08-01T00:00:00Z',
        is_current: true,
        current_revision: 1,
        preview_url: `/api/teaching-prep/pptx-versions/${versionId}/preview`,
        file_verified: true,
      }],
    }
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(response(payload))

    await expect(teachingPrepWorkbenchApi.pptxVersions(lessonId))
      .resolves.toMatchObject([{ id: versionId, file_verified: true }])

    ;(payload.items[0] as Record<string, unknown>).local_file_path = (
      'C:\\private\\lesson-v1.pptx'
    )
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(response(payload))
    await expect(teachingPrepWorkbenchApi.pptxVersions(lessonId)).rejects.toMatchObject({
      kind: 'contract',
      code: 'invalid_success_contract',
    })
  })
})
