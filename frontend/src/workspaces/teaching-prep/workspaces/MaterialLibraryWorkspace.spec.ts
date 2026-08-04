import { createPinia, setActivePinia } from 'pinia'
import { createApp, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { JobResponse } from '../../../api/jobs'
import { useJobStore } from '../../../stores/jobs'
import {
  teachingPrepCatalogApi,
  type CurriculumEdition,
  type MaterialUnit,
  type MaterialVersion,
  type SemesterMappingProposal,
  type SemesterMaterialRecord,
  type TeachingSemester,
} from '../api/catalog'
import { teachingPrepWorkbenchKey } from '../workbench/context'
import type { TeachingPrepWorkbench } from '../workbench/state'
import { useTeachingPrepCatalogStore } from '../stores/catalog'
import MaterialLibraryWorkspace from './MaterialLibraryWorkspace.vue'

const semesterId = 's'.repeat(32)

function curriculum(): CurriculumEdition {
  return {
    id: 'c'.repeat(32), title: '八年级上册', grade_level: 8, volume: 'first',
    publisher: null, edition_label: null, revision: 1, is_active: true,
    created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
  }
}

function semester(curriculumId: string): TeachingSemester {
  return {
    id: semesterId, curriculum_id: curriculumId, curriculum_title: '八年级上册',
    school_year: '2026-2027', term: 'first', planned_new_lesson_count: 48,
    status: 'planning', active_lesson_count: 1, not_started_lesson_count: 1,
    preparing_lesson_count: 0, ready_lesson_count: 0, taught_lesson_count: 0,
    skipped_lesson_count: 0, material_count: 2, parsed_material_count: 2,
    mapped_material_count: 0, revision: 1,
    created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
  }
}

function material(id: string, name: string): MaterialVersion {
  return {
    id, source_id: id, display_name: name, material_type: 'pdf',
    content_sha256: id[0]!.repeat(64), safe_filename: `${id[0]}.pdf`, size_bytes: 20,
    modified_ns: null, unit_count: 1, inspection_status: 'ready',
    availability: 'available', created_at: '2026-08-03T00:00:00Z',
  }
}

function record(id: string, item: MaterialVersion): SemesterMaterialRecord {
  return {
    id, semester_id: semesterId, material_source_id: item.source_id,
    display_name: item.display_name, material_role: 'textbook', parse_status: 'parsed',
    mapping_status: 'unmapped', current_material_version_id: item.id,
    safe_filename: item.safe_filename, current_inspection_status: 'ready',
    current_unit_count: 1, last_parsed_version_id: item.id, has_unparsed_update: false,
    parsed_at: '2026-08-03T00:00:00Z', is_active: true, revision: 1,
    created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
  }
}

function unit(id: string, item: MaterialVersion): MaterialUnit {
  return {
    id, material_version_id: item.id, unit_kind: 'pdf_page', unit_index: 1,
    title: null, text_excerpt: '', text_status: 'empty', formula_review_required: false,
    object_summary: {}, preview_url: `/preview/${item.id}`, revision: 1,
    created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
  }
}

function proposal(id: string, materialRecordId: string): SemesterMappingProposal {
  return {
    id, semester_id: semesterId, operation_id: `operation-${id[0]}`,
    source_state_sha256: 'f'.repeat(64), status: 'proposed',
    payload: {
      tree: [], uncertainties: [], source_material_record_ids: [materialRecordId],
      mappings: [{
        mapping_id: `mapping-${id[0]}`, material_record_id: materialRecordId,
        lesson_ref: 'lesson-1', start_unit: 1, end_unit: 1, purpose: 'textbook',
        decision: 'pending', teacher_revision: null, decision_reason: null,
      }],
    },
    revision: 1, created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z', applied_at: null,
  }
}

function mappingJob(recordId: string): JobResponse {
  return {
    id: 71, job_type: 'teaching_prep.semester_mapping',
    payload: {
      semester_id: semesterId, material_record_id: recordId,
      operation_id: 'semester-mapping-1234567890abcdef1234567890abcdef',
      source_state_sha256: 'a'.repeat(64),
    },
    result: {}, status: 'running', progress: 0.45, stage: 'calling_model',
    detail: '唯一一次模型请求正在运行', error: null, cancel_requested: false,
    created_at: '2026-08-03T00:00:00Z', started_at: '2026-08-03T00:00:01Z',
    updated_at: '2026-08-03T00:00:02Z', finished_at: null,
  }
}

async function flush(): Promise<void> {
  for (let index = 0; index < 5; index += 1) {
    await Promise.resolve()
    await nextTick()
  }
}

function mountWorkspace() {
  const catalog = useTeachingPrepCatalogStore()
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(MaterialLibraryWorkspace)
  app.provide(teachingPrepWorkbenchKey, {
    catalog,
    workspace: ref('materials'), stage: ref('materials'), stages: ref([]),
    lessonStatuses: ref([]), selectedStatus: ref(null), referencePreflight: ref(null),
    activeSuggestionRun: ref(null), pptxVersions: ref([]), dirtyReason: ref(null),
    workbenchError: ref(''), loading: ref(false), load: vi.fn(), openLesson: vi.fn(),
    openStage: vi.fn(), openWorkspace: vi.fn(), requestNavigation: vi.fn(),
    refreshCurrentWorkspace: vi.fn(), setDirty: vi.fn(), watchSuggestionRun: vi.fn(),
  } as unknown as TeachingPrepWorkbench)
  app.mount(host)
  return { app, host, catalog }
}

beforeEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
  document.body.innerHTML = ''
  setActivePinia(createPinia())
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('MaterialLibraryWorkspace current-material safety', () => {
  it('keeps rail, dropdown, title, and preview on the newly selected material', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialA = material('a'.repeat(32), '第一份教材')
    const materialB = material('b'.repeat(32), '第二份教辅')
    const recordA = record('r'.repeat(32), materialA)
    const recordB = record('t'.repeat(32), materialB)
    let releaseB!: (items: MaterialUnit[]) => void
    const pendingB = new Promise<MaterialUnit[]>(resolve => { releaseB = resolve })
    vi.spyOn(teachingPrepCatalogApi, 'listMaterialUnits').mockImplementation(id => (
      id === materialB.id ? pendingB : Promise.resolve([unit('1'.repeat(32), materialA)])
    ))
    vi.spyOn(teachingPrepCatalogApi, 'listMaterials').mockResolvedValue([materialA, materialB])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMaterials').mockResolvedValue([recordA, recordB])
    vi.spyOn(teachingPrepCatalogApi, 'listSemesters').mockResolvedValue([semesterItem])
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight').mockResolvedValue({
      semester_id: semesterId, source_state_sha256: 'a'.repeat(64), will_call_model: true,
      model_available: true, model_label: '合成模型', material_count: 1, unit_count: 1,
      existing_lesson_count: 1, creates_initial_tree: false, automatic_retry: false,
    })
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semesterItem]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [materialA, materialB]
    catalog.semesterMaterials = [recordA, recordB]
    await catalog.openMaterial(materialA)
    await flush()

    const select = host.querySelector<HTMLSelectElement>('.tp-directory-actions select')!
    select.value = materialB.id
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await flush()
    expect(catalog.selectedMaterialId).toBe(materialB.id)
    expect(catalog.materialUnits).toEqual([])
    expect(select.value).toBe(materialB.id)
    expect(host.querySelector('.tp-material-card.is-selected')?.textContent).toContain('第二份教辅')
    expect(host.querySelector('.tp-document-workspace__canvas header')?.textContent).toContain('第二份教辅')

    releaseB([unit('2'.repeat(32), materialB)])
    await flush()
    expect(host.querySelector<HTMLImageElement>('img')?.src).toContain(`/preview/${materialB.id}`)
    app.unmount()
  })

  it('never renders another material proposal in the current inspector', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialA = material('a'.repeat(32), '第一份教材')
    const materialB = material('b'.repeat(32), '第二份教辅')
    const recordA = record('r'.repeat(32), materialA)
    const recordB = record('t'.repeat(32), materialB)
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semesterItem]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [materialA, materialB]
    catalog.semesterMaterials = [recordA, recordB]
    catalog.selectedMaterialId = materialB.id
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight').mockResolvedValue({
      semester_id: semesterId, source_state_sha256: 'f'.repeat(64), will_call_model: true,
      model_available: true, model_label: '合成模型', material_count: 1, unit_count: 1,
      existing_lesson_count: 1, creates_initial_tree: false, automatic_retry: false,
    })
    await catalog.prepareSemesterMapping([recordB.id])
    catalog.semesterMappingProposals = [proposal('1'.repeat(32), recordA.id)]
    await flush()

    expect(host.textContent).toContain('暂无待审核建议')
    expect(host.textContent).not.toContain('mapping-1')
    catalog.semesterMappingProposals = [
      proposal('1'.repeat(32), recordA.id),
      proposal('2'.repeat(32), recordB.id),
    ]
    await flush()
    expect(host.querySelectorAll('.tp-mapping-review')).toHaveLength(1)
    app.unmount()
  })

  it('shows the proposed lesson tree, model basis, one lesson group, and preview errors', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('a'.repeat(32), '目录教辅')
    const materialRecord = record('r'.repeat(32), materialItem)
    const item = proposal('2'.repeat(32), materialRecord.id)
    item.payload.tree = [{
      key: 'chapter_001', title: '第一章 勾股定理', sections: [{
        key: 'section_001_001', title: '探索勾股定理', lessons: [
          { key: 'lesson_001_001_001', title: '勾股定理第1课时', duration_minutes: 45 },
          { key: 'lesson_001_001_002', title: '勾股定理第2课时', duration_minutes: 45 },
        ],
      }],
    }]
    item.payload.mappings = [
      {
        ...item.payload.mappings[0]!, lesson_ref: 'proposal:lesson_001_001_001',
        basis: '目录页码与正文标题一致', evidence_refs: ['toc-001', 'anchor-0007'],
      },
      {
        ...item.payload.mappings[0]!, mapping_id: 'mapping-second',
        lesson_ref: 'proposal:lesson_001_001_002', start_unit: 2, end_unit: 4,
        basis: '下一节目录起始页', evidence_refs: ['toc-002'],
      },
    ]
    item.payload.directory_evidence = {
      strategy: 'toc_calibrated', confidence: 'high', issues: [],
      full_page_text_sent: false,
      toc_entries: [{
        evidence_id: 'toc-001', title: '第一章 勾股定理', printed_page: 1, source_unit: 1,
      }],
      resolved_ranges: [],
      anchors: [{
        evidence_id: 'anchor-0007', unit_index: 1, title: '探索勾股定理', text_excerpt: '正文标题',
      }],
    }
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight').mockResolvedValue({
      semester_id: semesterId, source_state_sha256: 'f'.repeat(64), will_call_model: true,
      model_available: true, model_label: '合成模型', material_count: 1, unit_count: 4,
      existing_lesson_count: 0, creates_initial_tree: true, automatic_retry: false,
    })
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semesterItem]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [materialItem]
    catalog.semesterMaterials = [materialRecord]
    catalog.selectedMaterialId = materialItem.id
    catalog.materialUnits = [unit('1'.repeat(32), materialItem)]
    await catalog.prepareSemesterMapping([materialRecord.id])
    catalog.semesterMappingProposals = [item]
    await flush()

    expect(host.textContent).toContain('待确认课时树')
    expect(host.textContent).toContain('勾股定理第1课时')
    expect(host.querySelectorAll('.tp-mapping-review')).toHaveLength(1)
    expect(host.textContent).toContain('目录页码与正文标题一致')
    expect(host.textContent).toContain('目录：第一章 勾股定理 · 书上第 1 页')
    expect(host.textContent).toContain('正文锚点：PDF 第 1 页 · 探索勾股定理')
    const lessonSelect = host.querySelector<HTMLSelectElement>('.tp-mapping-review select')!
    expect([...lessonSelect.options].some(option => (
      option.value === 'proposal:lesson_001_001_001'
    ))).toBe(true)

    const image = host.querySelector<HTMLImageElement>('.tp-document-workspace__preview')!
    image.dispatchEvent(new Event('error'))
    await flush()
    expect(host.textContent).toContain('原页预览加载失败')
    app.unmount()
  })

  it('shows durable Job stage, progress, and result-unknown retry guard', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('a'.repeat(32), '当前教材')
    const materialRecord = record('r'.repeat(32), materialItem)
    const running = mappingJob(materialRecord.id)
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight').mockResolvedValue({
      semester_id: semesterId, source_state_sha256: 'a'.repeat(64), will_call_model: true,
      model_available: true, model_label: '合成模型', material_count: 1, unit_count: 1,
      existing_lesson_count: 1, creates_initial_tree: false, automatic_retry: false,
    })
    vi.spyOn(teachingPrepCatalogApi, 'startSemesterMappingProposalJob').mockResolvedValue(running)
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semesterItem]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [materialItem]
    catalog.semesterMaterials = [materialRecord]
    catalog.selectedMaterialId = materialItem.id
    await catalog.prepareSemesterMapping([materialRecord.id])
    await catalog.generateSemesterMapping([materialRecord.id])
    await flush()

    expect(host.textContent).toContain('模型正在生成建议')
    expect(host.textContent).toContain('45%')
    expect(host.textContent).toContain('单次调用 · 不自动重试')
    expect(host.querySelector('.tp-mapping-job-status dl')).toBeNull()

    useJobStore().track({
      ...running, status: 'failed', stage: 'result_unknown',
      detail: '应用重启，结果未知', error: '无法确认模型结果',
      updated_at: '2026-08-03T00:00:10Z', finished_at: '2026-08-03T00:00:10Z',
    })
    await flush()
    expect(host.textContent).toContain('结果未知，禁止自动重试')
    expect(host.textContent).toContain('为避免重复费用')
    expect(host.textContent).not.toContain('重新检查并生成新建议')
    app.unmount()
  })
})
