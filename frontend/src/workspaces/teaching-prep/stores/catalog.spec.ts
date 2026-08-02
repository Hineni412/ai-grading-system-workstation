import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../../api/errors'
import { jobApi, type JobResponse } from '../../../api/jobs'
import {
  teachingPrepCatalogApi,
  type CurriculumEdition,
  type LessonNode,
  type LessonDraftPreflight,
  type MaterialLink,
  type MaterialUnit,
  type MaterialVersion,
  type ResourcePack,
  type SemesterMappingPreflight,
  type SemesterMappingProposal,
  type TeachingSemester,
  type TeachingPreferencesPayload,
} from '../api/catalog'
import {
  SEMESTER_MAPPING_COMMAND_STORAGE_KEY,
  useTeachingPrepCatalogStore,
} from './catalog'

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

function mappingPreflight(semesterId: string): SemesterMappingPreflight {
  return {
    semester_id: semesterId,
    source_state_sha256: 'a'.repeat(64),
    will_call_model: true,
    model_available: true,
    model_label: '合成模型',
    material_count: 1,
    unit_count: 3,
    existing_lesson_count: 1,
    creates_initial_tree: false,
    automatic_retry: false,
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

beforeEach(() => {
  vi.restoreAllMocks()
  localStorage.removeItem(SEMESTER_MAPPING_COMMAND_STORAGE_KEY)
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
  })

  it('reuses the operation id after a lost mapping response', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const preflightItem = mappingPreflight(semesterItem.id)
    const proposalItem = mappingProposal(semesterItem.id)
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(preflightItem)
    const generate = vi.spyOn(
      teachingPrepCatalogApi,
      'generateSemesterMappingProposal',
    )
      .mockRejectedValueOnce(new Error('synthetic lost response'))
      .mockResolvedValue(proposalItem)
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals')
      .mockResolvedValue([proposalItem])
    const store = useTeachingPrepCatalogStore()
    store.curricula = [curriculumItem]
    store.semesters = [semesterItem]
    store.selectedCurriculumId = curriculumItem.id

    await expect(
      store.generateSemesterMapping(['m'.repeat(32)]),
    ).rejects.toThrow()
    await expect(
      store.generateSemesterMapping(['m'.repeat(32)]),
    ).resolves.toBeUndefined()

    expect(generate).toHaveBeenCalledTimes(2)
    expect(generate.mock.calls[0]?.[1].operation_id).toBe(
      generate.mock.calls[1]?.[1].operation_id,
    )
  })

  it('preserves a lost mapping request across a page reload', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const preflightItem = mappingPreflight(semesterItem.id)
    const proposalItem = mappingProposal(semesterItem.id)
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(preflightItem)
    const generate = vi.spyOn(
      teachingPrepCatalogApi,
      'generateSemesterMappingProposal',
    )
      .mockRejectedValueOnce(new Error('synthetic lost response'))
      .mockResolvedValue(proposalItem)
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals')
      .mockResolvedValue([proposalItem])
    const firstStore = useTeachingPrepCatalogStore()
    firstStore.curricula = [curriculumItem]
    firstStore.semesters = [semesterItem]
    firstStore.selectedCurriculumId = curriculumItem.id

    await expect(
      firstStore.generateSemesterMapping(['m'.repeat(32)]),
    ).rejects.toThrow()

    setActivePinia(createPinia())
    const reloadedStore = useTeachingPrepCatalogStore()
    reloadedStore.curricula = [curriculumItem]
    reloadedStore.semesters = [semesterItem]
    reloadedStore.selectedCurriculumId = curriculumItem.id

    await expect(
      reloadedStore.generateSemesterMapping(['m'.repeat(32)]),
    ).resolves.toBeUndefined()

    expect(generate).toHaveBeenCalledTimes(2)
    expect(generate.mock.calls[0]?.[1].operation_id).toBe(
      generate.mock.calls[1]?.[1].operation_id,
    )
  })

  it('starts a new mapping operation only after the server confirms failure', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const preflightItem = mappingPreflight(semesterItem.id)
    const proposalItem = mappingProposal(semesterItem.id)
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(preflightItem)
    const generate = vi.spyOn(
      teachingPrepCatalogApi,
      'generateSemesterMappingProposal',
    )
      .mockRejectedValueOnce(new ApiError({
        kind: 'conflict',
        status: 409,
        code: 'semester_mapping_retry_available',
        message: 'mapping failed',
        details: {},
        requestId: 'request-1',
        retryable: false,
      }))
      .mockResolvedValue(proposalItem)
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals')
      .mockResolvedValue([proposalItem])
    const store = useTeachingPrepCatalogStore()
    store.curricula = [curriculumItem]
    store.semesters = [semesterItem]
    store.selectedCurriculumId = curriculumItem.id

    await expect(
      store.generateSemesterMapping(['m'.repeat(32)]),
    ).rejects.toThrow()
    await expect(
      store.generateSemesterMapping(['m'.repeat(32)]),
    ).resolves.toBeUndefined()

    expect(generate).toHaveBeenCalledTimes(2)
    expect(generate.mock.calls[0]?.[1].operation_id).not.toBe(
      generate.mock.calls[1]?.[1].operation_id,
    )
  })
})

describe('teaching preparation selection consistency', () => {
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

      await vi.advanceTimersByTimeAsync(350)
      await Promise.resolve()

      expect(store.materialParseJobs[materialItem.id]).toEqual(succeededJob)
      expect(jobApi.getJob).toHaveBeenCalledWith(runningJob.id)
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
