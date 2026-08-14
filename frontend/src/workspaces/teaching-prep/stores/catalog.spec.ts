import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../../api/errors'
import { jobApi, type JobResponse } from '../../../api/jobs'
import { useJobStore } from '../../../stores/jobs'
import {
  teachingPrepCatalogApi,
  type CurriculumEdition,
  type LessonNode,
  type LessonDraftPreflight,
  type MaterialLink,
  type MaterialUnit,
  type MaterialVersion,
  type ResourcePack,
  type SemesterMaterialRecord,
  type SemesterMappingPreflight,
  type SemesterMappingProposal,
  type TeachingSemester,
  type TeachingPreferencesPayload,
} from '../api/catalog'
import { useTeachingPrepCatalogStore } from './catalog'

const preferences: TeachingPreferencesPayload = {
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
}

function lesson(id: string, title: string): LessonNode {
  return {
    id,
    curriculum_id: 'c'.repeat(32),
    parent_id: null,
    node_type: 'lesson',
    title,
    sort_order: 1,
    duration_minutes: 45,
    source_kind: 'teacher',
    is_active: true,
    revision: 1,
    created_at: '2026-07-30T00:00:00Z',
    updated_at: '2026-07-30T00:00:00Z',
  }
}

function materialLink(id: string, lessonNodeId: string): MaterialLink {
  return {
    id,
    lesson_node_id: lessonNodeId,
    material_version_id: 'm'.repeat(32),
    material_name: `资料 ${lessonNodeId[0]}`,
    material_type: 'pdf',
    start_unit: 1,
    end_unit: 1,
    crop: null,
    purpose: 'textbook',
    teacher_note: null,
    confirmation_status: 'confirmed',
    source_version_sha256: '1'.repeat(64),
    sort_order: 1,
    is_active: true,
    revision: 1,
    created_at: '2026-07-30T00:00:00Z',
    updated_at: '2026-07-30T00:00:00Z',
  }
}

function material(id: string): MaterialVersion {
  return {
    id,
    source_id: id,
    display_name: `资料 ${id[0]}`,
    material_type: 'pdf',
    content_sha256: '2'.repeat(64),
    safe_filename: `${id[0]}.pdf`,
    size_bytes: 20,
    modified_ns: null,
    unit_count: 1,
    inspection_status: 'ready',
    availability: 'available',
    created_at: '2026-07-30T00:00:00Z',
  }
}

function materialUnit(id: string, materialVersionId: string): MaterialUnit {
  return {
    id,
    material_version_id: materialVersionId,
    unit_kind: 'pdf_page',
    unit_index: 1,
    title: null,
    text_excerpt: '',
    text_status: 'empty',
    formula_review_required: false,
    object_summary: {},
    preview_url: `/api/teaching-prep/material-units/${id}/preview`,
    revision: 1,
    created_at: '2026-07-30T00:00:00Z',
    updated_at: '2026-07-30T00:00:00Z',
  }
}

function resourcePack(id: string): ResourcePack {
  return {
    id,
    lesson_node_id: 'l'.repeat(32),
    version_number: 1,
    source_state_sha256: '3'.repeat(64),
    pack_sha256: '4'.repeat(64),
    payload: {},
    created_at: '2026-07-30T00:00:00Z',
  }
}

function preflight(resourcePackId: string): LessonDraftPreflight {
  return {
    resource_pack_id: resourcePackId,
    resource_pack_version: 1,
    resource_pack_sha256: '4'.repeat(64),
    mode: 'local_template',
    will_call_model: false,
    model_available: false,
    model_label: null,
    model_destination_fingerprint: 'f'.repeat(64),
    data_scope: {},
    references: [{ id: resourcePackId, label: `范围 ${resourcePackId[0]}` }],
    missing_and_uncertain_count: 0,
    preparation_preferences: preferences,
  }
}

function curriculum(id = 'c'.repeat(32)): CurriculumEdition {
  return {
    id,
    title: '八年级上册',
    grade_level: 8,
    volume: 'first',
    publisher: null,
    edition_label: null,
    revision: 1,
    is_active: true,
    created_at: '2026-07-31T00:00:00Z',
    updated_at: '2026-07-31T00:00:00Z',
  }
}

function semester(
  curriculumId = 'c'.repeat(32),
  id = 's'.repeat(32),
): TeachingSemester {
  return {
    id,
    curriculum_id: curriculumId,
    curriculum_title: '八年级上册',
    school_year: '2026-2027',
    term: 'first',
    planned_new_lesson_count: 48,
    status: 'planning',
    active_lesson_count: 0,
    not_started_lesson_count: 0,
    preparing_lesson_count: 0,
    ready_lesson_count: 0,
    taught_lesson_count: 0,
    skipped_lesson_count: 0,
    material_count: 0,
    parsed_material_count: 0,
    mapped_material_count: 0,
    revision: 1,
    created_at: '2026-07-31T00:00:00Z',
    updated_at: '2026-07-31T00:00:00Z',
  }
}

function semesterMaterial(
  semesterId: string,
  materialItem: MaterialVersion,
  id = 'm'.repeat(32),
): SemesterMaterialRecord {
  return {
    id,
    semester_id: semesterId,
    material_source_id: materialItem.source_id,
    display_name: materialItem.display_name,
    material_role: 'textbook',
    parse_status: 'parsed',
    mapping_status: 'unmapped',
    current_material_version_id: materialItem.id,
    safe_filename: materialItem.safe_filename,
    current_inspection_status: 'ready',
    current_unit_count: 3,
    last_parsed_version_id: materialItem.id,
    has_unparsed_update: false,
    parsed_at: '2026-07-31T00:00:00Z',
    is_active: true,
    revision: 1,
    created_at: '2026-07-31T00:00:00Z',
    updated_at: '2026-07-31T00:00:00Z',
  }
}

function configureMappingSelection(
  store: ReturnType<typeof useTeachingPrepCatalogStore>,
  curriculumItem: CurriculumEdition,
  semesterItem: TeachingSemester,
  materialItem: MaterialVersion,
  record: SemesterMaterialRecord,
): void {
  store.curricula = [curriculumItem]
  store.semesters = [semesterItem]
  store.selectedCurriculumId = curriculumItem.id
  store.materials = [materialItem]
  store.semesterMaterials = [record]
  store.selectedMaterialId = materialItem.id
}

function mappingPreflight(semesterId: string): SemesterMappingPreflight {
  return {
    semester_id: semesterId,
    source_state_sha256: 'a'.repeat(64),
    will_call_model: true,
    model_available: true,
    model_label: '合成模型',
    model_destination_fingerprint: 'f'.repeat(64),
    material_count: 1,
    unit_count: 3,
    existing_lesson_count: 1,
    creates_initial_tree: false,
    automatic_retry: false,
    evidence_strategy: 'toc_calibrated',
    evidence_confidence: 'high',
    scanned_unit_count: 3,
    directory_page_image_count: 0,
    directory_page_images_sent: false,
    toc_entry_count: 1,
    anchor_count: 1,
    estimated_input_characters: 120,
    full_page_text_sent: false,
    evidence_issues: [],
  }
}

function mappingProposal(semesterId: string): SemesterMappingProposal {
  return {
    id: 'p'.repeat(32),
    semester_id: semesterId,
    operation_id: 'stored-operation',
    source_state_sha256: 'a'.repeat(64),
    status: 'proposed',
    payload: {
      tree: [],
      mappings: [],
      uncertainties: [],
      source_material_record_ids: ['m'.repeat(32)],
    },
    revision: 1,
    created_at: '2026-07-31T00:00:00Z',
    updated_at: '2026-07-31T00:00:00Z',
    applied_at: null,
  }
}

function mappingJob(
  semesterId: string,
  materialRecordId = 'm'.repeat(32),
): JobResponse {
  return {
    id: 92,
    job_type: 'teaching_prep.semester_mapping',
    payload: {
      semester_id: semesterId,
      material_record_id: materialRecordId,
      operation_id: 'semester-mapping-1234567890abcdef1234567890abcdef',
      source_state_sha256: 'a'.repeat(64),
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
}

beforeEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
  setActivePinia(createPinia())
})

describe('semester workflow idempotency', () => {
  it('uses one atomic semester-workspace request after a lost response', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const createWorkspace = vi.spyOn(
      teachingPrepCatalogApi,
      'createSemesterWorkspace',
    )
      .mockRejectedValueOnce(new Error('synthetic lost response'))
      .mockResolvedValue({
        curriculum: curriculumItem,
        semester: semesterItem,
      })
    const createCurriculum = vi.spyOn(
      teachingPrepCatalogApi,
      'createCurriculum',
    )
    const createSemester = vi.spyOn(
      teachingPrepCatalogApi,
      'createSemester',
    )
    vi.spyOn(teachingPrepCatalogApi, 'listSemesters')
      .mockResolvedValue([semesterItem])
    const lessons = [lesson('l'.repeat(32), '认识勾股定理')]
    const listLessons = vi.spyOn(teachingPrepCatalogApi, 'listLessons')
      .mockResolvedValue(lessons)
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterLessonProgress')
      .mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMaterials')
      .mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals')
      .mockResolvedValue([])
    const store = useTeachingPrepCatalogStore()
    const input = {
      curriculum: {
        title: curriculumItem.title,
        grade_level: 8,
        volume: 'first' as const,
        publisher: null,
        edition_label: null,
      },
      semester: {
        school_year: semesterItem.school_year,
        term: 'first' as const,
        planned_new_lesson_count: 48,
      },
    }

    await expect(store.createSemesterWorkspace(input)).rejects.toThrow()
    await expect(store.createSemesterWorkspace(input)).resolves.toBeUndefined()

    expect(createWorkspace).toHaveBeenCalledTimes(2)
    expect(createWorkspace.mock.calls[0]?.[0].request_token).not.toBe(
      createWorkspace.mock.calls[1]?.[0].request_token,
    )
    expect(createWorkspace.mock.calls[0]?.[0]).toMatchObject({
      curriculum: input.curriculum,
      ...input.semester,
    })
    expect(createCurriculum).not.toHaveBeenCalled()
    expect(createSemester).not.toHaveBeenCalled()
    expect(listLessons).toHaveBeenCalledWith(curriculumItem.id)
    expect(store.lessonNodes).toEqual(lessons)
  })

  it('submits the checked source fingerprint and tracks the returned Job', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('v'.repeat(32))
    const record = semesterMaterial(semesterItem.id, materialItem)
    const job = mappingJob(semesterItem.id, record.id)
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight(semesterItem.id))
    const submit = vi.spyOn(teachingPrepCatalogApi, 'startSemesterMappingProposalJob')
      .mockResolvedValue(job)
    const listProposals = vi.spyOn(
      teachingPrepCatalogApi,
      'listSemesterMappingProposals',
    )
    const store = useTeachingPrepCatalogStore()
    configureMappingSelection(store, curriculumItem, semesterItem, materialItem, record)

    await store.prepareSemesterMapping([record.id])
    await store.generateSemesterMapping([record.id])

    expect(submit).toHaveBeenCalledExactlyOnceWith(
      semesterItem.id,
      expect.objectContaining({
        material_record_id: record.id,
        expected_source_state_sha256: 'a'.repeat(64),
        operation_id: expect.stringMatching(/^semester-mapping-[0-9a-f]{32}$/),
      }),
    )
    expect(useJobStore().jobs[job.id]).toEqual(job)
    expect(store.currentSemesterMappingJob?.id).toBe(job.id)
    expect(listProposals).not.toHaveBeenCalled()
    expect(localStorage.getItem('ai-grading:teaching-prep:semester-mapping-pending-command:v1')).toBeNull()
  })

  it('does not POST again while the same source Job is active', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('v'.repeat(32))
    const record = semesterMaterial(semesterItem.id, materialItem)
    const job = mappingJob(semesterItem.id, record.id)
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight(semesterItem.id))
    const submit = vi.spyOn(teachingPrepCatalogApi, 'startSemesterMappingProposalJob')
      .mockResolvedValue(job)
    const store = useTeachingPrepCatalogStore()
    configureMappingSelection(store, curriculumItem, semesterItem, materialItem, record)

    await store.prepareSemesterMapping([record.id])
    await store.generateSemesterMapping([record.id])
    await store.generateSemesterMapping([record.id])

    expect(submit).toHaveBeenCalledTimes(1)
  })

  it('does not let an old successful source block a changed source Job', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('v'.repeat(32))
    const record = semesterMaterial(semesterItem.id, materialItem)
    const oldJob = {
      ...mappingJob(semesterItem.id, record.id),
      status: 'succeeded' as const,
      progress: 1,
      stage: 'completed',
      result: { source_state_sha256: 'a'.repeat(64) },
      finished_at: '2026-08-03T00:00:01Z',
    }
    const nextJob = {
      ...mappingJob(semesterItem.id, record.id),
      id: 93,
      payload: {
        ...mappingJob(semesterItem.id, record.id).payload,
        source_state_sha256: 'b'.repeat(64),
      },
    }
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValueOnce(mappingPreflight(semesterItem.id))
      .mockResolvedValueOnce({
        ...mappingPreflight(semesterItem.id),
        source_state_sha256: 'b'.repeat(64),
      })
    const submit = vi.spyOn(teachingPrepCatalogApi, 'startSemesterMappingProposalJob')
      .mockResolvedValueOnce(oldJob)
      .mockResolvedValueOnce(nextJob)
    const store = useTeachingPrepCatalogStore()
    configureMappingSelection(store, curriculumItem, semesterItem, materialItem, record)

    await store.prepareSemesterMapping([record.id])
    await store.generateSemesterMapping([record.id])
    expect(store.currentSemesterMappingJob?.id).toBe(oldJob.id)
    await store.prepareSemesterMapping([record.id])
    expect(store.currentSemesterMappingJob).toBeNull()
    await store.generateSemesterMapping([record.id])

    expect(submit).toHaveBeenNthCalledWith(
      2,
      semesterItem.id,
      expect.objectContaining({ expected_source_state_sha256: 'b'.repeat(64) }),
    )
    expect(store.currentSemesterMappingJob?.id).toBe(nextJob.id)
  })

  it('recovers a lost POST response with GET and never repeats the POST', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('v'.repeat(32))
    const record = semesterMaterial(semesterItem.id, materialItem)
    const job = mappingJob(semesterItem.id, record.id)
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight(semesterItem.id))
    const submit = vi.spyOn(teachingPrepCatalogApi, 'startSemesterMappingProposalJob')
      .mockRejectedValue(new Error('synthetic lost response'))
    const recover = vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposalJobs')
      .mockResolvedValue([job])
    const store = useTeachingPrepCatalogStore()
    configureMappingSelection(store, curriculumItem, semesterItem, materialItem, record)

    await store.prepareSemesterMapping([record.id])
    await store.generateSemesterMapping([record.id])
    await store.generateSemesterMapping([record.id])

    expect(submit).toHaveBeenCalledTimes(1)
    expect(recover).toHaveBeenCalledTimes(1)
    expect(store.currentSemesterMappingJobRecovered).toBe(true)
  })

  it('refreshes and locates the proposal after the tracked Job succeeds', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('v'.repeat(32))
    const record = semesterMaterial(semesterItem.id, materialItem)
    const queued = mappingJob(semesterItem.id, record.id)
    const proposal = mappingProposal(semesterItem.id)
    proposal.payload.source_material_record_ids = [record.id]
    proposal.operation_id = String(queued.payload.operation_id)
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight(semesterItem.id))
    vi.spyOn(teachingPrepCatalogApi, 'startSemesterMappingProposalJob')
      .mockResolvedValue(queued)
    const listProposals = vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals')
      .mockResolvedValue([proposal])
    const store = useTeachingPrepCatalogStore()
    configureMappingSelection(store, curriculumItem, semesterItem, materialItem, record)

    await store.prepareSemesterMapping([record.id])
    await store.generateSemesterMapping([record.id])
    useJobStore().track({
      ...queued,
      status: 'succeeded',
      progress: 1,
      stage: 'completed',
      result: {
        semester_id: semesterItem.id,
        operation_id: proposal.operation_id,
        source_state_sha256: proposal.source_state_sha256,
        proposal_id: proposal.id,
        recovered_existing: false,
      },
      updated_at: '2026-08-03T00:00:10Z',
      finished_at: '2026-08-03T00:00:10Z',
    })
    await vi.waitFor(() => expect(listProposals).toHaveBeenCalledTimes(1))

    expect(store.currentSemesterMappingProposal?.id).toBe(proposal.id)
  })

  it('recovers a durable proposal when the first terminal Job refresh is stale', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('v'.repeat(32))
    const record = semesterMaterial(semesterItem.id, materialItem)
    const queued = mappingJob(semesterItem.id, record.id)
    const proposal = mappingProposal(semesterItem.id)
    proposal.payload.source_material_record_ids = [record.id]
    proposal.operation_id = String(queued.payload.operation_id)
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight(semesterItem.id))
    vi.spyOn(teachingPrepCatalogApi, 'startSemesterMappingProposalJob')
      .mockResolvedValue(queued)
    const listProposals = vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals')
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([proposal])
    const store = useTeachingPrepCatalogStore()
    configureMappingSelection(store, curriculumItem, semesterItem, materialItem, record)

    await store.prepareSemesterMapping([record.id])
    await store.generateSemesterMapping([record.id])
    useJobStore().track({
      ...queued,
      status: 'succeeded',
      progress: 1,
      stage: 'completed',
      result: {
        semester_id: semesterItem.id,
        operation_id: proposal.operation_id,
        source_state_sha256: proposal.source_state_sha256,
        proposal_id: proposal.id,
        recovered_existing: false,
      },
      updated_at: '2026-08-03T00:00:10Z',
      finished_at: '2026-08-03T00:00:10Z',
    })
    await vi.waitFor(() => expect(listProposals).toHaveBeenCalledTimes(2))
    await vi.waitFor(() => expect(store.currentSemesterMappingProposal?.id).toBe(proposal.id))
  })

  it('does not expose another operation proposal while recovering the current Job', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('v'.repeat(32))
    const record = semesterMaterial(semesterItem.id, materialItem)
    const queued = mappingJob(semesterItem.id, record.id)
    const old = mappingProposal(semesterItem.id)
    old.payload.source_material_record_ids = [record.id]
    old.operation_id = 'semester-mapping-old-operation'
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight(semesterItem.id))
    vi.spyOn(teachingPrepCatalogApi, 'startSemesterMappingProposalJob')
      .mockResolvedValue(queued)
    const listProposals = vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals')
      .mockResolvedValue([old])
    const store = useTeachingPrepCatalogStore()
    configureMappingSelection(store, curriculumItem, semesterItem, materialItem, record)

    await store.prepareSemesterMapping([record.id])
    await store.generateSemesterMapping([record.id])
    useJobStore().track({
      ...queued,
      status: 'succeeded',
      progress: 1,
      stage: 'completed',
      result: {
        semester_id: semesterItem.id,
        operation_id: String(queued.payload.operation_id),
        source_state_sha256: String(queued.payload.source_state_sha256),
        proposal_id: 'z'.repeat(32),
        recovered_existing: false,
      },
      updated_at: '2026-08-03T00:00:10Z',
      finished_at: '2026-08-03T00:00:10Z',
    })
    await vi.waitFor(() => expect(listProposals).toHaveBeenCalledTimes(2))

    expect(store.currentSemesterMappingProposal).toBeNull()
  })

  it('falls back to the local PPT proposal when the current Job has no exact match', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('v'.repeat(32))
    const record = semesterMaterial(semesterItem.id, materialItem)
    const queued = mappingJob(semesterItem.id, record.id)
    const localProposal = mappingProposal(semesterItem.id)
    localProposal.payload.source_material_record_ids = [record.id]
    localProposal.payload.generation_source = 'local_reference_ppt_names'
    localProposal.operation_id = 'local-ppt-operation'
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight(semesterItem.id))
    vi.spyOn(teachingPrepCatalogApi, 'startSemesterMappingProposalJob')
      .mockResolvedValue(queued)
    const listProposals = vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals')
      .mockResolvedValue([localProposal])
    const store = useTeachingPrepCatalogStore()
    configureMappingSelection(store, curriculumItem, semesterItem, materialItem, record)
    // 页面加载时已读到本地 PPT 建议；job 恢复只写回精确匹配的建议，不会覆盖它
    store.semesterMappingProposals = [localProposal]

    await store.prepareSemesterMapping([record.id])
    await store.generateSemesterMapping([record.id])
    useJobStore().track({
      ...queued,
      status: 'succeeded',
      progress: 1,
      stage: 'completed',
      result: {
        semester_id: semesterItem.id,
        operation_id: String(queued.payload.operation_id),
        source_state_sha256: String(queued.payload.source_state_sha256),
        proposal_id: 'z'.repeat(32),
        recovered_existing: false,
      },
      updated_at: '2026-08-03T00:00:10Z',
      finished_at: '2026-08-03T00:00:10Z',
    })
    await vi.waitFor(() => expect(listProposals).toHaveBeenCalledTimes(2))

    // 本地 PPT 课时树建议不依赖 job 匹配，始终可达
    expect(store.currentSemesterMappingProposal?.id).toBe(localProposal.id)
  })

  it('deduplicates concurrent recovery for the same terminal Job', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('v'.repeat(32))
    const record = semesterMaterial(semesterItem.id, materialItem)
    const queued = mappingJob(semesterItem.id, record.id)
    const proposal = mappingProposal(semesterItem.id)
    proposal.payload.source_material_record_ids = [record.id]
    proposal.operation_id = String(queued.payload.operation_id)
    let releaseFirst!: (items: SemesterMappingProposal[]) => void
    const firstResponse = new Promise<SemesterMappingProposal[]>(resolve => {
      releaseFirst = resolve
    })
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight(semesterItem.id))
    vi.spyOn(teachingPrepCatalogApi, 'startSemesterMappingProposalJob')
      .mockResolvedValue(queued)
    const listProposals = vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals')
      .mockReturnValueOnce(firstResponse)
      .mockResolvedValueOnce([proposal])
    const store = useTeachingPrepCatalogStore()
    configureMappingSelection(store, curriculumItem, semesterItem, materialItem, record)

    await store.prepareSemesterMapping([record.id])
    await store.generateSemesterMapping([record.id])
    useJobStore().track({
      ...queued,
      status: 'succeeded',
      progress: 1,
      stage: 'completed',
      result: {
        semester_id: semesterItem.id,
        operation_id: proposal.operation_id,
        source_state_sha256: proposal.source_state_sha256,
        proposal_id: proposal.id,
        recovered_existing: false,
      },
      updated_at: '2026-08-03T00:00:10Z',
      finished_at: '2026-08-03T00:00:10Z',
    })
    await vi.waitFor(() => expect(listProposals).toHaveBeenCalledTimes(1))
    const concurrentPrepare = store.prepareSemesterMapping([record.id])
    await Promise.resolve()
    expect(listProposals).toHaveBeenCalledTimes(1)

    releaseFirst([])
    await concurrentPrepare
    await vi.waitFor(() => expect(store.currentSemesterMappingProposal?.id).toBe(proposal.id))
    expect(listProposals).toHaveBeenCalledTimes(2)
  })

  it('recovers the durable proposal even when the Job completion record fails', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('v'.repeat(32))
    const record = semesterMaterial(semesterItem.id, materialItem)
    const queued = mappingJob(semesterItem.id, record.id)
    const durable = mappingProposal(semesterItem.id)
    durable.payload.source_material_record_ids = [record.id]
    durable.operation_id = String(queued.payload.operation_id)
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight(semesterItem.id))
    vi.spyOn(teachingPrepCatalogApi, 'startSemesterMappingProposalJob')
      .mockResolvedValue(queued)
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals')
      .mockResolvedValue([durable])
    const store = useTeachingPrepCatalogStore()
    configureMappingSelection(store, curriculumItem, semesterItem, materialItem, record)

    await store.prepareSemesterMapping([record.id])
    await store.generateSemesterMapping([record.id])
    useJobStore().track({
      ...queued,
      status: 'failed',
      stage: 'persisting_proposal',
      error: '任务完成记录没有写入',
      updated_at: '2026-08-03T00:00:10Z',
      finished_at: '2026-08-03T00:00:10Z',
    })
    await vi.waitFor(() => expect(store.currentSemesterMappingProposal?.id).toBe(durable.id))
  })

  it('restores the server-listed Job when switching semesters without POSTing', async () => {
    const oldCurriculum = curriculum('o'.repeat(32))
    const nextCurriculum = curriculum('n'.repeat(32))
    const semesterItem = semester(nextCurriculum.id)
    const materialItem = material('v'.repeat(32))
    const record = semesterMaterial(semesterItem.id, materialItem)
    const job = mappingJob(semesterItem.id, record.id)
    vi.spyOn(teachingPrepCatalogApi, 'listLessons').mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterLessonProgress').mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMaterials').mockResolvedValue([record])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals').mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposalJobs').mockResolvedValue([job])
    const submit = vi.spyOn(teachingPrepCatalogApi, 'startSemesterMappingProposalJob')
    const store = useTeachingPrepCatalogStore()
    store.curricula = [oldCurriculum, nextCurriculum]
    store.semesters = [semesterItem]
    store.selectedCurriculumId = oldCurriculum.id
    store.materials = [materialItem]

    await store.selectCurriculum(nextCurriculum.id)
    store.selectedMaterialId = materialItem.id

    expect(store.currentSemesterMappingJob?.id).toBe(job.id)
    expect(store.currentSemesterMappingJobRecovered).toBe(true)
    expect(useJobStore().jobs[job.id]).toEqual(job)
    expect(submit).not.toHaveBeenCalled()
  })

  it('requires an exact current-material preflight before creating a Job', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('v'.repeat(32))
    const record = semesterMaterial(semesterItem.id, materialItem)
    const submit = vi.spyOn(teachingPrepCatalogApi, 'startSemesterMappingProposalJob')
    const store = useTeachingPrepCatalogStore()
    configureMappingSelection(store, curriculumItem, semesterItem, materialItem, record)

    await expect(store.generateSemesterMapping([record.id])).rejects.toThrow(
      '发送范围已失效',
    )
    expect(submit).not.toHaveBeenCalled()
  })

  it('does not expose an old proposal until the current source is preflighted', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('v'.repeat(32))
    const record = semesterMaterial(semesterItem.id, materialItem)
    const old = mappingProposal(semesterItem.id)
    old.payload.source_material_record_ids = [record.id]
    const store = useTeachingPrepCatalogStore()
    configureMappingSelection(store, curriculumItem, semesterItem, materialItem, record)
    store.semesterMappingProposals = [old]

    expect(store.currentSemesterMappingProposal).toBeNull()
  })

  it('ignores a terminal Job proposal refresh after the material changes', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialA = material('a'.repeat(32))
    const materialB = material('b'.repeat(32))
    const recordA = semesterMaterial(semesterItem.id, materialA, 'm'.repeat(32))
    const recordB = semesterMaterial(semesterItem.id, materialB, 'n'.repeat(32))
    const jobA = {
      ...mappingJob(semesterItem.id, recordA.id),
      id: 101,
      status: 'succeeded' as const,
      progress: 1,
      stage: 'completed',
      result: { proposal_id: 'q'.repeat(32) },
      finished_at: '2026-08-03T00:00:01Z',
    }
    const jobB = {
      ...mappingJob(semesterItem.id, recordB.id),
      id: 102,
      payload: {
        ...mappingJob(semesterItem.id, recordB.id).payload,
        source_state_sha256: 'b'.repeat(64),
      },
      status: 'succeeded' as const,
      progress: 1,
      stage: 'completed',
      result: { proposal_id: 'r'.repeat(32) },
      finished_at: '2026-08-03T00:00:02Z',
    }
    const proposalA = {
      ...mappingProposal(semesterItem.id),
      id: 'q'.repeat(32),
      payload: {
        ...mappingProposal(semesterItem.id).payload,
        source_material_record_ids: [recordA.id],
      },
    }
    const proposalB = {
      ...mappingProposal(semesterItem.id),
      id: 'r'.repeat(32),
      operation_id: 'semester-mapping-1234567890abcdef1234567890abcdef',
      source_state_sha256: 'b'.repeat(64),
      payload: {
        ...mappingProposal(semesterItem.id).payload,
        source_material_record_ids: [recordB.id],
      },
    }
    let releaseA!: (items: SemesterMappingProposal[]) => void
    let releaseB!: (items: SemesterMappingProposal[]) => void
    const responseA = new Promise<SemesterMappingProposal[]>(resolve => { releaseA = resolve })
    const responseB = new Promise<SemesterMappingProposal[]>(resolve => { releaseB = resolve })
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValueOnce(mappingPreflight(semesterItem.id))
      .mockResolvedValueOnce({
        ...mappingPreflight(semesterItem.id),
        source_state_sha256: 'b'.repeat(64),
      })
    vi.spyOn(teachingPrepCatalogApi, 'startSemesterMappingProposalJob')
      .mockResolvedValueOnce(jobA)
      .mockResolvedValueOnce(jobB)
    const list = vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals')
      .mockReturnValueOnce(responseA)
      .mockReturnValueOnce(responseB)
    const store = useTeachingPrepCatalogStore()
    configureMappingSelection(store, curriculumItem, semesterItem, materialA, recordA)
    store.materials = [materialA, materialB]
    store.semesterMaterials = [recordA, recordB]

    await store.prepareSemesterMapping([recordA.id])
    await store.generateSemesterMapping([recordA.id])
    await vi.waitFor(() => expect(list).toHaveBeenCalledTimes(1))
    store.selectedMaterialId = materialB.id
    await store.prepareSemesterMapping([recordB.id])
    await store.generateSemesterMapping([recordB.id])
    await vi.waitFor(() => expect(list).toHaveBeenCalledTimes(2))

    releaseB([proposalB])
    await vi.waitFor(() => expect(store.currentSemesterMappingProposal?.id).toBe(proposalB.id))
    releaseA([proposalA])
    await Promise.resolve()
    await Promise.resolve()
    expect(store.currentSemesterMappingProposal?.id).toBe(proposalB.id)
  })
})

describe('teaching preparation selection consistency', () => {
  it('keeps successful same-semester material data when a retry is only partly available', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('a'.repeat(32))
    const record = semesterMaterial(semesterItem.id, materialItem)
    const proposal = mappingProposal(semesterItem.id)
    vi.spyOn(teachingPrepCatalogApi, 'listMaterials').mockResolvedValue([materialItem])
    vi.spyOn(teachingPrepCatalogApi, 'listMaterialParseJobs').mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMaterials')
      .mockResolvedValueOnce([record])
      .mockRejectedValueOnce(new Error('semester materials temporarily unavailable'))
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals')
      .mockResolvedValueOnce([proposal])
      .mockRejectedValueOnce(new Error('mapping proposals temporarily unavailable'))
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposalJobs')
      .mockRejectedValueOnce(new Error('mapping jobs temporarily unavailable'))
      .mockResolvedValueOnce([])
    const store = useTeachingPrepCatalogStore()
    store.curricula = [curriculumItem]
    store.semesters = [semesterItem]
    store.selectedCurriculumId = curriculumItem.id
    store.selectedSemesterId = semesterItem.id

    await store.ensureMaterialData()
    expect(store.semesterMaterials).toEqual([record])
    expect(store.semesterMappingProposals).toEqual([proposal])

    await store.ensureMaterialData()

    expect(store.semesterMaterials).toEqual([record])
    expect(store.semesterMappingProposals).toEqual([proposal])
    expect(store.errorMessage).toContain('学期资料、目录建议')
  })

  it('reuses global material data when switching to another semester', async () => {
    const curriculumItem = curriculum()
    const firstSemester = semester(curriculumItem.id, 's'.repeat(32))
    const secondSemester = semester(curriculumItem.id, 't'.repeat(32))
    const materialItem = material('a'.repeat(32))
    const firstRecord = semesterMaterial(firstSemester.id, materialItem, 'm'.repeat(32))
    const secondRecord = semesterMaterial(secondSemester.id, materialItem, 'n'.repeat(32))
    const listMaterials = vi.spyOn(teachingPrepCatalogApi, 'listMaterials')
      .mockResolvedValue([materialItem])
    const listParseJobs = vi.spyOn(teachingPrepCatalogApi, 'listMaterialParseJobs')
      .mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listLessons').mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterLessonProgress').mockResolvedValue([])
    const listSemesterMaterials = vi.spyOn(
      teachingPrepCatalogApi,
      'listSemesterMaterials',
    ).mockImplementation(async semesterId => (
      semesterId === firstSemester.id ? [firstRecord] : [secondRecord]
    ))
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals')
      .mockImplementation(async semesterId => [mappingProposal(semesterId)])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposalJobs')
      .mockResolvedValue([])
    const store = useTeachingPrepCatalogStore()
    store.curricula = [curriculumItem]
    store.semesters = [firstSemester, secondSemester]
    store.selectedCurriculumId = curriculumItem.id
    store.selectedSemesterId = firstSemester.id

    await store.ensureMaterialData()
    await store.selectSemester(secondSemester.id)

    expect(listMaterials).toHaveBeenCalledTimes(1)
    expect(listParseJobs).toHaveBeenCalledTimes(1)
    expect(listSemesterMaterials).toHaveBeenCalledTimes(4)
    expect(store.selectedSemester?.id).toBe(secondSemester.id)
    expect(store.semesterMaterials).toEqual([secondRecord])
  })

  it('opens saved partial pages without implicitly restarting parsing', async () => {
    const incomplete: MaterialVersion = {
      ...material('c'.repeat(32)),
      unit_count: null,
      inspection_status: 'uninspected',
      parse_expected_unit_count: 3,
      preview_completed_count: 1,
    }
    const partial = materialUnit('3'.repeat(32), incomplete.id)
    vi.spyOn(teachingPrepCatalogApi, 'listMaterialUnits').mockResolvedValue([partial])
    vi.spyOn(teachingPrepCatalogApi, 'listMaterials').mockResolvedValue([incomplete])
    const start = vi.spyOn(teachingPrepCatalogApi, 'startMaterialParse')
    const store = useTeachingPrepCatalogStore()

    await store.openMaterial(incomplete)

    expect(store.materialUnits).toEqual([partial])
    expect(start).not.toHaveBeenCalled()
  })

  it('restores and continues polling a persisted material parse job', async () => {
    vi.useFakeTimers()
    const materialItem = material('a'.repeat(32))
    const runningJob: JobResponse = {
      id: 91,
      job_type: 'teaching_prep.material_parse',
      payload: { material_version_id: materialItem.id },
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
    const succeededJob: JobResponse = {
      ...runningJob,
      status: 'succeeded',
      progress: 1,
      stage: 'completed',
      detail: '已完成 67 页/张',
      updated_at: '2026-08-02T00:02:00Z',
      finished_at: '2026-08-02T00:02:00Z',
    }
    vi.spyOn(teachingPrepCatalogApi, 'status').mockResolvedValue({
      module: 'teaching-prep',
      enabled: true,
      schema_version: '013_workbench_iteration',
      real_model_enabled: false,
      semester_mapping_model_available: false,
      exercise_suggestion_model_available: false,
      real_wps_enabled: false,
      wps_execution_available: false,
    })
    vi.spyOn(teachingPrepCatalogApi, 'getTeachingPreferences')
      .mockResolvedValue({
        revision: 1,
        payload: preferences,
        updated_at: '2026-08-02T00:00:00Z',
      })
    vi.spyOn(teachingPrepCatalogApi, 'listCurricula').mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesters').mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listMaterials')
      .mockResolvedValue([materialItem])
    vi.spyOn(teachingPrepCatalogApi, 'listMaterialParseJobs')
      .mockResolvedValue([runningJob])
    vi.spyOn(jobApi, 'getJob').mockResolvedValue(succeededJob)
    const store = useTeachingPrepCatalogStore()

    try {
      await store.load()
      expect(store.materialParseJobs[materialItem.id]).toEqual(runningJob)

      await vi.advanceTimersByTimeAsync(2_000)
      await Promise.resolve()

      expect(store.materialParseJobs[materialItem.id]).toEqual(succeededJob)
      expect(jobApi.getJob).toHaveBeenCalledWith(runningJob.id)
    } finally {
      vi.useRealTimers()
    }
  })

  it('starts material parse polling at two seconds and backs off after consecutive network failures', async () => {
    vi.useFakeTimers()
    const materialItem = material('a'.repeat(32))
    const runningJob: JobResponse = {
      id: 101,
      job_type: 'teaching_prep.material_parse',
      payload: { material_version_id: materialItem.id },
      result: {},
      status: 'running',
      progress: 0.2,
      stage: 'ocr',
      detail: '正在识别',
      error: null,
      cancel_requested: false,
      created_at: '2026-08-03T00:00:00Z',
      started_at: '2026-08-03T00:00:00Z',
      updated_at: '2026-08-03T00:00:00Z',
      finished_at: null,
    }
    const succeededJob: JobResponse = {
      ...runningJob,
      status: 'succeeded',
      progress: 1,
      stage: 'completed',
      detail: '解析完成',
      updated_at: '2026-08-03T00:00:08Z',
      finished_at: '2026-08-03T00:00:08Z',
    }
    const offline = new ApiError({
      kind: 'network', status: null, code: 'network_error', message: 'offline',
      details: {}, requestId: 'material-poll-offline', retryable: true,
    })
    vi.spyOn(teachingPrepCatalogApi, 'startMaterialParse').mockResolvedValue(runningJob)
    vi.spyOn(teachingPrepCatalogApi, 'listMaterials').mockResolvedValue([materialItem])
    vi.spyOn(jobApi, 'getJob')
      .mockRejectedValueOnce(offline)
      .mockRejectedValueOnce(offline)
      .mockResolvedValueOnce(succeededJob)
    const store = useTeachingPrepCatalogStore()
    store.materials = [materialItem]

    try {
      const parsing = store.parseMaterialInBackground(materialItem)

      await vi.advanceTimersByTimeAsync(1_999)
      expect(jobApi.getJob).not.toHaveBeenCalled()
      await vi.advanceTimersByTimeAsync(1)
      expect(jobApi.getJob).toHaveBeenCalledTimes(1)

      await vi.advanceTimersByTimeAsync(2_000)
      expect(jobApi.getJob).toHaveBeenCalledTimes(2)
      await vi.advanceTimersByTimeAsync(3_999)
      expect(jobApi.getJob).toHaveBeenCalledTimes(2)
      await vi.advanceTimersByTimeAsync(1)
      await parsing

      expect(jobApi.getJob).toHaveBeenCalledTimes(3)
      expect(store.materialParseJobs[materialItem.id]).toEqual(succeededJob)
      expect(teachingPrepCatalogApi.listMaterials).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('stops material parse polling after cancellation reaches a terminal state', async () => {
    vi.useFakeTimers()
    const materialItem = material('a'.repeat(32))
    const runningJob: JobResponse = {
      id: 102,
      job_type: 'teaching_prep.material_parse',
      payload: { material_version_id: materialItem.id },
      result: {},
      status: 'running',
      progress: 0.2,
      stage: 'ocr',
      detail: '正在识别',
      error: null,
      cancel_requested: false,
      created_at: '2026-08-03T00:00:00Z',
      started_at: '2026-08-03T00:00:00Z',
      updated_at: '2026-08-03T00:00:00Z',
      finished_at: null,
    }
    const cancelledJob: JobResponse = {
      ...runningJob,
      status: 'cancelled',
      stage: 'cancelled',
      detail: '资料解析已取消',
      cancel_requested: true,
      updated_at: '2026-08-03T00:00:01Z',
      finished_at: '2026-08-03T00:00:01Z',
    }
    vi.spyOn(teachingPrepCatalogApi, 'startMaterialParse').mockResolvedValue(runningJob)
    vi.spyOn(teachingPrepCatalogApi, 'listMaterials').mockResolvedValue([materialItem])
    vi.spyOn(jobApi, 'cancelJob').mockResolvedValue(cancelledJob)
    vi.spyOn(jobApi, 'getJob').mockResolvedValue(cancelledJob)
    const store = useTeachingPrepCatalogStore()
    store.materials = [materialItem]

    try {
      const parsing = store.parseMaterialInBackground(materialItem)
      await Promise.resolve()
      await store.cancelMaterialParse(materialItem.id)

      expect(jobApi.cancelJob).toHaveBeenCalledWith(runningJob.id)
      expect(store.materialParseJobs[materialItem.id]).toEqual(cancelledJob)

      const cancelled = expect(parsing).rejects.toThrow(
        '资料解析已取消，可保留进度后重试。',
      )
      await vi.advanceTimersByTimeAsync(2_000)
      await cancelled

      expect(jobApi.getJob).toHaveBeenCalledTimes(1)
      expect(store.materialParseJobs[materialItem.id]).toEqual(cancelledJob)
      expect(teachingPrepCatalogApi.listMaterials).toHaveBeenCalledTimes(1)

      await vi.advanceTimersByTimeAsync(60_000)
      expect(jobApi.getJob).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not let a background parse preview overwrite a newer material', async () => {
    vi.useFakeTimers()
    const materialA = material('a'.repeat(32))
    const materialB = material('b'.repeat(32))
    const unitA = materialUnit('1'.repeat(32), materialA.id)
    const running: JobResponse = {
      id: 103,
      job_type: 'teaching_prep.material_parse',
      payload: { material_version_id: materialA.id },
      result: {}, status: 'running', progress: 0.1, stage: 'preview',
      detail: '正在生成预览', error: null, cancel_requested: false,
      created_at: '2026-08-03T00:00:00Z', started_at: '2026-08-03T00:00:00Z',
      updated_at: '2026-08-03T00:00:00Z', finished_at: null,
    }
    const preview = { ...running, progress: 0.2, updated_at: '2026-08-03T00:00:01Z' }
    const succeeded: JobResponse = {
      ...preview, status: 'succeeded', progress: 1, stage: 'completed',
      updated_at: '2026-08-03T00:00:02Z', finished_at: '2026-08-03T00:00:02Z',
    }
    let releaseUnits!: (items: MaterialUnit[]) => void
    const pendingUnits = new Promise<MaterialUnit[]>(resolve => { releaseUnits = resolve })
    vi.spyOn(teachingPrepCatalogApi, 'startMaterialParse').mockResolvedValue(running)
    vi.spyOn(teachingPrepCatalogApi, 'listMaterialUnits').mockReturnValue(pendingUnits)
    vi.spyOn(teachingPrepCatalogApi, 'listMaterials').mockResolvedValue([materialA, materialB])
    vi.spyOn(jobApi, 'getJob')
      .mockResolvedValueOnce(preview)
      .mockResolvedValueOnce(succeeded)
    const store = useTeachingPrepCatalogStore()
    store.materials = [materialA, materialB]
    store.selectedMaterialId = materialA.id

    try {
      const parsing = store.parseMaterialInBackground(materialA)
      await vi.advanceTimersByTimeAsync(2_000)
      await vi.waitFor(() => expect(teachingPrepCatalogApi.listMaterialUnits).toHaveBeenCalled())
      store.selectedMaterialId = materialB.id
      releaseUnits([unitA])
      await Promise.resolve()
      await vi.advanceTimersByTimeAsync(2_000)
      await parsing

      expect(store.selectedMaterialId).toBe(materialB.id)
      expect(store.materialUnits).toEqual([])
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not let a slower old lesson response replace the current lesson', async () => {
    const lessonA = lesson('a'.repeat(32), '课时 A')
    const lessonB = lesson('b'.repeat(32), '课时 B')
    const linkA = materialLink('1'.repeat(32), lessonA.id)
    const linkB = materialLink('2'.repeat(32), lessonB.id)
    let releaseLessonA!: (items: MaterialLink[]) => void
    const lessonAResponse = new Promise<MaterialLink[]>((resolve) => {
      releaseLessonA = resolve
    })
    vi.spyOn(teachingPrepCatalogApi, 'listMaterialLinks')
      .mockImplementation((lessonId) => (
        lessonId === lessonA.id ? lessonAResponse : Promise.resolve([linkB])
      ))
    vi.spyOn(teachingPrepCatalogApi, 'listExerciseCandidates')
      .mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listResourcePacks')
      .mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'getResourcePackStatus')
      .mockImplementation(async (lessonId) => ({
        lesson_node_id: lessonId,
        has_pack: false,
        latest_version_number: null,
        latest_pack_id: null,
        local_sources_changed: false,
      }))
    vi.spyOn(teachingPrepCatalogApi, 'listClassVariants')
      .mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listUpClassPackages')
      .mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listPostLessonReviews')
      .mockResolvedValue([])
    const store = useTeachingPrepCatalogStore()

    const selectA = store.selectLesson(lessonA)
    await Promise.resolve()
    await store.selectLesson(lessonB)
    releaseLessonA([linkA])
    await selectA

    expect(store.selectedLessonId).toBe(lessonB.id)
    expect(store.materialLinks).toEqual([linkB])
    expect(store.errorMessage).toBe('')
  })

  it('clears the first material pages as soon as a second material is selected', async () => {
    const materialA = material('a'.repeat(32))
    const materialB = material('b'.repeat(32))
    const unitA = materialUnit('1'.repeat(32), materialA.id)
    const unitB = materialUnit('2'.repeat(32), materialB.id)
    let releaseMaterialB!: (items: MaterialUnit[]) => void
    const materialBResponse = new Promise<MaterialUnit[]>((resolve) => {
      releaseMaterialB = resolve
    })
    vi.spyOn(teachingPrepCatalogApi, 'listMaterialUnits')
      .mockImplementation((materialId) => (
        materialId === materialA.id
          ? Promise.resolve([unitA])
          : materialBResponse
      ))
    vi.spyOn(teachingPrepCatalogApi, 'listMaterials')
      .mockResolvedValue([materialA, materialB])
    const store = useTeachingPrepCatalogStore()

    await store.openMaterial(materialA)
    const openB = store.openMaterial(materialB)

    expect(store.selectedMaterialId).toBe(materialB.id)
    expect(store.materialUnits).toEqual([])

    releaseMaterialB([unitB])
    await openB
    expect(store.materialUnits).toEqual([unitB])
  })

  it('does not let a slower old material response replace the open material', async () => {
    const materialA = material('a'.repeat(32))
    const materialB = material('b'.repeat(32))
    const unitA = materialUnit('1'.repeat(32), materialA.id)
    const unitB = materialUnit('2'.repeat(32), materialB.id)
    let releaseMaterialA!: (items: MaterialUnit[]) => void
    const materialAResponse = new Promise<MaterialUnit[]>((resolve) => {
      releaseMaterialA = resolve
    })
    vi.spyOn(teachingPrepCatalogApi, 'listMaterialUnits')
      .mockImplementation((materialId) => (
        materialId === materialA.id
          ? materialAResponse
          : Promise.resolve([unitB])
      ))
    vi.spyOn(teachingPrepCatalogApi, 'listMaterials')
      .mockResolvedValue([materialA, materialB])
    const store = useTeachingPrepCatalogStore()

    const openA = store.openMaterial(materialA)
    await Promise.resolve()
    await store.openMaterial(materialB)
    releaseMaterialA([unitA])
    await openA

    expect(store.selectedMaterialId).toBe(materialB.id)
    expect(store.materialUnits).toEqual([unitB])
    expect(store.loadState).toBe('ready')
  })

  it('ignores an old material failure after a newer material has loaded', async () => {
    const materialA = material('a'.repeat(32))
    const materialB = material('b'.repeat(32))
    const unitB = materialUnit('2'.repeat(32), materialB.id)
    let rejectMaterialA!: (error: Error) => void
    const materialAResponse = new Promise<MaterialUnit[]>((_, reject) => {
      rejectMaterialA = reject
    })
    vi.spyOn(teachingPrepCatalogApi, 'listMaterialUnits')
      .mockImplementation(materialId => (
        materialId === materialA.id
          ? materialAResponse
          : Promise.resolve([unitB])
      ))
    vi.spyOn(teachingPrepCatalogApi, 'listMaterials')
      .mockResolvedValue([materialA, materialB])
    const store = useTeachingPrepCatalogStore()

    const openA = store.openMaterial(materialA)
    await Promise.resolve()
    await store.openMaterial(materialB)
    rejectMaterialA(new Error('synthetic old failure'))

    await expect(openA).resolves.toBe('stale')
    expect(store.selectedMaterialId).toBe(materialB.id)
    expect(store.materialUnits).toEqual([unitB])
    expect(store.errorMessage).toBe('')
  })

  it('clears pages and rethrows when the current material fails to open', async () => {
    const materialItem = material('a'.repeat(32))
    vi.spyOn(teachingPrepCatalogApi, 'listMaterialUnits')
      .mockRejectedValue(new Error('synthetic current failure'))
    const store = useTeachingPrepCatalogStore()
    store.materialUnits = [materialUnit('1'.repeat(32), materialItem.id)]

    await expect(store.openMaterial(materialItem)).rejects.toThrow(
      'synthetic current failure',
    )
    expect(store.materialUnits).toEqual([])
    expect(store.loadState).toBe('error')
  })

  it('discards a stale mapping preflight after the material changes', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialA = material('a'.repeat(32))
    const materialB = material('b'.repeat(32))
    let releasePreflight!: (item: SemesterMappingPreflight) => void
    const oldPreflight = new Promise<SemesterMappingPreflight>((resolve) => {
      releasePreflight = resolve
    })
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockReturnValue(oldPreflight)
    vi.spyOn(teachingPrepCatalogApi, 'listMaterialUnits').mockResolvedValue([])
    const recordA = semesterMaterial(semesterItem.id, materialA, 'r'.repeat(32))
    vi.spyOn(teachingPrepCatalogApi, 'listMaterials')
      .mockResolvedValue([materialA, materialB])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMaterials')
      .mockResolvedValue([recordA])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesters')
      .mockResolvedValue([semesterItem])
    const store = useTeachingPrepCatalogStore()
    configureMappingSelection(store, curriculumItem, semesterItem, materialA, recordA)

    const prepare = store.prepareSemesterMapping([recordA.id])
    await Promise.resolve()
    await store.openMaterial(materialB)
    releasePreflight(mappingPreflight(semesterItem.id))
    await prepare

    expect(store.selectedMaterialId).toBe(materialB.id)
    expect(store.semesterMappingPreflight).toBeNull()
    expect(store.errorMessage).toBe('')
  })

  it('shows only a proposal that belongs to the current material', async () => {
    const semesterItem = semester()
    const curriculumItem = curriculum()
    const materialA = material('a'.repeat(32))
    const materialB = material('b'.repeat(32))
    const proposalA = mappingProposal(semesterItem.id)
    proposalA.id = '1'.repeat(32)
    proposalA.payload.source_material_record_ids = ['r'.repeat(32)]
    const proposalB = mappingProposal(semesterItem.id)
    proposalB.id = '2'.repeat(32)
    proposalB.payload.source_material_record_ids = ['t'.repeat(32)]
    const store = useTeachingPrepCatalogStore()
    store.curricula = [curriculumItem]
    store.semesters = [semesterItem]
    store.selectedCurriculumId = curriculumItem.id
    store.materials = [materialA, materialB]
    store.semesterMaterials = [
      {
        id: 'r'.repeat(32),
        semester_id: semesterItem.id,
        material_source_id: materialA.source_id,
        display_name: materialA.display_name,
        material_role: 'textbook',
        parse_status: 'parsed',
        mapping_status: 'proposed',
        current_material_version_id: materialA.id,
        safe_filename: materialA.safe_filename,
        current_inspection_status: 'ready',
        current_unit_count: 1,
        last_parsed_version_id: materialA.id,
        has_unparsed_update: false,
        parsed_at: '2026-07-31T00:00:00Z',
        is_active: true,
        revision: 1,
        created_at: '2026-07-31T00:00:00Z',
        updated_at: '2026-07-31T00:00:00Z',
      },
      {
        id: 't'.repeat(32),
        semester_id: semesterItem.id,
        material_source_id: materialB.source_id,
        display_name: materialB.display_name,
        material_role: 'supplement',
        parse_status: 'parsed',
        mapping_status: 'proposed',
        current_material_version_id: materialB.id,
        safe_filename: materialB.safe_filename,
        current_inspection_status: 'ready',
        current_unit_count: 1,
        last_parsed_version_id: materialB.id,
        has_unparsed_update: false,
        parsed_at: '2026-07-31T00:00:00Z',
        is_active: true,
        revision: 1,
        created_at: '2026-07-31T00:00:00Z',
        updated_at: '2026-07-31T00:00:00Z',
      },
    ]
    store.semesterMappingProposals = [proposalA, proposalB]
    store.selectedMaterialId = materialB.id
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight(semesterItem.id))
    await store.prepareSemesterMapping(['t'.repeat(32)])

    expect(store.currentSemesterMappingProposal?.id).toBe(proposalB.id)
  })

  it('keeps the current resource-pack preflight when an old one returns later', async () => {
    const packA = resourcePack('a'.repeat(32))
    const packB = resourcePack('b'.repeat(32))
    let releasePackA!: (item: LessonDraftPreflight) => void
    const packAResponse = new Promise<LessonDraftPreflight>((resolve) => {
      releasePackA = resolve
    })
    vi.spyOn(teachingPrepCatalogApi, 'listLessonDrafts').mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'getLessonDraftPreflight')
      .mockImplementation((packId) => (
        packId === packA.id
          ? packAResponse
          : Promise.resolve(preflight(packB.id))
      ))
    const store = useTeachingPrepCatalogStore()
    await store.selectResourcePack(packA)

    const prepareA = store.prepareLessonDraft()
    await Promise.resolve()
    await store.selectResourcePack(packB)
    await store.prepareLessonDraft()
    releasePackA(preflight(packA.id))
    await prepareA

    expect(store.selectedResourcePackId).toBe(packB.id)
    expect(store.lessonDraftPreflight?.resource_pack_id).toBe(packB.id)
    expect(store.errorMessage).toBe('')
  })

  it('ignores an old resource-pack preflight error after switching packs', async () => {
    const packA = resourcePack('a'.repeat(32))
    const packB = resourcePack('b'.repeat(32))
    let rejectPackA!: (error: Error) => void
    const packAResponse = new Promise<LessonDraftPreflight>(
      (_resolve, reject) => {
        rejectPackA = reject
      },
    )
    vi.spyOn(teachingPrepCatalogApi, 'listLessonDrafts').mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'getLessonDraftPreflight')
      .mockReturnValue(packAResponse)
    const store = useTeachingPrepCatalogStore()
    await store.selectResourcePack(packA)

    const prepareA = store.prepareLessonDraft()
    await Promise.resolve()
    await store.selectResourcePack(packB)
    rejectPackA(new Error('old pack failed'))

    await expect(prepareA).resolves.toBeUndefined()
    expect(store.selectedResourcePackId).toBe(packB.id)
    expect(store.lessonDraftPreflight).toBeNull()
    expect(store.errorMessage).toBe('')
  })
})
