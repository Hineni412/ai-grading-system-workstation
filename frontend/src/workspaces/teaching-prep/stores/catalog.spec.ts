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
  type SemesterMaterialRecord,
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

})

describe('teaching preparation selection consistency', () => {
  it('keeps successful same-semester material data when a retry is only partly available', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('a'.repeat(32))
    const record = semesterMaterial(semesterItem.id, materialItem)
    vi.spyOn(teachingPrepCatalogApi, 'listMaterials').mockResolvedValue([materialItem])
    vi.spyOn(teachingPrepCatalogApi, 'listMaterialParseJobs').mockResolvedValue([])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMaterials')
      .mockRejectedValueOnce(new Error('semester materials temporarily unavailable'))
      .mockResolvedValue([record])
    const store = useTeachingPrepCatalogStore()
    store.curricula = [curriculumItem]
    store.semesters = [semesterItem]
    store.selectedCurriculumId = curriculumItem.id
    store.selectedSemesterId = semesterItem.id

    await store.ensureMaterialData()
    expect(store.semesterMaterials).toEqual([])
    expect(store.errorMessage).toContain('学期资料')

    await store.ensureMaterialData()

    expect(store.semesterMaterials).toEqual([record])
    expect(store.errorMessage).toBe('')
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
      slide_animation_model_available: false,
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
