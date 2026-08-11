import { createPinia, setActivePinia } from 'pinia'
import { computed, createApp, defineComponent, h, nextTick, ref, shallowRef } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { LessonNode, UpClassPackage } from '../../api/catalog'
import { teachingPrepWorkbenchApi, type TrustedPptxVersion } from '../../api/workbench'
import { useTeachingPrepCatalogStore } from '../../stores/catalog'
import { teachingPrepLessonWorkbenchKey } from '../../workbench/routeContext'
import type { TeachingPrepLessonWorkbench } from '../../workbench/lessonWorkbench'
import CopiesStep from './CopiesStep.vue'

const lessonId = '1'.repeat(32)

const lessonNode: LessonNode = {
  id: lessonId, curriculum_id: 'c'.repeat(32), parent_id: null,
  node_type: 'lesson', title: '第一课时', sort_order: 1, duration_minutes: 45,
  source_kind: 'teacher', is_active: true, revision: 1,
  created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
}

function version(id: string, number: number, isCurrent: boolean): TrustedPptxVersion {
  return {
    id,
    slide_plan_id: 'p'.repeat(32),
    lesson_node_id: lessonId,
    execution_run_id: `run-${number}`,
    version_number: number,
    status: 'published',
    output_filename: `一次函数_v${number}.pptx`,
    output_sha256: 'a'.repeat(64),
    slide_count: 3,
    verification_report: {},
    download_url: `/download/${id}`,
    created_at: '2026-08-03T00:00:00Z',
    published_at: '2026-08-03T00:00:00Z',
    is_current: isCurrent,
    current_revision: number,
    preview_url: `/preview/${id}`,
    file_verified: true,
  }
}

function upClassPackage(id: string, status: UpClassPackage['status']): UpClassPackage {
  return {
    id,
    pptx_version_id: 'v'.repeat(32),
    slide_plan_id: 'p'.repeat(32),
    lesson_draft_id: 'd'.repeat(32),
    resource_pack_id: 'k'.repeat(32),
    lesson_node_id: lessonId,
    class_name: null,
    version_number: 1,
    status,
    output_filename: null,
    package_sha256: null,
    manifest: null,
    error_code: null,
    is_current: false,
    staging_retained: true,
    recovery_actions: [],
    created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z',
    completed_at: null,
    download_url: null,
  }
}

interface StepExpose {
  primaryLabel: string
  primaryDisabled: boolean
  runPrimary: () => Promise<void>
}

async function mountStep(options: {
  versions?: TrustedPptxVersion[]
  packages?: UpClassPackage[]
} = {}) {
  const catalog = useTeachingPrepCatalogStore()
  catalog.lessonNodes = [lessonNode]
  catalog.selectedLessonId = lessonId
  catalog.slidePlans = []
  catalog.slidePlanPreview = null
  catalog.pptxExecutions = []
  catalog.upClassPackages = options.packages ?? []

  const workbench = {
    catalog,
    routeState: {
      currentView: computed(() => 'lesson' as const),
      currentLessonId: computed(() => lessonId),
      currentStep: computed(() => 3 as const),
      openOverview: vi.fn(async () => {}),
      openLibrary: vi.fn(async () => {}),
      openLesson: vi.fn(async () => {}),
      setStep: vi.fn(async () => {}),
    },
    lessonStatuses: ref([]),
    selectedLesson: computed(() => lessonNode),
    selectedStatus: computed(() => null),
    referencePreflight: shallowRef(null),
    pptxVersions: ref(options.versions ?? []),
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
      return () => h(CopiesStep, {
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
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('CopiesStep', () => {
  it('renders the version table with current and historical rows', async () => {
    const { app, host } = await mountStep({
      versions: [version('v1', 2, true), version('v2', 1, false)],
    })

    expect(host.textContent).toContain('第 2 版')
    expect(host.textContent).toContain('一次函数_v2.pptx')
    expect(host.textContent).toContain('当前使用')
    expect(host.textContent).toContain('历史')
    app.unmount()
  })

  it('activates a historical version through the workbench API', async () => {
    const old = version('v2', 1, false)
    const { app, host, workbench } = await mountStep({
      versions: [version('v1', 2, true), old],
    })
    const activate = vi.spyOn(teachingPrepWorkbenchApi, 'activatePptxVersion')
      .mockResolvedValue({ changed: true } as never)

    ;[...host.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.trim() === '恢复为当前' && !button.disabled)?.click()
    await vi.waitFor(() => expect(activate).toHaveBeenCalledWith(old.id, old.current_revision))
    await vi.waitFor(() => expect(workbench.refresh).toHaveBeenCalled())
    expect(host.textContent).toContain('已把第 1 版恢复为当前可信 PPTX')
    app.unmount()
  })

  it('disables the primary action until a current version exists', async () => {
    const empty = await mountStep()
    expect(empty.getExpose().primaryLabel).toBe('请先生成 PPTX 副本')
    expect(empty.getExpose().primaryDisabled).toBe(true)
    empty.app.unmount()

    const withVersion = await mountStep({ versions: [version('v1', 1, true)] })
    expect(withVersion.getExpose().primaryLabel).toBe('生成上课包')
    expect(withVersion.getExpose().primaryDisabled).toBe(false)
    withVersion.app.unmount()
  })

  it('creates an up-class package from the current version via the primary action', async () => {
    const { app, catalog, getExpose } = await mountStep({
      versions: [version('v1', 1, true)],
    })
    const createPackage = vi.spyOn(catalog, 'createUpClassPackage').mockResolvedValue({} as never)

    await getExpose().runPrimary()
    expect(createPackage).toHaveBeenCalledWith('v1')
    app.unmount()
  })

  it('discards an interrupted package staging after confirmation', async () => {
    const staging = upClassPackage('u'.repeat(32), 'interrupted')
    const { app, host, catalog } = await mountStep({ packages: [staging] })
    const discard = vi.spyOn(catalog, 'discardUpClassPackageStaging').mockResolvedValue({} as never)
    vi.stubGlobal('confirm', vi.fn(() => true))

    host.querySelector<HTMLButtonElement>('[data-testid="discard-package-staging"]')?.click()
    await vi.waitFor(() => expect(discard).toHaveBeenCalledWith(staging))
    await vi.waitFor(() => expect(host.textContent).toContain('暂存的上课包构建已丢弃'))
    app.unmount()
  })
})
