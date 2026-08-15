import { createPinia, setActivePinia } from 'pinia'
import { computed, createApp, defineComponent, h, nextTick, ref, shallowRef } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  teachingPrepCatalogApi,
  type LessonNode,
  type MaterialLink,
  type MaterialUnit,
  type MaterialVersion,
  type SemesterMaterialRecord,
  type TeachingSemester,
} from '../../api/catalog'
import type {
  ReferenceMaterialLink,
  ReferenceSelectionPreflight,
  SlideAnimationRun,
} from '../../api/workbench'
import { teachingPrepWorkbenchApi } from '../../api/workbench'
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

function material(id: string, name: string, materialType: MaterialVersion['material_type'] = 'pdf'): MaterialVersion {
  return {
    id, source_id: id, display_name: name, material_type: materialType,
    content_sha256: id[0]!.repeat(64), safe_filename: `${id[0]}.${materialType === 'pptx' ? 'pptx' : 'pdf'}`, size_bytes: 20,
    modified_ns: null, unit_count: 5, inspection_status: 'ready',
    availability: 'available', created_at: '2026-08-03T00:00:00Z',
  }
}

function previewUnit(versionId: string, index: number): MaterialUnit {
  const id = `u${index}`.padEnd(32, '0')
  return {
    id,
    material_version_id: versionId,
    unit_kind: 'pdf_page',
    unit_index: index,
    title: null,
    text_excerpt: '',
    text_status: 'embedded',
    formula_review_required: false,
    object_summary: {},
    preview_url: `/api/teaching-prep/material-units/${id}/preview`,
    revision: 1,
    created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z',
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
  pptUnits?: MaterialUnit[]
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

  vi.spyOn(teachingPrepCatalogApi, 'listMaterialUnits').mockResolvedValue(
    options.pptUnits ?? [1, 2].map(index => previewUnit('v-link-ppt', index)),
  )
  vi.spyOn(teachingPrepCatalogApi, 'requestPptPreviewRender').mockImplementation(async (unitId) => ({
    ...previewUnit('v-link-ppt', 1),
    id: unitId,
    unit_kind: 'ppt_slide',
    object_summary: {
      preview_kind: 'structural',
      preview_notice: '本机拼出的页，不是放映软件实拍',
      preview_render_status: 'queued',
    },
  }))
  vi.spyOn(teachingPrepCatalogApi, 'getMaterialUnit').mockImplementation(async (unitId) => ({
    ...previewUnit('v-link-ppt', 1),
    id: unitId,
    unit_kind: 'ppt_slide',
    object_summary: {
      preview_kind: 'structural',
      preview_notice: '本机拼出的页，不是放映软件实拍',
      preview_render_status: 'failed',
      preview_render_error_code: 'wps_preview_timeout',
    },
  }))
  vi.spyOn(teachingPrepWorkbenchApi, 'listSlideAnimationRuns').mockResolvedValue({
    lesson_node_id: lessonId,
    items: [],
    billed_count: 0,
    billed_limit: 3,
    page_limit: 4,
  })

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
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

function pptLinkWithPages(linkId: string, name: string, pages: number): ReferenceMaterialLink {
  const link = referenceLink(linkId, name, 'reference_ppt', 'pptx')
  link.end_unit = pages
  link.units = Array.from({ length: pages }, (_, index) => {
    const page = index + 1
    const unitId = `u${page}`.padEnd(32, '0')
    return {
      unit_id: unitId,
      unit_index: page,
      unit_kind: 'ppt_slide',
      title: `第${page}页标题`,
      preview_url: `/api/teaching-prep/material-units/${unitId}/preview`,
      text_status: 'embedded',
      formula_review_required: false,
      object_summary: {
        preview_kind: 'structural',
        preview_notice: '本机拼出的页，不是放映软件实拍',
      },
    }
  })
  return link
}

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
    const listUnits = vi.spyOn(teachingPrepCatalogApi, 'listMaterialUnits')
      .mockResolvedValue([1, 2, 3, 4, 5].map(index => previewUnit(versionId, index)))
    const createLink = vi.spyOn(teachingPrepCatalogApi, 'createMaterialLink')
      .mockResolvedValue(materialLink('link-new', '补充讲义.pdf'))
    await nextTick()

    const candidate = host.querySelector<HTMLSelectElement>('[data-testid="lesson-material-candidate"]')
    expect(candidate?.querySelectorAll('option')).toHaveLength(2)
    candidate!.value = 'r'.repeat(32)
    candidate!.dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect(listUnits).toHaveBeenCalledWith(versionId))
    await nextTick()
    expect(host.querySelector('[data-testid="material-page-preview"] img')?.getAttribute('src'))
      .toContain('/preview')
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

  it('does not list PPT files in the add-material candidates', async () => {
    const pdfId = 'v'.repeat(32)
    const pptId = 'p'.repeat(32)
    const { app, host, catalog } = await mountStep({
      links: [referenceLink('link-ppt', '认识勾股定理.pptx', 'reference_ppt', 'pptx')],
    })
    catalog.semesterMaterials = [
      parsedRecord('r'.repeat(32), pdfId, '补充讲义.pdf'),
      {
        ...parsedRecord('q'.repeat(32), pptId, '1.1 第1课时.pptx'),
        material_role: 'reference_ppt',
      },
    ]
    catalog.materials = [
      material(pdfId, '补充讲义.pdf'),
      material(pptId, '1.1 第1课时.pptx', 'pptx'),
    ]
    await nextTick()

    const candidate = host.querySelector<HTMLSelectElement>('[data-testid="lesson-material-candidate"]')
    const labels = [...(candidate?.querySelectorAll('option') ?? [])].map(item => item.textContent ?? '')
    expect(labels.some(item => item.includes('补充讲义'))).toBe(true)
    expect(labels.some(item => item.includes('第1课时') || item.includes('pptx'))).toBe(false)
    const purposeLabels = [...(host.querySelector('[data-testid="lesson-material-purpose"]')?.querySelectorAll('option') ?? [])]
      .map(item => item.textContent ?? '')
    expect(purposeLabels).not.toContain('参考课件')
    app.unmount()
  })

  it('previews the linked pages of an existing material', async () => {
    const book = referenceLink('link-book', '教材第 4—5 页', 'textbook')
    book.start_unit = 4
    book.end_unit = 5
    book.units = [
      {
        unit_id: 'u4'.padEnd(32, '0'),
        unit_index: 4,
        unit_kind: 'pdf_page',
        title: null,
        preview_url: '/api/teaching-prep/material-units/u4/preview',
        text_status: 'embedded',
        formula_review_required: false,
      },
      {
        unit_id: 'u5'.padEnd(32, '0'),
        unit_index: 5,
        unit_kind: 'pdf_page',
        title: null,
        preview_url: '/api/teaching-prep/material-units/u5/preview',
        text_status: 'embedded',
        formula_review_required: false,
      },
    ]
    const { app, host } = await mountStep({
      links: [
        referenceLink('link-ppt', '一次函数课件.pptx', 'reference_ppt', 'pptx'),
        book,
      ],
    })

    const buttons = [...host.querySelectorAll<HTMLButtonElement>('[data-testid="preview-lesson-material"]')]
    expect(buttons).toHaveLength(2)
    buttons[1]?.click()
    await nextTick()
    const previewSources = [...host.querySelectorAll('[data-testid="material-page-preview"] img')]
      .map(item => item.getAttribute('src'))
    expect(previewSources.some(item => (
      item?.includes('/api/teaching-prep/material-units/u4/preview')
    ))).toBe(true)
    app.unmount()
  })

  it('previews the primary PPT in send scope and keeps adaptation as a separate unpaid selection', async () => {
    const { app, host, getExpose } = await mountStep({
      links: [
        pptLinkWithPages('link-ppt', '一次函数课件.pptx', 5),
        referenceLink('link-book', '教材第 1—2 页', 'textbook'),
      ],
    })
    await nextTick()

    expect(host.querySelector('[data-testid="ppt-animation-page-picker"]')).toBeTruthy()
    expect(host.textContent).toContain('本机拼出的页')
    expect(host.textContent).toContain('停住后会换成放映软件实拍')
    expect(host.textContent).toContain('课堂动画页')
    expect(host.textContent).toContain('未建任务（生成动画才单独计费，不改课件）')
    expect(host.textContent).toContain('1 次：课件改编')
    expect(getExpose().primaryDisabled).toBe(false)
    app.unmount()
  })

  it('creates animation tasks to the right and sends typed PPT pages', async () => {
    const { app, host } = await mountStep({
      links: [
        pptLinkWithPages('link-ppt', '一次函数课件.pptx', 5),
        referenceLink('link-book', '教材第 1—2 页', 'textbook'),
      ],
    })
    await nextTick()

    host.querySelector<HTMLButtonElement>('[data-testid="create-slide-animation-task"]')?.click()
    await nextTick()
    host.querySelector<HTMLButtonElement>('[data-testid="create-slide-animation-task"]')?.click()
    await nextTick()

    const tasks = [...host.querySelectorAll('[data-testid="slide-animation-task"]')]
    expect(tasks).toHaveLength(2)
    expect(tasks[0]?.textContent).toContain('任务 1')
    expect(tasks[1]?.textContent).toContain('任务 2')

    const firstInput = tasks[0]?.querySelector<HTMLTextAreaElement>('[data-testid="slide-animation-pages"]')
    firstInput!.value = '1,2,3,4,5'
    firstInput!.dispatchEvent(new Event('input'))
    await nextTick()
    expect(tasks[0]?.textContent).toContain('每次最多 4 页')
    expect(tasks[0]?.querySelector<HTMLButtonElement>('[data-testid="generate-slide-animation"]')?.disabled).toBe(true)

    firstInput!.value = '1,3'
    firstInput!.dispatchEvent(new Event('input'))
    await nextTick()
    expect(host.textContent).toContain('任务1：第 1、3 页')
    expect(tasks[0]?.querySelector<HTMLButtonElement>('[data-testid="generate-slide-animation"]')?.disabled).toBe(false)
    app.unmount()
  })

  it('clears animation tasks when the primary PPT changes', async () => {
    const { app, host } = await mountStep({
      links: [
        pptLinkWithPages('link-ppt-a', '课件A.pptx', 3),
        pptLinkWithPages('link-ppt-b', '课件B.pptx', 3),
        referenceLink('link-book', '教材第 1—2 页', 'textbook'),
      ],
    })
    await nextTick()

    host.querySelector<HTMLButtonElement>('[data-testid="create-slide-animation-task"]')?.click()
    await nextTick()
    const firstInput = host.querySelector<HTMLTextAreaElement>('[data-testid="slide-animation-pages"]')
    firstInput!.value = '1'
    firstInput!.dispatchEvent(new Event('input'))
    await nextTick()
    expect(host.querySelector('[data-testid="slide-animation-task"]')?.textContent)
      .toContain('将使用第 1 页')

    const radios = [...host.querySelectorAll<HTMLInputElement>('input[name="primary-reference-ppt"]')]
    radios[1]!.checked = true
    radios[1]!.dispatchEvent(new Event('change'))
    await nextTick()
    await nextTick()

    expect(host.querySelector('[data-testid="slide-animation-task"]')).toBeNull()
    expect(host.textContent).toContain('未建任务（生成动画才单独计费，不改课件）')
    app.unmount()
  })

  it('sends selected PPT pages as a separate billed animation request and can accept the draft', async () => {
    const draft: SlideAnimationRun = {
      id: 'a'.repeat(32),
      lesson_node_id: lessonId,
      material_version_id: 'v-link-ppt',
      material_link_id: 'link-ppt',
      operation_id: 'slide-animation-test',
      page_indexes: [1],
      storyboard: { title: '一次函数引入', scenes: [] },
      status: 'succeeded',
      teacher_decision: 'pending',
      error_code: null,
      model_call_count: 1,
      revision: 2,
      can_preview: true,
      can_download: false,
      created_at: '2026-08-14T00:00:00Z',
      updated_at: '2026-08-14T00:00:00Z',
      finished_at: '2026-08-14T00:00:00Z',
    }
    const { app, host, getExpose } = await mountStep({
      links: [
        pptLinkWithPages('link-ppt', '一次函数课件.pptx', 5),
        referenceLink('link-book', '教材第 1—2 页', 'textbook'),
      ],
    })
    const start = vi.spyOn(teachingPrepWorkbenchApi, 'startSlideAnimationRun').mockResolvedValue(draft)
    vi.spyOn(teachingPrepWorkbenchApi, 'listSlideAnimationRuns').mockResolvedValue({
      lesson_node_id: lessonId,
      items: [draft],
      billed_count: 1,
      billed_limit: 3,
      page_limit: 4,
    })
    const accept = vi.spyOn(teachingPrepWorkbenchApi, 'acceptSlideAnimationRun').mockResolvedValue({
      ...draft,
      status: 'accepted',
      teacher_decision: 'accepted',
      can_download: true,
      revision: 3,
    })
    await nextTick()

    host.querySelector<HTMLButtonElement>('[data-testid="create-slide-animation-task"]')?.click()
    await nextTick()
    const pages = host.querySelector<HTMLTextAreaElement>('[data-testid="slide-animation-pages"]')
    pages!.value = '1'
    pages!.dispatchEvent(new Event('input'))
    await nextTick()
    host.querySelector<HTMLButtonElement>('[data-testid="generate-slide-animation"]')?.click()
    await Promise.resolve()
    await nextTick()
    await Promise.resolve()
    await nextTick()

    expect(start).toHaveBeenCalledWith(lessonId, {
      operationId: expect.stringMatching(/^slide-animation-/),
      materialLinkId: 'link-ppt',
      pageIndexes: [1],
    })
    expect(host.querySelector('[data-testid="ppt-animation-draft"]')).toBeTruthy()
    expect(host.querySelector('iframe.tp-animation-preview')?.getAttribute('sandbox')).toBe('allow-scripts')
    expect(getExpose().primaryDisabled).toBe(false)

    host.querySelector<HTMLButtonElement>('[data-testid="accept-slide-animation"]')?.click()
    await nextTick()
    await nextTick()
    expect(accept).toHaveBeenCalledWith(draft.id, 2)
    app.unmount()
  })

  it('requests a capture after the current PPT page stays in view', async () => {
    vi.useFakeTimers()
    const { app, host } = await mountStep({
      links: [
        pptLinkWithPages('link-ppt', '一次函数课件.pptx', 5),
        referenceLink('link-book', '教材第 1—2 页', 'textbook'),
      ],
    })
    await nextTick()
    const requestRender = vi.mocked(teachingPrepCatalogApi.requestPptPreviewRender)
    expect(requestRender).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(400)
    expect(requestRender).toHaveBeenCalledWith('u1'.padEnd(32, '0'))

    const nextButton = [...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '下一页')
    nextButton?.click()
    await nextTick()
    requestRender.mockClear()
    await vi.advanceTimersByTimeAsync(399)
    expect(requestRender).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(requestRender).toHaveBeenCalledWith('u2'.padEnd(32, '0'))
    app.unmount()
  })
})
