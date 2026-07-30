import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  teachingPrepCatalogApi,
  type LessonNode,
  type LessonDraftPreflight,
  type MaterialLink,
  type MaterialUnit,
  type MaterialVersion,
  type ResourcePack,
} from '../api/catalog'
import { useTeachingPrepCatalogStore } from './catalog'

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
  }
}

beforeEach(() => {
  vi.restoreAllMocks()
  setActivePinia(createPinia())
})

describe('teaching preparation selection consistency', () => {
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
    vi.spyOn(teachingPrepCatalogApi, 'parseMaterial')
      .mockImplementation((materialId) => (
        materialId === materialA.id
          ? materialAResponse
          : Promise.resolve([unitB])
      ))
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
