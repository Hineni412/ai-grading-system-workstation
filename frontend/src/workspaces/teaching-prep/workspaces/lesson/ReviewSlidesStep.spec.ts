import { createPinia, setActivePinia } from 'pinia'
import { computed, createApp, defineComponent, h, nextTick, ref, shallowRef } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { LessonNode, PptxExecution, SlideOperation, SlidePlan } from '../../api/catalog'
import { teachingPrepWorkbenchApi } from '../../api/workbench'
import { useWorkspaceAITaskStore } from '../../../shared/ai-tasks/store'
import { workspaceAITaskApi } from '../../../shared/ai-tasks/api'
import type { WorkspaceAITask } from '../../../shared/ai-tasks/contracts'
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

function operation(
  id: string,
  decision: SlideOperation['decision'] = 'proposed',
  extras: Partial<SlideOperation> = {},
): SlideOperation {
  const { target, ...rest } = extras
  return {
    operation_id: id,
    kind: 'delete_slide',
    decision,
    reason: '与教材例题重复',
    citations: [],
    planned_minutes: 0,
    risk: 'low',
    execution_mode: 'automatic',
    support_note: null,
    details: {},
    teacher_note: null,
    ...rest,
    target: { generated_page_number: 1, ...(target ?? {}) },
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

function runningSlideTask(): WorkspaceAITask {
  return {
    contract_version: 'teacher_workspace_ai_task.v1',
    task_id: 't'.repeat(32),
    operation_id: 'slide-proposal-op-01',
    module: 'teaching_prep',
    task_kind: 'teaching_prep.slide_change_proposal',
    source_ref: { kind: 'lesson', id: lessonId, revision: '1' },
    context_refs: [],
    return_target: 'teaching-prep.lesson.review',
    status: 'running',
    phase: 'model',
    progress: 0.2,
    send_attempt_count: 1,
    dispatch_evidence: 'may_have_started',
    cancel_requested: false,
    job_id: 1,
    proposal_ref_id: null,
    proposal_revision: null,
    error_code: null,
    error_detail: null,
    revision: 1,
    safe_title: '改编课件',
    safe_source: '第一课时',
    teacher_message: '正在逐页分析',
    next_action: '等待结果',
    handoffs: [],
    handoff_total: 0,
    adopted_count: 0,
    discarded_count: 0,
    stale_count: 0,
    pending_count: 0,
    created_at: '2026-08-16T00:00:00Z',
    updated_at: '2026-08-16T00:00:01Z',
    finished_at: null,
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
  before?: Array<Record<string, unknown>>
} = {}) {
  const catalog = useTeachingPrepCatalogStore()
  const beforeSlides = options.before ?? [{ title: '引入页', original_index: 1, preview_url: '/before.png' }]
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
    before_slide_count: beforeSlides.length,
    after_slide_count: beforeSlides.length,
    before: beforeSlides,
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
  vi.useRealTimers()
  vi.restoreAllMocks()
  document.body.innerHTML = ''
  localStorage.removeItem('teacher-platform:tracked-ai-tasks:v1')
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

  it('shows thinking, page fetch and returned preview in the waiting panel', async () => {
    const task = runningSlideTask()
    const previewUrl = `/api/teaching-prep/material-units/${'b'.repeat(32)}/preview`
    vi.spyOn(workspaceAITaskApi, 'get').mockResolvedValue(task)
    vi.spyOn(teachingPrepWorkbenchApi, 'getAdaptationTrace').mockResolvedValue({
      operation_id: task.operation_id,
      lesson_node_id: lessonId,
      status: 'running',
      model_calls_used: 2,
      model_calls_max: 6,
      events: [
        {
          round: 1,
          phase: 'thinking',
          summary: '模型正在分析本课目录和已取原页。',
          thinking_excerpt: '先看教材第 1 页',
          tool: null,
          result: null,
          model_calls_used: 1,
          model_calls_max: 6,
        },
        {
          round: 1,
          phase: 'tool_call',
          summary: '取页 · 教材 第 1 页',
          thinking_excerpt: null,
          tool: {
            name: 'get_frozen_page',
            purpose: 'textbook',
            page: 1,
            source_ref: `material:${'a'.repeat(32)}:unit:1`,
          },
          result: null,
          model_calls_used: 1,
          model_calls_max: 6,
        },
        {
          round: 1,
          phase: 'tool_result',
          summary: '已返回教材 第 1 页',
          thinking_excerpt: null,
          tool: {
            name: 'get_frozen_page',
            purpose: 'textbook',
            page: 1,
            source_ref: `material:${'a'.repeat(32)}:unit:1`,
          },
          result: {
            ok: true,
            label: '教材 第 1 页',
            preview_url: previewUrl,
          },
          model_calls_used: 2,
          model_calls_max: 6,
        },
      ],
    })
    const aiTasks = useWorkspaceAITaskStore()
    aiTasks.track(task)
    const { app, host } = await mountStep()

    await vi.waitFor(() => {
      expect(host.querySelector('[data-testid="adaptation-trace"]')?.textContent).toContain('第 2 / 6 轮')
    })
    expect(host.querySelector('.tp-adaptation-trace__group-head')?.textContent).toContain('第 1 轮 · 进行中')
    expect(host.textContent).toContain('取页 · 教材 第 1 页 ✓')
    expect(host.textContent).not.toContain('已返回教材 第 1 页')
    expect(host.textContent).not.toContain('先看教材第 1 页')
    const toggle = host.querySelector<HTMLButtonElement>('.tp-adaptation-trace__thinking-toggle')
    expect(toggle?.textContent).toContain('查看模型思考')
    toggle?.click()
    await nextTick()
    expect(host.textContent).toContain('先看教材第 1 页')
    expect(host.querySelector('.tp-adaptation-trace__thumb')?.getAttribute('src')).toBe(previewUrl)
    aiTasks.remove(task.task_id)
    app.unmount()
  })

  it('groups trace events by round with compact lines, thumbs and tone markers', async () => {
    const task = runningSlideTask()
    const tool = (page: number) => ({
      name: 'get_frozen_page',
      purpose: 'textbook',
      page,
      source_ref: `material:${'a'.repeat(32)}:unit:${page}`,
    })
    const fetchPair = (page: number, round: number) => ([
      {
        round,
        phase: 'tool_call',
        summary: `取页 · 教材 第 ${page} 页`,
        thinking_excerpt: null,
        tool: tool(page),
        result: null,
        model_calls_used: 1,
        model_calls_max: 6,
      },
      {
        round,
        phase: 'tool_result',
        summary: `已返回教材 第 ${page} 页`,
        thinking_excerpt: null,
        tool: tool(page),
        result: {
          ok: true,
          label: `教材 第 ${page} 页`,
          preview_url: `/preview/page-${page}.png`,
        },
        model_calls_used: 1,
        model_calls_max: 6,
      },
    ])
    vi.spyOn(workspaceAITaskApi, 'get').mockResolvedValue(task)
    vi.spyOn(teachingPrepWorkbenchApi, 'getAdaptationTrace').mockResolvedValue({
      operation_id: task.operation_id,
      lesson_node_id: lessonId,
      status: 'running',
      model_calls_used: 3,
      model_calls_max: 6,
      events: [
        {
          round: 0,
          phase: 'started',
          summary: '已发送本课目录，等待模型按页取图。',
          thinking_excerpt: null,
          tool: null,
          result: null,
          model_calls_used: 0,
          model_calls_max: 6,
        },
        ...fetchPair(10, 1),
        ...fetchPair(11, 1),
        {
          round: 1,
          phase: 'round_retry',
          summary: '第 1 轮遇到可恢复异常，已自动重试一次。',
          thinking_excerpt: null,
          tool: null,
          result: null,
          model_calls_used: 2,
          model_calls_max: 6,
        },
        {
          round: 1,
          phase: 'round_done',
          summary: '第 1 轮已取回 2 页。',
          thinking_excerpt: null,
          tool: null,
          result: null,
          model_calls_used: 2,
          model_calls_max: 6,
        },
        {
          round: 2,
          phase: 'tool_call',
          summary: '取页 · 教材 第 12 页',
          thinking_excerpt: null,
          tool: tool(12),
          result: null,
          model_calls_used: 3,
          model_calls_max: 6,
        },
      ],
    })
    const aiTasks = useWorkspaceAITaskStore()
    aiTasks.track(task)
    const { app, host } = await mountStep()

    await vi.waitFor(() => {
      expect(host.textContent).toContain('第 1 轮已取回 2 页。')
    })
    const heads = [...host.querySelectorAll('.tp-adaptation-trace__group-head')].map(item => item.textContent)
    expect(heads).toEqual(['开始', '第 1 轮已取回 2 页。', '第 2 轮 · 进行中'])
    expect(host.textContent).toContain('已发送本课目录，等待模型按页取图。')
    expect(host.textContent).toContain('取页 · 教材 第 10 页 ✓')
    expect(host.textContent).toContain('取页 · 教材 第 12 页 …')
    const retryLine = [...host.querySelectorAll('.tp-adaptation-trace__line')]
      .find(item => item.textContent?.includes('已自动重试一次'))
    expect(retryLine?.classList.contains('is-warn')).toBe(true)
    const thumbs = host.querySelectorAll('.tp-adaptation-trace__thumbs .tp-adaptation-trace__thumb')
    expect(thumbs).toHaveLength(2)
    expect(thumbs[0]?.getAttribute('src')).toBe('/preview/page-10.png')
    aiTasks.remove(task.task_id)
    app.unmount()
  })

  it('opens a large preview dialog when a fetched page thumb is clicked', async () => {
    const task = runningSlideTask()
    vi.spyOn(workspaceAITaskApi, 'get').mockResolvedValue(task)
    vi.spyOn(teachingPrepWorkbenchApi, 'getAdaptationTrace').mockResolvedValue({
      operation_id: task.operation_id,
      lesson_node_id: lessonId,
      status: 'running',
      model_calls_used: 2,
      model_calls_max: 6,
      events: [
        {
          round: 1,
          phase: 'tool_result',
          summary: '已返回教材 第 10 页',
          thinking_excerpt: null,
          tool: null,
          result: { ok: true, label: '教材 第 10 页', preview_url: '/preview/page-10.png' },
          model_calls_used: 2,
          model_calls_max: 6,
        },
      ],
    })
    const aiTasks = useWorkspaceAITaskStore()
    aiTasks.track(task)
    const { app, host } = await mountStep()

    await vi.waitFor(() => {
      expect(host.querySelector('.tp-adaptation-trace__thumb-button')).toBeTruthy()
    })
    host.querySelector<HTMLButtonElement>('.tp-adaptation-trace__thumb-button')?.click()
    await nextTick()
    await nextTick()
    const dialog = host.querySelector<HTMLDialogElement>('.tp-adaptation-trace__preview')
    expect(dialog?.querySelector('img')?.getAttribute('src')).toBe('/preview/page-10.png')
    aiTasks.remove(task.task_id)
    app.unmount()
  })

  it('shows the failed summary in full as an ending line', async () => {
    const task = { ...runningSlideTask(), status: 'failed' as const }
    vi.spyOn(workspaceAITaskApi, 'get').mockResolvedValue(task)
    vi.spyOn(teachingPrepWorkbenchApi, 'getAdaptationTrace').mockResolvedValue({
      operation_id: task.operation_id,
      lesson_node_id: lessonId,
      status: 'failed',
      model_calls_used: 2,
      model_calls_max: 6,
      events: [
        {
          round: 1,
          phase: 'round_done',
          summary: '第 1 轮已取回 4 页。',
          thinking_excerpt: null,
          tool: null,
          result: null,
          model_calls_used: 2,
          model_calls_max: 6,
        },
        {
          round: 1,
          phase: 'failed',
          summary: '改编清单校验失败：第 2 项缺少目标页码。',
          thinking_excerpt: null,
          tool: null,
          result: null,
          model_calls_used: 2,
          model_calls_max: 6,
        },
      ],
    })
    const aiTasks = useWorkspaceAITaskStore()
    aiTasks.track(task)
    const { app, host } = await mountStep()

    await vi.waitFor(() => {
      expect(host.textContent).toContain('改编清单校验失败：第 2 项缺少目标页码。')
    })
    const failedLine = [...host.querySelectorAll('.tp-adaptation-trace__line')]
      .find(item => item.textContent?.includes('校验失败'))
    expect(failedLine?.classList.contains('is-error')).toBe(true)
    expect(host.querySelector('[data-testid="adaptation-wait-hint"]')).toBeNull()
    aiTasks.remove(task.task_id)
    app.unmount()
  })

  it('ticks a wait hint while running and hides it once the trace ends', async () => {
    vi.useFakeTimers()
    const task = runningSlideTask()
    const runningTrace = {
      operation_id: task.operation_id,
      lesson_node_id: lessonId,
      status: 'running',
      model_calls_used: 1,
      model_calls_max: 6,
      events: [
        {
          round: 1,
          phase: 'thinking',
          summary: '模型正在分析本课目录和已取原页。',
          thinking_excerpt: null,
          tool: null,
          result: null,
          model_calls_used: 1,
          model_calls_max: 6,
        },
      ],
    }
    const traceSpy = vi.spyOn(teachingPrepWorkbenchApi, 'getAdaptationTrace').mockResolvedValue(runningTrace)
    vi.spyOn(workspaceAITaskApi, 'get').mockResolvedValue(task)
    const aiTasks = useWorkspaceAITaskStore()
    aiTasks.track(task)
    const { app, host } = await mountStep()

    const hintText = () => host.querySelector('[data-testid="adaptation-wait-hint"]')?.textContent ?? ''
    const elapsedSeconds = () => {
      const match = hintText().match(/已等待 (?:(\d+) 分 )?(\d+) 秒/)
      return match ? Number(match[1] ?? 0) * 60 + Number(match[2]) : -1
    }

    await vi.waitFor(() => {
      expect(hintText()).toContain('本轮请求已发出')
    })
    const before = elapsedSeconds()
    expect(before).toBeGreaterThanOrEqual(0)

    await vi.advanceTimersByTimeAsync(83_000)
    const after = elapsedSeconds()
    expect(after - before).toBeGreaterThanOrEqual(83)
    expect(after - before).toBeLessThanOrEqual(84)
    expect(hintText()).toContain('分')

    traceSpy.mockResolvedValue({
      ...runningTrace,
      status: 'succeeded',
      events: [
        ...runningTrace.events,
        {
          round: 1,
          phase: 'final_accepted',
          summary: '已收到改编清单。',
          thinking_excerpt: null,
          tool: null,
          result: null,
          model_calls_used: 2,
          model_calls_max: 6,
        },
      ],
    })
    await vi.advanceTimersByTimeAsync(2_500)
    await nextTick()
    expect(host.querySelector('[data-testid="adaptation-wait-hint"]')).toBeNull()
    expect(host.textContent).toContain('已收到改编清单。')
    aiTasks.remove(task.task_id)
    app.unmount()
  })

  it('shows the summary bar and compact page rows with change tags', async () => {
    const plan = slidePlan([operation('op-1'), operation('op-2')])
    const { app, host, catalog } = await mountStep({ plans: [plan] })

    await vi.waitFor(() => expect(catalog.reviewSlidePlan).toHaveBeenCalled())
    const summary = host.querySelector('[data-testid="review-summary"]')
    expect(summary?.textContent).toContain('计划 1')
    expect(summary?.textContent).toContain('共 1 页 · 有改动 1 页')
    expect(summary?.textContent).toContain('删 2 处')
    const row = host.querySelector('.tp-slide-checker__page-row')
    expect(row?.querySelector('.tp-slide-checker__page-no')?.textContent).toBe('1')
    expect(row?.querySelector('.tp-slide-checker__page-title')?.textContent).toBe('引入页')
    expect(row?.querySelector('.tp-page-tag.is-delete')?.textContent).toBe('删')
    expect(row?.querySelector('img')).toBeNull()
    app.unmount()
  })

  it('filters to changed pages only and falls back to a visible selection', async () => {
    const plan = slidePlan(
      [operation('op-1', 'approved', { target: { generated_page_number: 2 } })],
      'approved',
    )
    const { app, host } = await mountStep({
      plans: [plan],
      executions: [execution('verifying', { verification_report: { verified: true }, expected_slide_count: 2 })],
      before: [
        { title: '引入页', original_index: 1, preview_url: '/p1.png' },
        { title: '例题页', original_index: 2, preview_url: '/p2.png' },
      ],
    })

    expect(host.querySelectorAll('.tp-slide-checker__page-row')).toHaveLength(2)
    const rows = host.querySelectorAll<HTMLButtonElement>('.tp-slide-checker__page-row')
    rows[0]?.click()
    await nextTick()
    expect(host.querySelector('.tp-slide-checker__canvas header strong')?.textContent).toBe('第 1 页')

    const toggle = host.querySelector<HTMLInputElement>('[data-testid="filter-changed-only"]')
    toggle?.click()
    await nextTick()
    const visible = host.querySelectorAll('.tp-slide-checker__page-row')
    expect(visible).toHaveLength(1)
    expect(visible[0]?.textContent).toContain('例题页')
    expect(host.querySelector('.tp-slide-checker__canvas header strong')?.textContent).toBe('第 2 页')
    app.unmount()
  })

  it('renders no card for keep_slide and shows the kept hint', async () => {
    const keep = operation('op-keep', 'approved', { kind: 'keep_slide', execution_mode: 'noop' })
    const plan = slidePlan([keep], 'approved')
    const { app, host } = await mountStep({ plans: [plan] })

    expect(host.querySelectorAll('.tp-operation-row')).toHaveLength(0)
    expect(host.querySelector('[data-testid="page-kept"]')?.textContent).toContain('本页保留，无改动')
    expect(host.querySelector('[data-testid="review-summary"]')?.textContent).toContain('无实质改动')
    expect(host.querySelector('[data-testid="simulated-after"]')).toBeTruthy()
    expect(host.querySelectorAll('[data-testid="simulated-overlay-box"]')).toHaveLength(0)
    expect(host.querySelector('[data-testid="simulated-opsbar"]')).toBeNull()
    app.unmount()
  })

  it('uses target content_summary as the card title with a category chip', async () => {
    const op = operation('op-1', 'approved', {
      kind: 'delete_shape',
      target: { generated_page_number: 1, content_summary: '原大棚应用题' },
      reason: '与教材例题重复。第二句。',
      citations: ['material:abc123:unit:5', 'exercise:ex-9'],
      planned_minutes: 3,
      risk: 'medium',
    })
    const plan = slidePlan([op], 'approved')
    plan.payload.source_presentations = [{ link_id: 'abc123', material_name: '三下教材' }]
    const { app, host } = await mountStep({ plans: [plan] })

    expect(host.querySelector('.tp-operation-row__title')?.textContent).toBe('原大棚应用题')
    const chip = host.querySelector('[data-testid="operation-kind"]')
    expect(chip?.textContent).toBe('删除页内内容')
    expect(chip?.classList.contains('is-delete')).toBe(true)
    const meta = host.querySelector('.tp-operation-row__meta')?.textContent ?? ''
    expect(meta).toContain('三下教材 第 5 页')
    expect(meta).toContain('题库候选题')
    expect(meta).toContain('中风险')
    expect(meta).toContain('预计 3 分钟')
    app.unmount()
  })

  it('renders Chinese labels for every operation kind without English fallback', async () => {
    const kinds: Array<[string, string, SlideOperation['execution_mode']]> = [
      ['delete_slide', '删除整页', 'automatic'],
      ['reorder_slide', '调整页序', 'automatic'],
      ['delete_shape', '删除页内内容', 'automatic'],
      ['add_text_box', '添加文本标注', 'automatic'],
      ['add_slide', '新增页面', 'automatic'],
      ['insert_static_image', '插入图片', 'automatic'],
      ['move_static_image', '移动图片', 'automatic'],
      ['scale_static_image', '缩放图片', 'automatic'],
      ['crop_static_image', '裁剪图片', 'automatic'],
      ['replace_static_image', '替换图片', 'automatic'],
      ['hide_slide', '隐藏本页', 'manual_only'],
      ['copy_slide', '复制本页', 'manual_only'],
      ['modify_text_box', '修改文本', 'manual_only'],
      ['manual_note', '人工处理', 'manual_only'],
    ]
    const ops = kinds.map(([kind, , mode], index) => operation(`op-${kind}`, 'approved', {
      kind,
      execution_mode: mode,
      target: { generated_page_number: 1, content_summary: `对象${index}` },
    }))
    const plan = slidePlan(ops, 'approved')
    const { app, host } = await mountStep({ plans: [plan] })

    const chips = [...host.querySelectorAll('[data-testid="operation-kind"]')].map(item => item.textContent)
    expect(chips).toEqual(kinds.map(([, label]) => label))
    expect(host.querySelectorAll('.tp-op-flag')).toHaveLength(4)
    const inspector = host.querySelector('.tp-slide-checker__details')?.textContent ?? ''
    for (const [kind] of kinds) {
      expect(inspector).not.toContain(kind)
    }
    app.unmount()
  })

  it('defaults manual-only proposals to not adopted', async () => {
    const op = operation('op-1', 'proposed', { kind: 'hide_slide', execution_mode: 'manual_only' })
    const plan = slidePlan([op])
    const { app, host, catalog } = await mountStep({ plans: [plan] })

    await vi.waitFor(() => expect(catalog.reviewSlidePlan).toHaveBeenCalled())
    expect(vi.mocked(catalog.reviewSlidePlan).mock.calls[0]?.[1]).toEqual([
      expect.objectContaining({ operation_id: 'op-1', decision: 'rejected' }),
    ])
    await nextTick()
    const reject = host.querySelector<HTMLButtonElement>('[data-testid="operation-reject"]')
    expect(reject?.classList.contains('is-on')).toBe(true)
    expect(host.querySelector('.tp-op-flag')?.textContent).toContain('需人工在 WPS 中处理')
    app.unmount()
  })

  it('saves the decision and regenerates the preview when toggled', async () => {
    const plan = slidePlan([operation('op-1', 'approved')], 'approved')
    const { app, host, catalog } = await mountStep({ plans: [plan], wps: true })

    await vi.waitFor(() => expect(catalog.executePptx).toHaveBeenCalledTimes(1))
    vi.mocked(catalog.reviewSlidePlan).mockImplementation(async () => {
      catalog.pptxExecutions = []
    })
    const reject = host.querySelector<HTMLButtonElement>('[data-testid="operation-reject"]')
    expect(reject?.disabled).toBe(false)
    reject?.click()
    await vi.waitFor(() => expect(catalog.reviewSlidePlan).toHaveBeenCalled())
    expect(vi.mocked(catalog.reviewSlidePlan).mock.calls[0]?.[1]).toEqual([
      expect.objectContaining({ operation_id: 'op-1', decision: 'rejected' }),
    ])
    await vi.waitFor(() => expect(catalog.executePptx).toHaveBeenCalledTimes(2))
    app.unmount()
  })

  it('rolls back the toggle when saving fails', async () => {
    const plan = slidePlan([operation('op-1', 'approved')], 'approved')
    const { app, host, catalog } = await mountStep({
      plans: [plan],
      executions: [execution('verifying', { verification_report: { verified: true } })],
    })

    vi.mocked(catalog.reviewSlidePlan).mockRejectedValue(new Error('boom'))
    host.querySelector<HTMLButtonElement>('[data-testid="operation-reject"]')?.click()
    await vi.waitFor(() => expect(host.textContent).toContain('改编草稿还没有保存成功'))
    const approve = host.querySelector<HTMLButtonElement>('[data-testid="operation-approve"]')
    expect(approve?.classList.contains('is-on')).toBe(true)
    app.unmount()
  })

  it('draws a red delete overlay at the planned position in the simulated after view', async () => {
    const op = operation('op-1', 'approved', {
      kind: 'delete_shape',
      target: {
        generated_page_number: 1,
        content_summary: '原大棚应用题',
        position: { x: 0.1, y: 0.2, width: 0.4, height: 0.3 },
      },
    })
    const plan = slidePlan([op], 'approved')
    const { app, host } = await mountStep({ plans: [plan] })

    expect(host.textContent).toContain('模拟改后 · 示意')
    const sim = host.querySelector('[data-testid="simulated-after"]')
    expect(sim).toBeTruthy()
    expect(sim?.querySelector('.tp-sim-canvas__base')?.getAttribute('src')).toContain('/before.png')
    const box = host.querySelector<HTMLElement>('[data-testid="simulated-overlay-box"]')
    expect(box?.classList.contains('is-delete')).toBe(true)
    expect(box?.style.left).toBe('10%')
    expect(box?.style.top).toBe('20%')
    expect(box?.style.width).toBe('40%')
    expect(box?.style.height).toBe('30%')
    expect(box?.querySelector('.tp-sim-box__tag')?.textContent).toBe('删')
    expect(host.querySelector('[data-testid="simulated-opsbar"]')).toBeNull()
    app.unmount()
  })

  it('loads the cropped question image for insert operations at the planned position', async () => {
    const op = operation('op-1', 'approved', {
      kind: 'insert_static_image',
      target: {
        generated_page_number: 1,
        content_summary: '插入练习题',
        position: { x: 0.5, y: 0.5, width: 0.25, height: 0.2 },
      },
      details: { asset_ref: '/crop/question-9.png' },
    })
    const plan = slidePlan([op], 'approved')
    const { app, host } = await mountStep({ plans: [plan] })

    const box = host.querySelector<HTMLElement>('[data-testid="simulated-overlay-box"]')
    expect(box?.classList.contains('is-insert')).toBe(true)
    expect(box?.style.left).toBe('50%')
    const image = box?.querySelector('.tp-sim-box__image')
    expect(image?.getAttribute('src')).toBe('/crop/question-9.png')
    expect(box?.querySelector('.tp-sim-box__tag')?.textContent).toBe('插')
    app.unmount()
  })

  it('draws a dashed note box with the planned text for text annotations', async () => {
    const op = operation('op-1', 'approved', {
      kind: 'add_text_box',
      target: {
        generated_page_number: 1,
        position: { x: 0.05, y: 0.85, width: 0.3, height: 0.1 },
      },
      details: { text: '教材第10-11页' },
    })
    const plan = slidePlan([op], 'approved')
    const { app, host } = await mountStep({ plans: [plan] })

    const box = host.querySelector<HTMLElement>('[data-testid="simulated-overlay-box"]')
    expect(box?.classList.contains('is-note')).toBe(true)
    expect(box?.querySelector('.tp-sim-box__text')?.textContent).toBe('教材第10-11页')
    expect(box?.querySelector('.tp-sim-box__tag')?.textContent).toBe('注')
    app.unmount()
  })

  it('does not draw overlay boxes for rejected operations', async () => {
    const op = operation('op-1', 'rejected', {
      kind: 'delete_shape',
      target: {
        generated_page_number: 1,
        content_summary: '原大棚应用题',
        position: { x: 0.1, y: 0.2, width: 0.4, height: 0.3 },
      },
    })
    const plan = slidePlan([op], 'approved')
    const { app, host } = await mountStep({ plans: [plan] })

    expect(host.querySelectorAll('[data-testid="simulated-overlay-box"]')).toHaveLength(0)
    expect(host.querySelector('[data-testid="simulated-opsbar"]')).toBeNull()
    expect(host.querySelector('.tp-operation-row.is-rejected')).toBeTruthy()
    app.unmount()
  })

  it('falls back to a top label bar for operations without a position', async () => {
    const op = operation('op-1', 'approved', {
      kind: 'reorder_slide',
      target: { generated_page_number: 1, content_summary: '移到例题之后' },
    })
    const plan = slidePlan([op], 'approved')
    const { app, host } = await mountStep({ plans: [plan] })

    const bar = host.querySelector('[data-testid="simulated-opsbar"]')
    expect(bar?.textContent).toContain('调整页序')
    expect(host.querySelectorAll('[data-testid="simulated-overlay-box"]')).toHaveLength(0)
    app.unmount()
  })

  it('shows the before image from preview_url and offers retry when it fails to load', async () => {
    const plan = slidePlan([operation('op-1', 'approved')], 'approved')
    const { app, host } = await mountStep({
      plans: [plan],
      executions: [execution('verifying', { verification_report: { verified: true } })],
    })

    const beforeImage = host.querySelector<HTMLImageElement>('section[aria-label="改前页"] .tp-slide-stage img')
    expect(beforeImage?.getAttribute('src')).toContain('/before.png')
    beforeImage?.dispatchEvent(new Event('error'))
    await nextTick()
    expect(host.querySelector('[data-testid="before-load-failed"]')).toBeTruthy()
    host.querySelector<HTMLButtonElement>('[data-testid="before-retry"]')?.click()
    await nextTick()
    const retried = host.querySelector<HTMLImageElement>('section[aria-label="改前页"] .tp-slide-stage img')
    expect(retried?.getAttribute('src')).toContain('/before.png')
    expect(retried?.getAttribute('src')).toContain('retry=1')
    app.unmount()
  })

  it('shows the real rendered copy instead of the simulation once the preview is ready', async () => {
    const op = operation('op-1', 'approved', {
      kind: 'delete_shape',
      target: {
        generated_page_number: 1,
        position: { x: 0.1, y: 0.2, width: 0.4, height: 0.3 },
      },
    })
    const plan = slidePlan([op], 'approved')
    const { app, host } = await mountStep({
      plans: [plan],
      executions: [execution('verifying', { verification_report: { verified: true } })],
    })

    expect(host.querySelector('[data-testid="simulated-after"]')).toBeNull()
    expect(host.textContent).toContain('已渲染副本')
    const afterImage = host.querySelector('section[aria-label="改后页"] .tp-slide-stage img')
    expect(afterImage?.getAttribute('src')).toBe(
      `/api/teaching-prep/pptx-executions/${'e'.repeat(32)}/preview?slide=1`,
    )
    app.unmount()
  })

  it('renders preliminary review findings from a findings_ready trace event', async () => {
    const task = runningSlideTask()
    vi.spyOn(workspaceAITaskApi, 'get').mockResolvedValue(task)
    vi.spyOn(teachingPrepWorkbenchApi, 'getAdaptationTrace').mockResolvedValue({
      operation_id: task.operation_id,
      lesson_node_id: lessonId,
      status: 'running',
      model_calls_used: 1,
      model_calls_max: 6,
      events: [
        {
          round: 1,
          phase: 'findings_ready',
          summary: '已给出初步审课发现 2 条（细看后可能修正）',
          thinking_excerpt: null,
          tool: null,
          result: null,
          model_calls_used: 1,
          model_calls_max: 6,
          findings: [
            { finding: '例题与教材第 3 页重复', category: 'content', pages: [3, 5] },
            { finding: '练习总量偏少', category: 'practice_load', pages: [] },
          ],
        },
      ],
    })
    const aiTasks = useWorkspaceAITaskStore()
    aiTasks.track(task)
    const { app, host } = await mountStep()

    await vi.waitFor(() => {
      expect(host.querySelector('[data-testid="trace-findings"]')).toBeTruthy()
    })
    const card = host.querySelector('[data-testid="trace-findings"]')!
    expect(card.textContent).toContain('初步审课发现（细看后可能修正）')
    expect(card.textContent).not.toContain('已给出初步审课发现 2 条')
    const items = card.querySelectorAll('.tp-findings-item')
    expect(items).toHaveLength(2)
    expect(items[0]?.querySelector('.tp-finding-chip')?.textContent).toBe('内容')
    expect(items[0]?.textContent).toContain('例题与教材第 3 页重复')
    expect(items[0]?.textContent).toContain('涉及第 3、5 页')
    expect(items[1]?.querySelector('.tp-finding-chip')?.textContent).toBe('练习量')
    expect(items[1]?.textContent).not.toContain('涉及第')
    aiTasks.remove(task.task_id)
    app.unmount()
  })

  it('falls back to the summary line when findings_ready carries no findings', async () => {
    const task = runningSlideTask()
    vi.spyOn(workspaceAITaskApi, 'get').mockResolvedValue(task)
    vi.spyOn(teachingPrepWorkbenchApi, 'getAdaptationTrace').mockResolvedValue({
      operation_id: task.operation_id,
      lesson_node_id: lessonId,
      status: 'running',
      model_calls_used: 1,
      model_calls_max: 6,
      events: [
        {
          round: 1,
          phase: 'findings_ready',
          summary: '已给出初步审课发现 3 条（细看后可能修正）',
          thinking_excerpt: null,
          tool: null,
          result: null,
          model_calls_used: 1,
          model_calls_max: 6,
        },
      ],
    })
    const aiTasks = useWorkspaceAITaskStore()
    aiTasks.track(task)
    const { app, host } = await mountStep()

    await vi.waitFor(() => {
      expect(host.textContent).toContain('已给出初步审课发现 3 条（细看后可能修正）')
    })
    expect(host.querySelector('[data-testid="trace-findings"]')).toBeNull()
    aiTasks.remove(task.task_id)
    app.unmount()
  })

  it('renders review findings in the compare panel and jumps to the referenced page', async () => {
    const plan = slidePlan([operation('op-1', 'approved')], 'approved')
    plan.payload.slides = [
      { source_link_id: 'link1', original_index: 1, title: '引入页' },
      { source_link_id: 'link1', original_index: 2, title: '例题页' },
      { source_link_id: 'link1', original_index: 3, title: '练习页' },
    ]
    plan.payload.review_findings = [
      {
        slide_refs: ['material:link1:unit:3'],
        finding: '练习页题目梯度跳跃过大',
        category: 'sequence',
        suggested_action: '在第 2 页后补一道过渡题',
        citations: [],
      },
      {
        slide_refs: ['material:unknown:unit:9'],
        finding: '整体小结缺失',
        category: 'other',
        suggested_action: '',
        citations: [],
      },
    ]
    const { app, host } = await mountStep({
      plans: [plan],
      executions: [execution('verifying', { verification_report: { verified: true }, expected_slide_count: 3 })],
      before: [
        { title: '引入页', original_index: 1, preview_url: '/p1.png' },
        { title: '例题页', original_index: 2, preview_url: '/p2.png' },
        { title: '练习页', original_index: 3, preview_url: '/p3.png' },
      ],
    })

    const section = host.querySelector('[data-testid="review-findings"]')
    expect(section).toBeTruthy()
    const items = section!.querySelectorAll('.tp-findings-item')
    expect(items).toHaveLength(2)
    expect(items[0]?.querySelector('.tp-finding-chip')?.textContent).toBe('顺序')
    expect(items[0]?.textContent).toContain('练习页题目梯度跳跃过大')
    expect(items[0]?.textContent).toContain('建议：在第 2 页后补一道过渡题')
    const links = items[0]?.querySelectorAll('[data-testid="finding-page-link"]') ?? []
    expect(links).toHaveLength(1)
    expect(links[0]?.textContent).toBe('第 3 页')
    expect(items[1]?.querySelectorAll('[data-testid="finding-page-link"]')).toHaveLength(0)
    expect(items[1]?.textContent).not.toContain('建议：')

    host.querySelector<HTMLInputElement>('[data-testid="filter-changed-only"]')?.click()
    await nextTick()
    expect(host.querySelectorAll('.tp-slide-checker__page-row')).toHaveLength(1)
    host.querySelector<HTMLButtonElement>('[data-testid="finding-page-link"]')?.click()
    await nextTick()
    expect(host.querySelector<HTMLInputElement>('[data-testid="filter-changed-only"]')?.checked).toBe(false)
    expect(host.querySelector('.tp-slide-checker__canvas header strong')?.textContent).toBe('第 3 页')
    app.unmount()
  })

  it('collapses long review finding lists and expands them on demand', async () => {
    const plan = slidePlan([operation('op-1', 'approved')], 'approved')
    plan.payload.review_findings = ['一', '二', '三', '四'].map((mark, index) => ({
      slide_refs: [],
      finding: `发现${mark}`,
      category: 'content',
      suggested_action: '',
      citations: [`cite-${index}`],
    }))
    const { app, host } = await mountStep({ plans: [plan] })

    const section = host.querySelector('[data-testid="review-findings"]')!
    expect(section).toBeTruthy()
    expect(section.querySelectorAll('.tp-findings-item')).toHaveLength(3)
    const toggle = host.querySelector<HTMLButtonElement>('[data-testid="findings-toggle"]')
    expect(toggle?.textContent).toBe('展开全部 4 条')
    toggle?.click()
    await nextTick()
    expect(section.querySelectorAll('.tp-findings-item')).toHaveLength(4)
    expect(host.querySelector('[data-testid="findings-toggle"]')?.textContent).toBe('收起')
    host.querySelector<HTMLButtonElement>('[data-testid="findings-toggle"]')?.click()
    await nextTick()
    expect(section.querySelectorAll('.tp-findings-item')).toHaveLength(3)
    app.unmount()
  })

  it('renders no findings section for an old plan without review_findings', async () => {
    const plan = slidePlan([operation('op-1', 'approved')], 'approved')
    const { app, host } = await mountStep({ plans: [plan] })

    expect(host.querySelector('[data-testid="review-findings"]')).toBeNull()
    app.unmount()
  })
})
