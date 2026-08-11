import { createPinia, setActivePinia } from 'pinia'
import { computed, createApp, defineComponent, h, nextTick, ref, shallowRef } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { LessonNode, SlideOperation, SlidePlan } from '../../api/catalog'
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

function slidePlan(operations: SlideOperation[]): SlidePlan {
  return {
    id: 'p'.repeat(32),
    lesson_draft_id: 'd'.repeat(32),
    resource_pack_id: 'k'.repeat(32),
    version_number: 1,
    based_on_plan_id: null,
    source_ppt_state_sha256: 'a'.repeat(64),
    status: 'in_review',
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

interface StepExpose {
  primaryLabel: string
  primaryDisabled: boolean
  runPrimary: () => Promise<void>
}

async function mountStep(options: { plans?: SlidePlan[] } = {}) {
  const catalog = useTeachingPrepCatalogStore()
  catalog.lessonNodes = [lessonNode]
  catalog.selectedLessonId = lessonId
  catalog.slidePlans = options.plans ?? []
  catalog.selectedSlidePlanId = options.plans?.[0]?.id ?? null
  catalog.slidePlanPreview = {
    valid_for_execution: true,
    source_changed: false,
    includes_proposed_operations: true,
    before_slide_count: 1,
    after_slide_count: 1,
    before: [{ title: '引入页', original_index: 1 }],
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

  it('renders operations and keeps the primary action disabled until all decided', async () => {
    const { app, host, getExpose } = await mountStep({
      plans: [slidePlan([operation('op-1'), operation('op-2')])],
    })

    expect(host.textContent).toContain('删除整页')
    expect(host.querySelectorAll('.tp-operation-row')).toHaveLength(2)
    expect(getExpose().primaryLabel).toBe('保存审核决定并进入副本')
    expect(getExpose().primaryDisabled).toBe(true)
    app.unmount()
  })

  it('renders the change overlay for an operation with a valid position', async () => {
    const positioned = {
      ...operation('op-1'),
      target: {
        generated_page_number: 1,
        position: { x: 0.1, y: 0.2, width: 0.3, height: 0.4 },
      },
    }
    const { app, host } = await mountStep({ plans: [slidePlan([positioned])] })

    const overlay = host.querySelector('.tp-change-overlay')
    expect(overlay).toBeTruthy()
    expect((overlay as HTMLElement).style.left).toBe('10%')
    expect((overlay as HTMLElement).style.top).toBe('20%')
    expect((overlay as HTMLElement).style.width).toBe('30%')
    expect((overlay as HTMLElement).style.height).toBe('40%')
    app.unmount()
  })

  it('saves review decisions through the catalog fallback path', async () => {
    const plan = slidePlan([operation('op-1')])
    const { app, host, catalog, workbench } = await mountStep({ plans: [plan] })
    const review = vi.spyOn(catalog, 'reviewSlidePlan').mockResolvedValue(undefined)

    const approve = host.querySelector<HTMLInputElement>('input[type="radio"][value="approved"]')
    expect(approve).toBeTruthy()
    approve!.click()
    await nextTick()

    ;[...host.querySelectorAll('button')]
      .find(button => button.textContent?.trim() === '保存全部审核决定')?.click()
    await vi.waitFor(() => expect(review).toHaveBeenCalled())
    expect(review.mock.calls[0]?.[1]).toEqual([expect.objectContaining({
      operation_id: 'op-1',
      decision: 'approved',
    })])
    expect(workbench.setDirty).toHaveBeenCalledWith(null)
    expect(host.textContent).toContain('课件计划审核已保存')
    app.unmount()
  })

  it('moves to the copies step after a successful primary run', async () => {
    const plan = slidePlan([operation('op-1', 'approved')])
    const { app, catalog, workbench, getExpose } = await mountStep({ plans: [plan] })
    vi.spyOn(catalog, 'reviewSlidePlan').mockResolvedValue(undefined)

    expect(getExpose().primaryDisabled).toBe(false)
    await getExpose().runPrimary()
    expect(workbench.routeState.setStep).toHaveBeenCalledWith(3)
    app.unmount()
  })
})
