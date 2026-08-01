import { beforeEach, describe, expect, it, vi } from 'vitest'

import { teachingPrepWorkbenchApi } from './workbench'

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

beforeEach(() => vi.restoreAllMocks())

describe('teaching preparation workbench API', () => {
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
})
