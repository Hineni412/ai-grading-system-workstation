import { createPinia, setActivePinia } from 'pinia'
import { createApp, computed, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  LessonNode,
  MaterialUnit,
  MaterialVersion,
  SemesterMappingProposal,
  SemesterMaterialRecord,
} from '../../api/catalog'
import { teachingPrepWorkbenchApi } from '../../api/workbench'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import { teachingPrepRouteStateKey } from '../../workbench/routeContext'
import type { TeachingPrepRouteState } from '../../workbench/routeState'
import MappingPanel from './MappingPanel.vue'

const semesterId = 's'.repeat(32)
const lessonId = '1'.repeat(32)

function material(id: string, name: string): MaterialVersion {
  return {
    id, source_id: id, display_name: name, material_type: 'pdf',
    content_sha256: id[0]!.repeat(64), safe_filename: `${id[0]}.pdf`, size_bytes: 20,
    modified_ns: null, unit_count: 2, inspection_status: 'ready',
    availability: 'available', created_at: '2026-08-03T00:00:00Z',
  }
}

function record(id: string, item: MaterialVersion): SemesterMaterialRecord {
  return {
    id, semester_id: semesterId, material_source_id: item.source_id,
    display_name: item.display_name, material_role: 'textbook', parse_status: 'parsed',
    mapping_status: 'unmapped', current_material_version_id: item.id,
    safe_filename: item.safe_filename, current_inspection_status: 'ready',
    current_unit_count: 2, last_parsed_version_id: item.id, has_unparsed_update: false,
    parsed_at: '2026-08-03T00:00:00Z', is_active: true, revision: 1,
    created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
  }
}

function unit(id: string, item: MaterialVersion, index: number): MaterialUnit {
  return {
    id, material_version_id: item.id, unit_kind: 'pdf_page', unit_index: index,
    title: null, text_excerpt: '', text_status: 'empty', formula_review_required: false,
    object_summary: {}, preview_url: `/preview/${item.id}`, revision: 1,
    created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
  }
}

function lesson(id: string): LessonNode {
  return {
    id, curriculum_id: 'c'.repeat(32), parent_id: null, node_type: 'lesson',
    title: '第一课时', sort_order: 1, duration_minutes: 45, source_kind: 'teacher',
    is_active: true, revision: 1, created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z',
  }
}

function proposal(
  recordId: string,
  decision: 'pending' | 'accepted' = 'pending',
): SemesterMappingProposal {
  return {
    id: 'p'.repeat(32), semester_id: semesterId, operation_id: 'operation-p',
    source_state_sha256: 'f'.repeat(64), status: 'proposed',
    payload: {
      tree: [], uncertainties: [], source_material_record_ids: [recordId],
      generation_source: 'local_reference_ppt_names',
      mappings: [{
        mapping_id: 'mapping-1', material_record_id: recordId,
        lesson_ref: lessonId, start_unit: 1, end_unit: 2, purpose: 'textbook',
        decision, teacher_revision: null, decision_reason: null,
        confidence: 'high', basis: '依据文件命名提出。',
      }],
    },
    revision: 1, created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z', applied_at: null,
  } as SemesterMappingProposal
}

const textbook = material('a'.repeat(32), '教材.pdf')
const textbookRecord = record('r'.repeat(32), textbook)

function fakeRouteState(): TeachingPrepRouteState {
  return {
    currentView: computed(() => 'library' as const),
    currentLessonId: computed(() => null),
    currentStep: computed(() => 1 as const),
    openOverview: vi.fn(async () => {}),
    openLibrary: vi.fn(async () => {}),
    openLesson: vi.fn(async () => {}),
    setStep: vi.fn(async () => {}),
  }
}

async function mountPanel(options: { proposals?: SemesterMappingProposal[]; selectMaterial?: boolean } = {}) {
  const catalog = useTeachingPrepCatalogStore()
  catalog.materials = [textbook]
  catalog.semesterMaterials = [textbookRecord]
  catalog.semesterMappingProposals = options.proposals ?? []
  catalog.lessonNodes = [lesson(lessonId)]
  catalog.materialUnits = [unit('u1', textbook, 1), unit('u2', textbook, 2)]
  catalog.selectedLessonId = lessonId
  if (options.selectMaterial) catalog.selectedMaterialId = textbook.id
  vi.spyOn(catalog, 'prepareSemesterMapping').mockResolvedValue(undefined as never)

  const routeState = fakeRouteState()
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(MappingPanel)
  app.provide(teachingPrepRouteStateKey, routeState)
  app.mount(host)
  await nextTick()
  await nextTick()
  return { app, host, catalog, routeState }
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('MappingPanel', () => {
  it('renders mapping rows with a pending decision badge', async () => {
    const { app, host } = await mountPanel({
      proposals: [proposal(textbookRecord.id)],
      selectMaterial: true,
    })

    expect(host.textContent).toContain('1—2 页/张')
    expect(host.textContent).toContain('待处理')
    expect(host.textContent).toContain('置信度 高')
    app.unmount()
  })

  it('accepts a mapping through the workbench review API', async () => {
    const original = proposal(textbookRecord.id)
    const updated = proposal(textbookRecord.id, 'accepted')
    const review = vi.spyOn(teachingPrepWorkbenchApi, 'reviewMapping')
      .mockResolvedValue(updated as never)
    const { app, host } = await mountPanel({
      proposals: [original],
      selectMaterial: true,
    })

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '接受')?.click()
    await vi.waitFor(() => expect(review).toHaveBeenCalled())
    expect(review.mock.calls[0]?.[0]).toBe(original.id)
    expect(review.mock.calls[0]?.[1]).toBe('mapping-1')
    expect(review.mock.calls[0]?.[2]).toMatchObject({
      decision: 'accepted',
      lesson_ref: lessonId,
      start_unit: 1,
      end_unit: 2,
    })
    app.unmount()
  })

  it('blocks applying while any mapping is still pending', async () => {
    const { app, host, catalog } = await mountPanel({
      proposals: [proposal(textbookRecord.id)],
      selectMaterial: true,
    })
    const apply = vi.spyOn(catalog, 'applySemesterMapping').mockResolvedValue({} as never)
    const notices: string[] = []
    // capture emitted notices via a listener on the root element
    host.addEventListener('notice', event => notices.push((event as CustomEvent).detail))

    host.querySelector<HTMLButtonElement>('[data-testid="apply-semester-mapping"]')?.click()
    await nextTick()
    expect(apply).not.toHaveBeenCalled()
    app.unmount()
  })

  it('applies the proposal through the store once every mapping is decided', async () => {
    const { app, host, catalog, routeState } = await mountPanel({
      proposals: [proposal(textbookRecord.id, 'accepted')],
      selectMaterial: true,
    })
    const apply = vi.spyOn(catalog, 'applySemesterMapping').mockResolvedValue({} as never)
    vi.spyOn(catalog, 'load').mockResolvedValue()

    host.querySelector<HTMLButtonElement>('[data-testid="apply-semester-mapping"]')?.click()
    await vi.waitFor(() => expect(apply).toHaveBeenCalled())
    await vi.waitFor(() => expect(routeState.openLesson).toHaveBeenCalledWith(lessonId))
    app.unmount()
  })

  it('saves a manual page-range link for the selected material', async () => {
    const { app, host, catalog } = await mountPanel({ selectMaterial: true })
    const confirmRanges = vi.spyOn(catalog, 'confirmMaterialRanges').mockResolvedValue({} as never)

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '保存人工关联')?.click()
    await vi.waitFor(() => expect(confirmRanges).toHaveBeenCalled())
    expect(confirmRanges.mock.calls[0]?.[0]).toEqual([{ start: 1, end: 1 }])
    app.unmount()
  })
})
