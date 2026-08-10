import { createPinia, setActivePinia } from 'pinia'
import { createApp, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { JobResponse } from '../../../api/jobs'
import { useJobStore } from '../../../stores/jobs'
import { useWorkspaceAITaskStore } from '../../shared/ai-tasks/store'
import { workspaceAITaskApi } from '../../shared/ai-tasks/api'
import * as aiAdoption from '../aiAdoption'
import { adoptedTeachingPrepTask } from '../aiAdoptionTestFixture'
import {
  teachingPrepCatalogApi,
  type CurriculumEdition,
  type MaterialUnit,
  type MaterialVersion,
  type MaterialDeletionPreview,
  type SemesterMappingProposal,
  type SemesterMappingPreflight,
  type SemesterMaterialRecord,
  type TeachingSemester,
} from '../api/catalog'
import { teachingPrepWorkbenchApi } from '../api/workbench'
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

function mappingPreflight(
  overrides: Partial<SemesterMappingPreflight> = {},
): SemesterMappingPreflight {
  return {
    semester_id: semesterId,
    source_state_sha256: 'f'.repeat(64),
    will_call_model: true,
    model_available: true,
    model_label: '合成模型',
    model_destination_fingerprint: 'f'.repeat(64),
    material_count: 1,
    unit_count: 1,
    existing_lesson_count: 1,
    creates_initial_tree: false,
    automatic_retry: false,
    evidence_strategy: 'toc_calibrated',
    evidence_confidence: 'high',
    scanned_unit_count: 1,
    directory_page_image_count: 0,
    directory_page_images_sent: false,
    toc_entry_count: 1,
    anchor_count: 1,
    estimated_input_characters: 120,
    full_page_text_sent: false,
    evidence_issues: [],
    ...overrides,
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

function deletionPreview(
  item: MaterialVersion,
  overrides: Partial<MaterialDeletionPreview> = {},
): MaterialDeletionPreview {
  return {
    source_id: item.source_id,
    display_name: item.display_name,
    source_revision: item.source_revision ?? 1,
    impact_counts: {
      material_sources: 1, material_versions: 1, material_units: item.unit_count ?? 0,
      lesson_material_links: 0, semester_material_records: 1,
      semester_mapping_proposals: 0, reference_ppt_collections: 0,
      exercise_regions: 0, exercise_candidates: 0,
    },
    affected_semesters: [{
      semester_id: semesterId, title: '八年级上册', school_year: '2026-2027', term: 'first',
    }],
    generation_history_count: 0,
    preserved_snapshot_count: 0,
    blocking_generation_count: 0,
    can_delete: true,
    blocker_code: null,
    preserved_history_note: '没有正式生成历史。',
    confirmation_phrase: '确认彻底删除资料',
    preview_version: `preview-${item.id[0]}`,
    owned_file_count: 2,
    ...overrides,
  }
}

async function flush(): Promise<void> {
  for (let index = 0; index < 5; index += 1) {
    await Promise.resolve()
    await nextTick()
  }
}

function mountWorkspace(options: {
  refreshCurrentWorkspace?: () => Promise<void>
} = {}) {
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
    refreshCurrentWorkspace: options.refreshCurrentWorkspace ?? vi.fn(),
    setDirty: vi.fn(), watchSuggestionRun: vi.fn(),
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
  it('offers a semester-scoped PPT folder picker and keeps ordinary multi-file import', async () => {
    const { app, host, catalog } = mountWorkspace()
    const folderInput = host.querySelector<HTMLInputElement>('input[webkitdirectory]')
    expect(folderInput).not.toBeNull()
    expect(folderInput?.disabled).toBe(true)
    expect(host.textContent).toContain('导入课件文件夹')
    expect(host.textContent).toContain('选择多份资料')

    const curriculumItem = curriculum()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semester(curriculumItem.id)]
    catalog.selectedCurriculumId = curriculumItem.id
    await flush()

    expect(folderInput?.disabled).toBe(false)
    app.unmount()
  })

  it('excludes reference PPT from directory analysis unless the teacher includes it', async () => {
    const curriculumItem = curriculum()
    const textbook = material('a'.repeat(32), '数学教材')
    const referencePpt = material('b'.repeat(32), '一学期参考课件')
    referencePpt.material_type = 'pptx'
    const textbookRecord = record('r'.repeat(32), textbook)
    const pptRecord = record('p'.repeat(32), referencePpt)
    pptRecord.material_role = 'reference_ppt'
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semester(curriculumItem.id)]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [textbook, referencePpt]
    catalog.semesterMaterials = [textbookRecord, pptRecord]
    await flush()

    const directorySelect = host.querySelector<HTMLSelectElement>('.tp-directory-actions select')!
    expect(directorySelect.textContent).toContain('数学教材')
    expect(directorySelect.textContent).not.toContain('一学期参考课件')
    expect(host.textContent).toContain('默认不包含参考 PPT')

    const includePpt = host.querySelector<HTMLInputElement>('[data-testid="include-reference-ppt-directory"]')!
    includePpt.checked = true
    includePpt.dispatchEvent(new Event('change'))
    await flush()
    expect(directorySelect.textContent).toContain('一学期参考课件')
    app.unmount()
  })

  it('excludes legacy PPT files even when their saved role is not reference_ppt', async () => {
    const curriculumItem = curriculum()
    const legacyPpt = material('b'.repeat(32), '旧课件.pptx')
    legacyPpt.material_type = 'pptx'
    legacyPpt.safe_filename = '旧课件.pptx'
    const legacyRecord = record('p'.repeat(32), legacyPpt)
    legacyRecord.material_role = 'supplement'
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semester(curriculumItem.id)]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [legacyPpt]
    catalog.semesterMaterials = [legacyRecord]
    await flush()

    const directorySelect = host.querySelector<HTMLSelectElement>('.tp-directory-actions select')!
    expect(directorySelect.textContent).not.toContain('旧课件.pptx')
    app.unmount()
  })

  it('resets the reference-PPT directory opt-in after a scope change and after re-entering', async () => {
    const curriculumItem = curriculum()
    const referencePpt = material('b'.repeat(32), '参考课件.pptx')
    referencePpt.material_type = 'pptx'
    referencePpt.safe_filename = 'synthetic.pptx'
    const pptRecord = record('p'.repeat(32), referencePpt)
    pptRecord.material_role = 'reference_ppt'
    const firstMount = mountWorkspace()
    firstMount.catalog.curricula = [curriculumItem]
    firstMount.catalog.semesters = [semester(curriculumItem.id)]
    firstMount.catalog.selectedCurriculumId = curriculumItem.id
    firstMount.catalog.materials = [referencePpt]
    firstMount.catalog.semesterMaterials = [pptRecord]
    await flush()

    const include = firstMount.host.querySelector<HTMLInputElement>('[data-testid="include-reference-ppt-directory"]')!
    include.checked = true
    include.dispatchEvent(new Event('change', { bubbles: true }))
    await flush()
    expect(include.checked).toBe(true)
    const scope = (await import('../../../stores/curriculum-scope')).useCurriculumScopeStore()
    scope.selectedVolumeId = 'synthetic-volume'
    await flush()
    expect(include.checked).toBe(false)

    include.checked = true
    include.dispatchEvent(new Event('change', { bubbles: true }))
    await flush()
    firstMount.app.unmount()
    const secondMount = mountWorkspace()
    await flush()
    expect(secondMount.host.querySelector<HTMLInputElement>('[data-testid="include-reference-ppt-directory"]')?.checked).toBe(false)
    expect(localStorage.getItem('teaching-prep:include-reference-ppt-directory')).toBeNull()
    secondMount.app.unmount()
  })

  it('can preview deletion for an old-term PPT by the currently visible PPT scope', async () => {
    const curriculumItem = curriculum()
    const oldPpt = material('b'.repeat(32), '旧学期参考课件.pptx')
    oldPpt.material_type = 'pptx'
    oldPpt.safe_filename = 'old-term.pptx'
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semester(curriculumItem.id)]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [oldPpt]
    catalog.semesterMaterials = []
    const preview = vi.spyOn(catalog, 'getMaterialDeletionPreview').mockResolvedValue({
      source_id: oldPpt.source_id, display_name: oldPpt.display_name, source_revision: 1,
      impact_counts: {
        material_sources: 1, material_versions: 1, material_units: 18,
        lesson_material_links: 0, semester_material_records: 1,
        semester_mapping_proposals: 0, reference_ppt_collections: 1,
        exercise_regions: 0, exercise_candidates: 0,
      },
      affected_semesters: [{ semester_id: 'o'.repeat(32), title: '七年级上册', school_year: '2025-2026', term: 'second' }],
      generation_history_count: 0, preserved_snapshot_count: 0,
      blocking_generation_count: 0, can_delete: true, blocker_code: null,
      preserved_history_note: '没有正式生成历史。', confirmation_phrase: '确认彻底删除资料',
      preview_version: 'old-ppt-preview-v1', owned_file_count: 19,
    })
    await flush()

    const deletePpts = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.includes('删除当前范围参考 PPT'))!
    expect(deletePpts).toBeDefined()
    deletePpts.click()
    await vi.waitFor(() => expect(preview).toHaveBeenCalledWith(oldPpt))
    await vi.waitFor(() => expect(host.textContent).toContain('七年级上册 · 2025-2026 · 下学期'))
    expect(host.textContent).toContain('旧学期参考课件.pptx')
    app.unmount()
  })

  it('shows a complete deletion impact and request number before one final confirmation', async () => {
    const curriculumItem = curriculum()
    const first = material('a'.repeat(32), '旧教材')
    const firstRecord = record('r'.repeat(32), first)
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semester(curriculumItem.id)]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [first]
    catalog.semesterMaterials = [firstRecord]
    const counts = {
      material_sources: 1, material_versions: 2, material_units: 36,
      lesson_material_links: 3, semester_material_records: 1,
      semester_mapping_proposals: 1, reference_ppt_collections: 0,
      exercise_regions: 2, exercise_candidates: 1,
    }
    const preview = {
      source_id: first.source_id, display_name: first.display_name, source_revision: 1,
      impact_counts: counts,
      affected_semesters: [{ semester_id: semesterId, title: '八年级上册', school_year: '2026-2027', term: 'first' as const }],
      generation_history_count: 2, preserved_snapshot_count: 2,
      blocking_generation_count: 0, can_delete: true, blocker_code: null,
      preserved_history_note: '仅保留资料名/安全文件名/类型/版本标识/内容指纹等生成事实，不保留原文件、解析正文或本机路径；原文件/解析页/课时学期关联永久删除无法恢复。',
      confirmation_phrase: '确认彻底删除资料', preview_version: 'preview-v1', owned_file_count: 4,
    }
    const getPreview = vi.spyOn(catalog, 'getMaterialDeletionPreview').mockResolvedValue(preview)
    const deleteSource = vi.spyOn(catalog, 'deleteMaterialSource').mockImplementation(async (_item, input) => ({
      operation_id: input.operation_id, status: 'succeeded', preview_version: input.preview_version,
      deleted_source_id: first.source_id, deleted_file_count: 4, counts, error_code: null,
    }))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    await flush()

    const deleteButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.trim() === '彻底删除')!
    deleteButton.click()
    await vi.waitFor(() => expect(getPreview).toHaveBeenCalledWith(first))
    await vi.waitFor(() => expect(host.textContent).toContain('将删除 2 个资料版本、36 页解析内容、3 条课时关联'))
    expect(confirm).not.toHaveBeenCalled()
    expect(deleteSource).not.toHaveBeenCalled()
    expect(host.textContent).toContain('八年级上册 · 2026-2027 · 上学期')
    expect(host.textContent).toContain('保留事实快照2')
    expect(host.textContent).toContain('阻断中的生成0')
    expect(host.textContent).toContain('仅保留资料名/安全文件名/类型/版本标识/内容指纹等生成事实')
    const operationLabel = host.querySelector<HTMLElement>('[data-testid="deletion-operation-id"]')!
    expect(operationLabel.textContent).toMatch(/^delete-material-/)

    host.querySelector<HTMLButtonElement>('[data-testid="confirm-material-deletion"]')!.click()
    await vi.waitFor(() => expect(deleteSource).toHaveBeenCalledOnce())
    expect(confirm).toHaveBeenCalledOnce()
    const confirmationText = String(confirm.mock.calls[0]?.[0])
    expect(confirmationText).toContain('保留 2 份生成事实快照')
    expect(confirmationText).toContain('资料名、安全文件名、类型、版本标识和内容指纹')
    expect(confirmationText).toContain('不保留原文件、解析正文或本机路径')
    expect(confirmationText).toContain('原文件、解析页、课时和学期关联将永久删除且无法恢复')
    expect(deleteSource).toHaveBeenCalledWith(first, {
      operation_id: operationLabel.textContent,
      preview_version: 'preview-v1',
      confirmation_phrase: '确认彻底删除资料',
    })
    await vi.waitFor(() => expect(host.textContent).toContain('请求已确认成功'))
    app.unmount()
  })

  it('checks an uncertain result then idempotently replays DELETE with the same request number when status is missing', async () => {
    const curriculumItem = curriculum()
    const item = material('a'.repeat(32), '网络结果测试资料')
    const itemRecord = record('r'.repeat(32), item)
    const impact = deletionPreview(item)
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semester(curriculumItem.id)]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [item]
    catalog.semesterMaterials = [itemRecord]
    vi.spyOn(catalog, 'getMaterialDeletionPreview').mockResolvedValue(impact)
    const deleteSource = vi.spyOn(catalog, 'deleteMaterialSource')
      .mockRejectedValueOnce(new Error('synthetic response lost'))
      .mockImplementationOnce(async (_material, input) => ({
        operation_id: input.operation_id, status: 'succeeded', preview_version: impact.preview_version,
        deleted_source_id: item.source_id, deleted_file_count: 2,
        counts: impact.impact_counts, error_code: null,
      }))
    const status = vi.spyOn(catalog, 'getMaterialDeletionStatus')
      .mockRejectedValue(new Error('synthetic 404'))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await flush()

    const deleteButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.trim() === '彻底删除')!
    deleteButton.click()
    await vi.waitFor(() => expect(host.querySelector('[data-testid="deletion-operation-id"]')).not.toBeNull())
    const operationId = host.querySelector<HTMLElement>('[data-testid="deletion-operation-id"]')!.textContent!
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-material-deletion"]')!.click()

    await vi.waitFor(() => expect(status).toHaveBeenCalledWith(operationId, item))
    await vi.waitFor(() => expect(deleteSource).toHaveBeenCalledTimes(2))
    expect(deleteSource.mock.calls.map(call => call[1])).toEqual([
      {
        operation_id: operationId,
        preview_version: impact.preview_version,
        confirmation_phrase: impact.confirmation_phrase,
      },
      {
        operation_id: operationId,
        preview_version: impact.preview_version,
        confirmation_phrase: impact.confirmation_phrase,
      },
    ])
    await vi.waitFor(() => expect(host.textContent).toContain('请求已确认成功'))
    app.unmount()
  })

  it('continues an interrupted deletion once after the teacher explicitly checks the same request number', async () => {
    const curriculumItem = curriculum()
    const item = material('a'.repeat(32), '重启后续作资料')
    const itemRecord = record('r'.repeat(32), item)
    const impact = deletionPreview(item)
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semester(curriculumItem.id)]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [item]
    catalog.semesterMaterials = [itemRecord]
    vi.spyOn(catalog, 'getMaterialDeletionPreview').mockResolvedValue(impact)
    const interrupted = {
      operation_id: 'placeholder', status: 'interrupted', preview_version: impact.preview_version,
      deleted_source_id: null, deleted_file_count: 0,
      counts: impact.impact_counts, error_code: 'application_restarted_during_material_delete',
    }
    const deleteSource = vi.spyOn(catalog, 'deleteMaterialSource')
      .mockImplementationOnce(async (_material, input) => ({
        ...interrupted,
        operation_id: input.operation_id,
      }))
      .mockImplementationOnce(async (_material, input) => ({
        operation_id: input.operation_id, status: 'succeeded', preview_version: input.preview_version,
        deleted_source_id: item.source_id, deleted_file_count: 2,
        counts: impact.impact_counts, error_code: null,
      }))
    const status = vi.spyOn(catalog, 'getMaterialDeletionStatus')
      .mockImplementation(async operationId => ({
        ...interrupted,
        operation_id: operationId,
      }))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await flush()

    const deleteButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.trim() === '彻底删除')!
    deleteButton.click()
    await vi.waitFor(() => expect(host.querySelector('[data-testid="deletion-operation-id"]')).not.toBeNull())
    const operationId = host.querySelector<HTMLElement>('[data-testid="deletion-operation-id"]')!.textContent!
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-material-deletion"]')!.click()
    await vi.waitFor(() => expect(deleteSource).toHaveBeenCalledOnce())

    await vi.waitFor(() => expect(
      [...host.querySelectorAll<HTMLButtonElement>('button')]
        .find(button => button.textContent?.includes('按此请求号核对 / 幂等重放')),
    ).toBeDefined())
    const explicitCheck = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.includes('按此请求号核对 / 幂等重放'))!
    explicitCheck.click()

    await vi.waitFor(() => expect(status).toHaveBeenCalledWith(operationId, item))
    await vi.waitFor(() => expect(deleteSource).toHaveBeenCalledTimes(2))
    expect(deleteSource.mock.calls.map(call => call[1])).toEqual([
      {
        operation_id: operationId,
        preview_version: impact.preview_version,
        confirmation_phrase: impact.confirmation_phrase,
      },
      {
        operation_id: operationId,
        preview_version: impact.preview_version,
        confirmation_phrase: impact.confirmation_phrase,
      },
    ])
    await vi.waitFor(() => expect(host.textContent).toContain('请求已确认成功'))
    app.unmount()
  })

  it('keeps a running deletion pending when the teacher checks its request number', async () => {
    const curriculumItem = curriculum()
    const item = material('a'.repeat(32), '仍在删除中的资料')
    const itemRecord = record('r'.repeat(32), item)
    const impact = deletionPreview(item)
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semester(curriculumItem.id)]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [item]
    catalog.semesterMaterials = [itemRecord]
    vi.spyOn(catalog, 'getMaterialDeletionPreview').mockResolvedValue(impact)
    const deleteSource = vi.spyOn(catalog, 'deleteMaterialSource')
      .mockImplementation(async (_material, input) => ({
        operation_id: input.operation_id, status: 'running', preview_version: input.preview_version,
        deleted_source_id: null, deleted_file_count: 0,
        counts: impact.impact_counts, error_code: null,
      }))
    const status = vi.spyOn(catalog, 'getMaterialDeletionStatus')
      .mockImplementation(async operationId => ({
        operation_id: operationId, status: 'running', preview_version: impact.preview_version,
        deleted_source_id: null, deleted_file_count: 0,
        counts: impact.impact_counts, error_code: null,
      }))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await flush()

    const deleteButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.trim() === '彻底删除')!
    deleteButton.click()
    await vi.waitFor(() => expect(host.querySelector('[data-testid="deletion-operation-id"]')).not.toBeNull())
    const operationId = host.querySelector<HTMLElement>('[data-testid="deletion-operation-id"]')!.textContent!
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-material-deletion"]')!.click()
    await vi.waitFor(() => expect(
      [...host.querySelectorAll<HTMLButtonElement>('button')]
        .find(button => button.textContent?.includes('按此请求号核对 / 幂等重放')),
    ).toBeDefined())

    const explicitCheck = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.includes('按此请求号核对 / 幂等重放'))!
    explicitCheck.click()

    await vi.waitFor(() => expect(status).toHaveBeenCalledWith(operationId, item))
    await flush()
    expect(deleteSource).toHaveBeenCalledOnce()
    expect(host.textContent).toContain('结果尚未完成；请使用同一请求号继续核对')
    app.unmount()
  })

  it('blocks deletion only while courseware generation is active or recoverable', async () => {
    const curriculumItem = curriculum()
    const item = material('a'.repeat(32), '已用于正式课件的资料')
    const itemRecord = record('r'.repeat(32), item)
    const impact = deletionPreview(item, {
      generation_history_count: 2,
      preserved_snapshot_count: 1,
      blocking_generation_count: 1,
      can_delete: false,
      blocker_code: 'active_courseware_generation',
      preserved_history_note: '已有 1 份终态生成事实快照会保留；另有 1 个生成仍在进行或等待恢复。',
    })
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semester(curriculumItem.id)]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [item]
    catalog.semesterMaterials = [itemRecord]
    vi.spyOn(catalog, 'getMaterialDeletionPreview').mockResolvedValue(impact)
    const deleteSource = vi.spyOn(catalog, 'deleteMaterialSource')
    const confirm = vi.spyOn(window, 'confirm')
    await flush()

    const deleteButton = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.trim() === '彻底删除')!
    deleteButton.click()
    await vi.waitFor(() => expect(host.textContent).toContain('另有 1 个生成仍在进行或等待恢复'))
    expect(host.textContent).toContain('请先完成、取消或放弃恢复')
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-material-deletion"]')!.click()
    await flush()

    expect(confirm).not.toHaveBeenCalled()
    expect(deleteSource).not.toHaveBeenCalled()
    expect(host.textContent).toContain('当前没有可执行的删除项')
    app.unmount()
  })

  it('shows each result separately when a batch deletion only partly succeeds', async () => {
    const curriculumItem = curriculum()
    const first = material('a'.repeat(32), '批量资料一')
    const second = material('b'.repeat(32), '批量资料二')
    const firstImpact = deletionPreview(first)
    const secondImpact = deletionPreview(second)
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semester(curriculumItem.id)]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [first, second]
    catalog.semesterMaterials = [record('r'.repeat(32), first), record('t'.repeat(32), second)]
    vi.spyOn(catalog, 'getMaterialDeletionPreview').mockImplementation(async item => (
      item.id === first.id ? firstImpact : secondImpact
    ))
    const deleteSource = vi.spyOn(catalog, 'deleteMaterialSource')
      .mockImplementation(async (item, input) => ({
        operation_id: input.operation_id,
        status: item.id === first.id ? 'succeeded' : 'failed',
        preview_version: input.preview_version,
        deleted_source_id: item.id === first.id ? item.source_id : null,
        deleted_file_count: item.id === first.id ? 2 : 0,
        counts: item.id === first.id ? firstImpact.impact_counts : secondImpact.impact_counts,
        error_code: item.id === first.id ? null : 'revision_changed',
      }))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    await flush()

    const batchDelete = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.includes('删除本学期全部资料'))!
    batchDelete.click()
    await vi.waitFor(() => expect(host.querySelectorAll('[data-testid="deletion-operation-id"]')).toHaveLength(2))
    host.querySelector<HTMLButtonElement>('[data-testid="confirm-material-deletion"]')!.click()

    await vi.waitFor(() => expect(deleteSource).toHaveBeenCalledTimes(2))
    expect(confirm).toHaveBeenCalledOnce()
    await vi.waitFor(() => expect(host.textContent).toContain('已确认成功 1 份，1 份未得到明确结果'))
    expect(host.textContent).toContain('删除未完成（revision_changed），不会自动重试')
    expect(new Set(deleteSource.mock.calls.map(call => call[1].operation_id)).size).toBe(2)
    app.unmount()
  })

  it('bulk-confirms only high-confidence local PPT mappings', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('a'.repeat(32), '参考课件')
    const materialRecord = record('r'.repeat(32), materialItem)
    materialRecord.material_role = 'reference_ppt'
    const item = proposal('2'.repeat(32), materialRecord.id)
    item.payload.generation_source = 'local_reference_ppt_names'
    item.payload.mappings[0]!.purpose = 'reference_ppt'
    item.payload.mappings[0]!.confidence = 'high'
    const updated = structuredClone(item)
    updated.revision = 2
    updated.payload.mappings[0]!.decision = 'accepted'
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight())
    const accept = vi.spyOn(
      teachingPrepCatalogApi,
      'acceptLocalReferencePptMappings',
    ).mockResolvedValue(updated)
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semesterItem]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [materialItem]
    catalog.semesterMaterials = [materialRecord]
    catalog.selectedMaterialId = materialItem.id
    await catalog.prepareSemesterMapping([materialRecord.id])
    catalog.semesterMappingProposals = [item]
    await flush()

    expect(host.textContent).toContain(
      '本机无法预估金额；由当前模型服务商按实际用量计费',
    )

    const button = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(value => value.textContent?.includes('确认高置信建议（1 条）'))
    expect(button).toBeDefined()
    button?.click()
    await vi.waitFor(() => expect(accept).toHaveBeenCalledWith(item))
    expect(catalog.semesterMappingProposals[0]?.payload.mappings[0]?.decision)
      .toBe('accepted')
    app.unmount()
  })

  it('lets the teacher discard an incomplete mapping before a deliberate regeneration', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('a'.repeat(32), '目录教材')
    const materialRecord = record('r'.repeat(32), materialItem)
    const item = proposal('2'.repeat(32), materialRecord.id)
    const rejected = structuredClone(item)
    rejected.status = 'rejected'
    rejected.revision = 2
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight())
    const reject = vi.spyOn(
      teachingPrepCatalogApi,
      'rejectSemesterMappingProposal',
    ).mockResolvedValue(rejected)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semesterItem]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [materialItem]
    catalog.semesterMaterials = [materialRecord]
    catalog.selectedMaterialId = materialItem.id
    await catalog.prepareSemesterMapping([materialRecord.id])
    catalog.semesterMappingProposals = [item]
    await flush()

    const button = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(value => value.textContent?.includes('放弃本份建议，重新判断'))
    expect(button).toBeDefined()
    button?.click()

    await vi.waitFor(() => expect(reject).toHaveBeenCalledWith(item))
    await vi.waitFor(() => expect(catalog.currentSemesterMappingProposal).toBeNull())
    expect(host.textContent).toContain('本操作没有调用模型')
    app.unmount()
  })

  it('offers one visible confirmation action for all pending directory suggestions', async () => {
    const curriculumItem = curriculum()
    const materialItem = material('a'.repeat(32), '目录教材')
    const materialRecord = record('r'.repeat(32), materialItem)
    const item = proposal('2'.repeat(32), materialRecord.id)
    const updated = structuredClone(item)
    updated.revision = 2
    updated.payload.mappings[0]!.decision = 'accepted'
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight())
    const review = vi.spyOn(teachingPrepWorkbenchApi, 'reviewMapping')
      .mockResolvedValue(updated)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semester(curriculumItem.id)]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [materialItem]
    catalog.semesterMaterials = [materialRecord]
    catalog.selectedMaterialId = materialItem.id
    await catalog.prepareSemesterMapping([materialRecord.id])
    catalog.semesterMappingProposals = [item]
    await flush()

    const acceptAll = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.includes('接受全部剩余建议'))!
    const apply = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.includes('应用全部接受项'))!
    expect(acceptAll).toBeDefined()
    expect(apply.disabled).toBe(false)
    apply.click()
    await flush()
    expect(host.textContent).toContain('还有 1 条建议尚未决定')
    expect(review).not.toHaveBeenCalled()
    acceptAll.click()

    await vi.waitFor(() => expect(review).toHaveBeenCalledOnce())
    await vi.waitFor(() => expect(host.textContent).toContain('现在可以应用到正式课时树'))
    expect(apply.disabled).toBe(false)
    app.unmount()
  })

  it('keeps mapping review actions visible and clickable while explaining unmet gates without writing', async () => {
    const { app, host, catalog } = mountWorkspace()
    const acceptLocal = vi.spyOn(teachingPrepCatalogApi, 'acceptLocalReferencePptMappings')
    const review = vi.spyOn(teachingPrepWorkbenchApi, 'reviewMapping')
    const apply = vi.spyOn(catalog, 'applySemesterMapping')
    await flush()

    const confirmHigh = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.includes('确认高置信建议'))!
    const acceptAll = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.includes('接受全部剩余建议'))!
    const applyAll = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.includes('应用全部接受项'))!
    expect(confirmHigh).toBeDefined()
    expect(acceptAll).toBeDefined()
    expect(applyAll).toBeDefined()
    expect(confirmHigh.disabled).toBe(false)
    expect(acceptAll.disabled).toBe(false)
    expect(applyAll.disabled).toBe(false)

    confirmHigh.click()
    await flush()
    expect(host.textContent).toContain('当前没有可直接确认的高置信建议')
    acceptAll.click()
    await flush()
    expect(host.textContent).toContain('当前没有待接受的映射建议')
    applyAll.click()
    await flush()
    expect(host.textContent).toContain('请先取得建议并逐条完成决定')
    expect(acceptLocal).not.toHaveBeenCalled()
    expect(review).not.toHaveBeenCalled()
    expect(apply).not.toHaveBeenCalled()
    app.unmount()
  })

  it('recovers an adopted mapping handoff instead of applying the stale proposal directly', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('a'.repeat(32), '当前教材')
    const materialRecord = record('r'.repeat(32), materialItem)
    const item = proposal('2'.repeat(32), materialRecord.id)
    item.payload.mappings[0]!.decision = 'accepted'
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight())
    const receipt = {
      adoption_id: 'adoption-task-mapping', handoff_id: 'handoff-task-mapping',
      task_kind: 'teaching_prep.semester_mapping', proposal_ref_id: item.id,
      object_kind: 'semester_mapping', object_id: item.id,
      object_ref: `teaching_prep:semester_mapping:${item.id}`, object_status: 'applied',
      draft_revision: '1', target_revision: '1', receipt_revision: '1', adopted_at: '',
    }
    const adopt = vi.spyOn(teachingPrepWorkbenchApi, 'adoptAIHandoff').mockResolvedValue(receipt)
    const aiTasks = useWorkspaceAITaskStore()
    aiTasks.track(adoptedTeachingPrepTask({
      taskId: 'task-mapping', taskKind: 'teaching_prep.semester_mapping',
      proposalId: item.id, sourceRef: { kind: 'semester', id: semesterId, revision: '1' },
      draftKind: 'semester_mapping', draftRevision: '1',
    }))
    vi.spyOn(aiTasks, 'refresh').mockResolvedValue()
    const { app, host, catalog } = mountWorkspace({
      refreshCurrentWorkspace: vi.fn().mockRejectedValue(new Error('offline')),
    })
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semesterItem]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [materialItem]
    catalog.semesterMaterials = [materialRecord]
    catalog.selectedMaterialId = materialItem.id
    await catalog.prepareSemesterMapping([materialRecord.id])
    catalog.semesterMappingProposals = [item]
    const legacyApply = vi.spyOn(catalog, 'applySemesterMapping').mockResolvedValue(undefined)
    await flush()

    const apply = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.includes('应用全部接受项'))
    expect(apply).toBeDefined()
    expect(catalog.currentSemesterMappingProposal?.id).toBe(item.id)
    expect(catalog.currentSemesterMappingProposal?.payload.mappings[0]?.decision).toBe('accepted')
    expect(apply?.disabled).toBe(false)
    expect(aiAdoption.findTeachingPrepAdoption(
      aiTasks.orderedTasks,
      'teaching_prep.semester_mapping',
      item.id,
    )).not.toBeNull()
    apply?.click()

    await vi.waitFor(() => expect(adopt).toHaveBeenCalledOnce())
    expect(legacyApply).not.toHaveBeenCalled()
    await vi.waitFor(() => {
      expect(host.textContent).toContain('正式映射已写入且已有回执')
      expect(host.textContent).not.toContain('正式映射尚未确认')
    })
    app.unmount()
  })

  it.each([
    ['AI proposal', true],
    ['local proposal', false],
  ])('routes %s application through the correct adoption path', async (_label, isAI) => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const materialItem = material('a'.repeat(32), '当前教材')
    const materialRecord = record('r'.repeat(32), materialItem)
    const item = proposal('2'.repeat(32), materialRecord.id)
    item.payload.mappings[0]!.decision = 'accepted'
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight())
    const adoption = vi.spyOn(aiAdoption, 'adoptTeachingPrepProposal')
      .mockResolvedValue(isAI ? ({
        match: { task: { task_id: 'task-mapping' } },
        receipt: { object_id: item.id },
      } as never) : null)
    if (isAI) {
      vi.spyOn(useWorkspaceAITaskStore(), 'refresh')
        .mockRejectedValue(new Error('synthetic task refresh failure'))
    }
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semesterItem]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [materialItem]
    catalog.semesterMaterials = [materialRecord]
    catalog.selectedMaterialId = materialItem.id
    await catalog.prepareSemesterMapping([materialRecord.id])
    catalog.semesterMappingProposals = [item]
    const legacyApply = vi.spyOn(catalog, 'applySemesterMapping')
      .mockResolvedValue(undefined)
    await flush()

    const apply = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.includes('应用全部接受项'))
    apply?.click()
    await vi.waitFor(() => expect(adoption).toHaveBeenCalled())

    if (isAI) {
      expect(legacyApply).not.toHaveBeenCalled()
      await vi.waitFor(() => {
        expect(host.textContent).toContain('正式映射已写入且已有回执')
        expect(host.textContent).not.toContain('正式映射尚未确认')
      })
    } else expect(legacyApply).toHaveBeenCalledOnce()
    app.unmount()
  })

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
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight({ source_state_sha256: 'a'.repeat(64) }))
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

  it('identifies a structural PPT preview instead of presenting it as the original slide', async () => {
    const curriculumItem = curriculum()
    const ppt = material('a'.repeat(32), '合成参考课件')
    ppt.material_type = 'pptx'
    ppt.safe_filename = 'synthetic.pptx'
    const pptRecord = record('r'.repeat(32), ppt)
    pptRecord.material_role = 'reference_ppt'
    const slide = unit('1'.repeat(32), ppt)
    slide.unit_kind = 'ppt_slide'
    slide.object_summary = { preview_kind: 'structural' }
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semester(curriculumItem.id)]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [ppt]
    catalog.semesterMaterials = [pptRecord]
    catalog.selectedMaterialId = ppt.id
    catalog.materialUnits = [slide]
    const renderedSlide = { ...slide, object_summary: { preview_kind: 'rendered' } }
    const refreshUnits = vi.spyOn(teachingPrepCatalogApi, 'listMaterialUnits')
      .mockResolvedValue([renderedSlide])
    await flush()

    expect(host.textContent).toContain('结构预览，不是原页')
    expect(host.textContent).not.toContain('真实原页')
    const image = host.querySelector<HTMLImageElement>('.tp-document-workspace__preview')!
    image.dispatchEvent(new Event('load'))
    image.dispatchEvent(new Event('load'))
    await vi.waitFor(() => expect(host.textContent).toContain('真实原页'))
    expect(host.textContent).not.toContain('结构预览，不是原页')
    expect(refreshUnits).toHaveBeenCalledOnce()
    expect(refreshUnits).toHaveBeenCalledWith(ppt.id)
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
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight())
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
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight').mockResolvedValue(mappingPreflight({
      unit_count: 4,
      scanned_unit_count: 4,
      existing_lesson_count: 0,
      creates_initial_tree: true,
    }))
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
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight({ source_state_sha256: 'a'.repeat(64) }))
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

    vi.spyOn(teachingPrepCatalogApi, 'listSemesterMappingProposals').mockResolvedValue([])
    useJobStore().track({
      ...running, status: 'succeeded', progress: 1, stage: 'completed',
      result: {
        semester_id: semesterItem.id,
        operation_id: running.payload.operation_id,
        source_state_sha256: running.payload.source_state_sha256,
        proposal_id: 'p'.repeat(32), recovered_existing: false,
      },
      updated_at: '2026-08-03T00:00:08Z', finished_at: '2026-08-03T00:00:08Z',
    })
    await vi.waitFor(() => expect(host.textContent).toContain('正在恢复审核内容'))
    expect(host.querySelector('.tp-mapping-job-status')?.classList.contains('is-success')).toBe(false)

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

  it('does not let a stale material result-unknown task block the current revision', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const selectedMaterial = material('a'.repeat(32), '当前教材')
    const selectedRecord = record('r'.repeat(32), selectedMaterial)
    const otherTask = adoptedTeachingPrepTask({
      taskId: 'task-other-material',
      taskKind: 'teaching_prep.semester_mapping',
      proposalId: 'p'.repeat(32),
      sourceRef: { kind: 'semester', id: semesterId, revision: 'a'.repeat(64) },
      draftKind: 'semester_mapping',
    })
    otherTask.status = 'result_unknown'
    otherTask.phase = 'result_unknown'
    otherTask.context_refs = [{ kind: 'material', id: selectedRecord.id, revision: '0' }]
    useWorkspaceAITaskStore().track(otherTask)
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight({ source_state_sha256: 'a'.repeat(64) }))
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semesterItem]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [selectedMaterial]
    catalog.semesterMaterials = [selectedRecord]
    catalog.selectedMaterialId = selectedMaterial.id

    await catalog.prepareSemesterMapping([selectedRecord.id])
    await flush()

    expect(host.textContent).toContain('交给 AI 整理课时树')
    expect(host.textContent).not.toContain('结果未知，不能自动重试')
    app.unmount()
  })

  it('lets the teacher explicitly discard an unknown result without sending again', async () => {
    const curriculumItem = curriculum()
    const semesterItem = semester(curriculumItem.id)
    const selectedMaterial = material('a'.repeat(32), '当前教材')
    const selectedRecord = record('r'.repeat(32), selectedMaterial)
    const unknownTask = adoptedTeachingPrepTask({
      taskId: 'task-current-material',
      taskKind: 'teaching_prep.semester_mapping',
      proposalId: 'p'.repeat(32),
      sourceRef: { kind: 'semester', id: semesterId, revision: 'a'.repeat(64) },
      draftKind: 'semester_mapping',
    })
    unknownTask.status = 'result_unknown'
    unknownTask.phase = 'result_unknown'
    unknownTask.context_refs = [{ kind: 'material', id: selectedRecord.id, revision: '1' }]
    useWorkspaceAITaskStore().track(unknownTask)
    vi.spyOn(teachingPrepCatalogApi, 'semesterMappingPreflight')
      .mockResolvedValue(mappingPreflight({ source_state_sha256: 'a'.repeat(64) }))
    const discard = vi.spyOn(workspaceAITaskApi, 'discard').mockResolvedValue({
      ...unknownTask, status: 'discarded', phase: 'finished', revision: unknownTask.revision + 1,
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { app, host, catalog } = mountWorkspace()
    catalog.curricula = [curriculumItem]
    catalog.semesters = [semesterItem]
    catalog.selectedCurriculumId = curriculumItem.id
    catalog.materials = [selectedMaterial]
    catalog.semesterMaterials = [selectedRecord]
    catalog.selectedMaterialId = selectedMaterial.id

    await catalog.prepareSemesterMapping([selectedRecord.id])
    await flush()
    const button = [...host.querySelectorAll('button')].find(item => (
      item.textContent?.includes('放弃旧结果，重新准备')
    ))
    expect(button).toBeTruthy()
    button?.click()
    await flush()

    expect(discard).toHaveBeenCalledWith(unknownTask.operation_id)
    expect(host.textContent).toContain('旧结果已放弃')
    expect(host.textContent).toContain('交给 AI 整理课时树')
    app.unmount()
  })
})
