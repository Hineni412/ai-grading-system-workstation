import { createPinia, setActivePinia } from 'pinia'
import { computed, createApp, defineComponent, h, nextTick, ref, shallowRef } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  teachingPrepCatalogApi,
  type LessonNode,
  type MaterialLink,
  type MaterialVersion,
  type SemesterMaterialRecord,
  type TeachingSemester,
} from '../../api/catalog'
import type {
  ReferenceMaterialLink,
  ReferenceSelectionPreflight,
} from '../../api/workbench'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import { teachingPrepLessonWorkbenchKey } from '../../workbench/routeContext'
import type { TeachingPrepLessonWorkbench } from '../../workbench/lessonWorkbench'
import ConfirmMaterialsStep from './ConfirmMaterialsStep.vue'

const lessonId = '1'.repeat(32)
const semesterId = 's'.repeat(32)

const preferences = {
  schema_version: 1 as const,
  label_textbook_pages: true,
  page_label_font_size: 28 as const,
  trim_excess_practice: true,
  practice_trim_level: 'moderate' as const,
  preserve_teaching_examples: true,
  prefer_short_practice: true,
  supplement_from_references: true,
  supplement_question_limit: 2,
  supplement_as_source_image: true,
  prioritize_homework_workbook: true,
  avoid_direct_homework_copy: true,
  avoid_ppt_duplicates: true,
}

const semester: TeachingSemester = {
  id: semesterId, curriculum_id: 'c'.repeat(32), curriculum_title: '八年级上册',
  school_year: '2026-2027', term: 'first', planned_new_lesson_count: 48,
  status: 'active', active_lesson_count: 1, not_started_lesson_count: 1,
  preparing_lesson_count: 0, ready_lesson_count: 0, taught_lesson_count: 0,
  skipped_lesson_count: 0, material_count: 0, parsed_material_count: 0,
  mapped_material_count: 0, revision: 1,
  created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
}

const lessonNode: LessonNode = {
  id: lessonId, curriculum_id: semester.curriculum_id, parent_id: null,
  node_type: 'lesson', title: '第一课时', sort_order: 1, duration_minutes: 45,
  source_kind: 'teacher', is_active: true, revision: 1,
  created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
}

function referenceLink(
  linkId: string,
  name: string,
  purpose: string,
  materialType = 'pdf',
): ReferenceMaterialLink {
  return {
    link_id: linkId,
    link_revision: 1,
    purpose,
    material_version_id: `v-${linkId}`,
    material_name: name,
    material_type: materialType,
    content_sha256: 'a'.repeat(64),
    start_unit: 1,
    end_unit: 2,
    units: [],
  }
}

function preflight(links: ReferenceMaterialLink[]): ReferenceSelectionPreflight {
  return {
    lesson_node_id: lessonId,
    source_state_sha256: 'a'.repeat(64),
    catalog: { lesson: {}, material_links: links },
    draft: null,
    model_available: false,
    model_label: null,
    model_destination_fingerprint: 'f'.repeat(64),
    will_call_model: false,
  }
}

function materialLink(linkId: string, name: string): MaterialLink {
  return {
    id: linkId,
    lesson_node_id: lessonId,
    material_version_id: `v-${linkId}`,
    material_name: name,
    material_type: 'pptx',
    start_unit: 1,
    end_unit: 2,
    crop: null,
    purpose: 'reference_ppt',
    teacher_note: null,
    confirmation_status: 'confirmed',
    source_version_sha256: 'a'.repeat(64),
    sort_order: 1,
    is_active: true,
    revision: 1,
    created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z',
  }
}

function parsedRecord(id: string, versionId: string, name: string): SemesterMaterialRecord {
  return {
    id, semester_id: semesterId, material_source_id: id,
    display_name: name, material_role: 'supplement', parse_status: 'parsed',
    mapping_status: 'unmapped', current_material_version_id: versionId,
    safe_filename: `${id[0]}.pdf`, current_inspection_status: 'ready',
    current_unit_count: 5, last_parsed_version_id: versionId, has_unparsed_update: false,
    parsed_at: '2026-08-03T00:00:00Z', is_active: true, revision: 1,
    created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
  }
}

function material(id: string, name: string): MaterialVersion {
  return {
    id, source_id: id, display_name: name, material_type: 'pdf',
    content_sha256: id[0]!.repeat(64), safe_filename: `${id[0]}.pdf`, size_bytes: 20,
    modified_ns: null, unit_count: 5, inspection_status: 'ready',
    availability: 'available', created_at: '2026-08-03T00:00:00Z',
  }
}

interface StepExpose {
  primaryLabel: string
  primaryDisabled: boolean
  runPrimary: () => Promise<void>
}

async function mountStep(options: {
  links?: ReferenceMaterialLink[]
  materialLinks?: MaterialLink[]
  withPreferences?: boolean
} = {}) {
  const catalog = useTeachingPrepCatalogStore()
  catalog.semesters = [semester]
  catalog.selectedCurriculumId = semester.curriculum_id
  catalog.selectedSemesterId = semesterId
  catalog.lessonNodes = [lessonNode]
  catalog.selectedLessonId = lessonId
  catalog.materialLinks = options.materialLinks ?? []
  catalog.semesterMaterials = []
  catalog.materials = []
  catalog.exerciseCandidates = []
  catalog.lessonDrafts = []
  catalog.resourcePacks = []
  if (options.withPreferences !== false) {
    catalog.teachingPreferences = {
      revision: 1,
      payload: preferences,
      updated_at: '2026-08-03T00:00:00Z',
    }
  }

  const workbench = {
    catalog,
    routeState: {
      currentView: computed(() => 'lesson' as const),
      currentLessonId: computed(() => lessonId),
      currentStep: computed(() => 1 as const),
      openOverview: vi.fn(async () => {}),
      openLibrary: vi.fn(async () => {}),
      openLesson: vi.fn(async () => {}),
      setStep: vi.fn(async () => {}),
    },
    lessonStatuses: ref([]),
    selectedLesson: computed(() => lessonNode),
    selectedStatus: computed(() => null),
    referencePreflight: shallowRef(preflight(options.links ?? [])),
    pptxVersions: ref([]),
    dirtyReason: ref<string | null>(null),
    workbenchError: ref(''),
    loading: ref(false),
    load: vi.fn(async () => {}),
    refresh: vi.fn(async () => {}),
    setDirty: vi.fn(),
    requestLeave: vi.fn(async () => true),
  } as unknown as TeachingPrepLessonWorkbench

  let expose!: StepExpose
  const Wrapper = defineComponent({
    setup() {
      const stepRef = ref<StepExpose | null>(null)
      return () => h(ConfirmMaterialsStep, {
        ref: (value: unknown) => {
          stepRef.value = value as StepExpose
          if (value) expose = value as StepExpose
        },
      })
    },
  })

  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp(Wrapper)
  app.provide(teachingPrepLessonWorkbenchKey, workbench)
  app.mount(host)
  await nextTick()
  await nextTick()
  return { app, host, catalog, workbench, getExpose: () => expose }
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('ConfirmMaterialsStep', () => {
  it('renders linked materials and defaults to the generate primary action', async () => {
    const { app, host, getExpose } = await mountStep({
      links: [
        referenceLink('link-ppt', '一次函数课件.pptx', 'reference_ppt', 'pptx'),
        referenceLink('link-book', '教材第 1—2 页', 'textbook'),
      ],
    })

    expect(host.textContent).toContain('一次函数课件.pptx')
    expect(host.textContent).toContain('教材第 1—2 页')
    expect(getExpose().primaryLabel).toBe('确认资料并生成 AI 改编（调用 1 次）')
    expect(getExpose().primaryDisabled).toBe(false)
    app.unmount()
  })

  it('requires a primary PPT before the primary action is enabled', async () => {
    const { app, getExpose } = await mountStep({
      links: [referenceLink('link-book', '教材第 1—2 页', 'textbook')],
    })

    expect(getExpose().primaryDisabled).toBe(true)
    app.unmount()
  })

  it('removes a material link after confirmation', async () => {
    const link = materialLink('link-ppt', '一次函数课件.pptx')
    const { app, host, catalog, workbench } = await mountStep({
      links: [referenceLink('link-ppt', '一次函数课件.pptx', 'reference_ppt', 'pptx')],
      materialLinks: [link],
    })
    const deactivate = vi.spyOn(catalog, 'deactivateMaterialLink').mockResolvedValue({} as never)
    vi.stubGlobal('confirm', vi.fn(() => true))

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '移除')?.click()
    await vi.waitFor(() => expect(deactivate).toHaveBeenCalledWith(link))
    expect(workbench.refresh).toHaveBeenCalled()
    app.unmount()
  })

  it('adds a quick page-range link for an unlinked parsed material', async () => {
    const versionId = 'v'.repeat(32)
    const { app, host, catalog, workbench } = await mountStep({
      links: [referenceLink('link-ppt', '一次函数课件.pptx', 'reference_ppt', 'pptx')],
    })
    catalog.semesterMaterials = [parsedRecord('r'.repeat(32), versionId, '补充讲义.pdf')]
    catalog.materials = [material(versionId, '补充讲义.pdf')]
    const createLink = vi.spyOn(teachingPrepCatalogApi, 'createMaterialLink')
      .mockResolvedValue(materialLink('link-new', '补充讲义.pdf'))
    await nextTick()

    const candidate = host.querySelector<HTMLSelectElement>('[data-testid="lesson-material-candidate"]')
    expect(candidate?.querySelectorAll('option')).toHaveLength(2)
    candidate!.value = 'r'.repeat(32)
    candidate!.dispatchEvent(new Event('change'))
    await nextTick()
    const purpose = host.querySelector<HTMLSelectElement>('[data-testid="lesson-material-purpose"]')
    purpose!.value = 'supplement'
    purpose!.dispatchEvent(new Event('change'))
    const ranges = host.querySelectorAll<HTMLInputElement>('[data-testid="lesson-material-range"]')
    ranges[0]!.value = '2'
    ranges[0]!.dispatchEvent(new Event('input'))
    ranges[1]!.value = '4'
    ranges[1]!.dispatchEvent(new Event('input'))
    await nextTick()

    host.querySelector<HTMLButtonElement>('[data-testid="add-lesson-material"]')?.click()
    await vi.waitFor(() => expect(createLink).toHaveBeenCalled())
    expect(createLink.mock.calls[0]?.[0]).toBe(lessonId)
    expect(createLink.mock.calls[0]?.[1]).toMatchObject({
      material_version_id: versionId,
      start_unit: 2,
      end_unit: 4,
      purpose: 'supplement',
      confirmation_status: 'confirmed',
    })
    expect(workbench.refresh).toHaveBeenCalled()
    app.unmount()
  })
})
