import { createPinia, setActivePinia } from 'pinia'
import { computed, createApp, defineComponent, h, nextTick, ref, shallowRef } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { LessonNode, PptxExecution, SlideOperation, SlidePlan } from '../../api/catalog'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import { teachingPrepLessonWorkbenchKey } from '../../workbench/routeContext'
import type { TeachingPrepLessonWorkbench } from '../../workbench/lessonWorkbench'
import ReviewSlidesStep from './ReviewSlidesStep.vue'

const lessonId = '1'.repeat(32)

const lessonNode: LessonNode = {
  id: lessonId, curriculum_id: 'c'.repeat(32), parent_id: null,
  node_type: 'lesson', title: '第一课时', sort_order: 1, duration_minutes: 45,
  source_kind: 'teacher', is_active: true, revision: 1,
  created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
}

function operation(id: string, decision: SlideOperation['decision'] = 'proposed'): SlideOperation {
  return {
    operation_id: id,
    kind: 'delete_slide',
    decision,
    target: { generated_page_number: 1 },
    reason: '与教材例题重复',
    citations: [],
    planned_minutes: 0,
    risk: 'low',
    execution_mode: 'automatic',
    support_note: null,
    details: {},
    teacher_note: null,
  }
}

function slidePlan(operations: SlideOperation[], status: SlidePlan['status'] = 'in_review'): SlidePlan {
  return {
    id: 'p'.repeat(32),
    lesson_draft_id: 'd'.repeat(32),
    resource_pack_id: 'k'.repeat(32),
    version_number: 1,
    based_on_plan_id: null,
    source_ppt_state_sha256: 'a'.repeat(64),
    status,
    payload: {
      schema_version: 1,
      source_presentations: [],
      slides: [],
      operations,
      unsupported_objects: [],
      approval_history: [],
    },
    created_at: '2026-08-03T00:00:00Z',
  }
}

function execution(status: PptxExecution['status'], extras: Partial<PptxExecution> = {}): PptxExecution {
  return {
    id: 'e'.repeat(32),
    operation_id: 'op-exec-1',
    slide_plan_id: 'p'.repeat(32),
    source_material_version_id: 'v'.repeat(32),
    source_sha256: 'a'.repeat(64),
    expected_slide_count: 1,
    status,
    execution_report: null,
    verification_report: null,
    error_code: null,
    published_version_id: null,
    phase: status === 'published' ? 'done' : 'verifying',
    cancel_requested: false,
    staging_retained: true,
    recovery_actions: [],
    created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z',
    finished_at: null,
    ...extras,
  }
}

interface StepExpose {
  primaryLabel: string
  primaryDisabled: boolean
  runPrimary: () => Promise<void>
}

async function mountStep(options: {
  plans?: SlidePlan[]
  executions?: PptxExecution[]
  wps?: boolean
} = {}) {
  const catalog = useTeachingPrepCatalogStore()
  catalog.lessonNodes = [lessonNode]
  catalog.selectedLessonId = lessonId
  catalog.slidePlans = options.plans ?? []
  catalog.selectedSlidePlanId = options.plans?.[0]?.id ?? null
  catalog.pptxExecutions = options.executions ?? []
  catalog.moduleStatus = {
    module: 'teaching-prep',
    enabled: true,
    schema_version: '1',
    real_model_enabled: false,
    semester_mapping_model_available: false,
    exercise_suggestion_model_available: false,
    real_wps_enabled: options.wps === true,
    wps_execution_available: options.wps === true,
  }
  catalog.slidePlanPreview = {
    valid_for_execution: true,
    source_changed: false,
    includes_proposed_operations: true,
    before_slide_count: 1,
    after_slide_count: 1,
    before: [{ title: '引入页', original_index: 1, preview_url: '/before.png' }],
    after: [],
    changes: [],
    manual_only: [],
  }

  const workbench = {
    catalog,
    routeState: {
      currentView: computed(() => 'lesson' as const),
      currentLessonId: computed(() => lessonId),
      currentStep: computed(() => 2 as const),
      openOverview: vi.fn(async () => {}),
      openLibrary: vi.fn(async () => {}),
      openLesson: vi.fn(async () => {}),
      setStep: vi.fn(async () => {}),
    },
    lessonStatuses: ref([]),
    selectedLesson: computed(() => lessonNode),
    selectedStatus: computed(() => null),
    referencePreflight: shallowRef(null),
    pptxVersions: ref([]),
    dirtyReason: ref<string | null>(null),
    workbenchError: ref(''),
    loading: ref(false),
    load: vi.fn(async () => {}),
    refresh: vi.fn(async () => {}),
    setDirty: vi.fn(),
    requestLeave: vi.fn(async () => true),
  } as unknown as TeachingPrepLessonWorkbench

  vi.spyOn(catalog, 'reviewSlidePlan').mockResolvedValue(undefined)
  vi.spyOn(catalog, 'selectSlidePlan').mockResolvedValue(undefined)
  vi.spyOn(catalog, 'executePptx').mockImplementation(async () => {
    if (options.wps === true && catalog.pptxExecutions.length === 0) {
      catalog.pptxExecutions = [execution('verifying', { verification_report: { verified: true } })]
    }
  })
  vi.spyOn(catalog, 'confirmPptxPreview').mockResolvedValue(undefined)

  let expose!: StepExpose
  const Wrapper = defineComponent({
    setup() {
      return () => h(ReviewSlidesStep, {
        ref: (value: unknown) => {
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
  document.body.innerHTML = ''
})

describe('ReviewSlidesStep', () => {
  it('shows the idle task panel when no slide plan exists', async () => {
    const { app, host } = await mountStep()

    expect(host.textContent).toContain('还没有发送改编任务')
    app.unmount()
  })

  it('shows before/after compare and does not require per-operation radios', async () => {
    const { app, host, getExpose } = await mountStep({
      plans: [slidePlan([operation('op-1'), operation('op-2')])],
    })

    expect(host.textContent).toContain('改前 / 改后对照')
    expect(host.querySelector('input[type="radio"]')).toBeNull()
    expect(getExpose().primaryLabel).toBe('这台电脑还不能导出副本')
    expect(getExpose().primaryDisabled).toBe(true)
    app.unmount()
  })

  it('auto-adopts proposed operations without a teacher checkbox', async () => {
    const plan = slidePlan([operation('op-1')])
    const { app, catalog } = await mountStep({ plans: [plan] })

    await vi.waitFor(() => expect(catalog.reviewSlidePlan).toHaveBeenCalled())
    expect(vi.mocked(catalog.reviewSlidePlan).mock.calls[0]?.[1]).toEqual([
      expect.objectContaining({ operation_id: 'op-1', decision: 'approved' }),
    ])
    app.unmount()
  })

  it('enables export after an isolated preview is ready', async () => {
    const plan = slidePlan([operation('op-1', 'approved')], 'approved')
    const { app, host, catalog, workbench, getExpose } = await mountStep({
      plans: [plan],
      executions: [execution('verifying', { verification_report: { verified: true } })],
    })

    expect(host.textContent).toContain('改前 / 改后对照')
    expect(getExpose().primaryLabel).toBe('确认这一版并导出副本')
    expect(getExpose().primaryDisabled).toBe(false)
    await getExpose().runPrimary()
    expect(catalog.confirmPptxPreview).toHaveBeenCalled()
    expect(workbench.routeState.setStep).toHaveBeenCalledWith(3)
    app.unmount()
  })

  it('starts an isolated preview for an approved plan', async () => {
    const plan = slidePlan([operation('op-1', 'approved')], 'approved')
    const { app, catalog } = await mountStep({
      plans: [plan],
      wps: true,
    })

    await vi.waitFor(() => expect(catalog.executePptx).toHaveBeenCalledWith(plan, { previewOnly: true }))
    expect(catalog.pptxExecutions[0]?.verification_report).toEqual({ verified: true })
    app.unmount()
  })

  it('enables resend after a page note is entered', async () => {
    const { app, host } = await mountStep({
      plans: [slidePlan([operation('op-1', 'approved')], 'approved')],
      executions: [execution('verifying', { verification_report: { verified: true } })],
    })
    const button = host.querySelector<HTMLButtonElement>('[data-testid="resend-with-page-notes"]')
    expect(button?.disabled).toBe(true)
    const textarea = host.querySelector<HTMLTextAreaElement>('[data-testid="compare-page-note"]')
    expect(textarea).toBeTruthy()
    textarea!.value = '插题太大，挡住例题'
    textarea!.dispatchEvent(new Event('input'))
    await nextTick()
    expect(host.querySelector<HTMLButtonElement>('[data-testid="resend-with-page-notes"]')?.disabled).toBe(false)
    app.unmount()
  })
})
